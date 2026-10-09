from __future__ import annotations

import base64
import json
import sys
import wave
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gpt_sovits_worker import (  # noqa: E402
    PROTOCOL_VERSION,
    WorkerCommand,
    encode_event,
    parse_command,
    stream_events,
)


def command(command: str, request_id: str = "r1", **extra: object) -> str:
    return json.dumps({"version": PROTOCOL_VERSION, "command": command, "request_id": request_id, **extra})


@pytest.mark.parametrize("name", ["ping", "cancel", "shutdown"])
def test_parses_control_commands(name: str) -> None:
    parsed = parse_command(command(name))
    assert parsed == WorkerCommand(name, "r1", None)


def test_parses_trimmed_synthesis_text() -> None:
    parsed = parse_command(command("synthesize", text=" 你好 "))
    assert parsed == WorkerCommand("synthesize", "r1", "你好")


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ("not json", "INVALID_JSON"),
        (json.dumps({"version": 9, "command": "ping", "request_id": "r"}), "PROTOCOL_VERSION_UNSUPPORTED"),
        (command("unknown"), "COMMAND_UNSUPPORTED"),
        (command("ping", request_id=""), "REQUEST_ID_INVALID"),
        (command("synthesize", text=" "), "TEXT_EMPTY"),
        (command("synthesize", text="你" * 501), "TEXT_TOO_LONG"),
    ],
)
def test_rejects_invalid_commands(payload: str, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        parse_command(payload)


def test_rejects_oversized_command_line() -> None:
    with pytest.raises(ValueError, match="COMMAND_TOO_LARGE"):
        parse_command("x" * (16 * 1024 + 1))


def test_encode_event_adds_protocol_version() -> None:
    payload = json.loads(encode_event({"event": "ready", "request_id": "r"}))
    assert payload == {"version": 1, "event": "ready", "request_id": "r"}


def test_stream_events_emits_decodable_ordered_wav_fragments() -> None:
    class FakeModel:
        def stream(self, text: str):
            assert text == "你好"
            yield 48_000, np.full(480, 0.1, dtype=np.float32)
            yield 48_000, np.full(960, 1000, dtype=np.int16)

    events = list(stream_events(FakeModel(), WorkerCommand("synthesize", "r1", "你好"), device="cpu"))
    assert [event["event"] for event in events] == ["started", "fragment", "fragment", "completed"]
    assert [event["index"] for event in events[1:3]] == [0, 1]
    for event in events[1:3]:
        payload = base64.b64decode(event["audio_base64"], validate=True)
        with wave.open(BytesIO(payload), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 48_000
            assert wav_file.getnframes() > 0


def test_cancellation_stops_at_fragment_boundary() -> None:
    calls = 0

    class FakeModel:
        def stream(self, _text: str):
            nonlocal calls
            calls += 1
            yield 48_000, np.ones(100, dtype=np.int16)
            calls += 1
            yield 48_000, np.ones(100, dtype=np.int16)

    def cancelled() -> bool:
        return calls >= 1

    events = list(
        stream_events(
            FakeModel(),
            WorkerCommand("synthesize", "r1", "你好"),
            device="cpu",
            cancelled=cancelled,
        )
    )
    assert [event["event"] for event in events] == ["started", "cancelled"]


def test_empty_model_stream_is_rejected() -> None:
    class EmptyModel:
        def stream(self, _text: str):
            return iter(())

    with pytest.raises(ValueError, match="EMPTY_STREAM"):
        list(stream_events(EmptyModel(), WorkerCommand("synthesize", "r1", "你好"), device="cpu"))
