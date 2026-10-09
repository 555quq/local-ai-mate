# 露娜 v2.1.0 本地模型升级验证

验证日期：2026-10-09（Windows，本机 RTX 3050 Laptop 4GB）

## 已部署版本

- 文字：Ollama `qwen3:4b-instruct`，模型清单确认存在，loopback `127.0.0.1:11434`。
- 语音识别：FunASR `1.4.16` + SenseVoiceSmall，独立 CPU venv；PyTorch `2.14.1+cpu`、torchaudio `2.11.0+cpu`、PyAV `15.1.0`。
- 语音合成：GPT-SoVITS v1.9.1 CUDA 主链，CPU GPT-SoVITS/Piper 回退与 v2.0.1 中断恢复保持启用。
- 兼容回退：原 faster-whisper base 仍由后端保留，SenseVoice worker 失败时自动使用。

## 自动化回归

- 后端 pytest：`81 passed`。
- 前端 Vitest：`5 files / 31 tests passed`。
- Vite production build：通过，43 modules transformed。

## 真实回环 smoke

| 检查 | 结果 |
| --- | --- |
| `/api/v1/health` | database/model/tts/stt 均 ready；model=`qwen3:4b-instruct`，stt=`sensevoice`/CPU，tts=`gpt-sovits`/CUDA |
| 本地消息 SSE | `source=ollama-local`，收到 `token` 与 `completed`，Qwen3-4B 中文回复成功 |
| `/api/v1/stt/transcribe` | SenseVoiceSmall 中文样例返回“开放时间早上9点至下午5点。”，`duration_ms=5616`，HTTP 200 |
| `/api/v1/tts/stream` | HTTP 200，`started → fragment → completed`，GPT-SoVITS CUDA 生成成功，`last_error=null` |

## 延迟与资源观察

- SenseVoice worker 首次冷启动约 36.6 秒；已改为后端启动后台预热，预热完成后健康状态显示 `ready`，首个实际转写约 1.34 秒。
- SenseVoice 预热后的同一中文样例约 0.96–1.34 秒。
- Qwen3-4B 热请求约 1.13 秒（短中文回复，`num_predict=128`，`num_ctx=2048`）；Ollama 冷启动超时提高到 60 秒以覆盖 GPT-SoVITS 同时预热场景。
- GPT-SoVITS 继续使用已验证的 RTX 3050 混合 CUDA 配置；本次升级未改变其显存策略。抽样查看 GPU 空闲显存占用约 336 MiB，语音请求完成后健康状态无错误。

## 回退与隐私确认

- SenseVoice worker 运行时、模型缺失或协议异常会降级到 faster-whisper，健康响应只返回脱敏状态和引擎名，不返回路径、命令行、音频或转写正文。
- 音频通过父子进程内存管道传递；接口仍限制 8 MiB、15 秒，未新增网络端口或云端出站。
- Ollama 仅允许 HTTP loopback；模型请求失败仍保留确定性本地回复。

## 浏览器无声修复（v2.1.1 hotfix）

实测后端 WAV 正常，但浏览器在网络请求完成后直接调用 `HTMLAudioElement.play()` 会触发自动播放策略。前端现已在发送、开始语音和手动朗读等用户手势中播放一个有效的静音 WAV 进行媒体解锁，再播放 GPT-SoVITS 片段；无法解锁时仍显示可操作的“浏览器阻止了自动播放”提示。前端回归现为 32 tests passed。
