# 隐私与安全断言（v1.6.0 MVP）

以下断言是发布门槛，不是目标性描述。每项应以隔离测试数据、假供应商和无正文测试记录验证。

| ID | 必须成立的事实 | 自动验证 | 人工验证 |
|---|---|---|---|
| P-01 | 初始配置中云端增强为关闭。 | `test_default_local_flow_creates_no_outbound_observation` | 首次设置与设置页截图检查。 |
| P-02 | 本地路由不能要求或消费云端同意令牌。 | `test_local_message_requires_no_cloud_consent_and_reports_local_route` | 断网发送消息。 |
| P-03 | 未绑定到本轮 `request_id` 的云端路由必须被拒绝，且没有上传。 | `test_cloud_route_is_denied_without_a_bound_consent` | 检查 CloudAudit 只记录未发送结果。 |
| P-04 | `仅当前消息` 的上传预览只列 `current_message`。 | `test_cloud_consent_is_minimal_and_single_use` | 假供应商捕获字段名，确认没有摘要、记忆、音频、历史或密钥。 |
| P-05 | `带上下文增强` 的预览只列 `current_message` 与 `ephemeral_summary`。 | `test_summary_mode_preview_explicitly_lists_only_summary_and_current_message` | 预览中供应商、模型、字符数和 5 分钟有效期可见。 |
| P-06 | API 错误不回显用户正文、文件路径、SQL、堆栈或认证材料。 | `test_error_payload_does_not_reflect_untrusted_input` | 检查诊断导出和运行日志。 |
| P-07 | 所有应用 API 仅接受回环请求、预期 Origin 和短期会话令牌。 | `test_rejects_missing_session_token` | 使用另一台机器或非回环地址确认无法连接。 |
| P-08 | 假供应商和检查接口只在 `APP_TEST_MODE=1` 允许，生产配置不可创建回环供应商。 | 启动配置测试（实施时补充） | 审核发行配置与端点校验。 |
| P-09 | 可选 Ollama 仅连接 `127.0.0.1`；不可用或降级时不得改发云端。 | `test_optional_ollama_fallback_remains_local_and_never_calls_cloud` | 断开 Ollama 后观察状态和网络监控。 |
| P-10 | `memory_candidates_paused` 与 `retrieval_paused` 是可持久化的本地隐私控制，并分别阻止候选生成和记忆检索。 | `test_persistent_privacy_toggles_control_candidates_and_retrieval` | 重启后检查设置页和行为。 |
| P-11 | 隐私导出不含会话令牌、API 密钥、授权头、密码或秘密字段。 | `test_export_uses_normal_envelope_and_contains_no_credentials` | 在隔离目录检查导出文件的字段清单。 |
| P-12 | 清空本地数据必须同时收到 `confirmed: true` 和确认短语；成功后删除本地记录并重置隐私控制。 | `test_clear_requires_explicit_confirmation_and_removes_local_records` | 在 UI 核对双重确认和完成提示。 |

判定规则：任一 P-01 至 P-12 失败即为 `not-releasable`。测试跳过、缺少假供应商捕获或无法完成断网检查同样不能视为通过。
