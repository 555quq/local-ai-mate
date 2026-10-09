from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings
from .db import Database
from .gpt_sovits import AudioFragment, FallbackStreamingTTSService, GPTSoVITSWorkerService, StreamingTTSService
from .schemas import (
    ActionConfirm,
    ActionProposalCreate,
    CandidateApprove,
    CloudConsentCreate,
    ConversationCreate,
    MemoryUpdate,
    MessageCreate,
    PrivacyClear,
    PrivacyControlsUpdate,
    TTSSynthesizeRequest,
)
from .services import LocalCompanion, OllamaLocalAdapter, Repository, new_id
from .stt import (
    FasterWhisperSTTService,
    SenseVoiceSTTService,
    STTBusy,
    STTService,
    STTTranscriptionFailed,
    STTUnavailable,
)
from .tts import PiperTTSService, TTSBusy, TTSSynthesisFailed, TTSService, TTSUnavailable


class ApiProblem(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


def now() -> str:
    return datetime.now(UTC).isoformat()


def envelope(data: Any, request_id: str | None = None) -> dict[str, Any]:
    return {"request_id": request_id or new_id(), "server_time": now(), "data": data}


class RateLimiter:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, scope: str, key: str, limit: int) -> None:
        clock = time.monotonic()
        values = self.events[(scope, key)]
        while values and clock - values[0] > 60:
            values.popleft()
        if len(values) >= limit:
            raise ApiProblem(429, "RATE_LIMITED", "请求过于频繁，请稍后重试。", retryable=True)
        values.append(clock)


