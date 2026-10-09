# 本地 AI 伴侣混合私有上线设计 v2.3.0

日期：2026-10-09
状态：方案 A 已选择，待书面规格复核
适用项目：露娜本地 AI 伴侣

## 1. 目标

使用腾讯云轻量服务器提供稳定的公网 HTTPS 入口，同时让 Ollama、SenseVoiceSmall、GPT-SoVITS、Piper、聊天数据库和记忆数据继续运行并保存在当前 Windows 电脑上。上线后，用户可以通过 `https://luna.shipmaster.asia` 从浏览器或手机访问；本机电脑关机或隧道断开时，站点应明确显示本地 AI 服务离线，而不是把请求发送到第三方模型。

本版本是单用户私有上线，不开放注册，不支持陌生用户或多租户。

## 2. 已确认的服务器现状

2026-10-09 通过腾讯云 OrcaTerm 进行了只读盘点：

- Ubuntu Server 24.04 LTS，2 核 CPU、2GB 内存、50GB SSD、4Mbps 公网带宽。
- 可用内存约 1.1GB，已配置约 9.9GB Swap。
- 系统盘已使用约 25GB，剩余约 23GB。
- 现有 Nginx 由宝塔面板管理，虚拟主机位于 `/www/server/panel/vhost/nginx/`。
- 80 端口已有 Nginx 监听，`www.shipmaster.asia` 已有独立站点配置。
- OpenClaw 网关和宝塔面板正在运行；本次不得覆盖、重启替换或迁移这些应用。
- UFW 已启用，80/443 已放行；现有若干历史端口不属于本次范围，不擅自关闭。

结论：服务器资源足以运行 Nginx 静态站点和 FRP 服务端，但不承载 AI 模型、Node 开发服务器或项目数据库。

## 3. 最终架构

```text
用户浏览器
  │ HTTPS + Nginx Basic Auth
  ▼
luna.shipmaster.asia
  │
  ├─ /、静态资源 ──► 腾讯云 /www/wwwroot/luna.shipmaster.asia
  │
  └─ /api/* ──► Nginx ──► 127.0.0.1:18765
                                  │
                                  ▼
                         FRP TLS 加密隧道
                                  │
                                  ▼
                   Windows 本机 127.0.0.1:8765
                                  │
             FastAPI ── Ollama / SenseVoice / GPT-SoVITS / SQLite
```

腾讯云运行 `frps`，仅负责隧道接入；Windows 运行 `frpc`，主动向腾讯云建立连接。FRP 代理端口 `18765` 只绑定服务器回环地址，不允许公网直接访问。公网仅开放 80、443 和 FRP 控制端口 7000/TCP。

## 4. 域名与 HTTPS

- 新建 `luna.shipmaster.asia` 的 A 记录，指向当前轻量服务器公网 IPv4。
- 不修改根域名和 `www.shipmaster.asia` 的现有记录与站点。
- 新增宝塔 Nginx 虚拟主机文件，不修改其他虚拟主机文件。
- DNS 生效后，通过 ACME WebRoot 签发 `luna.shipmaster.asia` 单域名证书。
- 80 端口只保留 ACME 校验并重定向到 HTTPS；业务请求统一走 443。
- 证书必须具备自动续期和续期后 Nginx reload；reload 前先执行 `nginx -t`。

浏览器麦克风只在 HTTPS 页面上启用，因此没有有效证书时不得把上线标记为完成。

## 5. 访问认证

当前前端会把 `VITE_SESSION_TOKEN` 编译进静态 JavaScript，该令牌不能单独作为公网身份认证。公网入口增加 Nginx Basic Auth，形成两层保护：

1. Nginx Basic Auth 阻止未授权用户下载前端和访问 `/api`。
2. FastAPI `X-Session-Token` 与精确 Origin 校验继续阻止绕过网页的回环 API 请求。

部署时生成：

- 一个不使用默认名称的登录用户名。
- 一个至少 20 位的随机登录密码，只向用户显示一次。
- 一个独立的 FRP 随机令牌。
- 一个独立的 FastAPI 会话令牌。

Basic Auth 密码只以 bcrypt 哈希保存在服务器；FRP 配置文件权限为 root 可读；本机明文秘密保存在 Git 忽略目录并限制当前 Windows 用户读取。任何秘密均不得进入 Git、Nginx 访问日志或设计文档。

## 6. Nginx 行为

新增站点根目录 `/www/wwwroot/luna.shipmaster.asia`，仅放置 `frontend/dist` 构建产物。Nginx 配置必须满足：

- `/api/*` 反向代理到 `127.0.0.1:18765`，不改写 `/api/v1` 路径。
- SSE 和 NDJSON 流关闭代理缓冲，禁用响应缓存。
- 连接、读取和发送超时至少 180 秒，允许 GPT-SoVITS CPU 回退的冷启动。
- `client_max_body_size 9m`，覆盖现有 8MiB 语音上传限制但不无限放大。
- 转发原始 `Host`、客户端协议和客户端地址。
- 增加 `X-Content-Type-Options`、`Referrer-Policy` 和适合当前本地资源的 CSP。
- `Permissions-Policy` 只允许本站使用麦克风。
- SPA 未命中文件时回退 `index.html`，但 `/api` 永不回退到 HTML。
- 独立访问日志和错误日志，不记录请求正文。

