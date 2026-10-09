# 露娜 v2.0.1 语音中断自动恢复验证

日期：2026-10-09  
范围：GPT-SoVITS 流式 TTS 的 CUDA/CPU/Piper 自动恢复、NDJSON `reset` 协议、前端音频队列清理。

## 目标

- 首段生成前失败时，保持 CUDA → CPU → Piper 的既有回退顺序。
- 已经输出片段后失败时，不把两个引擎的 WAV 片段混在同一次播放中。
- 后端先生成完整替代流，再发送 `reset`，替代流从片段索引 0 重新开始。
- 成功恢复后清除健康状态中的旧错误；完整恢复链失败才报告 `TTS_STREAM_INTERRUPTED`。
- 文本回复和语音失败相互独立。

## 自动化结果

| 检查 | 结果 |
|---|---:|
| 后端全量 pytest | 78 passed，1 warning |
| 前端 Vitest | 31 passed |
| 前端 TypeScript/Vite build | passed |
| 后端恢复定向测试 | 21 passed，1 warning |

## 本机回环 smoke test

- 后端 `127.0.0.1:8765`、前端 `127.0.0.1:5173`、Ollama `127.0.0.1:11434` 均处于监听状态。
- `/api/v1/health` 返回 `tts.status=ready`、`device=cuda`、`fallback_ready=true`、`last_error=null`。
- 真实 `POST /api/v1/tts/stream` 返回 `started`、2 个 `fragment` 和 `completed`，HTTP 200；短文本命中已验证的内存缓存，未记录文本或音频内容。
- 强制中断场景由后端恢复单元测试模拟 CUDA 首段后失败，并验证 CPU/Piper 替代流、`reset` 和索引重启。

说明：唯一 warning 是测试依赖中 Starlette 对 `httpx` 的弃用提示，不影响本次行为验证。

## 覆盖的恢复场景

1. CUDA 在首段之后失败：保留已发出的 CUDA 首段，生成完整 CPU 替代流，发送 `reset`，替代片段索引重新从 0 开始。
2. CUDA 首段之后失败且 CPU 不可用：生成完整 Piper WAV，发送 `reset`，只缓存干净的 Piper 替代流。
3. 全部语音引擎失败：发送经过清理的 `TTS_STREAM_INTERRUPTED`，前端保留文字回复并显示语音提示。
4. 前端收到 `reset`：暂停并释放当前 `<audio>`，撤销旧 Blob URL，清空排队片段，继续播放替代流。
5. 正常完成或缓存命中：清除历史错误，不重复触发恢复。

## NDJSON 约定

恢复事件示例：

```json
{"event":"reset","request_id":"...","engine":"gpt-sovits","device":"cpu","reason":"primary_failed"}
```

`reset` 后的下一条 `fragment` 必须使用 `index: 0`；最终 `completed.fragment_count` 只统计替代流，不包含已丢弃的旧片段。

## 手工验收

启动本地后端和前端后，播放一条较长中文回复，确认：

- 正常情况下首段到达后立即播放，后续片段连续播放。
- 恢复时不会听到 CUDA 片段和 CPU/Piper 片段的重叠或交叉串音。
- 恢复成功后 `/api/v1/health` 的 `tts.last_error` 为空。
- 恢复链全部失败时，文字消息仍显示，语音区域提示“语音生成中断，文字回复不受影响”。

本文件不记录真实文本、音频、令牌、模型路径或原始工作进程日志。
