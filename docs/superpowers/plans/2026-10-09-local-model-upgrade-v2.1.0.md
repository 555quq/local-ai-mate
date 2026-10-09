# 本地模型升级 v2.1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地文字模型升级到 Qwen3-4B-Instruct，将中文语音识别升级到 SenseVoiceSmall，并保持 GPT-SoVITS 主音色与回退链稳定。

**Architecture:** Ollama 继续负责 loopback-only 文本生成，仅切换模型标签。SenseVoiceSmall 在独立 Python 运行时中由持久 worker 进程加载，后端通过 NDJSON 管道发送内存音频；worker 失败时自动回退现有 faster-whisper 服务。GPT-SoVITS 代码和 v2.0.1 reset 播放协议不改变。

**Tech Stack:** Ollama, Qwen3, FunASR SenseVoiceSmall, Python subprocess/NDJSON, FastAPI, pytest, Vitest, Vite。

**Spec:** `docs/superpowers/specs/2026-10-09-local-model-upgrade-design-v2.1.0.md`

## Global Constraints

- 模型与音频全部本地处理，禁止新增云端出站。
- SenseVoice worker 只通过父子进程管道通信，不监听局域网端口。
- 单次音频仍限制 8 MiB 和 15 秒；输入音频不写入磁盘。
- GPT-SoVITS CUDA 主链和 Piper/CPU/reset 回退行为不能回归。
- 健康响应不得泄露路径、文本、音频或完整异常。

## Review Focus

- Qwen3-4B 与 GPT-SoVITS 同时运行时显存竞争：应保持语音服务可用，必要时让 Ollama CPU/混合运行。
- SenseVoice worker 首次加载慢或启动失败：应显示 configured/degraded 并无缝回退 faster-whisper。
- WebM/Ogg/WAV 三种输入：应通过同一内存协议完成识别，不能写临时音频文件。
- worker 输出非法 JSON 或超大结果：应转换为脱敏 STT 错误并回退，不污染 API 响应。
- 用户取消/并发识别：应释放管道和锁，下一次请求可继续工作。

### Task 1: 安装模型与隔离运行时

**Files:** Create `runtime/sensevoice/requirements.txt`, model directory `models/stt/sensevoice-small`; modify local startup notes.

- [x] 安装隔离 Python 环境和 FunASR 依赖。
- [x] 下载 `iic/SenseVoiceSmall` 及其必要配置到项目模型目录。
- [x] 用独立脚本执行一次离线模型加载与中文样例识别，记录版本和资源占用。

### Task 2: SenseVoice worker 与后端适配器

**Files:** Create `tools/sensevoice_worker.py`, modify `backend/app/config.py`, `backend/app/stt.py`, `backend/app/main.py`, test `backend/tests/test_stt.py`.

- [x] 为 worker 定义 `ready/transcribed/error/shutdown` NDJSON 事件，拒绝未知命令和超大行。
- [x] 实现 `SenseVoiceSTTService` 的懒加载、单并发锁、worker 重启、脱敏状态和异常映射。
- [x] 保留 `FasterWhisperSTTService` 作为 fallback；以 `LOCAL_AI_MATE_STT_ENGINE=sensevoice` 控制首选引擎。
- [x] 添加 fake worker/模型测试，覆盖正常转写、缺失运行时、非法协议、忙锁、fallback 和健康脱敏。

### Task 3: Qwen3-4B 配置与回归

**Files:** Modify `backend/app/config.py`, `README.md`, `backend/README.md`, tests for Ollama status/message path.

- [x] 下载 `qwen3:4b-instruct` 并确认 Ollama `/api/tags` 可见。
- [x] 启动参数切换到 Qwen3-4B，保持 non-thinking、128 token 上限和本地短回复约束。
- [x] 实测健康、消息 SSE、回退行为及显存观察值；异常时恢复原模型标签而不破坏数据库。

### Task 4: 全量验证与文档

**Files:** Create `quality/luna-v2.1.0-model-upgrade-verification.md`, update `quality/README.md`.

- [x] 运行后端全量 pytest、前端 Vitest 和 Vite build。
- [x] 运行真实健康、聊天、STT、TTS 流式 smoke test。
- [x] 记录模型版本、延迟、资源占用和 fallback 结果，确认服务重启后仍可用。
