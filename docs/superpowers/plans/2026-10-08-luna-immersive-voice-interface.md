# 露娜沉浸式界面与 V3 连续语音 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将现有 React/FastAPI 本地伴侣升级为使用“露娜纯净版”背景的沉浸式对话界面，并实现 CPU 本地 STT 驱动的 V3 半双工连续语音会话。

**Architecture:** 对话页替换为独立 `ImmersiveChat` 画布，保留现有 App 的会话、云端同意、记忆和 Piper 调用；浏览器 `useVoiceSession` 负责麦克风、VAD 和内存音频轮次，后端 `STTService` 负责单任务 faster-whisper 转写。非对话页面继续使用现有控制台布局，STT/TTS 任一失败都只降级为文字路径。

**Tech Stack:** React 18、TypeScript 5.6、Vite 5、Vitest + Testing Library、Web Audio/MediaRecorder、FastAPI、Python 3.12、faster-whisper base CPU int8、Piper 1.8.0。

**Spec:** `docs/superpowers/specs/2026-10-08-local-ai-companion-design-v1.7.0.md`

## Global Constraints

- 目标机器固定为 Ryzen 5 6600H、16 GB 内存、RTX 3050 Laptop 4 GB；STT/TTS 禁止自动使用 GPU。
- 背景源文件固定为 `D:/A/露娜纯净版.png`，前端只打包本地副本，不加载远程图片。
- 对话页背景只做 1–2% CSS 微动，并遵守 `prefers-reduced-motion`；不实现 Wav2Lip、Live2D 或实时生成脸部。
- 历史默认隐藏，左上按钮打开；覆盖层不推动、不缩放、不变暗背景；容器和气泡透明，仅文字阴影。
- S1 字幕固定为右下双层：用户小号灰白、助手大号淡紫，最多三行，完整正文保留在历史。
- V3 是显式点击开始、显式点击停止的半双工会话；thinking/speaking 时暂停麦克风，不实现背景唤醒词和全双工打断。
- 原始音频只存在于浏览器/请求内存，最大 8 MiB、单轮最长 15 秒，不写日志、数据库、磁盘或备份。
- 现有本机会话令牌、Origin 校验、云端确认、记忆审核、提醒和 Piper 接口行为不得回退。
- 工作区不是 Git 仓库；本计划中的提交步骤以“记录任务验证结果”替代，不执行 `git commit`。

## Review Focus

- 麦克风权限被拒绝或运行中撤销：立即释放已有轨道、显示可恢复错误，文字输入仍可发送。
- 只有静音或环境底噪：不调用 STT、不创建空用户消息，继续等待下一轮或保持可停止状态。
- 用户点击停止、切换页面或组件卸载：MediaRecorder、AudioContext、定时器、动画帧和所有 MediaStream 轨道均停止。
- STT 请求为空、超 8 MiB、类型不支持、服务正忙或模型缺失：返回固定错误码且不保留音频；前端不自动重试或改发云端。
- TTS 自动播放被阻止或合成失败：助手文字和 S1 字幕保持可读，V3 会话安全恢复聆听或允许用户停止，不陷入 speaking。

---

### Task 1: 本地 STT 服务与 API

**Files:**
- Create: `backend/app/stt.py`
- Create: `backend/tests/test_stt.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: 原始请求体 `audio/webm|audio/ogg|audio/wav`，`Settings.stt_*` 配置，现有 `write_guard`。
- Produces: `STTResult(text: str, language: str, duration_ms: int)`；`STTService.transcribe(audio: bytes, content_type: str) -> STTResult`；`POST /api/v1/stt/transcribe`；健康检查 `stt: {enabled,status}`。

- [x] **Step 1: 编写失败测试**

在 `backend/tests/test_stt.py` 覆盖：有效音频返回信封；会话令牌/Origin 必须正确；空音频、超 8 MiB、未知类型；busy/unavailable/failed 映射固定错误码；健康检查不泄露路径。

- [x] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_stt -v`
Expected: FAIL，原因是 `app.stt` 或 STT 路由尚不存在。

