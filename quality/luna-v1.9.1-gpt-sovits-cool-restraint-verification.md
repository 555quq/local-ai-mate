# 露娜 v1.9.1 GPT-SoVITS「自然克制」调音验证

- 验证日期：2026-10-08
- 设计版本：v1.9.1
- 风格：C1 / `natural-restraint-c1`
- 状态：试听生成完成，等待人工听感选择；未接入实时语音

## 变更范围

v1.9.1 在 v1.9.0 零样本音色基础上，仅调整推理节奏与采样稳定性：

| 参数 | v1.9.0 | v1.9.1 |
|---|---:|---:|
| `speed_factor` | 1.00 | 0.96 |
| `fragment_interval` | 0.30 | 0.22 |
| `temperature` | 0.60 | 0.50 |
| `top_p` | 0.90 | 0.85 |

`top_k=5`、`repetition_penalty=1.35`、`sample_steps=4`、`seed=20261008` 和 `cut5` 均保持不变。参考音、提示文本、模型权重与 GPT-SoVITS commit 均未改变。

## 试听结果

| ID | 文件 | 文本 | 时长 | 推理耗时 | 峰值 | SHA-256 |
|---|---|---|---:|---:|---:|---|
| C1 | `cool-1.wav` | 你回来了。今天还顺利吗？ | 2.113 s | 19.428 s | 0.441925 | `821dc4d05cec7c30de498d9fbab27b8c3b0858f392141032baa445eb0056e977` |
| C2 | `cool-2.wav` | 累了就先休息。其他事情，明天再处理。 | 3.840 s | 22.105 s | 0.427643 | `1bfcef2ab9ef001dbffe00791052a8c3441c180654b8d7e831633625da369f1b` |
| C3 | `cool-3.wav` | 嗯，我在。你慢慢说。 | 2.153 s | 14.052 s | 0.548431 | `8c58e3e3ba41aca99f374a4c222f3024a5518e34ab73c6c70e0dcdca239bd576` |

三段文件均为 48 kHz、单声道、PCM16，可解码、非空且未发生满幅削波。文件 SHA-256 与 `manifest.json` 全部一致，输出目录中没有遗留临时 WAV。

## 离线与隔离验证

- GPT-SoVITS 仓库固定 commit：`e7cd61ec3dcc9a34089f50ed169e3103a16ad675`。
- 执行前已验证 BERT、HuBERT、GPT、V5 Turbo、vocoder、G2PW 与 FastText 文件均在本地。
- 生成命令设置 `HF_HUB_OFFLINE=1` 和 `TRANSFORMERS_OFFLINE=1`；成功运行日志没有模型下载。
- 新文件只写入 `auditions/gpt-sovits-v1.9.1/`，v1.9.0 参考与试听文件未覆盖。
- 实时 `/api/v1/tts/synthesize` 未修改，Piper 仍是当前语音引擎。

## 自动回归

- GPT-SoVITS 专项：12 项测试通过。
- 后端全量：49 项测试通过，耗时 6.79 秒。
- 前端：4 个测试文件、23 项测试通过。
- 前端生产构建：43 个模块构建成功。
- 前端：`http://127.0.0.1:5173/` 返回 HTTP 200。
- 后端健康检查：数据库 ready；Ollama `qwen3:1.7b` ready；Piper 与 Faster-Whisper configured；云端出站关闭。

## 人工验收门槛

用户需试听 C1–C3，并选择：接受当前 C1 风格、继续微调，或重新选择参考片段。只有明确接受后，才能单独设计后端 GPT-SoVITS 接入、性能策略和 Piper 回退；v1.9.1 不包含该接入。

## 复现入口

- 设计：`docs/superpowers/specs/2026-10-08-gpt-sovits-cool-restraint-tuning-design-v1.9.1.md`
- 计划：`docs/superpowers/plans/2026-10-08-gpt-sovits-cool-restraint-tuning-v1.9.1.md`
- 工具：`tools/gpt_sovits_audition.py`
- 清单：`auditions/gpt-sovits-v1.9.1/manifest.json`
