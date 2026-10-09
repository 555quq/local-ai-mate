from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _nonblank(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("must not be blank")
    if any(ord(char) < 32 and char not in "\n\t" for char in normalized):
        raise ValueError("must not contain control characters")
    return normalized


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(RequestModel):
    title: str = Field(default="新对话", max_length=80)
    route_default: Literal["local", "ask-cloud-each-turn"] = "local"
    idempotency_key: str | None = Field(default=None, max_length=128)

    _validate_title = field_validator("title")(_nonblank)


class MessageCreate(RequestModel):
    content: str = Field(max_length=8_000)
    route: Literal["local", "cloud-current-message", "cloud-with-summary"] = "local"
    request_id: UUID | None = None
    cloud_consent_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)

    _validate_content = field_validator("content")(_nonblank)


class TTSSynthesizeRequest(RequestModel):
    text: str


class CloudConsentCreate(RequestModel):
    request_id: UUID
    provider_id: str = Field(min_length=1, max_length=80)
    upload_mode: Literal["current-message", "cloud-with-summary"]
    idempotency_key: str | None = Field(default=None, max_length=128)

    _validate_provider = field_validator("provider_id")(_nonblank)


class CandidateApprove(RequestModel):
    content: str = Field(max_length=2_000)
    category: Literal["profile", "preference", "event", "plan"]
    importance: int = Field(ge=1, le=5)
    private_local_only: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)

    _validate_content = field_validator("content")(_nonblank)


class MemoryUpdate(RequestModel):
    content: str | None = Field(default=None, max_length=2_000)
    category: Literal["profile", "preference", "event", "plan"] | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    private_local_only: bool | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("content")
    @classmethod
    def content_nonblank(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else value


class ActionProposalCreate(RequestModel):
    conversation_id: UUID | None = None
    kind: Literal["task", "reminder"]
    title: str = Field(max_length=160)
    absolute_due_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    repeat_rule: str | None = Field(default=None, max_length=256)
    source_text: str = Field(max_length=8_000)
    idempotency_key: str | None = Field(default=None, max_length=128)

    _validate_title = field_validator("title")(_nonblank)
    _validate_source = field_validator("source_text")(_nonblank)

    @field_validator("timezone")
    @classmethod
    def timezone_nonblank(cls, value: str | None) -> str | None:
        return _nonblank(value) if value else value


class ActionConfirm(RequestModel):
    title: str | None = Field(default=None, max_length=160)
    absolute_due_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    repeat_rule: str | None = Field(default=None, max_length=256)
    replace_existing: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("title", "timezone")
    @classmethod
    def optional_nonblank(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else value


class PrivacyControlsUpdate(RequestModel):
    memory_candidates_paused: bool | None = None
    retrieval_paused: bool | None = None
    proactive_interactions_paused: bool | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class PrivacyClear(RequestModel):
    confirmed: Literal[True]
    confirmation: Literal["清空本地数据"]
    idempotency_key: str | None = Field(default=None, max_length=128)
