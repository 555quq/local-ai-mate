# 露娜 GPT-SoVITS 实时分片语音设计 v2.0.0

**状态：** 已确认，按 F2 直接实施  
**版本：** v2.0.0  
**日期：** 2026-10-08  
**基线：** v1.9.1 `natural-restraint-c1`

## 1. 目标

将用户已接受的 v1.9.1 GPT-SoVITS 音色作为前端默认朗读音色，并通过 GPU 探测、常驻模型和音频分片把“听到第一句”的等待显著缩短。RTX 3050 4 GB 无法稳定运行时自动使用 CPU GPT-SoVITS；两者在首片段前均不可用时回退到 Piper。

暖机后的目标，而非硬性保证：

- GPU：提交语音请求后 2–5 秒收到首个可播放片段。
- CPU：提交语音请求后 6–10 秒收到首个可播放片段。
- 文字回复不等待语音，仍按现有流式聊天立即显示。

实际延迟必须由本机基准记录，不以预计值冒充实测结果。

## 2. 已确认方案 F2

- 为 CUDA 单独创建 `runtime/gpt-sovits/.venv-cuda`，不修改已经可用的 CPU 环境。
- 使用仓库 Windows Python 3.12 / CUDA 12.8 指引中的 `torch==2.7.1+cu128` 与 `torchaudio==2.7.1+cu128`。
- CUDA 模式使用 FP16，关闭 Flash Attention 与 CUDA Graph，限制单并发，降低 4 GB 显存风险。
- CUDA 探测失败、初始化超时或首片段前 OOM 时，自动启动现有 CPU worker 并重试当前文本一次。
- CPU worker 在首片段前失败时，使用 Piper 生成单个完整片段。
- 已播放 GPT-SoVITS 片段后发生错误时不切换音色，只终止本次播放并提示重试。

## 3. 进程架构

GPT-SoVITS 不导入后端虚拟环境。后端通过无网络端口的标准输入/输出协议管理独立常驻 worker：

1. `tools/gpt_sovits_worker.py` 在指定解释器中加载固定模型、参考音和 C1 参数。
2. worker 启动后输出 `ready`；后端健康状态从 `warming` 转为 `ready`。
3. 后端发送一条 `synthesize` JSON 命令，worker 使用 `return_fragment=True`、`streaming_mode=True` 顺序产生音频片段。
4. worker 将每个片段包装为独立、可解码的 WAV，再用一行 NDJSON 返回。
5. 模型库的普通输出和进度信息统一重定向到标准错误，标准输出只承载协议。

进程协议命令：`ping`、`synthesize`、`cancel`、`shutdown`。事件：`ready`、`fragment`、`completed`、`cancelled`、`error`。每条消息含协议版本和请求 ID；未知版本、超长行、无效 Base64 或乱序事件必须拒绝。

后端关闭时向 worker 发送 `shutdown`，短暂等待后再终止子进程，不能遗留常驻 Python 进程。

## 4. 后端服务与回退状态机

新增 `GPTSoVITSWorkerService`，负责：

- 启动、健康探测、单请求互斥、读取分片、取消和关闭 worker。
- 在后台异步预热，不能阻塞 FastAPI 启动或文字聊天。
- GPU worker 失败后熔断本次进程周期，不反复触发显存 OOM；随后使用 CPU。
- CPU worker 失败后保留 Piper 可用性。

新增 `FallbackStreamingTTSService`，优先级固定为：

```text
CUDA GPT-SoVITS → CPU GPT-SoVITS → Piper
```

同一请求只能在首个片段发送前向下回退。一旦发送首片段，后续错误直接结束流。

健康信息只暴露：启用状态、`warming/ready/degraded/unavailable`、当前主引擎、设备 `cuda/cpu`、Piper 回退是否可用、最近一次失败的稳定错误代码；不得暴露本地路径、原文、参考音文本或进程命令。

## 5. HTTP 流协议

保留现有 `POST /api/v1/tts/synthesize` 作为兼容和 Piper 单 WAV 路径。新增：

```text
POST /api/v1/tts/stream
Content-Type: application/json
Accept: application/x-ndjson
```

请求继续使用现有 `TTSSynthesizeRequest`、会话令牌、Origin 检查、500 字限制和速率限制。响应为 `application/x-ndjson`，每行一个事件：

- `started`：实际引擎、设备和请求 ID。
- `fragment`：序号、`audio/wav` 和 Base64 音频；独立的 `completed` 事件标记片段流结束，避免为判断末片段而延迟发送首段音频。
- `completed`：片段数和总生成耗时。
- `error`：稳定错误码、可重试标志；仅允许出现在响应已开始后。