- [x] **Step 3: 实现服务与配置**

在 `backend/app/stt.py` 定义 `STTResult`、`STTService`、`FasterWhisperSTTService`、`STTBusy`、`STTUnavailable`、`STTTranscriptionFailed`。模型延迟加载，固定 `device="cpu"`、`compute_type="int8"`、`language="zh"`、`beam_size=1`、`vad_filter=True`，使用非阻塞互斥锁且直接读取 `io.BytesIO`。

- [x] **Step 4: 实现 API 和健康状态**

将 `create_app(settings, tts_service=None, stt_service=None)` 扩展为可注入 STT；请求读取上限 8 MiB，严格检查媒体类型并用 `asyncio.to_thread` 转写。成功返回 `text`、`language`、`duration_ms`，全部响应 `Cache-Control: no-store`。

- [x] **Step 5: 运行后端测试**

Run: `python -m unittest discover -s tests -v`
Expected: PASS，原 API/TTS 测试与新 STT 测试全部通过。

### Task 2: 浏览器 VAD、录音状态机与 API 客户端

**Files:**
- Create: `frontend/src/voice/activity.ts`
- Create: `frontend/src/voice/activity.test.ts`
- Create: `frontend/src/hooks/useVoiceSession.ts`
- Create: `frontend/src/hooks/useVoiceSession.test.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/vite.config.ts`

**Interfaces:**
- Consumes: `api.transcribeSpeech(blob)`、浏览器 `getUserMedia`/`MediaRecorder`/`AudioContext`、外部 `suspended` 标志。
- Produces: `VoicePhase = idle|requesting|listening|recording|transcribing|thinking|speaking|error`；`useVoiceSession({ suspended, onTranscript, maxTurnMs, silenceMs })` 返回 `{active,phase,error,start,stop,setPhase}`。

- [x] **Step 1: 添加 Vitest 测试入口并写 VAD 失败测试**

测试阈值以下持续静音返回 `discard`，检测到人声后静音 900ms 返回 `submit`，15 秒硬上限返回 `submit`，从未检测到人声的 15 秒返回 `discard`。

- [x] **Step 2: 实现纯函数 VAD 跟踪器并验证**

Run: `npm test -- --run src/voice/activity.test.ts`
Expected: PASS。

- [x] **Step 3: 写 Hook 生命周期失败测试**

覆盖权限拒绝、停止时释放每条 track、静音不请求 STT、有效 blob 调用 `onTranscript`、unmount 清理 AudioContext/RAF/计时器、`suspended=true` 不采集。

- [x] **Step 4: 实现 `api.transcribeSpeech` 与 `useVoiceSession`**

原始 Blob 作为请求体发送，保留其受支持媒体类型；Hook 不落盘音频、不在状态中长期保存 Blob；停止和异常共用同一幂等清理路径。

- [x] **Step 5: 运行前端单元测试**

Run: `npm test -- --run`
Expected: PASS。

### Task 3: 露娜沉浸式对话组件

