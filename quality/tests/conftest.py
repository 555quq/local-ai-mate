"""Shared fixtures for black-box FastAPI contract testing.

These tests intentionally require explicit environment configuration.  They
must never discover a service or read credentials from the user's machine.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not set; no backend was contacted")
    return value


@pytest.fixture(scope="session")
def base_url() -> str:
    value = _required_env("AI_COMPANION_BASE_URL").rstrip("/")
    if not value.startswith("http://127.0.0.1:") and not value.startswith("https://127.0.0.1:"):
        pytest.fail("AI_COMPANION_BASE_URL must be a loopback 127.0.0.1 address")
    return value


@pytest.fixture(scope="session")
def session_token() -> str:
    return _required_env("AI_COMPANION_SESSION_TOKEN")


@pytest.fixture()
def api(base_url: str, session_token: str) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=f"{base_url}/api/v1",
        headers={"X-Session-Token": session_token, "Origin": base_url},
        timeout=35.0,
        follow_redirects=False,
    ) as client:
        yield client


def assert_safe_error(response: httpx.Response, expected_code: str) -> None:
    """Validate the deliberately small public error contract from §45."""
    payload = response.json()
    assert payload["error"]["code"] == expected_code
    assert isinstance(payload["error"].get("message"), str) and payload["error"]["message"]
    assert isinstance(payload.get("request_id"), str) and payload["request_id"]
    assert set(payload).issubset({"request_id", "server_time", "error"})
    assert set(payload["error"]).issubset({"code", "message", "retryable"})
    rendered = response.text.lower()
    for forbidden in ("traceback", "sql", "select ", "c:\\", "authorization"):
        assert forbidden not in rendered


@pytest.fixture()
def conversation_id(api: httpx.Client) -> str:
    response = api.post("/conversations", json={})
    assert response.status_code in (200, 201), response.text
    payload = response.json()
    assert set(payload) == {"request_id", "server_time", "data"}
    assert isinstance(payload["data"]["id"], str) and payload["data"]["id"]
    return payload["data"]["id"]