所有变更在 reload 前运行 Nginx 语法测试。若测试失败，不加载新配置。

## 7. FRP 隧道

服务器端：

- 使用固定版本的官方 FRP 二进制，记录下载来源和 SHA-256。
- `frps` 通过 systemd 启动，监听 7000/TCP。
- 强制 TLS，启用令牌认证，关闭 Dashboard。
- `proxyBindAddr = "127.0.0.1"`，确保远端代理端口不会绑定公网地址。
- 只接受一个名为 `local-ai-mate-api` 的 TCP 代理，远端端口固定为 18765。

Windows 本机：

- `frpc` 将服务器 `127.0.0.1:18765` 映射到本机 `127.0.0.1:8765`。
- 本机 FastAPI 仍只监听回环地址，不能改为 `0.0.0.0`。
- `LOCAL_AI_MATE_ALLOWED_ORIGIN` 固定为 `https://luna.shipmaster.asia`。
- 使用 Windows 计划任务在用户登录后启动后端和 FRP，并在异常退出后重试。
- Ollama、SenseVoice 和 GPT-SoVITS 继续使用现有本机模型目录，不上传模型或参考音频。

腾讯云防火墙和 UFW 只新增 7000/TCP；18765 和 8765 均不得开放。

## 8. 前端构建与发布

- 使用生产环境 `VITE_API_BASE=/api/v1`。
- 使用独立生产 FastAPI 会话令牌构建，开发用 `.env.local` 不复用。
- 运行全部前端测试和 `npm run build` 后才上传 `dist`。
- 上传到新的时间戳发布目录，再以符号链接切换当前版本，避免半上传状态。
- 至少保留上一个静态版本，回滚时只切换符号链接并 reload Nginx。
- 腾讯云不安装或常驻运行 Vite/Node 开发服务器。

## 9. 故障与降级

- Windows 关机、休眠或断网：静态页面和登录仍可打开，`/api/v1/health` 返回网关错误；前端显示本地服务不可用。
- FRP 断线：systemd 和 Windows 计划任务负责自动重连；Nginx 不切换到第三方 API。
- Ollama 不可用：沿用本地确定性文字回退。
- GPT-SoVITS 不可用：沿用 CPU GPT-SoVITS → Piper 回退。
- DNS 或证书失败：保留现有站点，不将未加密 HTTP 当作成功上线。
- Nginx 新配置失败：恢复备份文件并保持原有 OpenClaw 与 `www.shipmaster.asia` 服务。

## 10. 隐私与安全边界

- 对话、记忆和音频会加密通过腾讯云转发，但只在 Windows 后端处理和存储。
- 腾讯云不保存聊天数据库、模型、参考音频或 TTS 结果。
- Nginx 和 FRP 日志不得记录 Authorization、会话令牌、请求正文或音频内容。
- 服务器配置备份不得包含明文登录密码。
- 上线前验证服务器的 18765、8765 不可从公网访问。
- 上线后验证未认证访问返回 401，错误密码不能访问静态文件或 API。
- 单用户 Basic Auth 不等同于多用户账户系统；不得宣称支持公开注册。

## 11. 验收标准

1. `https://luna.shipmaster.asia` 使用有效证书，HTTP 自动跳转 HTTPS。
2. 未登录访问站点和 `/api/v1/health` 均返回 401。
3. 正确登录后能加载沉浸式背景和关系设定。
4. 公网文字回复走本机 Ollama；关闭 Ollama 后出现本地回退而非云端调用。
5. 公网语音识别、GPT-SoVITS 流式播放和 Piper 回退各完成一次真实测试。
6. 本机断开 FRP 后，公网清晰显示离线；重连后无需重启 Nginx即可恢复。
7. 公网端口扫描只出现预期端口；18765 和 8765 不可访问。
8. 现有 `www.shipmaster.asia`、OpenClaw、宝塔面板在部署前后行为不变。
9. 服务器重启后 Nginx 与 frps 自动恢复；Windows 重新登录后后端与 frpc 自动恢复。
10. Git 中不存在任何部署密码、FRP 令牌、会话令牌、证书私钥或用户聊天数据。

## 12. 回滚

回滚顺序：

1. 从 Nginx 启用目录移除 `luna.shipmaster.asia` 新虚拟主机并通过 `nginx -t` 后 reload。
2. 停止并禁用 `frps`，删除只为本项目新增的 7000/TCP 防火墙规则。
3. 在 Windows 禁用本项目计划任务并停止 `frpc` 与生产 FastAPI 进程。
4. 删除 `luna.shipmaster.asia` DNS 记录。
5. 保留服务器发布目录和配置备份，直到确认不再需要恢复。

回滚不得触碰 `www.shipmaster.asia`、OpenClaw、宝塔面板、现有证书和现有防火墙规则。

## 13. 资源结论

新增 Nginx 虚拟主机、静态文件和 frps 对 2 核 2GB 服务器的资源影响很小。4Mbps 带宽适合单用户文字和短语音流，但不承诺多人并发。主要运行条件是 Windows 电脑保持开机、联网，并避免自动休眠。
