from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token = "test-session-token"
        self.settings = Settings(
            data_dir=Path(self.temp_dir.name),
            session_token=self.token,
            allowed_origin="http://testserver",
        )
        self.client = TestClient(create_app(self.settings))
        self.headers = {"X-Session-Token": self.token, "Origin": "http://testserver"}

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def create_conversation(self) -> str:
        response = self.client.post("/api/v1/conversations", headers=self.headers, json={"title": "测试"})
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]["id"]

    def test_write_requires_session_and_origin(self) -> None:
        self.assertEqual(self.client.post("/api/v1/conversations", json={}).status_code, 401)
        wrong_origin = {"X-Session-Token": self.token, "Origin": "https://example.invalid"}
        self.assertEqual(self.client.post("/api/v1/conversations", headers=wrong_origin, json={}).status_code, 401)

    def test_local_message_stream_and_memory_approval(self) -> None:
        conversation_id = self.create_conversation()
        response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=self.headers,
            json={"content": "请记住我喜欢晚上安排待办", "route": "local", "idempotency_key": "message-1"},
        )
        self.assertEqual(response.status_code, 200)
        events = response.text
        self.assertIn("event: queued", events)
        self.assertIn("event: completed", events)
        candidates = self.client.get(f"/api/v1/conversations/{conversation_id}/memory-candidates").json()["data"]["items"]
        self.assertEqual(len(candidates), 1)
        approved = self.client.post(
            f"/api/v1/memory-candidates/{candidates[0]['id']}/approve",
            headers=self.headers,
            json={
                "content": "用户更偏好在晚上安排待办。",
                "category": "preference",
                "importance": 3,
                "private_local_only": True,
                "idempotency_key": "memory-1",
            },
        )
        self.assertEqual(approved.status_code, 200)
        memories = self.client.get("/api/v1/memories").json()["data"]["items"]
        self.assertEqual(memories[0]["content"], "用户更偏好在晚上安排待办。")
        replay = self.client.post(
            f"/api/v1/memory-candidates/{candidates[0]['id']}/approve",
            headers=self.headers,
            json={
                "content": "用户更偏好在晚上安排待办。", "category": "preference", "importance": 3,
                "private_local_only": True, "idempotency_key": "memory-1",
            },
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["data"]["id"], approved.json()["data"]["id"])

    def test_cloud_consent_is_single_use_and_never_calls_network(self) -> None:
        os.environ["APP_TEST_MODE"] = "1"
        conversation_id = self.create_conversation()
        request_id = str(uuid.uuid4())
        consent = self.client.post(
            "/api/v1/cloud/consents",
            headers=self.headers,
            json={
                "request_id": request_id, "provider_id": "example-provider", "upload_mode": "current-message",
                "idempotency_key": "consent-1",
            },
        )
        self.assertEqual(consent.status_code, 201)
        consent_id = consent.json()["data"]["consent_id"]
        response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=self.headers,
            json={
                "content": "只把这一句用于云端增强", "route": "cloud-current-message", "request_id": request_id,
                "cloud_consent_id": consent_id, "idempotency_key": "cloud-message-1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: degraded", response.text)
        replay = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=self.headers,
            json={
                "content": "另一次", "route": "cloud-current-message", "request_id": str(uuid.uuid4()),
                "cloud_consent_id": consent_id, "idempotency_key": "cloud-message-2",
            },
        )
        self.assertEqual(replay.status_code, 409)
        observations = self.client.get("/api/v1/test/network-observations", headers=self.headers)
        self.assertEqual(observations.status_code, 200)
        self.assertEqual(observations.json()["data"]["outbound_count"], 0)

    def test_proposal_only_creates_reminder_after_confirmation_and_is_idempotent(self) -> None:
        due_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        proposal = self.client.post(
            "/api/v1/action-proposals",
            headers=self.headers,
            json={
                "kind": "reminder", "title": "缴费", "absolute_due_at": due_at, "timezone": "Asia/Shanghai",
                "source_text": "明天提醒我缴费", "idempotency_key": "proposal-1",
            },
        )
        self.assertEqual(proposal.status_code, 201)
        self.assertEqual(self.client.get("/api/v1/reminders").json()["data"]["items"], [])
        proposal_id = proposal.json()["data"]["id"]
        confirmed = self.client.post(
            f"/api/v1/action-proposals/{proposal_id}/confirm",
            headers=self.headers,
            json={"idempotency_key": "confirm-1"},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(len(self.client.get("/api/v1/reminders").json()["data"]["items"]), 1)
        replay = self.client.post(
            f"/api/v1/action-proposals/{proposal_id}/confirm",
            headers=self.headers,
            json={"idempotency_key": "confirm-1"},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["data"]["entity_id"], confirmed.json()["data"]["entity_id"])

    def test_settings_persistently_pause_candidates_and_retrieval(self) -> None:
        conversation_id = self.create_conversation()
        settings = self.client.get("/api/v1/settings", headers=self.headers)
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(
            settings.json()["data"]["privacy"],
            {"memory_candidates_paused": False, "retrieval_paused": False, "proactive_interactions_paused": False},
        )
        paused = self.client.patch(
            "/api/v1/settings", headers=self.headers,
            json={"memory_candidates_paused": True, "idempotency_key": "pause-candidates"},
        )
        self.assertEqual(paused.status_code, 200)
        self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages", headers=self.headers,
            json={"content": "请记住我喜欢晚上安排待办", "idempotency_key": "paused-candidate"},
        )
        candidates = self.client.get(f"/api/v1/conversations/{conversation_id}/memory-candidates").json()["data"]["items"]
        self.assertEqual(candidates, [])

        self.client.patch("/api/v1/settings", headers=self.headers, json={"memory_candidates_paused": False})
        self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages", headers=self.headers,
            json={"content": "请记住我喜欢晚上安排待办", "idempotency_key": "candidate-now"},
        )
        candidate = self.client.get(f"/api/v1/conversations/{conversation_id}/memory-candidates").json()["data"]["items"][0]
        self.client.post(
            f"/api/v1/memory-candidates/{candidate['id']}/approve", headers=self.headers,
            json={"content": "用户喜欢晚上安排待办。", "category": "preference", "importance": 3},
        )
        self.client.patch("/api/v1/settings", headers=self.headers, json={"retrieval_paused": True})
        response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages", headers=self.headers,
            json={"content": "我喜欢晚上安排待办", "idempotency_key": "retrieval-paused"},
        )
        self.assertIn('"memory_count": 0', response.text)

    def test_privacy_export_and_explicit_clear(self) -> None:
        conversation_id = self.create_conversation()
        self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages", headers=self.headers,
            json={"content": "这是会被导出的本地内容", "idempotency_key": "export-message"},
        )
        exported = self.client.get("/api/v1/privacy/export", headers=self.headers)
        self.assertEqual(exported.status_code, 200)
        body = exported.json()["data"]
        self.assertEqual(body["format"], "local-ai-mate-export-v1")
        self.assertEqual(body["data"]["messages"][0]["content"], "这是会被导出的本地内容")
        rejected = self.client.post(
            "/api/v1/privacy/clear", headers=self.headers,
            json={"confirmed": True, "confirmation": "不是确认短语"},
        )
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(len(self.client.get(f"/api/v1/conversations/{conversation_id}/messages").json()["data"]["items"]), 2)
        cleared = self.client.post(
            "/api/v1/privacy/clear", headers=self.headers,
            json={"confirmed": True, "confirmation": "清空本地数据"},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertTrue(cleared.json()["data"]["cleared"])
        self.assertEqual(cleared.json()["data"]["tables"]["messages"], 2)
        self.assertEqual(self.client.get("/api/v1/privacy/export", headers=self.headers).json()["data"]["data"]["messages"], [])

    def test_invalid_ollama_config_falls_back_without_network(self) -> None:
        os.environ["APP_TEST_MODE"] = "1"
        settings = Settings(
            data_dir=Path(self.temp_dir.name) / "ollama",
            session_token=self.token,
            allowed_origin="http://testserver",
            ollama_enabled=True,
            ollama_base_url="https://example.invalid",
        )
        client = TestClient(create_app(settings))
        try:
            conversation_id = client.post("/api/v1/conversations", headers=self.headers, json={"title": "本地模型"}).json()["data"]["id"]
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/messages", headers=self.headers,
                json={"content": "你好", "idempotency_key": "ollama-fallback"},
            )
            self.assertIn("local_model_unavailable", response.text)
            health = client.get("/api/v1/health").json()["data"]["model"]
            self.assertEqual(health["status"], "misconfigured")
            observations = client.get("/api/v1/test/network-observations", headers=self.headers).json()["data"]
            self.assertEqual(observations["outbound_count"], 0)
            self.assertEqual(observations["local_model_request_count"], 0)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
