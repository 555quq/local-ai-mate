from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.relationship import DEFAULT_RELATIONSHIP_SETTINGS, RelationshipPromptBuilder, RelationshipSettingsConflict
from app.services import Repository


class RelationshipRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = Repository(Database(Path(self.temp_dir.name) / "app.db"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_defaults_update_idempotency_conflict_export_and_clear(self) -> None:
        initial = self.repository.relationship_settings()
        self.assertEqual(initial["companion_name"], "露娜")
        self.assertEqual(initial["version"], 1)

        updated = self.repository.update_relationship_settings(
            {"companion_name": "月影", "blocked_terms": ["宝贝"]}, initial["version"], "relationship-1"
        )
        self.assertEqual(updated["companion_name"], "月影")
        self.assertEqual(updated["blocked_terms"], ["宝贝"])
        self.assertEqual(updated["version"], 2)
        self.assertEqual(
            self.repository.update_relationship_settings({"companion_name": "被忽略"}, 1, "relationship-1"),
            updated,
        )
        with self.assertRaises(RelationshipSettingsConflict):
            self.repository.update_relationship_settings({"companion_name": "冲突"}, 1, None)

        self.assertEqual(self.repository.export_local_data()["data"]["relationship_settings"]["companion_name"], "月影")
        result = self.repository.clear_local_data(None)
        self.assertTrue(result["relationship_settings_reset"])
        reset = self.repository.relationship_settings()
        self.assertEqual(reset["companion_name"], DEFAULT_RELATIONSHIP_SETTINGS["companion_name"])
        self.assertEqual(reset["version"], 3)


class RelationshipPromptTestCase(unittest.TestCase):
    def test_prompt_is_bounded_and_contains_behavior_and_safety(self) -> None:
        settings = {
            **DEFAULT_RELATIONSHIP_SETTINGS,
            "companion_name": "月影",
            "self_reference": "姐姐",
            "user_nickname": "阿辰",
            "nickname_frequency": "frequent",
            "personality_style": "cool-restrained",
            "reply_length": "short",
            "comfort_style": "listen-first",
            "blocked_terms": [f"禁用{i}" for i in range(8)],
        }
        prompt = RelationshipPromptBuilder().build(settings)
        self.assertLessEqual(len(prompt), 600)
        self.assertIn("月影", prompt)
        self.assertIn("阿辰", prompt)
        self.assertIn("清冷克制", prompt)
        self.assertIn("不要冒充真人", prompt)
        self.assertEqual(prompt, RelationshipPromptBuilder().build(settings))

    def test_never_frequency_does_not_insert_nickname(self) -> None:
        prompt = RelationshipPromptBuilder().build(
            {**DEFAULT_RELATIONSHIP_SETTINGS, "user_nickname": "亲爱的", "nickname_frequency": "never"}
        )
        self.assertNotIn("亲爱的", prompt)
        self.assertIn("不擅自创造昵称", prompt)


class RelationshipApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token = "test-session-token"
        settings = Settings(
            data_dir=Path(self.temp_dir.name),
            session_token=self.token,
            allowed_origin="http://testserver",
        )
        self.client = TestClient(create_app(settings))
        self.headers = {"X-Session-Token": self.token, "Origin": "http://testserver"}

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_read_update_conflict_reset_and_fallback_reply(self) -> None:
        self.assertEqual(self.client.get("/api/v1/relationship-settings").status_code, 401)
        initial = self.client.get("/api/v1/relationship-settings", headers=self.headers)
        self.assertEqual(initial.status_code, 200)
        version = initial.json()["data"]["version"]
        updated = self.client.patch(
            "/api/v1/relationship-settings",
            headers=self.headers,
            json={
                "companion_name": "月影",
                "self_reference": "姐姐",
                "user_nickname": "阿辰",
                "nickname_frequency": "frequent",
                "expected_version": version,
                "idempotency_key": "api-update-1",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["data"]["companion_name"], "月影")
        next_version = updated.json()["data"]["version"]

        conflict = self.client.patch(
            "/api/v1/relationship-settings",
            headers=self.headers,
            json={"reply_length": "short", "expected_version": version},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "RELATIONSHIP_SETTINGS_CONFLICT")

        conversation_id = self.client.post(
            "/api/v1/conversations", headers=self.headers, json={"title": "测试"}
        ).json()["data"]["id"]
        reply = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=self.headers,
            json={"content": "今天有点累"},
        )
        self.assertIn("阿辰", reply.text)
        self.assertIn("姐姐", reply.text)

        reset = self.client.post(
            "/api/v1/relationship-settings/reset",
            headers=self.headers,
            json={"confirmed": True, "expected_version": next_version},
        )
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json()["data"]["companion_name"], "露娜")

    def test_validation_and_empty_update(self) -> None:
        cases = [
            {"companion_name": "", "expected_version": 1},
            {"personality_style": "unbounded", "expected_version": 1},
            {"blocked_terms": [str(index) for index in range(9)], "expected_version": 1},
            {"opening_greeting": "x" * 121, "expected_version": 1},
            {"companion_name": "露\n娜", "expected_version": 1},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.patch("/api/v1/relationship-settings", headers=self.headers, json=payload).status_code,
                    422,
                )
        empty = self.client.patch(
            "/api/v1/relationship-settings", headers=self.headers, json={"expected_version": 1}
        )
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(empty.json()["error"]["code"], "RELATIONSHIP_SETTINGS_UPDATE_EMPTY")


if __name__ == "__main__":
    unittest.main()
