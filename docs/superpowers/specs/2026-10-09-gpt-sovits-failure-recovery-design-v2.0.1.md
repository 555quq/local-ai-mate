# GPT-SoVITS 语音中断恢复设计 v2.0.1

## 目标

在保留 CUDA 首段低延迟的前提下，消除一次性 GPT‑SoVITS 推理异常直接暴露为“语音生成中断”的情况。系统按请求边界恢复，不把不同设备的音频片段无提示地拼接在一起。

## 方案

1. CUDA 在首段前失败时，沿用现有 `CUDA → CPU GPT‑SoVITS → Piper` 回退。
2. CUDA 已输出片段后失败时，启动一次从头开始的 CPU GPT‑SoVITS 重试。后端在 NDJSON 中发送 `reset` 事件；前端清空当前播放队列和正在播放的片段，再从 CPU 重试的第 0 段开始播放。这样不会把 CUDA 尾段、CPU 开头和 Piper 混成一条连续音频。
3. CPU 重试失败时，从头开始使用 Piper；Piper 也失败才返回 `TTS_STREAM_INTERRUPTED`。
4. 任一完整恢复成功后清除 `last_error`；健康状态不保留已经恢复的旧 CUDA 错误。
5. 所有恢复阶段只记录脱敏的设备、阶段和错误代码，不记录文本、参考音频、路径或音频内容。

## 协议

新增事件：

```json
{"event":"reset","request_id":"...","engine":"gpt-sovits","device":"cpu","reason":"primary_failed"}
```

`reset` 只能出现在至少一个旧片段已经发出后，随后片段索引从 0 重新开始。客户端收到后必须停止当前 `Audio`、撤销当前与排队的 Object URL，并继续消费同一 HTTP 流；它不能把 reset 当作请求失败。

## 边界

- 每个请求最多一次恢复链，避免 CUDA/CPU/Piper 循环。
- 播放器主动停止、替换消息或 AbortController 取消不显示错误提示。
- 仍然保留 4 MiB 单片段、32 MiB 总流和 500 字符限制。
- 恢复期间文字回复不回滚、不重复提交。

## 验收

- CUDA 首段前失败：CPU 或 Piper 正常完成且无 reset。
- CUDA 首段后失败：出现一次 reset，客户端从 0 顺序播放恢复音频并收到 completed。
- CUDA 与 CPU 都失败：Piper 从头恢复；Piper 失败才显示中断。
- 成功恢复后 health 的 `last_error` 为 null。
- 现有回归与新增恢复测试共 78 项后端、31 项前端测试，生产构建继续通过。