响应统一 `Cache-Control: no-store`。单片段解码后不得超过 4 MiB，完整请求的累计音频不得超过 32 MiB。浏览器断开连接时，后端发送 `cancel`；worker 至少在片段边界停止当前请求并释放中间数组。

## 6. 前端播放流水线

`api.streamSpeech` 使用 Fetch `ReadableStream` 增量解析 NDJSON，并把每个 Base64 WAV 转为 Blob。`useSpeechPlayback` 改为一个有序片段队列：

1. 收到第一片段立即创建 Audio 并播放。
2. 播放期间继续接收后续片段。
3. 当前片段结束后无缝切换到队列下一片段。
4. 用户停止、切换消息、关闭页面或开始录音时，中止 Fetch、停止 Audio、释放全部 Object URL。

不得同时播放两条消息。流开始前失败时，后端已负责回退；流中途失败时前端显示“语音生成中断，文字回复不受影响”，不自行再次请求 Piper。

自动播放被浏览器阻止时保留已有提示，队列保持可由用户再次点击播放。页面继续用当前字幕和文字消息，不因语音等待而延迟。

## 7. 内存缓存与隐私

- 只缓存最近 8 条完成的语音，累计上限 32 MiB，先按条数或总字节触发者淘汰最旧项。
- 缓存键为规范化文本、C1 配置版本和实际引擎的 SHA-256；缓存值只在内存中。
- 取消、失败和未完成的流不进入缓存。
- 后端退出即清空；不得写入数据库、临时目录、日志或浏览器持久存储。
- 日志只记录请求 ID、引擎、设备、片段数、耗时和错误码，不记录对话文本或音频 Base64。

## 8. 配置

新增环境变量，默认值均为本机安全值：

- `LOCAL_AI_MATE_TTS_PRIMARY=gpt-sovits`
- `LOCAL_AI_MATE_GSV_CUDA_PYTHON=runtime/gpt-sovits/.venv-cuda/Scripts/python.exe`
- `LOCAL_AI_MATE_GSV_CPU_PYTHON=runtime/gpt-sovits/.venv/Scripts/python.exe`
- `LOCAL_AI_MATE_GSV_WORKER=tools/gpt_sovits_worker.py`
- `LOCAL_AI_MATE_GSV_PROFILE=v1.9.1`
- `LOCAL_AI_MATE_GSV_WARMUP_TIMEOUT_SECONDS=90`
- `LOCAL_AI_MATE_GSV_FRAGMENT_TIMEOUT_SECONDS=30`
- `LOCAL_AI_MATE_GSV_CACHE_ITEMS=8`
- `LOCAL_AI_MATE_GSV_CACHE_BYTES=33554432`

所有路径启动时解析为绝对路径并验证位于项目目录中的预期运行时、工具、模型或试听目录。worker 不接受请求传入模型路径、参考路径或设备参数。

## 9. 安装与 GPU 判定

CUDA 环境的安装和基准是独立任务：

1. 从官方 PyTorch CUDA 12.8 索引安装固定 Torch/Torchaudio。
2. 安装 GPT-SoVITS 推理所需最小依赖，不安装 Flash Attention。
3. 先运行只加载模型的 GPU 探测，再合成三条 v1.9.1 短句。
4. 记录首片段延迟、总耗时、峰值显存和是否 OOM。
5. 只有连续三次成功、输出有效且模型加载后仍保留显存安全余量，健康状态才报告 CUDA ready。

若 GPU 不满足条件，保留环境和失败记录但默认使用 CPU；不得通过牺牲系统稳定性、关闭安全检查或让浏览器与模型争抢到系统无响应来强行启用。

## 10. 验证

- worker 协议：ready、分片顺序、取消、超时、异常退出、无效消息和优雅关闭。
- 后端：CUDA→CPU→Piper 首片段前回退；首片段后禁止换音色；并发返回 429；路径与隐私字段不泄露。
- HTTP：会话、Origin、长度、速率、NDJSON、no-store、断开取消和大小上限。
- 前端：首片段即播放、队列顺序、停止/替换、自动播放阻止、流中断、Object URL 释放。
- 真实基准：三条 v1.9.1 文本在实际选中设备上生成并播放；记录 TTFF、总耗时、峰值和哈希。
- 回归：后端全量测试、前端测试和构建；Ollama/STT/聊天/历史记录行为不变。

## 11. 非目标

- 不训练或微调模型。
- 不承诺 4 GB 显存一定可运行 CUDA。
- 不开放局域网或公网语音服务。
- 不在 v2.0.0 中实现情绪标签、音色切换或多角色。
- 不移除 Piper，也不删除 v1.9.0/v1.9.1 试听资产。
