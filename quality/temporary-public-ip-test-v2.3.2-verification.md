# 临时公网 IP 测试验收记录 v2.3.2

- 状态：已部署，72 小时测试窗口运行中
- 公网入口：`https://49.234.57.214:8443`
- 启动时间（UTC）：`2026-10-10T09:52:19Z`
- 自动关闭时间（UTC）：`2026-10-13T09:52:19Z`
- 自动关闭时间（Asia/Shanghai）：`2026-10-13 17:52:19 CST`
- 服务器备份：`/root/local-ai-mate-backup/20261010-093454-temp-ip-v2.3.2`
- 本地访问资料：`runtime/deployment/temp-ip-v2.3.2-secrets.psd1`（Git 忽略、仅当前用户与 SYSTEM 可读）

## 已验证结果

1. 服务器为 Ubuntu 24.04，2 核 2 GB；根盘剩余约 22 GB，时间同步正常。
2. Windows 后端 `127.0.0.1:8765` 健康，SSH 反向隧道仅在服务器 `127.0.0.1:18765` 监听。
3. Certbot 5.8.0 使用 `shortlived` profile 先完成 staging 签发，再完成正式签发。
4. 正式证书颁发者为 Let's Encrypt YE2，SAN 为 `IP Address:49.234.57.214`，部署时剩余有效期 159 小时。
5. 独立 Nginx 仅监听 `0.0.0.0:8443`；长期站点 Nginx 与 80 端口配置未被替换。
6. 腾讯云轻量服务器防火墙新增 TCP 8443 规则，备注为 `Luna temporary HTTPS v2.3.2`。
7. UFW 新增且记录了唯一规则 `luna-temp-edge-v2.3.2`；自动清理只会删除该规则。
8. 未认证公网请求返回 401；TLS 校验结果为 0；安全头包含 HSTS、CSP、nosniff、DENY frame、no-referrer 与 microphone self。
9. 服务器回环认证验收：页面 200、健康接口 200、恶意跨站 Origin 403。
10. 本地 TTS 实测返回 200、`audio/wav` 和有效 RIFF/WAVE 文件（149,036 字节）；健康接口报告模型、STT、GPT-SoVITS 均 ready。
11. `luna-temp-edge.service`、`luna-temp-edge-expiry.timer`、`luna-temp-edge-retry.timer` 均 active/enabled；截止守卫在未到期时返回 systemd condition skip（1）。
12. 4 小时初始 failsafe 只在 72 小时绝对 timer 验证后停用；旧 timer 或 retry cadence 无法越过当前 `expires-at`。
13. 临时 root 部署密钥已从服务器授权列表和本机删除，服务器暂存目录已清理。

## 自动化验证

- 后端：`116 passed`，仅 1 个既有 Starlette 弃用警告。
- 临时边缘定向测试：`25 passed`。
- 前端：`38 passed`。
- 前端生产构建：成功。
- Bash 语法检查：`cleanup.sh`、`check-expiry.sh`、`cleanup-due.sh` 通过。
- `git diff --check`：通过。

## 到期行为

到达 `2026-10-13T09:52:19Z` 后，服务器会自动停止并禁用独立边缘服务、连续验证 8443 已关闭、删除本项目拥有的 UFW 规则、临时 Basic Auth、专用证书 lineage、临时配置与 systemd 单元。清理失败时每分钟重试，并跨重启继续。

腾讯云控制台中的 8443 规则不受服务器内 systemd 管理；到期后即使该云规则暂时保留，因为服务器已无 8443 listener，也不能访问应用。Task 7 仍需在到期后删除该云规则、轮换测试期间嵌入前端的 FastAPI 会话令牌，并提交最终关闭记录。

## 未改动的既有服务

OpenClaw、宝塔面板、SSH、现有 80/443 规则和域名站点均保留。控制台原有的 35659、37833、8888 等公网规则不属于本次部署，未修改。
