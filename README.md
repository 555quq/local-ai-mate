# Local AI Mate MVP

这是按 [v1.7.0 设计方案](docs/superpowers/specs/2026-10-08-local-ai-companion-design-v1.7.0.md) 开发，并持续升级到 [v2.0.0 实时流式语音方案](docs/superpowers/specs/2026-10-08-gpt-sovits-realtime-streaming-design-v2.0.0.md) 与 [v2.0.1 中断恢复方案](docs/superpowers/specs/2026-10-09-gpt-sovits-failure-recovery-design-v2.0.1.md) 的本机优先 AI 陪伴原型。

当前 MVP 提供露娜全屏沉浸式对话界面、Ollama 本地回复、faster-whisper 本地中文转写、GPT-SoVITS 流式克隆音色、Piper 最终回退、记忆候选审核、提醒草案与确认、云端增强的显式一次性同意流程，以及默认零云端出站的验证入口。V3 语音会话采用显式开始/停止的半双工模式；Windows 通知和 SQLCipher 加密尚未接入。当前 SQLite 仅适合开发验证，不能存放真实私密数据。

## 本地启动

使用两个 PowerShell 窗口。先设置同一个仅本机使用的随机令牌，例如：

```powershell
$env:LOCAL_AI_MATE_SESSION_TOKEN = 'replace-with-a-long-random-local-token'
$env:LOCAL_AI_MATE_ALLOWED_ORIGIN = 'http://127.0.0.1:5173'
$env:LOCAL_AI_MATE_STT_ENGINE = 'sensevoice'
$env:LOCAL_AI_MATE_SENSEVOICE_MODEL_PATH = 'D:\A\models\stt\sensevoice-small'
$env:LOCAL_AI_MATE_OLLAMA_ENABLED = '1'
$env:LOCAL_AI_MATE_OLLAMA_MODEL = 'qwen3:4b-instruct'
$env:LOCAL_AI_MATE_OLLAMA_TIMEOUT_SECONDS = '60'
Set-Location D:\A\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

首次运行时先创建项目隔离环境并安装依赖：

```powershell
Set-Location D:\A\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

当前 Faster-Whisper 与 PyAV 组合已固定为 `faster-whisper>=1.2,<2.0` 和 `av>=11,<16`，避免新版 PyAV 删除兼容参数导致真实音频无法解码。

Piper 运行时固定为 `piper-tts==1.8.0`，默认读取 `models/tts/piper/zh_CN-huayan-medium/zh_CN-huayan-medium.onnx`。可通过 `LOCAL_AI_MATE_TTS_ENABLED=0` 禁用语音，或用 `LOCAL_AI_MATE_TTS_MODEL_PATH` 指定另一份本地 `.onnx`。合成固定使用 CPU，WAV 只在内存和当前浏览器页面中存在，不写入数据库或临时文件。

本地转写首选 SenseVoiceSmall，使用独立 CPU 运行时 `runtime/sensevoice/.venv` 和模型目录 `models/stt/sensevoice-small`；worker 不开放端口，失败时自动回退 faster-whisper base（CPU int8）。可通过 `LOCAL_AI_MATE_STT_ENGINE=faster-whisper` 直接选择旧引擎，或用 `LOCAL_AI_MATE_STT_ENABLED=0` 禁用。浏览器单轮录音和 `/api/v1/stt/transcribe` 请求都只在内存中存在，成功、失败或取消后立即释放，不写入数据库、日志或备份。应用不会退回浏览器云端语音识别。

Ollama 默认使用 `qwen3:4b-instruct`，关闭 Qwen3 的思考输出并限制为 128 个生成 token；连接会在后端进程内复用，模型默认保温 10 分钟，冷启动超时为 60 秒。可以通过 `LOCAL_AI_MATE_OLLAMA_MODEL`、`LOCAL_AI_MATE_OLLAMA_TIMEOUT_SECONDS`、`LOCAL_AI_MATE_OLLAMA_NUM_PREDICT`、`LOCAL_AI_MATE_OLLAMA_NUM_CTX` 和 `LOCAL_AI_MATE_OLLAMA_KEEP_ALIVE` 调整，但增大上下文或输出会提高延迟和内存占用。

Piper 运行时采用 GPL-3.0-or-later；当前华妍模型卡把原始数据集许可标为 `Unknown`，公开分发或商用前必须另行确认授权。

## Kokoro 音色试听（v1.8.0）

Kokoro 中文模型已安装在独立环境 `runtime/kokoro/.venv`，模型快照位于 `models/tts/kokoro/Kokoro-82M-v1.1-zh`。四个候选女声音频位于 `auditions/kokoro-v1.8.0/K1.wav` 至 `K4.wav`，对应关系和校验数据见同目录 `manifest.json`。当前实时接口仍使用 Piper；选定候选音色前不会切换。

离线重新生成：

```powershell
$env:HF_HUB_OFFLINE = '1'
Set-Location D:\A
.\runtime\kokoro\.venv\Scripts\python.exe tools\kokoro_audition.py --model-dir models\tts\kokoro\Kokoro-82M-v1.1-zh --output-dir auditions\kokoro-v1.8.0 --device cpu
```

## 露娜零样本音色试听（v1.9.0）

