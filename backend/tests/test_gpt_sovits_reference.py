from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gpt_sovits_reference import Candidate, TimeRange, parse_silence_intervals, rank_candidates  # noqa: E402


def _candidate(**changes: object) -> Candidate:
    values: dict[str, object] = {
        "start": 1.0,
        "end": 8.0,
        "text": "这是一段清晰的参考语音。",
        "avg_logprob": -0.2,
        "no_speech_prob": 0.02,
        "rms_dbfs": -18.0,
        "peak": 0.7,
        "silence_fraction": 0.05,
    }
    values.update(changes)
    return Candidate(**values)  # type: ignore[arg-type]


def test_parse_silence_intervals_clamps_and_merges_ranges() -> None:
    text = "silence_start: -1\nsilence_end: 2.0\nsilence_start: 1.5\nsilence_end: 3.0\nsilence_start: 9.0"
    assert parse_silence_intervals(text, 10.0) == [TimeRange(0.0, 3.0), TimeRange(9.0, 10.0)]


def test_rank_candidates_prefers_confident_low_silence_audio() -> None:
    weaker = _candidate(start=20.0, end=27.0, avg_logprob=-0.7, silence_fraction=0.2)
    stronger = _candidate(start=5.0, end=12.0, avg_logprob=-0.1, silence_fraction=0.03)
    assert rank_candidates([weaker, stronger]) == [stronger, weaker]


def test_rank_candidates_rejects_blank_low_confidence_and_invalid_duration() -> None:
    candidates = [
        _candidate(text=""),
        _candidate(avg_logprob=-1.3),
        _candidate(no_speech_prob=0.6),
        _candidate(end=3.0),
        _candidate(end=14.0),
    ]
    assert rank_candidates(candidates) == []


def test_unicode_source_paths_are_supported_by_pathlib() -> None:
    source = Path("D:/A/三角洲行动 - 露娜 - 语音合集.mp3")
    assert "露娜" in str(source)
