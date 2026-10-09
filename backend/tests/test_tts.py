from __future__ import annotations

import json
import tempfile
import threading
import unittest
import wave
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.gpt_sovits import AudioFragment
from app.main import create_app
from app.tts import PiperTTSService, TTSBusy, TTSSynthesisFailed, TTSUnavailable


class FakeVoice:
    def synthesize_wav(self, text: str, wav_file: wave.Wave_write) -> None:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22_050)
        wav_file.writeframes(b"\x00\x00" * max(1, len(text)))


class FakeTTSService:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[str] = []
        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            FakeVoice().synthesize_wav("测试", wav_file)
        self.wav = output.getvalue()

    def status(self) -> dict[str, object]:
        return {"enabled": True, "status": "ready"}

    def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if self.error:
            raise self.error
        return self.wav


class FakeStreamingTTSService:
    def __init__(self, wav: bytes) -> None:
        self.wav = wav
        self.error: Exception | None = None
        self.calls: list[str] = []
        self.closed = False
        self.cancelled = False

    def warmup(self) -> None:
        return None

    def stream(self, text: str):
        self.calls.append(text)
        if self.error:
            raise self.error
        yield AudioFragment(0, self.wav, "gpt-sovits", "cuda")
        yield AudioFragment(1, self.wav, "gpt-sovits", "cuda")

    def cancel(self, _request_id=None) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "status": "ready",
            "primary_engine": "gpt-sovits",
            "device": "cuda",
            "fallback_ready": True,
            "last_error": None,
        }


class PiperTTSServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp_dir.name) / "voice.onnx"
        self.model_path.write_bytes(b"model")
        Path(f"{self.model_path}.json").write_text("{}", encoding="utf-8")
        self.settings = Settings(
            data_dir=Path(self.temp_dir.name) / "data",
            tts_enabled=True,
            tts_model_path=self.model_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_synthesizes_valid_wav_in_memory(self) -> None:
        service = PiperTTSService(self.settings, voice_loader=lambda _: FakeVoice())
        self.assertEqual(service.status()["status"], "configured")
        audio = service.synthesize("你好")
        self.assertTrue(audio.startswith(b"RIFF"))
        with wave.open(BytesIO(audio), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 22_050)
            self.assertGreater(wav_file.getnframes(), 0)
        self.assertEqual(service.status()["status"], "ready")

    def test_missing_model_is_unavailable(self) -> None:
        self.model_path.unlink()
        service = PiperTTSService(self.settings, voice_loader=lambda _: FakeVoice())
        self.assertEqual(service.status()["status"], "unavailable")
        with self.assertRaises(TTSUnavailable):
            service.synthesize("你好")

    def test_concurrent_synthesis_is_rejected(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingVoice(FakeVoice):
            def synthesize_wav(self, text: str, wav_file: wave.Wave_write) -> None:
                started.set()
                release.wait(timeout=2)
                super().synthesize_wav(text, wav_file)

        service = PiperTTSService(self.settings, voice_loader=lambda _: BlockingVoice())
        worker = threading.Thread(target=lambda: service.synthesize("第一句"))
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        with self.assertRaises(TTSBusy):
            service.synthesize("第二句")
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())


class TTSApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token = "tts-test-token"
        self.origin = "http://testserver"
        self.settings = Settings(
            data_dir=Path(self.temp_dir.name),
            session_token=self.token,
            allowed_origin=self.origin,
            tts_enabled=True,
            tts_model_path=Path(self.temp_dir.name) / "voice.onnx",
        )
        self.tts = FakeTTSService()
        self.streaming_tts = FakeStreamingTTSService(self.tts.wav)
        self.client = TestClient(
            create_app(
                self.settings,
                tts_service=self.tts,
                streaming_tts_service=self.streaming_tts,
            )
        )
        self.headers = {"X-Session-Token": self.token, "Origin": self.origin}

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def synthesize(self, text: str):
        return self.client.post("/api/v1/tts/synthesize", headers=self.headers, json={"text": text})

    def test_returns_no_store_wav_and_trimmed_text(self) -> None:
        response = self.synthesize("  你好  ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.content.startswith(b"RIFF"))
        self.assertEqual(self.tts.calls, ["你好"])
        health = self.client.get("/api/v1/health").json()["data"]
        self.assertTrue(health["tts"]["enabled"])
        self.assertEqual(health["tts"]["status"], "ready")

    def test_requires_session_and_origin(self) -> None:
        self.assertEqual(self.client.post("/api/v1/tts/synthesize", json={"text": "你好"}).status_code, 401)
        wrong = {"X-Session-Token": self.token, "Origin": "https://example.invalid"}
        self.assertEqual(self.client.post("/api/v1/tts/synthesize", headers=wrong, json={"text": "你好"}).status_code, 401)
        self.assertEqual(self.tts.calls, [])

    def test_rejects_empty_and_overlong_text(self) -> None:
        empty = self.synthesize("   ")
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(empty.json()["error"]["code"], "TTS_EMPTY_TEXT")
        overlong = self.synthesize("你" * 501)
        self.assertEqual(overlong.status_code, 422)
        self.assertEqual(overlong.json()["error"]["code"], "TTS_TEXT_TOO_LONG")
        self.assertEqual(self.tts.calls, [])

    def test_maps_tts_failures_without_breaking_health(self) -> None:
        cases = [
            (TTSBusy("busy"), 429, "TTS_BUSY"),
            (TTSUnavailable("unavailable"), 503, "TTS_UNAVAILABLE"),
            (TTSSynthesisFailed("failed"), 503, "TTS_SYNTHESIS_FAILED"),
        ]
        for error, status, code in cases:
            with self.subTest(code=code):
                self.tts.error = error
                response = self.synthesize("你好")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertTrue(response.json()["error"]["retryable"])
                self.assertEqual(self.client.get("/api/v1/health").status_code, 200)

    def test_stream_returns_ordered_no_store_ndjson(self) -> None:
        response = self.client.post(
            "/api/v1/tts/stream",
            headers={**self.headers, "Accept": "application/x-ndjson"},
            json={"text": " 你好 "},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/x-ndjson"))
        self.assertEqual(response.headers["cache-control"], "no-store")
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([item["event"] for item in events], ["started", "fragment", "fragment", "completed"])
        self.assertEqual([item["index"] for item in events[1:3]], [0, 1])
        self.assertEqual(events[0]["engine"], "gpt-sovits")
        self.assertEqual(events[0]["device"], "cuda")
        self.assertEqual(self.streaming_tts.calls, ["你好"])

    def test_stream_reset_restarts_fragment_index_after_recovery(self) -> None:
        self.streaming_tts.stream = lambda _text: iter(
            [
                AudioFragment(0, self.tts.wav, "gpt-sovits", "cuda"),
                AudioFragment(0, self.tts.wav, "gpt-sovits", "cpu", restart=True),
            ]
        )
        response = self.client.post(
            "/api/v1/tts/stream",
            headers={**self.headers, "Accept": "application/x-ndjson"},
            json={"text": "你好"},
        )
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([item["event"] for item in events], ["started", "fragment", "reset", "fragment", "completed"])
        self.assertEqual([item["index"] for item in events if item["event"] == "fragment"], [0, 0])
        self.assertEqual(events[2]["device"], "cpu")

    def test_stream_maps_failure_before_headers(self) -> None:
        self.streaming_tts.error = TTSUnavailable("missing")
        response = self.client.post("/api/v1/tts/stream", headers=self.headers, json={"text": "你好"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "TTS_UNAVAILABLE")

    def test_stream_requires_session_origin_and_valid_text(self) -> None:
        unauthorized = self.client.post("/api/v1/tts/stream", json={"text": "你好"})
        self.assertEqual(unauthorized.status_code, 401)
        empty = self.client.post("/api/v1/tts/stream", headers=self.headers, json={"text": " "})
        self.assertEqual(empty.status_code, 422)
        overlong = self.client.post("/api/v1/tts/stream", headers=self.headers, json={"text": "你" * 501})
        self.assertEqual(overlong.status_code, 422)

    def test_health_uses_sanitized_streaming_state(self) -> None:
        health = self.client.get("/api/v1/health").json()["data"]["tts"]
        self.assertEqual(
            health,
            {
                "enabled": True,
                "status": "ready",
                "primary_engine": "gpt-sovits",
                "device": "cuda",
                "fallback_ready": True,
                "last_error": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