已在独立环境 `runtime/gpt-sovits/.venv` 安装 GPT-SoVITS V5 Turbo，并从用户提供的语音合集自动筛选 5.24 秒参考片段。两段试听位于 `auditions/gpt-sovits-v1.9.0/clone-1.wav` 和 `clone-2.wav`，完整参数、时长与哈希见同目录 `manifest.json`。当前实时接口仍使用 Piper；用户确认音色前不会切换。

离线重新生成：

```powershell
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
Set-Location D:\A
.\runtime\gpt-sovits\.venv\Scripts\python.exe tools\gpt_sovits_audition.py `
  --reference auditions\gpt-sovits-v1.9.0\reference.wav `
  --reference-metadata auditions\gpt-sovits-v1.9.0\reference.json `
  --output-dir auditions\gpt-sovits-v1.9.0 `
  --repo-dir runtime\gpt-sovits\repo `
  --model-dir models\tts\gpt-sovits
```

模型来源、固定版本和本机兼容调整见 `models/tts/gpt-sovits/SOURCE.md`；验证结果见 `quality/luna-v1.9.0-gpt-sovits-zero-shot-verification.md`。参考音色及角色素材仅限本机私人试听，公开发布或商用前必须另行确认授权。

## 露娜「自然克制」调音试听（v1.9.1）

用户选择 C1 风格后，v1.9.1 在同一参考音和模型上把语速调整为 `0.96`、句间静音调整为 `0.22` 秒，并降低采样随机性。三段试听位于 `auditions/gpt-sovits-v1.9.1/cool-1.wav` 至 `cool-3.wav`。该音色已经用户确认，并在 v2.0.0 中接入实时接口。

离线重新生成：

```powershell
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
Set-Location D:\A
.\runtime\gpt-sovits\.venv\Scripts\python.exe tools\gpt_sovits_audition.py `
  --profile v1.9.1 `
  --reference auditions\gpt-sovits-v1.9.0\reference.wav `
  --reference-metadata auditions\gpt-sovits-v1.9.0\reference.json `
  --output-dir auditions\gpt-sovits-v1.9.1 `
  --repo-dir runtime\gpt-sovits\repo `
  --model-dir models\tts\gpt-sovits
```

设计、实施计划和验证结果分别见：

- `docs/superpowers/specs/2026-10-08-gpt-sovits-cool-restraint-tuning-design-v1.9.1.md`
- `docs/superpowers/plans/2026-10-08-gpt-sovits-cool-restraint-tuning-v1.9.1.md`
- `quality/luna-v1.9.1-gpt-sovits-cool-restraint-verification.md`

## 实时流式语音（v2.0.0）

`POST /api/v1/tts/stream` 使用 NDJSON 逐段返回独立 WAV 音频。前端收到第一段后立即播放，并按序预取后续片段，不再等待整段语音生成完成。常用回复缓存在内存中，不写入磁盘。

后端使用长期驻留的 GPT-SoVITS 工作进程，按 `CUDA → CPU GPT-SoVITS → Piper` 顺序自动回退。CUDA 和 CPU 使用独立超时：CUDA 以快速响应为目标，CPU 允许较长的首次推理时间；一旦已经输出首段，本轮不会切换音色。设计和实施计划见：

- `docs/superpowers/specs/2026-10-08-gpt-sovits-realtime-streaming-design-v2.0.0.md`
- `docs/superpowers/plans/2026-10-08-gpt-sovits-realtime-streaming-v2.0.0.md`

本机 RTX 3050 4GB 已通过混合 CUDA 验收：文本 BERT 使用 CPU，语义生成与声码器使用 CUDA。9 轮基准峰值显存 2807 MiB，预热后首段约 0.73–1.09 秒。详细结果见 `quality/luna-v2.0.0-realtime-streaming-verification.md`。

### 语音中断自动恢复（v2.0.1）

当 GPT-SoVITS 在已经输出片段后发生 CUDA/工作进程异常，后端会先完整生成一份 CPU 替代音频，必要时再回退 Piper；替代内容准备好后发送 `reset` 事件，前端清理旧音频队列并从替代片段重新播放，不混接两种音色。成功恢复会清除健康状态中的旧错误；只有整条恢复链失败才显示“语音生成中断，文字回复不受影响”。验证记录见 `quality/luna-v2.0.1-failure-recovery-verification.md`。

然后启动前端：

```powershell
$env:VITE_SESSION_TOKEN = 'replace-with-the-same-local-token'
Set-Location D:\A\frontend
npm run dev
```

打开 Vite 显示的本机地址。开发服务器仅把同源 `/api` 请求转发到回环后端，因此浏览器不会直接跨域访问端口 8765。令牌仅用于浏览器到回环后端的写入请求，绝不能替换为云端 API 密钥或提交到版本库。

对话页使用本地打包的 `frontend/src/assets/luna-background.png`。点击中央光球后才请求麦克风权限；允许后显示“正在聆听”，正常音量说话后显示“正在听你说”，停顿约 0.9 秒自动提交。点击方形停止按钮、切换页面或关闭页面会释放麦克风。左上历史按钮显示透明纯文字历史，`Esc` 可关闭；开启系统“减少动态效果”时背景漂移和光球脉冲会停止。

## 验证

后端测试：

```powershell
Set-Location D:\A\backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

前端构建：

```powershell
Set-Location D:\A\frontend
npm run build
```

质量与隐私合约位于 [quality](quality/README.md)。
