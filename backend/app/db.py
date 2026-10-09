from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Small transactional SQLite repository.

    SQLCipher is the production encryption-at-rest upgrade. This MVP deliberately
    uses sqlite3 so that the local-only prototype has no native dependency.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, route_default TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          role TEXT NOT NULL, content TEXT NOT NULL, source TEXT, status TEXT NOT NULL,
          request_id TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at);
        CREATE TABLE IF NOT EXISTS memory_candidates (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          source_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          content TEXT NOT NULL, category TEXT NOT NULL, private_local_only INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, processed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL, importance INTEGER NOT NULL,
          private_local_only INTEGER NOT NULL, source_message_ids TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS action_proposals (
          id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
          kind TEXT NOT NULL, title TEXT NOT NULL, absolute_due_at TEXT, timezone TEXT,
          repeat_rule TEXT, source_text TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
          confirmed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, due_at TEXT, status TEXT NOT NULL,
          proposal_id TEXT NOT NULL UNIQUE REFERENCES action_proposals(id), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, trigger_at TEXT NOT NULL, timezone TEXT NOT NULL,
          repeat_rule TEXT, status TEXT NOT NULL, proposal_id TEXT NOT NULL UNIQUE REFERENCES action_proposals(id),
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cloud_consents (
          id TEXT PRIMARY KEY, request_id TEXT NOT NULL, provider_id TEXT NOT NULL, upload_mode TEXT NOT NULL,
          nonce TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cloud_audits (
          id TEXT PRIMARY KEY, request_id_hash TEXT NOT NULL, provider_id TEXT NOT NULL, upload_mode TEXT NOT NULL,
          result_status TEXT NOT NULL, duration_ms INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS idempotency_keys (
          operation TEXT NOT NULL, key TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(operation, key)
        );
        CREATE TABLE IF NOT EXISTS audit_events (
          id TEXT PRIMARY KEY, event_type TEXT NOT NULL, entity_id TEXT, result TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS privacy_controls (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          memory_candidates_paused INTEGER NOT NULL DEFAULT 0,
          retrieval_paused INTEGER NOT NULL DEFAULT 0,
          proactive_interactions_paused INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        );
        """
        with self.transaction() as conn:
            conn.executescript(schema)
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)", (1, utc_now()))
            conn.execute(
                """INSERT OR IGNORE INTO privacy_controls(singleton, memory_candidates_paused, retrieval_paused,
                   proactive_interactions_paused, updated_at) VALUES (1, 0, 0, 0, ?)""",
                (utc_now(),),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(sql, params).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                return [dict(row) for row in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()

    @staticmethod
    def encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def decode(value: str) -> Any:
        return json.loads(value)
