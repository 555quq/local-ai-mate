"""Contracts derived from v1.5.0 §§21, 31, 32, 45 and 48.

The suite is intentionally black-box: it verifies public HTTP behavior and
does not reach into the database, Ollama, or a cloud provider implementation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from conftest import assert_safe_error


def _sse_events(response: httpx.Response) -> Iterator[dict]:
    """Extract JSON payloads from SSE lines without storing message text."""
    event: dict | None = None
    for line in response.text.splitlines():
        if line.startswith("data:"):
            event = json.loads(line[5:].strip())
            yield event


@pytest.mark.api
def test_rejects_missing_session_token(base_url: str) -> None:
    response = httpx.post(
        f"{base_url}/api/v1/conversations",
        headers={"Origin": base_url},
        json={},
        timeout=10.0,
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "traceback" not in response.text.lower()


@pytest.mark.api
def test_creates_local_conversation_without_cloud_side_effect(api: httpx.Client) -> None:
    response = api.post("/conversations", json={})
    assert response.status_code in (200, 201), response.text
    payload = response.json()
    assert set(payload) == {"request_id", "server_time", "data"}
    assert isinstance(payload["data"]["id"], str)


@pytest.mark.api
@pytest.mark.privacy
def test_local_message_requires_no_cloud_consent_and_reports_local_route(
    api: httpx.Client, conversation_id: str
) -> None:
    response = api.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "今天有点累", "route": "local"},
    )
    assert response.status_code == 200, response.text
    events = list(_sse_events(response))
    assert events, "message endpoint must emit status events"
    assert all(isinstance(event.get("request_id"), str) for event in events)
    assert events[-1].get("event") in {"completed", "degraded"}
    # The response metadata, not model prose, is the authoritative route label.
    assert events[-1].get("data", {}).get("source") in {"local", "fallback-local"}


@pytest.mark.api
def test_rejects_blank_and_oversized_message(api: httpx.Client, conversation_id: str) -> None:
    blank = api.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": " \n\t", "route": "local"},
    )
    assert blank.status_code == 422
    assert_safe_error(blank, "VAL-01")

    oversized = api.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "测" * 8001, "route": "local"},
    )
    assert oversized.status_code == 422
    assert_safe_error(oversized, "VAL-02")


@pytest.mark.api
@pytest.mark.privacy
def test_cloud_route_is_denied_without_a_bound_consent(
    api: httpx.Client, conversation_id: str
) -> None:
    response = api.post(
        f"/conversations/{conversation_id}/messages",
        json={
            "content": "请给我一个更周全的建议",
            "route": "cloud-current-message",
            "request_id": "00000000-0000-4000-8000-000000000001",
        },
    )
    assert response.status_code == 409
    assert_safe_error(response, "VAL-03")


@pytest.mark.api
@pytest.mark.privacy
def test_cloud_consent_is_minimal_and_single_use(api: httpx.Client) -> None:
    provider_id = "fake-provider"
    request_id = "00000000-0000-4000-8000-000000000002"
    response = api.post(
        "/cloud/consents",
        json={
            "request_id": request_id,
            "provider_id": provider_id,
            "upload_mode": "current-message",
        },
    )
    if response.status_code == 422:
        pytest.skip("test provider is not configured; configure fake-provider for consent tests")
    assert response.status_code in (200, 201), response.text
    payload = response.json()["data"]
    assert isinstance(payload.get("consent_id"), str) and payload["consent_id"]
    assert payload.get("upload_fields") == ["current_message"]
    assert "ephemeral_summary" not in payload["upload_fields"]
    assert "expires_at" in payload


@pytest.mark.api
@pytest.mark.privacy
def test_summary_mode_preview_explicitly_lists_only_summary_and_current_message(api: httpx.Client) -> None:
    response = api.post(
        "/cloud/consents",
        json={
            "request_id": "00000000-0000-4000-8000-000000000003",
            "provider_id": "fake-provider",
            "upload_mode": "cloud-with-summary",
        },
    )
    if response.status_code == 422:
        pytest.skip("test provider is not configured; configure fake-provider for consent tests")
    assert response.status_code in (200, 201), response.text
    assert set(response.json()["data"]["upload_fields"]) == {"current_message", "ephemeral_summary"}


@pytest.mark.api
def test_error_payload_does_not_reflect_untrusted_input(api: httpx.Client, conversation_id: str) -> None:
    marker = "PRIVATE_CANARY_do_not_reflect"
    response = api.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": marker * 1000, "route": "invalid-route"},
    )
    assert response.status_code in (422, 409)
    assert marker not in response.text
