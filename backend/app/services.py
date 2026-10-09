from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Settings
from .db import Database, utc_now


def new_id() -> str:
    return str(uuid.uuid4())


def as_bool(value: Any) -> bool:
    return bool(value)


class LocalCompanion:
    """A deliberately offline stand-in for Ollama during the MVP.

    Keeping this interface narrow means a real local model adapter can replace it
    without granting a model direct access to the database or scheduler.
    """

    def reply(self, content: str, memories: list[dict[str, Any]]) -> str:
        reference = ""
        if memories:
            reference = " 我记得你之前提过相关的偏好，会把它作为参考。"
        if any(marker in content for marker in ("难过", "焦虑", "累", "压力")):
            return "听起来这件事让你不太轻松。我们可以先把眼前最需要处理的一小步理出来。" + reference
        if "?" in content or "？" in content:
            return "我在认真想这个问题。我的建议是先从最可控的一步开始；你愿意告诉我更在意哪一部分吗？" + reference
        return "我在。谢谢你愿意和我说这些；我们可以慢慢把它理清楚。" + reference


class OllamaLocalAdapter:
    """Explicitly-configured, loopback-only bridge to an Ollama server.

    It is intentionally not a general HTTP client: redirects and non-loopback
    URLs are rejected before a request is made.  Failures are represented as a
    status and callers always retain the deterministic local fallback.
    """

    _LOOPBACK_HOSTS = {"127.0.0.1", "::1"}

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.enabled = settings.ollama_enabled
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout_seconds = settings.ollama_timeout_seconds
        self.num_predict = settings.ollama_num_predict
        self.num_ctx = settings.ollama_num_ctx
        self.keep_alive = settings.ollama_keep_alive
        self._configuration_error = self._validate_endpoint()
        self._last_status: dict[str, Any] | None = None
        self.request_count = 0
        self._client = client

    def _http(self) -> Any:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _validate_endpoint(self) -> str | None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.hostname.lower() not in self._LOOPBACK_HOSTS:
            return "endpoint_must_be_http_loopback"
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return "endpoint_has_unsupported_components"
        return None

    def status(self, *, refresh: bool = True) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled", "model": self.model, "endpoint": None}
        if self._configuration_error:
            return {
                "enabled": True, "status": "misconfigured", "model": self.model,
                "endpoint": None, "reason": self._configuration_error,
            }
        if not refresh and self._last_status is not None:
            return self._last_status
        try:
            self.request_count += 1
            response = self._http().get(f"{self.base_url}/api/tags")
            if response.is_redirect:
                raise httpx.HTTPError("redirect rejected")
            response.raise_for_status()
            names = {str(item.get("name")) for item in response.json().get("models", []) if isinstance(item, dict)}
            model_present = self.model in names
            result = {
                "enabled": True,
                "status": "ready" if model_present else "model_missing",
                "model": self.model,
                "endpoint": self.base_url,
                "model_present": model_present,
            }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            result = {
                "enabled": True, "status": "unavailable", "model": self.model,
                "endpoint": self.base_url, "reason": type(exc).__name__,
            }
        self._last_status = result
        return result

    def reply(self, content: str, memories: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any]]:
        # The model list changes rarely. Reuse the last successful health result
        # instead of adding a /api/tags round trip before every message.
        status = self.status(refresh=False) if self._last_status is not None else self.status()
        if status["status"] != "ready":
            return None, status
        context = "\n".join(f"- {memory['content']}" for memory in memories[:5])
        prompt = (
            "你是一个温柔知性、尊重边界的本地 AI 陪伴者。关闭思考展示，用中文在两到四句话内直接回答。"
            "不要假装自己是真人，也不要建议用户疏远现实支持。"
            f"\n相关本地记忆（可能为空）：\n{context}\n\n用户：{content}\n/no_think"
        )
        try:
            self.request_count += 1
            response = self._http().post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "keep_alive": self.keep_alive,
                    "options": {
                        "num_predict": self.num_predict,
                        "num_ctx": self.num_ctx,
                    },
                },
            )
            if response.is_redirect:
                raise httpx.HTTPError("redirect rejected")
            response.raise_for_status()
            reply = str(response.json().get("response", "")).strip()
            if not reply:
                raise ValueError("empty model response")
            ready = {**status, "status": "ready"}
            self._last_status = ready
            return reply, ready
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            failed = {**status, "status": "unavailable", "reason": type(exc).__name__}
            self._last_status = failed
            return None, failed


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def idempotent_result(self, operation: str, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        row = self.db.fetchone(
            "SELECT response_json FROM idempotency_keys WHERE operation = ? AND key = ?", (operation, key)
        )
        return self.db.decode(row["response_json"]) if row else None

    def save_idempotent(self, conn: Any, operation: str, key: str | None, result: dict[str, Any]) -> None:
        if key:
            conn.execute(
                "INSERT INTO idempotency_keys(operation, key, response_json, created_at) VALUES (?, ?, ?, ?)",
                (operation, key, self.db.encode(result), utc_now()),
            )

    def create_conversation(self, title: str, route_default: str, idempotency_key: str | None) -> dict[str, Any]:
        cached = self.idempotent_result("conversation.create", idempotency_key)
        if cached:
            return cached
        now, conversation_id = utc_now(), new_id()
        result = {"id": conversation_id, "title": title, "route_default": route_default, "created_at": now}
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO conversations(id, title, route_default, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, title, route_default, now, now),
            )
            self.save_idempotent(conn, "conversation.create", idempotency_key, result)
        return result

    def conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return self.db.fetchone("SELECT * FROM conversations WHERE id = ?", (conversation_id,))

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return self.db.fetchall(
            "SELECT id, role, content, source, status, request_id, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at, id",
            (conversation_id,),
        )

    def privacy_controls(self) -> dict[str, bool]:
        row = self.db.fetchone("SELECT * FROM privacy_controls WHERE singleton = 1")
        assert row is not None
        return {
            "memory_candidates_paused": as_bool(row["memory_candidates_paused"]),
            "retrieval_paused": as_bool(row["retrieval_paused"]),
            "proactive_interactions_paused": as_bool(row["proactive_interactions_paused"]),
        }

    def update_privacy_controls(self, changes: dict[str, Any], idempotency_key: str | None) -> dict[str, bool]:
        operation = "privacy.controls.update"
        cached = self.idempotent_result(operation, idempotency_key)
        if cached:
            return cached
        fields = {
            key: value for key, value in changes.items()
            if key in {"memory_candidates_paused", "retrieval_paused", "proactive_interactions_paused"} and value is not None
        }
        if not fields:
            raise ValueError("no privacy control updates")
        with self.db.transaction() as conn:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            conn.execute(
                f"UPDATE privacy_controls SET {assignments}, updated_at = ? WHERE singleton = 1",
                [int(value) for value in fields.values()] + [utc_now()],
            )
            row = dict(conn.execute("SELECT * FROM privacy_controls WHERE singleton = 1").fetchone())
            result = {
                "memory_candidates_paused": as_bool(row["memory_candidates_paused"]),
                "retrieval_paused": as_bool(row["retrieval_paused"]),
                "proactive_interactions_paused": as_bool(row["proactive_interactions_paused"]),
            }
            self.save_idempotent(conn, operation, idempotency_key, result)
        return result

    def relevant_memories(self, content: str) -> list[dict[str, Any]]:
        # MVP retrieval: deterministic local word overlap. A vector store can replace this method.
        if self.privacy_controls()["retrieval_paused"]:
            return []
        terms = {item for item in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", content.lower()) if item}
        rows = self.db.fetchall("SELECT * FROM memories ORDER BY importance DESC, updated_at DESC LIMIT 20")
        scored = []
        for row in rows:
            score = sum(term in row["content"].lower() for term in terms)
            if score:
                row["private_local_only"] = as_bool(row["private_local_only"])
                scored.append((score, row))
        return [row for _, row in sorted(scored, key=lambda value: value[0], reverse=True)[:5]]

    def create_memory_candidate_if_requested(
        self, conn: Any, conversation_id: str, source_message_id: str, content: str
    ) -> dict[str, Any] | None:
        if self.privacy_controls()["memory_candidates_paused"]:
            return None
        if not any(marker in content for marker in ("记住", "我喜欢", "我偏好", "别再")):
            return None
        candidate_id, now = new_id(), utc_now()
        candidate = {
            "id": candidate_id,
            "conversation_id": conversation_id,
            "source_message_id": source_message_id,
            "content": content.replace("记住", "").strip() or content,
            "category": "preference",
            "private_local_only": False,
            "status": "proposed",
            "created_at": now,
        }
        conn.execute(
            """INSERT INTO memory_candidates(id, conversation_id, source_message_id, content, category,
               private_local_only, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_id, conversation_id, source_message_id, candidate["content"], "preference", 0,
                "proposed", now, (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            ),
        )
        return candidate

    def submit_message(
        self,
        conversation_id: str,
        content: str,
        route: str,
        request_id: str,
        idempotency_key: str | None,
        cloud_consent_id: str | None,
        companion: LocalCompanion,
        ollama: OllamaLocalAdapter,
    ) -> tuple[dict[str, Any], bool, dict[str, Any] | None, dict[str, Any] | None]:
        cached = self.idempotent_result(f"message.submit:{conversation_id}", idempotency_key)
        if cached:
            return cached, False, None, None
        if not self.conversation(conversation_id):
            raise KeyError("conversation")

        memories = self.relevant_memories(content)
        cloud_degraded = route != "local"
        now, user_message_id, assistant_message_id = utc_now(), new_id(), new_id()
        model_reply, model_status = ollama.reply(content, memories)
        reply = model_reply or companion.reply(content, memories)
        model_degraded = ollama.enabled and model_reply is None
        source = "ollama-local" if model_reply else ("fallback-local" if cloud_degraded or model_degraded else "local")
        with self.db.transaction() as conn:
            if cloud_degraded:
                self._consume_consent(conn, cloud_consent_id, request_id, route)
                conn.execute(
                    "INSERT INTO cloud_audits(id, request_id_hash, provider_id, upload_mode, result_status, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), hashlib.sha256(request_id.encode()).hexdigest(), "mvp-no-outbound", route, "blocked_mvp", 0, now),
                )
            conn.execute(
                "INSERT INTO messages(id, conversation_id, role, content, source, status, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_message_id, conversation_id, "user", content, None, "completed", request_id, now),
            )
            conn.execute(
                "INSERT INTO messages(id, conversation_id, role, content, source, status, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (assistant_message_id, conversation_id, "assistant", reply, source, "completed", request_id, utc_now()),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (utc_now(), conversation_id))
            candidate = self.create_memory_candidate_if_requested(conn, conversation_id, user_message_id, content)
            result = {
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "content": reply,
                "source": source,
                "memory_ids": [memory["id"] for memory in memories],
                "privacy": self.privacy_controls(),
            }
            self.save_idempotent(conn, f"message.submit:{conversation_id}", idempotency_key, result)
        return result, cloud_degraded, candidate, (model_status if model_degraded else None)

    def export_local_data(self) -> dict[str, Any]:
        """Return a complete local JSON-safe backup; it never performs I/O outside SQLite."""
        tables = (
            "conversations", "messages", "memory_candidates", "memories", "action_proposals", "tasks",
            "reminders", "cloud_consents", "cloud_audits", "audit_events",
        )
        exported = {table: self.db.fetchall(f"SELECT * FROM {table} ORDER BY rowid") for table in tables}
        for memory in exported["memories"]:
            memory["private_local_only"] = as_bool(memory["private_local_only"])
            memory["source_message_ids"] = self.db.decode(memory["source_message_ids"])
        exported["privacy_controls"] = self.privacy_controls()
        return {"format": "local-ai-mate-export-v1", "exported_at": utc_now(), "data": exported}

    def clear_local_data(self, idempotency_key: str | None) -> dict[str, Any]:
        operation = "privacy.clear"
        cached = self.idempotent_result(operation, idempotency_key)
        if cached:
            return cached
        # Every affected table is named here and returned to the caller. Database
        # schema/migration rows survive so the application remains runnable.
        tables = (
            "messages", "memory_candidates", "memories", "tasks", "reminders", "action_proposals",
            "conversations", "cloud_consents", "cloud_audits", "audit_events", "idempotency_keys",
        )
        with self.db.transaction() as conn:
            counts = {table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]) for table in tables}
            for table in tables:
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "UPDATE privacy_controls SET memory_candidates_paused = 0, retrieval_paused = 0, proactive_interactions_paused = 0, updated_at = ? WHERE singleton = 1",
                (utc_now(),),
            )
            result = {"cleared": True, "tables": counts, "privacy_controls_reset": True}
            # Do not save an idempotency record: it would be retained user data.
        return result

    def create_cloud_consent(
        self, request_id: str, provider_id: str, upload_mode: str, ttl_seconds: int, idempotency_key: str | None
    ) -> dict[str, Any]:
        cached = self.idempotent_result("cloud.consent", idempotency_key)
        if cached:
            return cached
        now = datetime.now(UTC)
        consent_id = new_id()
        fields = ["current_message"]
        if upload_mode == "cloud-with-summary":
            fields.append("ephemeral_summary")
        result = {
            "consent_id": consent_id,
            "request_id": request_id,
            "provider_id": provider_id,
            "upload_mode": upload_mode,
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "upload_fields": fields,
            "network_call_performed": False,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO cloud_consents(id, request_id, provider_id, upload_mode, nonce, issued_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (consent_id, request_id, provider_id, upload_mode, new_id(), now.isoformat(), result["expires_at"]),
            )
            conn.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_id(), "cloud.consent.created", consent_id, "created", utc_now()),
            )
            self.save_idempotent(conn, "cloud.consent", idempotency_key, result)
        return result

    def _consume_consent(self, conn: Any, consent_id: str | None, request_id: str, route: str) -> None:
        if not consent_id:
            raise PermissionError("missing cloud consent")
        consent = conn.execute("SELECT * FROM cloud_consents WHERE id = ?", (consent_id,)).fetchone()
        expected_mode = "current-message" if route == "cloud-current-message" else "cloud-with-summary"
        if (
            not consent
            or consent["used_at"] is not None
            or consent["request_id"] != request_id
            or consent["upload_mode"] != expected_mode
            or datetime.fromisoformat(consent["expires_at"]) <= datetime.now(UTC)
        ):
            raise PermissionError("invalid cloud consent")
        conn.execute("UPDATE cloud_consents SET used_at = ? WHERE id = ?", (utc_now(), consent_id))
        conn.execute(
            "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id(), "cloud.consent.used", consent_id, "mvp_no_outbound", utc_now()),
        )

    def candidates(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM memory_candidates WHERE conversation_id = ? AND status = 'proposed' ORDER BY created_at", (conversation_id,)
        )
        for row in rows:
            row["private_local_only"] = as_bool(row["private_local_only"])
        return rows

    def approve_candidate(self, candidate_id: str, payload: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        operation = f"memory.approve:{candidate_id}"
        cached = self.idempotent_result(operation, idempotency_key)
        if cached:
            return cached
        with self.db.transaction() as conn:
            candidate = conn.execute("SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)).fetchone()
            if not candidate:
                raise KeyError("candidate")
            if candidate["status"] != "proposed":
                raise ValueError("candidate already handled")
            memory_id, now = new_id(), utc_now()
            result = {
                "id": memory_id, "content": payload["content"], "category": payload["category"],
                "importance": payload["importance"], "private_local_only": payload["private_local_only"],
                "source_message_ids": [candidate["source_message_id"]], "created_at": now,
            }
            conn.execute(
                """INSERT INTO memories(id, content, category, importance, private_local_only, source_message_ids, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (memory_id, result["content"], result["category"], result["importance"], int(result["private_local_only"]),
                 self.db.encode(result["source_message_ids"]), now, now),
            )
            conn.execute("UPDATE memory_candidates SET status = 'approved', processed_at = ? WHERE id = ?", (now, candidate_id))
            conn.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_id(), "memory.created", memory_id, "approved", now),
            )
            self.save_idempotent(conn, operation, idempotency_key, result)
        return result

    def list_memories(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM memories ORDER BY updated_at DESC")
        for row in rows:
            row["private_local_only"] = as_bool(row["private_local_only"])
            row["source_message_ids"] = self.db.decode(row["source_message_ids"])
        return rows

    def update_memory(self, memory_id: str, changes: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        operation = f"memory.update:{memory_id}"
        cached = self.idempotent_result(operation, idempotency_key)
        if cached:
            return cached
        fields = {key: value for key, value in changes.items() if value is not None and key != "idempotency_key"}
        if not fields:
            raise ValueError("no updates")
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if not current:
                raise KeyError("memory")
            fields["updated_at"] = utc_now()
            sets = ", ".join(f"{key} = ?" for key in fields)
            values = [int(value) if key == "private_local_only" else value for key, value in fields.items()] + [memory_id]
            conn.execute(f"UPDATE memories SET {sets} WHERE id = ?", values)
            row = dict(conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone())
            row["private_local_only"] = as_bool(row["private_local_only"])
            row["source_message_ids"] = self.db.decode(row["source_message_ids"])
            self.save_idempotent(conn, operation, idempotency_key, row)
        return row

    def delete_memory(self, memory_id: str, idempotency_key: str | None) -> dict[str, Any]:
        operation = f"memory.delete:{memory_id}"
        cached = self.idempotent_result(operation, idempotency_key)
        if cached:
            return cached
        with self.db.transaction() as conn:
            if not conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone():
                raise KeyError("memory")
            # In the MVP the retrieval index is SQL-backed, so this transaction removes it too.
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            result = {"id": memory_id, "deleted": True}
            conn.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_id(), "memory.deleted", memory_id, "deleted", utc_now()),
            )
            self.save_idempotent(conn, operation, idempotency_key, result)
        return result

    def create_proposal(self, payload: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        operation = "proposal.create"
        cached = self.idempotent_result(operation, idempotency_key)
        if cached:
            return cached
        if payload["kind"] == "reminder" and (not payload.get("absolute_due_at") or not payload.get("timezone")):
            raise ValueError("reminder needs absolute due time and timezone")
        if payload.get("conversation_id") and not self.conversation(str(payload["conversation_id"])):
            raise KeyError("conversation")
        proposal_id, now = new_id(), utc_now()
        due_at = payload.get("absolute_due_at")
        due_at_text = due_at.astimezone(UTC).isoformat() if due_at else None
        result = {
            "id": proposal_id, "conversation_id": str(payload["conversation_id"]) if payload.get("conversation_id") else None,
            "kind": payload["kind"], "title": payload["title"], "absolute_due_at": due_at_text,
            "timezone": payload.get("timezone"), "repeat_rule": payload.get("repeat_rule"),
            "source_text": payload["source_text"], "status": "proposed", "created_at": now,
        }
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO action_proposals(id, conversation_id, kind, title, absolute_due_at, timezone, repeat_rule, source_text, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(result[key] for key in ("id", "conversation_id", "kind", "title", "absolute_due_at", "timezone", "repeat_rule", "source_text", "status", "created_at")),
            )
            self.save_idempotent(conn, operation, idempotency_key, result)
        return result

    def confirm_proposal(self, proposal_id: str, payload: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        operation = f"proposal.confirm:{proposal_id}"
        cached = self.idempotent_result(operation, idempotency_key)
        if cached:
            return cached
        with self.db.transaction() as conn:
            proposal = conn.execute("SELECT * FROM action_proposals WHERE id = ?", (proposal_id,)).fetchone()
            if not proposal:
                raise KeyError("proposal")
            if proposal["status"] != "proposed":
                raise ValueError("proposal already handled")
            title = payload.get("title") or proposal["title"]
            due = payload.get("absolute_due_at") or proposal["absolute_due_at"]
            due_text = due.astimezone(UTC).isoformat() if hasattr(due, "astimezone") else due
            timezone = payload.get("timezone") or proposal["timezone"]
            repeat = payload.get("repeat_rule") if payload.get("repeat_rule") is not None else proposal["repeat_rule"]
            if proposal["kind"] == "reminder" and (not due_text or not timezone):
                raise ValueError("reminder needs absolute due time and timezone")
            if proposal["kind"] == "reminder":
                duplicate = conn.execute(
                    "SELECT id FROM reminders WHERE title = ? AND trigger_at = ? AND COALESCE(repeat_rule, '') = COALESCE(?, '') AND status != 'deleted'",
                    (title, due_text, repeat),
                ).fetchone()
                if duplicate and not payload.get("replace_existing"):
                    raise FileExistsError("duplicate reminder")
                if duplicate:
                    conn.execute("UPDATE reminders SET status = 'deleted' WHERE id = ?", (duplicate["id"],))
                entity_id = new_id()
                conn.execute(
                    "INSERT INTO reminders(id, title, trigger_at, timezone, repeat_rule, status, proposal_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (entity_id, title, due_text, timezone, repeat, "scheduled", proposal_id, utc_now()),
                )
                entity_type = "reminder"
            else:
                entity_id = new_id()
                conn.execute(
                    "INSERT INTO tasks(id, title, due_at, status, proposal_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (entity_id, title, due_text, "open", proposal_id, utc_now()),
                )
                entity_type = "task"
            now = utc_now()
            conn.execute("UPDATE action_proposals SET status = 'confirmed', confirmed_at = ? WHERE id = ?", (now, proposal_id))
            result = {"proposal_id": proposal_id, "status": "confirmed", "entity_type": entity_type, "entity_id": entity_id}
            conn.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_id(), f"{entity_type}.created", entity_id, "confirmed", now),
            )
            self.save_idempotent(conn, operation, idempotency_key, result)
        return result

    def health(self, model_status: dict[str, Any]) -> dict[str, Any]:
        return {
            "database": "ready",
            "model": model_status,
            "cloud_outbound_enabled": False,
            "scheduler": "persistence-ready",
        }
