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


NicknameFrequency = Literal["never", "rare", "natural", "frequent"]
PersonalityStyle = Literal["gentle-intellectual", "cool-restrained", "bright-playful", "calm-supportive"]
IntimacyLevel = Literal["restrained", "natural", "close"]
ReplyLength = Literal["short", "medium", "detailed"]
QuestionFrequency = Literal["low", "medium", "high"]
ComfortStyle = Literal["listen-first", "balanced", "advice-first"]


def _visible_text(value: str, *, required: bool = False) -> str:
    normalized = value.strip()
    if required and not normalized:
        raise ValueError("must not be blank")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError("must contain visible text only")
    return normalized


class RelationshipSettingsUpdate(RequestModel):
    companion_name: str | None = Field(default=None, max_length=20)
    self_reference: str | None = Field(default=None, max_length=12)
    user_display_name: str | None = Field(default=None, max_length=20)
    user_nickname: str | None = Field(default=None, max_length=20)
    nickname_frequency: NicknameFrequency | None = None
    personality_style: PersonalityStyle | None = None
    intimacy_level: IntimacyLevel | None = None
    reply_length: ReplyLength | None = None
    question_frequency: QuestionFrequency | None = None
    comfort_style: ComfortStyle | None = None
    opening_greeting: str | None = Field(default=None, max_length=120)
    blocked_terms: list[str] | None = Field(default=None, max_length=8)
    expected_version: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("companion_name", "self_reference")
    @classmethod
    def required_visible(cls, value: str | None) -> str | None:
        return _visible_text(value, required=True) if value is not None else value

    @field_validator("user_display_name", "user_nickname", "opening_greeting")
    @classmethod
    def optional_visible(cls, value: str | None) -> str | None:
        return _visible_text(value) if value is not None else value

    @field_validator("blocked_terms")
    @classmethod
    def validate_blocked_terms(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        result: list[str] = []
        for item in value:
            normalized = _visible_text(item, required=True)
            if len(normalized) > 20:
                raise ValueError("blocked term is too long")
            if normalized not in result:
                result.append(normalized)
        return result


class RelationshipSettingsReset(RequestModel):
    confirmed: Literal[True]
    expected_version: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, max_length=128)
