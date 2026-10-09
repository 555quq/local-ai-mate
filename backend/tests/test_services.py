from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services import OllamaLocalAdapter


class FakeResponse:
    is_redirect = False

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeClient:
    def __init__(self) -> None:
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str) -> FakeResponse:
        self.get_calls.append(url)
        return FakeResponse({"models": [{"name": "qwen3:4b-instruct"}]})

    def post(self, url: str, *, json: dict[str, Any]) -> FakeResponse:
        self.post_calls.append((url, json))
        return FakeResponse({"response": "你好，我在。"})

    def close(self) -> None:
        self.closed = True


class OllamaLocalAdapterTest(unittest.TestCase):
    def test_reuses_health_state_and_requests_fast_non_thinking_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeClient()
            settings = Settings(
                data_dir=Path(temp_dir),
                ollama_enabled=True,
                ollama_num_predict=96,
                ollama_num_ctx=2048,
                ollama_keep_alive="10m",
            )
            adapter = OllamaLocalAdapter(settings, client=client)

            self.assertEqual(adapter.status()["status"], "ready")
            reply, status = adapter.reply("你好", [])

            self.assertEqual(reply, "你好，我在。")
            self.assertEqual(status["status"], "ready")
            self.assertEqual(len(client.get_calls), 1)
            self.assertEqual(len(client.post_calls), 1)
            payload = client.post_calls[0][1]
            self.assertFalse(payload["think"])
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["keep_alive"], "10m")
            self.assertEqual(payload["options"], {"num_predict": 96, "num_ctx": 2048})
            self.assertIn("/no_think", payload["prompt"])

            adapter.close()
            self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