def create_app(
    settings: Settings | None = None,
    tts_service: TTSService | None = None,
    stt_service: STTService | None = None,
    streaming_tts_service: StreamingTTSService | None = None,
) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.db_path)
    repository = Repository(database)
    companion = LocalCompanion()
    ollama = OllamaLocalAdapter(settings)
    tts = tts_service or PiperTTSService(settings)
    if streaming_tts_service is not None:
        streaming_tts = streaming_tts_service
    elif tts_service is None and settings.tts_primary == "gpt-sovits":
        cuda_worker = GPTSoVITSWorkerService(
            settings,
            python_path=settings.gsv_cuda_python,
            device="cuda",
        )
        cpu_worker = GPTSoVITSWorkerService(
            settings,
            python_path=settings.gsv_cpu_python,
            device="cpu",
        )
        streaming_tts = FallbackStreamingTTSService(
            settings,
            cuda=cuda_worker,
            cpu=cpu_worker,
            piper=tts,
        )
    else:
        streaming_tts = None
    if stt_service is not None:
        stt = stt_service
    else:
        faster_stt = FasterWhisperSTTService(settings)
        stt = (
            SenseVoiceSTTService(settings, fallback=faster_stt)
            if settings.stt_engine == "sensevoice"
            else faster_stt
        )
    limiter = RateLimiter()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        warmup_task: asyncio.Task[None] | None = None
        stt_warmup_task: asyncio.Task[None] | None = None
        if streaming_tts is not None:
            warmup_task = asyncio.create_task(asyncio.to_thread(streaming_tts.warmup))
        stt_warmup = getattr(stt, "warmup", None)
        if stt_warmup is not None:
            stt_warmup_task = asyncio.create_task(asyncio.to_thread(stt_warmup))
        try:
            yield
        finally:
            if warmup_task is not None and not warmup_task.done():
                warmup_task.cancel()
            if stt_warmup_task is not None and not stt_warmup_task.done():
                stt_warmup_task.cancel()
            if streaming_tts is not None:
                await asyncio.to_thread(streaming_tts.close)
            stt_close = getattr(stt, "close", None)
            if stt_close is not None:
                await asyncio.to_thread(stt_close)
            ollama.close()

    app = FastAPI(title="Local AI Mate API", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.repository = repository
    app.state.session_token = settings.session_token
    app.state.network_observations: list[dict[str, Any]] = []
    app.state.ollama = ollama
    app.state.tts = tts
    app.state.streaming_tts = streaming_tts
    app.state.stt = stt

    @app.exception_handler(ApiProblem)
    async def handle_problem(request: Request, error: ApiProblem) -> JSONResponse:
        request_id = getattr(request.state, "request_id", new_id())
        return JSONResponse(
            status_code=error.status_code,
            content={
                "request_id": request_id,
                "server_time": now(),
                "error": {"code": error.code, "message": error.message, "retryable": error.retryable},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", new_id())
        code = "VALIDATION_FAILED"
        message = "请求字段无效。"
        for item in error.errors():
            location = item.get("loc", ())
            if "content" not in location:
                continue
            if item.get("type") == "string_too_long":
                code, message = "VAL-02", "这条消息太长，请拆分后发送。"
            else:
                code, message = "VAL-01", "请输入内容后再发送。"
            break
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request_id,
                "server_time": now(),
                "error": {"code": code, "message": message, "retryable": False},
            },
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., Any]) -> Response:
        request.state.request_id = request.headers.get("X-Request-Id") or new_id()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_session(
        request: Request,
        x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> None:
        expected = settings.session_token
        if not x_session_token or not hmac.compare_digest(x_session_token, expected):
            raise ApiProblem(401, "SESSION_INVALID", "本机会话已失效，请重新打开应用。")
        # The launcher sets this to its exact, random-port frontend origin before
        # opening the browser. Missing Origin is rejected to prevent same-machine
        # webpages from driving the loopback API with a leaked token.
        origin = request.headers.get("Origin")
        if origin != settings.allowed_origin:
            raise ApiProblem(401, "ORIGIN_INVALID", "请求来源无效。")

    def write_guard(limit_scope: str, limit: int) -> Callable[..., None]:
        def guard(request: Request, _: None = Depends(require_session)) -> None:
            limiter.check(limit_scope, settings.session_token, limit)

        return guard

    @app.get("/api/v1/health")
    def health(request: Request) -> dict[str, Any]:
        data = repository.health(ollama.status())
        data["tts"] = streaming_tts.status() if streaming_tts is not None else tts.status()
        data["stt"] = stt.status()
        return envelope(data, request.state.request_id)

    @app.post("/api/v1/stt/transcribe", dependencies=[Depends(write_guard("stt", 60))])
    async def transcribe_speech(request: Request) -> dict[str, Any]:
        content_type = request.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if content_type not in {"audio/webm", "audio/ogg", "audio/wav"}:
            raise ApiProblem(415, "STT_UNSUPPORTED_MEDIA", "不支持这种音频格式，请使用 WebM、Ogg 或 WAV。")

        audio_buffer = bytearray()
        try:
            async for chunk in request.stream():
                if len(audio_buffer) + len(chunk) > settings.stt_max_audio_bytes:
                    raise ApiProblem(413, "STT_AUDIO_TOO_LARGE", "音频不能超过 8 MiB，请缩短后重试。")
                audio_buffer.extend(chunk)
            if not audio_buffer:
                raise ApiProblem(422, "STT_EMPTY_AUDIO", "没有识别到可用语音，请重试。")
            try:
                result = await asyncio.to_thread(stt.transcribe, bytes(audio_buffer), content_type)
            except STTBusy as exc:
                raise ApiProblem(429, "STT_BUSY", "本地语音识别正忙，请稍后重试。", retryable=True) from exc
            except STTUnavailable as exc:
                raise ApiProblem(
                    503,
                    "STT_UNAVAILABLE",
                    "本地语音识别暂时不可用，文字输入仍可使用。",
                    retryable=True,
                ) from exc
            except STTTranscriptionFailed as exc:
                raise ApiProblem(
                    503,
                    "STT_TRANSCRIPTION_FAILED",
                    "本地语音识别失败，文字输入仍可使用。",
                    retryable=True,
                ) from exc
            if result.duration_ms > settings.stt_max_duration_ms:
                raise ApiProblem(413, "STT_AUDIO_TOO_LARGE", "音频不能超过 15 秒，请缩短后重试。")
            return envelope(
                {"text": result.text, "language": result.language, "duration_ms": result.duration_ms},
                request.state.request_id,
            )
        finally:
            audio_buffer.clear()

    @app.post("/api/v1/tts/synthesize", dependencies=[Depends(write_guard("tts", 60))])
    async def synthesize_speech(payload: TTSSynthesizeRequest, request: Request) -> Response:
        text = payload.text.strip()
        if not text:
            raise ApiProblem(422, "TTS_EMPTY_TEXT", "请输入需要朗读的文字。")
        if len(text) > settings.tts_max_chars:
            raise ApiProblem(422, "TTS_TEXT_TOO_LONG", "朗读内容最多为 500 个字符，请缩短后重试。")
        try:
            audio = await asyncio.to_thread(tts.synthesize, text)
        except TTSBusy as exc:
            raise ApiProblem(429, "TTS_BUSY", "正在朗读上一条回复，请稍后重试。", retryable=True) from exc
        except TTSUnavailable as exc:
            raise ApiProblem(503, "TTS_UNAVAILABLE", "本地语音服务不可用，文字回复不受影响。", retryable=True) from exc
        except TTSSynthesisFailed as exc:
            raise ApiProblem(503, "TTS_SYNTHESIS_FAILED", "本地语音合成失败，可以稍后重试播放。", retryable=True) from exc
        return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    def next_fragment(iterator: Iterator[AudioFragment]) -> AudioFragment | None:
        try:
            return next(iterator)
        except StopIteration:
            return None

    def ndjson_event(event: dict[str, Any]) -> str:
        return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"

    @app.post("/api/v1/tts/stream", dependencies=[Depends(write_guard("tts", 60))])
    async def stream_speech(payload: TTSSynthesizeRequest, request: Request) -> StreamingResponse:
        text = payload.text.strip()
        if not text:
            raise ApiProblem(422, "TTS_EMPTY_TEXT", "请输入需要朗读的文字。")
        if len(text) > settings.tts_max_chars:
            raise ApiProblem(422, "TTS_TEXT_TOO_LONG", "朗读内容最多为 500 个字符，请缩短后重试。")

        if streaming_tts is not None:
            iterator = streaming_tts.stream(text)
        else:
            def legacy_stream() -> Iterator[AudioFragment]:
                yield AudioFragment(0, tts.synthesize(text), "piper", "cpu")

            iterator = legacy_stream()

        try:
            first = await asyncio.to_thread(next_fragment, iterator)
        except TTSBusy as exc:
            raise ApiProblem(429, "TTS_BUSY", "正在朗读上一条回复，请稍后重试。", retryable=True) from exc
        except TTSUnavailable as exc:
            raise ApiProblem(503, "TTS_UNAVAILABLE", "本地语音服务不可用，文字回复不受影响。", retryable=True) from exc
        except TTSSynthesisFailed as exc:
            raise ApiProblem(503, "TTS_SYNTHESIS_FAILED", "本地语音合成失败，可以稍后重试播放。", retryable=True) from exc
        if first is None:
            raise ApiProblem(503, "TTS_SYNTHESIS_FAILED", "本地语音没有生成可播放音频。", retryable=True)

        async def body() -> AsyncIterator[str]:
            started = time.monotonic()
            count = 0
            total_bytes = 0
            current: AudioFragment | None = first
            try:
                yield ndjson_event(
                    {
                        "event": "started",
                        "request_id": request.state.request_id,
                        "engine": first.engine,
                        "device": first.device,
                    }
                )
                while current is not None:
                    if len(current.audio) > 4 * 1024 * 1024:
                        raise TTSSynthesisFailed("audio fragment too large")
                    if current.restart and count > 0:
                        yield ndjson_event(
                            {
                                "event": "reset",
                                "request_id": request.state.request_id,
                                "engine": current.engine,
                                "device": current.device,
                                "reason": "primary_failed",
                            }
                        )
                        count = 0
                        total_bytes = 0
                    total_bytes += len(current.audio)
                    if total_bytes > settings.gsv_cache_bytes:
                        raise TTSSynthesisFailed("audio stream too large")
                    yield ndjson_event(
                        {
                            "event": "fragment",
                            "request_id": request.state.request_id,
                            "index": count,
                            "media_type": "audio/wav",
                            "audio_base64": base64.b64encode(current.audio).decode("ascii"),
                        }
                    )
                    count += 1
                    current = await asyncio.to_thread(next_fragment, iterator)
                yield ndjson_event(
                    {
                        "event": "completed",
                        "request_id": request.state.request_id,
                        "fragment_count": count,
                        "generation_seconds": round(time.monotonic() - started, 3),
                    }
                )
            except asyncio.CancelledError:
                if streaming_tts is not None:
                    streaming_tts.cancel()
                raise
            except (TTSUnavailable, TTSSynthesisFailed):
                yield ndjson_event(
                    {
                        "event": "error",
                        "request_id": request.state.request_id,
                        "code": "TTS_STREAM_INTERRUPTED",
                        "retryable": True,
                    }
                )
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    await asyncio.to_thread(close)

        return StreamingResponse(
            body(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    def settings_data() -> dict[str, Any]:
        return {
            "privacy": repository.privacy_controls(),
            "model": ollama.status(),
            "cloud_outbound_enabled": False,
        }

    @app.get("/api/v1/settings", dependencies=[Depends(require_session)])
    def get_settings(request: Request) -> dict[str, Any]:
        return envelope(settings_data(), request.state.request_id)

    @app.patch("/api/v1/settings", dependencies=[Depends(write_guard("settings", 20))])
    def patch_settings(payload: PrivacyControlsUpdate, request: Request) -> dict[str, Any]:
        try:
            repository.update_privacy_controls(payload.model_dump(), payload.idempotency_key)
        except ValueError as exc:
            raise ApiProblem(422, "SETTINGS_UPDATE_EMPTY", "请至少修改一项隐私控制。") from exc
        return envelope(settings_data(), request.state.request_id)

    @app.post("/api/v1/conversations", status_code=201, dependencies=[Depends(write_guard("conversation", 20))])
    def create_conversation(payload: ConversationCreate, request: Request) -> dict[str, Any]:
        data = repository.create_conversation(payload.title, payload.route_default, payload.idempotency_key)
        return envelope(data, request.state.request_id)

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    def get_messages(conversation_id: str, request: Request) -> dict[str, Any]:
        if not repository.conversation(conversation_id):
            raise ApiProblem(404, "CONVERSATION_NOT_FOUND", "找不到该对话。")
        return envelope({"items": repository.messages(conversation_id)}, request.state.request_id)

    def sse(event: str, request_id: str, data: dict[str, Any] | None = None) -> str:
        body: dict[str, Any] = {"event": event, "request_id": request_id}
        if data is not None:
            body["data"] = data
        return f"event: {event}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"

    @app.post("/api/v1/conversations/{conversation_id}/messages", dependencies=[Depends(write_guard("message", 20))])
    async def submit_message(conversation_id: str, payload: MessageCreate, request: Request) -> StreamingResponse:
        if not repository.conversation(conversation_id):
            raise ApiProblem(404, "CONVERSATION_NOT_FOUND", "找不到该对话。")
        request_id = str(payload.request_id or uuid.uuid4())
        try:
            result, cloud_degraded, candidate, model_degraded = await asyncio.to_thread(
                repository.submit_message,
                conversation_id=conversation_id,
                content=payload.content,
                route=payload.route,
                request_id=request_id,
                idempotency_key=payload.idempotency_key,
                cloud_consent_id=str(payload.cloud_consent_id) if payload.cloud_consent_id else None,
                companion=companion,
                ollama=ollama,
            )
        except PermissionError as exc:
            raise ApiProblem(409, "VAL-03", "尚未确认上传范围，内容没有发送。") from exc
        except KeyError as exc:
            raise ApiProblem(404, "CONVERSATION_NOT_FOUND", "找不到该对话。") from exc

        async def event_stream() -> AsyncIterator[str]:
            yield sse("queued", request_id, {"conversation_id": conversation_id})
            await asyncio.sleep(0)
            yield sse("retrieving_memory", request_id, {"memory_count": len(result["memory_ids"])})
            await asyncio.sleep(0)
            yield sse("generating", request_id, {"source": result["source"]})
            # One deterministic token event keeps the MVP stream contract intact while
            # avoiding an accidental cloud/Ollama call in test and offline modes.
            yield sse("token", request_id, {"text": result["content"]})
            if candidate:
                yield sse("memory_candidate", request_id, {"candidate": candidate})
            if model_degraded:
                yield sse(
                    "degraded", request_id,
                    {"reason": "local_model_unavailable", "message": "本地模型不可用，已使用本地确定性回答。", "model": model_degraded},
                )
            if cloud_degraded:
                yield sse(
                    "degraded",
                    request_id,
                    {"reason": "cloud_mvp_no_outbound", "message": "云端增强在 MVP 中未启用，已使用本地回答。", "message_id": result["assistant_message_id"]},
                )
            else:
                yield sse("completed", request_id, {"message_id": result["assistant_message_id"], "source": result["source"]})

        return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/v1/conversations/{conversation_id}/memory-candidates")
    def get_candidates(conversation_id: str, request: Request) -> dict[str, Any]:
        if not repository.conversation(conversation_id):
            raise ApiProblem(404, "CONVERSATION_NOT_FOUND", "找不到该对话。")
        return envelope({"items": repository.candidates(conversation_id)}, request.state.request_id)

    @app.post("/api/v1/memory-candidates/{candidate_id}/approve", dependencies=[Depends(write_guard("memory", 30))])
    def approve_candidate(candidate_id: str, payload: CandidateApprove, request: Request) -> dict[str, Any]:
        try:
            data = repository.approve_candidate(candidate_id, payload.model_dump(), payload.idempotency_key)
        except KeyError as exc:
            raise ApiProblem(404, "MEMORY_CANDIDATE_NOT_FOUND", "找不到该记忆候选。") from exc
        except ValueError as exc:
            raise ApiProblem(409, "MEMORY_CANDIDATE_HANDLED", "这条候选已更新，请刷新后查看。") from exc
        return envelope(data, request.state.request_id)

    @app.get("/api/v1/memories")
    def get_memories(request: Request) -> dict[str, Any]:
        return envelope({"items": repository.list_memories()}, request.state.request_id)

    @app.patch("/api/v1/memories/{memory_id}", dependencies=[Depends(write_guard("memory", 30))])
    def patch_memory(memory_id: str, payload: MemoryUpdate, request: Request) -> dict[str, Any]:
        try:
            data = repository.update_memory(memory_id, payload.model_dump(), payload.idempotency_key)
        except KeyError as exc:
            raise ApiProblem(404, "MEMORY_NOT_FOUND", "找不到该记忆。") from exc
        except ValueError as exc:
            raise ApiProblem(422, "MEMORY_UPDATE_EMPTY", "请至少修改一项记忆内容。") from exc
        return envelope(data, request.state.request_id)

    @app.delete("/api/v1/memories/{memory_id}", dependencies=[Depends(write_guard("memory", 30))])
    def remove_memory(memory_id: str, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
        try:
            data = repository.delete_memory(memory_id, idempotency_key)
        except KeyError as exc:
            raise ApiProblem(404, "MEMORY_NOT_FOUND", "找不到该记忆。") from exc
        return envelope(data, request.state.request_id)

    @app.get("/api/v1/privacy/export", dependencies=[Depends(require_session)])
    def export_privacy_data(request: Request) -> dict[str, Any]:
        return envelope(repository.export_local_data(), request.state.request_id)

    @app.post("/api/v1/privacy/clear", dependencies=[Depends(write_guard("privacy-clear", 5))])
    def clear_privacy_data(payload: PrivacyClear, request: Request) -> dict[str, Any]:
        return envelope(repository.clear_local_data(payload.idempotency_key), request.state.request_id)

    @app.post("/api/v1/cloud/consents", status_code=201, dependencies=[Depends(write_guard("cloud-consent", 5))])
    def create_cloud_consent(payload: CloudConsentCreate, request: Request) -> dict[str, Any]:
        data = repository.create_cloud_consent(
            str(payload.request_id), payload.provider_id, payload.upload_mode, settings.cloud_consent_ttl_seconds, payload.idempotency_key
        )
        return envelope(data, request.state.request_id)

    @app.post("/api/v1/action-proposals", status_code=201, dependencies=[Depends(write_guard("proposal", 30))])
    def create_action_proposal(payload: ActionProposalCreate, request: Request) -> dict[str, Any]:
        try:
            data = repository.create_proposal(payload.model_dump(), payload.idempotency_key)
        except KeyError as exc:
            raise ApiProblem(404, "CONVERSATION_NOT_FOUND", "找不到关联对话。") from exc
        except ValueError as exc:
            raise ApiProblem(422, "REMINDER_TIME_REQUIRED", "时间还不明确，请补充具体日期和时间。") from exc
        return envelope(data, request.state.request_id)

    @app.post("/api/v1/action-proposals/{proposal_id}/confirm", dependencies=[Depends(write_guard("proposal", 30))])
    def confirm_action_proposal(proposal_id: str, payload: ActionConfirm, request: Request) -> dict[str, Any]:
        try:
            data = repository.confirm_proposal(proposal_id, payload.model_dump(), payload.idempotency_key)
        except KeyError as exc:
            raise ApiProblem(404, "ACTION_PROPOSAL_NOT_FOUND", "找不到该操作草案。") from exc
        except FileExistsError as exc:
            raise ApiProblem(409, "REMINDER_DUPLICATE", "已存在相同提醒，请选择保留、替换或重新编辑。") from exc
        except ValueError as exc:
            message = "时间还不明确，请补充具体日期和时间。" if "due time" in str(exc) else "这条操作草案已更新，请刷新后查看。"
            raise ApiProblem(422 if "due time" in str(exc) else 409, "ACTION_PROPOSAL_INVALID", message) from exc
        return envelope(data, request.state.request_id)

    @app.get("/api/v1/reminders")
    def get_reminders(request: Request) -> dict[str, Any]:
        rows = database.fetchall("SELECT * FROM reminders WHERE status != 'deleted' ORDER BY trigger_at")
        return envelope({"items": rows}, request.state.request_id)

    @app.get("/api/v1/test/network-observations", dependencies=[Depends(require_session)])
    def network_observations(request: Request) -> dict[str, Any]:
        if os.environ.get("APP_TEST_MODE") != "1":
            raise ApiProblem(404, "NOT_FOUND", "找不到该接口。")
        fields = sorted({field for observation in app.state.network_observations for field in observation.get("fields", [])})
        return envelope(
            {
                "outbound_count": len(app.state.network_observations),
                "observed_fields": fields,
                "local_model_request_count": ollama.request_count,
                "local_model_status": ollama.status(refresh=False),
            },
            request.state.request_id,
        )

    return app


app = create_app()