**Files:**
- Create: `frontend/src/assets/luna-background.png`
- Create: `frontend/src/components/ImmersiveChat.tsx`
- Create: `frontend/src/components/ImmersiveChat.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `Conversation`、`VoicePhase`、draft/route/sending/health/speech 状态与 App 回调。
- Produces: `ImmersiveChat`；左上历史/页面/设置入口；透明历史覆盖层；中央状态光球；输入框；S1 字幕；无障碍键盘行为。

- [x] **Step 1: 写组件失败测试**

覆盖：历史默认隐藏；按钮打开；`Escape` 和空白关闭并返还焦点；最近用户/助手消息组成 S1 字幕；停止按钮仅在 V3 激活时出现；图标均有中文 accessible name。

- [x] **Step 2: 复制已批准背景资源并实现组件**

复制源图像字节，不做重编码；背景使用本地 import。历史只渲染纯文字和阴影，不创建不透明面板/气泡；关闭时 `pointer-events:none`。

- [x] **Step 3: 实现响应式与减少动态效果**

桌面保持原构图，窗口变窄时缩小光球、固定输入区并限制字幕安全区；`prefers-reduced-motion: reduce` 下禁用背景漂移、光晕脉冲和抽屉过渡。

- [x] **Step 4: 运行组件测试与构建**

Run: `npm test -- --run src/components/ImmersiveChat.test.tsx`
Expected: PASS。

Run: `npm run build`
Expected: TypeScript 与 Vite 构建无错误。

### Task 4: App、Piper 播放与 V3 轮次集成

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/hooks/useSpeechPlayback.ts`
- Modify: `frontend/src/hooks/useSpeechPlayback.test.tsx`
- Modify: `frontend/src/components/StatusBar.tsx`（仅在非对话页继续使用时）

**Interfaces:**
- Consumes: Tasks 1–3 的 `api.transcribeSpeech`、`useVoiceSession`、`ImmersiveChat`。
- Produces: 文本与语音共用 `submitContent(content, route)`；`useSpeechPlayback.play(message) -> Promise<'ended'|'blocked'|'failed'|'stopped'>`；V3 完整轮次协调。

- [x] **Step 1: 写播放完成与失败测试**

测试 `play` 只在 ended 后解析；自动播放拒绝/音频错误返回明确结果并清理 active state；`stop` 解析当前等待者且撤销活动状态。

- [x] **Step 2: 重构发送路径**

文字输入和语音转写都调用 `submitContent`；语音只走 `local` 路由，非空转写先显示在输入状态/S1 字幕再发送；云端模式仍要求原确认卡片。

- [x] **Step 3: 协调 V3 半双工状态**

语音转写提交后保持 suspended；模型生成时 `thinking`；TTS 加载/播放时 `speaking`；播放 ended/blocked/failed 后恢复 `listening`；用户停止、切页或卸载回 `idle` 并释放资源。

- [x] **Step 4: 保持非对话页面功能**

今日、记忆、关系设定、隐私与云端入口通过左上页面按钮访问；已有组件和云端同意对话框继续工作。

- [x] **Step 5: 运行完整前端测试和构建**

Run: `npm test -- --run`
Expected: PASS。

Run: `npm run build`
Expected: TypeScript 与 Vite 构建无错误。

### Task 5: 运行时安装、模型准备、文档与最终验证

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`
- Create: `quality/luna-v1.7.0-verification.md`

**Interfaces:**
- Consumes: 完整 v1.7.0 实现。
- Produces: 可重复的安装/启动/验证命令、STT 模型状态说明和实际测试记录。

- [x] **Step 1: 安装锁定依赖**

Run: `python -m pip install -r backend/requirements.txt` and `npm install`
Expected: faster-whisper 与前端测试依赖安装成功；不安装 GPU ONNX/CUDA STT 运行时。

- [x] **Step 2: 准备本地 faster-whisper base 模型**

将模型下载到 `models/stt/faster-whisper-base`；若网络不可达，保留完整降级路径并在验证报告中记录 `STT_UNAVAILABLE`，不得改用浏览器云端语音识别。

- [x] **Step 3: 更新文档**

记录背景来源、STT/TTS 配置变量、麦克风权限、V3 状态、隐私限制、启动方式和模型许可/分发注意事项。

- [x] **Step 4: 运行后端、前端与静态检查**

Run from `backend`: `python -m unittest discover -s tests -v`。

Run from `frontend`: `npm test -- --run`。

Run from `frontend`: `npm run build`。
Expected: 全部 PASS。

- [x] **Step 5: 运行本机冒烟验证**

验证健康接口、文字发送、Piper 播放、历史开关/S1 字幕、麦克风拒绝降级和一次真实短中文 STT；结果写入 `quality/luna-v1.7.0-verification.md`。


