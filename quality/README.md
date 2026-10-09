# 本地 AI 女友 MVP：质量与运维说明

本目录是 v1.7.0 MVP 的独立质量交付物，并记录 v2.0.0/v2.0.1 语音链路验证。它不包含真实聊天、密钥、音频或云端供应商配置。

版本验证记录包括 `luna-v1.7.0-verification.md`、`luna-v1.7.1-hotfix-verification.md`、`luna-v1.8.0-kokoro-audition-verification.md`、`luna-v1.9.0-gpt-sovits-zero-shot-verification.md`、`luna-v1.9.1-gpt-sovits-cool-restraint-verification.md`、`luna-v2.0.0-realtime-streaming-verification.md`、`luna-v2.0.1-failure-recovery-verification.md` 和 `luna-v2.1.0-model-upgrade-verification.md`。v2.0.0 的机器可读 CUDA 基准位于 `luna-v2.0.0-gpu-benchmark.json`。

## 范围

- `tests/`：面向 FastAPI 回环服务的 API 合约与隐私边界测试。
- `ACCEPTANCE_CHECKLIST.md`：发布前由操作者完成的人工验收记录。
- `PRIVACY_ASSERTIONS.md`：可验证的本地优先、Ollama 降级、隐私开关、导出与清空断言。

测试使用假供应商或测试配置；不要将真实云端 API 密钥、真实对话、生产数据目录传给测试进程。

## 本机启动前检查

1. 使用单独的测试数据目录，例如 `D:\A\.runtime-test`，不要复用日常数据目录。
2. 将后端绑定到 `127.0.0.1` 的随机端口；不得监听 `0.0.0.0` 或局域网地址。
3. 以测试模型或本地假模型启动 FastAPI 后端，并启用设计方案允许的开发测试假供应商配置。该配置仅允许回环假供应商，不能连接真实供应商。
4. 从启动日志或状态页取得本次短期页面会话令牌。令牌只放在当前终端环境变量中，不能写入文件或提交到版本控制。

PowerShell 示例（变量名可按实际启动器调整）：

```powershell
$env:AI_COMPANION_BASE_URL = 'http://127.0.0.1:8765'
$env:AI_COMPANION_SESSION_TOKEN = '<本次启动的短期令牌>'
$env:AI_COMPANION_TEST_PROVIDER_ID = 'fake-provider'
python -m pytest .\quality\tests -q
```

未设置 `AI_COMPANION_BASE_URL` 或 `AI_COMPANION_SESSION_TOKEN` 时，测试会整体跳过，避免误连到未知服务。该跳过不是通过；发布前必须在隔离环境中实际运行。

## 安装测试依赖

建议在项目虚拟环境中安装：

```powershell
python -m pip install -r .\quality\requirements-test.txt
```

## 运行分组

```powershell
# 所有 API 合约（需已启动测试后端）
python -m pytest .\quality\tests -m api -q

# 仅隐私/同意令牌边界
python -m pytest .\quality\tests -m privacy -q
```

## 结果处理

- `passed`：该契约在本次隔离运行中满足。
- `skipped`：环境未配置或实现尚未提供可测入口；不得作为发布依据。
- `failed`：阻止发布。保留 pytest 的无正文输出、版本、模型档案和时间；不要附加 HTTP 请求体、响应正文、会话令牌或真实数据。

## 本地停止与清理

先停止启动器或后端进程，再删除专用测试数据目录。测试运行完成后，应确认服务已清理短期会话令牌、临时摘要和音频缓冲；不要以删除测试目录替代此项验证。不要对用户的真实数据目录执行递归删除。
