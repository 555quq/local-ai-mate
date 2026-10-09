"""Privacy assertions that require a test-only inspection endpoint.

The MVP should expose this endpoint only when APP_TEST_MODE=1.  It must return
counts and field-name metadata, never request bodies, chat text, summaries,
audio, credentials, IP addresses, or provider responses.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import assert_safe_error


def _inspection(api: httpx.Client) -> dict:
    response = api.get("/test/network-observations")
    if response.status_code in (404, 403):
        pytest.skip("APP_TEST_MODE inspection endpoint is unavailable")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _envelope(response: httpx.Response) -> dict:
    """Assert the standard API envelope without recording any payload text."""
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"request_id", "server_time", "data"}
    assert isinstance(payload["request_id"], str) and payload["request_id"]
    return payload["data"]


def _events(response: httpx.Response) -> list[dict]:
    events: list[dict] = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            import json

            events.append(json.loads(line[5:].strip()))
    return events


@pytest.mark.api
@pytest.mark.privacy
def test_default_local_flow_creates_no_outbound_observation(
    api: httpx.Client, conversation_id: str
) -> None:
    before = _inspection(api)
    response = api.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "请用一句话鼓励我", "route": "local"},
    )
    assert response.status_code == 200, response.text
    after = _inspection(api)
    assert after["outbound_count"] == before["outbound_count"]


@pytest.mark.api
@pytest.mark.privacy
def test_test_observations_never_expose_content_or_secrets(api: httpx.Client) -> None:
    observation = _inspection(api)
    assert set(observation).issubset(
        {"outbound_count", "observed_fields", "local_model_request_count", "local_model_status"}
    )
    assert isinstance(observation.get("outbound_count"), int)
    assert all(isinstance(name, str) for name in observation.get("observed_fields", []))


@pytest.mark.api
@pytest.mark.privacy
def test_optional_ollama_fallback_remains_local_and_never_calls_cloud(
    api: httpx.Client, conversation_id: str
) -> None:
    """A local Ollama failure may degrade a response, but must not trigger cloud egress."""
    settings = _envelope(api.get("/settings"))
    model = settings["model"]
    assert set(settings).issuperset({"privacy", "model", "cloud_outbound_enabled"})
    if model["enabled"]:
        assert model["endpoint"].startswith("http://127.0.0.1:")
    else:
        assert model["endpoint"] is None
    assert settings["cloud_outbound_enabled"] is False

    before = _inspection(api)
    response = api.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "请用本地模型回答一句话", "route": "local"},
    )
    assert response.status_code == 200, response.text
    events = _events(response)
    generated = [event for event in events if event["event"] == "generating"]
    assert generated and generated[-1]["data"]["source"] in {"local", "fallback-local", "ollama-local"}
    if model["enabled"] and model["status"] != "ready":
        assert generated[-1]["data"]["source"] == "fallback-local"
    assert events[-1]["event"] in {"completed", "degraded"}
    after = _inspection(api)
    assert after["outbound_count"] == before["outbound_count"]


@pytest.mark.api
@pytest.mark.privacy
def test_persistent_privacy_toggles_control_candidates_and_retrieval(
    api: httpx.Client, conversation_id: str
) -> None:
    original = _envelope(api.get("/settings"))
    try:
        paused = _envelope(
            api.patch(
                "/settings",
                json={"memory_candidates_paused": True, "retrieval_paused": True},
            )
        )
        assert paused["privacy"]["memory_candidates_paused"] is True
        assert paused["privacy"]["retrieval_paused"] is True

        no_candidate = api.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "请记住我喜欢本地部署", "route": "local"},
        )
        assert no_candidate.status_code == 200, no_candidate.text
        candidates = _envelope(api.get(f"/conversations/{conversation_id}/memory-candidates"))
        assert candidates["items"] == []
        retrieval_events = _events(no_candidate)
        retrieval = [event for event in retrieval_events if event["event"] == "retrieving_memory"]
        assert retrieval and retrieval[-1]["data"]["memory_count"] == 0

        enabled = _envelope(
            api.patch(
                "/settings",
                json={"memory_candidates_paused": False, "retrieval_paused": False},
            )
        )
        assert enabled["privacy"]["memory_candidates_paused"] is False
        assert enabled["privacy"]["retrieval_paused"] is False
        candidate_response = api.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "请记住我喜欢本地部署", "route": "local"},
        )
        assert candidate_response.status_code == 200, candidate_response.text
        candidates = _envelope(api.get(f"/conversations/{conversation_id}/memory-candidates"))
        assert len(candidates["items"]) == 1
        candidate = candidates["items"][0]
        approval = api.post(
            f"/memory-candidates/{candidate['id']}/approve",
            json={
                "content": "用户喜欢本地部署。",
                "category": "preference",
                "importance": 3,
                "private_local_only": True,
            },
        )
        assert approval.status_code == 200, approval.text

        final_pause = _envelope(api.patch("/settings", json={"retrieval_paused": True}))
        assert final_pause["privacy"]["retrieval_paused"] is True
        no_retrieval = api.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "我喜欢什么？", "route": "local"},
        )
        assert no_retrieval.status_code == 200, no_retrieval.text
        retrieval = [event for event in _events(no_retrieval) if event["event"] == "retrieving_memory"]
        assert retrieval and retrieval[-1]["data"]["memory_count"] == 0
    finally:
        _envelope(
            api.patch(
                "/settings",
                json={
                    "memory_candidates_paused": original["privacy"]["memory_candidates_paused"],
                    "retrieval_paused": original["privacy"]["retrieval_paused"],
                    "proactive_interactions_paused": original["privacy"]["proactive_interactions_paused"],
                },
            )
        )


@pytest.mark.api
@pytest.mark.privacy
def test_export_uses_normal_envelope_and_contains_no_credentials(
    api: httpx.Client, session_token: str
) -> None:
    exported = _envelope(api.get("/privacy/export"))
    assert set(exported) == {"format", "exported_at", "data"}
    assert isinstance(exported["format"], str) and exported["format"]
    assert set(exported["data"]).issuperset(
        {
            "conversations",
            "messages",
            "memory_candidates",
            "memories",
            "action_proposals",
            "tasks",
            "reminders",
            "cloud_consents",
            "cloud_audits",
            "audit_events",
            "privacy_controls",
        }
    )
    rendered = str(exported).lower()
    assert session_token.lower() not in rendered

    def keys(value: object) -> list[str]:
        if isinstance(value, dict):
            return list(value) + [key for item in value.values() for key in keys(item)]
        if isinstance(value, list):
            return [key for item in value for key in keys(item)]
        return []

    forbidden = {"api_key", "apikey", "authorization", "password", "secret", "session_token"}
    assert not (set(key.lower() for key in keys(exported)) & forbidden)


@pytest.mark.api
@pytest.mark.privacy
def test_clear_requires_explicit_confirmation_and_removes_local_records(
    api: httpx.Client, conversation_id: str
) -> None:
    message = api.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "请记住测试数据只能留在本机", "route": "local"},
    )
    assert message.status_code == 200, message.text

    for payload in ({}, {"confirmed": True}, {"confirmed": True, "confirmation": "错误短语"}):
        rejected = api.post("/privacy/clear", json=payload)
        assert rejected.status_code == 422
        assert_safe_error(rejected, "VALIDATION_FAILED")

    cleared = _envelope(
        api.post("/privacy/clear", json={"confirmed": True, "confirmation": "清空本地数据"})
    )
    assert cleared["cleared"] is True
    assert cleared["privacy_controls_reset"] is True
    assert isinstance(cleared["tables"], dict)

    missing_conversation = api.get(f"/conversations/{conversation_id}/messages")
    assert missing_conversation.status_code == 404
    assert_safe_error(missing_conversation, "CONVERSATION_NOT_FOUND")
    assert _envelope(api.get("/memories"))["items"] == []
