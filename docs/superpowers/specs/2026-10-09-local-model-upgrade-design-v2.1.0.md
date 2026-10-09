# 本地模型升级设计 v2.1.0

## 目标

在 RTX 3050 Laptop 4GB、Windows 本地优先约束下，提升露娜的中文对话质量与语音输入识别质量，同时保留已经验证稳定的 GPT-SoVITS 露娜音色和语音中断恢复链。

## 选定方案 A

### 文字对话

- Ollama 模型从 `qwen3:1.7b` 升级为 `qwen3:4b-instruct`（Q4 量化）。
- 保持现有 loopback-only Ollama 适配器、关闭思考展示、中文短回复和本地回退逻辑。
- 因 4GB 显存需要与 GPT-SoVITS 共存，模型不强制占用 CUDA；先以 Ollama 默认的 CPU/混合调度运行并实测延迟和显存。

### 语音识别

- 采用独立 `SenseVoiceSmall` 本地运行环境，避免 FunASR/PyTorch 依赖污染后端和 GPT-SoVITS 环境。
- 后端通过受控的本地 SenseVoice worker 调用模型；音频仍只在内存和进程管道中传递，不写入数据库、日志或持久临时文件。
- worker 仅接受 `transcribe` 命令，限制单次输入 8 MiB、15 秒；返回清理后的中文文本、语言和时长。
- worker 启动失败、模型缺失或推理失败时，后端自动回退到现有 faster-whisper base，不影响文字聊天。
- 后端启动时在后台预热 worker；健康状态在预热期间为 `configured`，完成后为 `ready`，避免用户首次按下语音按钮时承担模型导入延迟。

### 语音合成

- GPT-SoVITS v1.9.1 CUDA 继续作为主音色引擎。
- CPU GPT-SoVITS、Piper 和 v2.0.1 reset 恢复机制保持不变。

## 安全与隐私

- SenseVoice worker 绑定本机进程管道，不开放新局域网端口。
- 健康状态只暴露 `enabled/status/engine/device/fallback` 等脱敏字段，不返回路径、音频、转写文本或命令行。
- 模型下载只写入项目模型目录和运行时目录；不使用云端语音识别 API。
- 用户提供的参考音色和第三方语音素材仅限有授权的本机私人使用。

## 验收标准

1. `qwen3:4b-instruct` 在 Ollama 中存在，后端健康状态显示该模型，消息请求仍可本地完成。
2. SenseVoice worker 能加载本地模型，真实 WAV/WebM 请求返回中文转写；worker 不可用时 faster-whisper 回退通过。
3. 现有 TTS、对话、隐私和前端测试全部通过。
4. 真实回环健康检查、聊天消息、STT 请求和 TTS 流式接口均为成功状态。
5. 记录升级后的首字延迟、转写延迟和显存/内存观察值；若 4GB 显存发生竞争，优先保证 GPT-SoVITS 音色与播放稳定性。

## 本机验证基线

- SenseVoiceSmall CPU worker 冷启动约 36.6 秒，后台预热完成后中文样例转写约 1 秒。
- Qwen3-4B 热请求约 1 秒；Ollama 冷启动超时设置为 60 秒，以覆盖首次加载和 GPT‑SoVITS 同时预热。
