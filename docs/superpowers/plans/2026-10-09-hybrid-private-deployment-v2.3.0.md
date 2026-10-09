# 混合私有上线 v2.3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地 AI 伴侣以 `https://luna.shipmaster.asia` 单用户私有方式上线，由腾讯云提供认证、HTTPS 和静态页面，本机继续承担全部 AI 推理与数据存储。

**Architecture:** 腾讯云现有宝塔 Nginx 新增独立虚拟主机，把 `/api/*` 代理到服务器回环端口 `127.0.0.1:18765`；FRP v0.71.0 以 TLS 和令牌认证把该端口连接到 Windows 本机 `127.0.0.1:8765`。静态前端部署在腾讯云并由 Nginx Basic Auth 保护，FastAPI 保留会话令牌和精确 Origin 第二层校验。

**Tech Stack:** Ubuntu 24.04、宝塔 Nginx、ACME/Certbot WebRoot、FRP v0.71.0、systemd、Windows PowerShell/计划任务、FastAPI、Vite、pytest/Vitest。

**Spec:** `docs/superpowers/specs/2026-10-09-hybrid-private-deployment-design-v2.3.0.md`

## Global Constraints

- 不修改、替换或迁移现有 `www.shipmaster.asia`、OpenClaw、宝塔面板和既有证书。
- 腾讯云不安装 AI 模型、项目数据库或常驻 Node/Vite 开发服务器。
- FastAPI 和服务器代理端口只绑定回环地址；公网不得访问 18765 或 8765。
- 公网只新增 `luna.shipmaster.asia`、FRP 控制端口 7000/TCP 和该站点独立配置。
- 所有秘密在执行时随机生成，不进入 Git、日志、设计文档或部署包。
- 每次 Nginx reload 前必须通过现有 Nginx 二进制的 `-t` 检查；失败立即恢复备份。
- FRP 固定为官方 v0.71.0：Linux AMD64 SHA-256 `84f27e39f11169f7adcef8e8b70c9329de17747b1f14dad9fb95eef5682ea716`，Windows AMD64 SHA-256 `9e5062e3e5cf07e67144a3a4acf175ef6a2486f3605dd6cf288bae34ab39819f`。
- 未取得有效 HTTPS 证书、未验证认证边界或破坏现有站点时，不得宣告上线完成。

## Review Focus

- 宝塔使用自带 Nginx 路径而非发行版默认路径：预检必须解析实际 `nginx`、主配置和 reload 命令，不能假设 `/etc/nginx`。
- DNS 尚未生效或解析到错误 IP：证书步骤必须等待权威/公共解析一致，失败时保留 HTTP 验证站点但不开放业务。
- FRP 配置字段与 v0.71.0 不兼容：服务启动前分别执行 `frps verify -c` 与 `frpc verify -c`，测试必须锁定字段。
- Windows 登录任务重复启动进程：启动脚本必须持有 PID/互斥锁，重复运行不得出现第二个 FastAPI 或 frpc。
- Nginx Basic Auth 只保护静态页面但遗漏 `/api`：未认证的 `/` 与 `/api/v1/health` 都必须返回 401。

---

### Task 1: 可审计的部署模板与合约测试

**Files:**

- Create: `deploy/server/nginx-luna-http.conf.template`
- Create: `deploy/server/nginx-luna-https.conf.template`
- Create: `deploy/server/frps.toml.template`
- Create: `deploy/server/frps.service`
- Create: `deploy/windows/frpc.toml.template`
- Create: `deploy/windows/secrets.psd1.example`
- Create: `frontend/.env.production.example`
- Create: `backend/tests/test_deployment_contract.py`

**Interfaces:**

- Consumes: v2.3.0 设计中的域名、端口、认证、超时和回环绑定约束。
- Produces: 只含 `__PLACEHOLDER__` 的无秘密模板，供后续渲染与远端安装使用。

- [ ] **Step 1: 写失败的部署合约测试**

测试 `test_nginx_template_secures_static_and_api`、`test_frps_is_loopback_only_and_tls_forced`、`test_frpc_only_targets_local_backend`、`test_templates_contain_no_literal_secrets`，断言 Basic Auth 同时覆盖 `/` 和 `/api`、上游为 `127.0.0.1:18765`、关闭缓冲、超时 180 秒、上传 9m、FRP TLS/令牌占位符和无真实秘密。

- [ ] **Step 2: 运行测试并确认失败**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_deployment_contract.py -q`
Expected: FAIL，原因是模板尚不存在。

- [ ] **Step 3: 实现部署模板**

HTTP 模板只提供 ACME WebRoot 与受认证业务入口；HTTPS 模板包含 HTTP 重定向、SPA 回退、安全响应头、独立日志、Basic Auth、`proxy_buffering off` 和回环 API 上游。FRP 模板使用 v0.71.0 TOML 字段，`proxyBindAddr = "127.0.0.1"`、7000、18765、TLS 强制和令牌占位符；systemd 使用 `Restart=always`、`NoNewPrivileges=true`。

- [ ] **Step 4: 运行合约与后端全量测试**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add deploy frontend/.env.production.example backend/tests/test_deployment_contract.py
git commit -m "feat: add secure hybrid deployment templates"
```

### Task 2: Windows 生产进程生命周期与离线提示

**Files:**

- Create: `deploy/windows/start-production.ps1`
- Create: `deploy/windows/stop-production.ps1`
- Create: `deploy/windows/install-autostart.ps1`
- Create: `deploy/windows/render-config.ps1`
- Modify: `frontend/src/App.tsx`
- Create: `deploy/windows/tests/production-scripts.Tests.ps1`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**

- Consumes: `runtime/deployment/secrets.psd1`、下载后的 `runtime/frp/frpc.exe` 和 Task 1 的 `frpc.toml.template`。
- Produces: 幂等启动/停止命令、两个用户登录计划任务 `LocalAIMate-Backend` 与 `LocalAIMate-Tunnel`、公网适用的离线文案。

- [ ] **Step 1: 写失败测试**

Pester 测试锁定回环监听、生产 Origin、秘密文件存在性检查、重复启动保护和计划任务名称；Vitest 测试后端健康请求失败时显示“本地 AI 主机暂时离线”，不再提示公网用户检查 `127.0.0.1`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `Invoke-Pester deploy/windows/tests/production-scripts.Tests.ps1` 和 `npm test -- --run src/App.test.tsx`（工作目录 `frontend`）
Expected: FAIL，原因是脚本和新文案尚不存在。

- [ ] **Step 3: 实现脚本与文案**

`render-config.ps1` 从 PowerShell 数据文件生成含秘密的本机 FRP 配置；`start-production.ps1` 只启动一个绑定 `127.0.0.1:8765` 的 Uvicorn 和一个 frpc；安装脚本创建当前用户登录任务并设置失败重试；停止脚本只终止由 PID 文件识别的本项目进程。

- [ ] **Step 4: 运行前端、Pester 和构建验证**

Run: `Invoke-Pester deploy/windows/tests/production-scripts.Tests.ps1`；在 `frontend` 运行 `npm test -- --run` 与 `npm run build`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add deploy/windows frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat: add production lifecycle for hybrid deployment"
```

### Task 3: 生成秘密、固定 FRP 产物并构建发布包

**Files:**

- Create (ignored): `runtime/deployment/secrets.psd1`
- Create (ignored): `runtime/frp/frpc.exe`
- Create (ignored): `runtime/deployment/releases/<timestamp>/frontend.zip`
- Create (ignored): `runtime/deployment/releases/<timestamp>/server-bundle.tar.gz`
- Create: `quality/hybrid-deployment-v2.3.0-manifest.md`

**Interfaces:**

- Consumes: Task 1/2 模板和 FRP v0.71.0 官方发布文件。
- Produces: 登录用户名/一次显示的密码、FRP 令牌、FastAPI 令牌、已校验的 Windows/Linux 二进制和无明文秘密的发布清单。

- [ ] **Step 1: 生成四项互不复用的随机凭据**

使用系统加密随机数；Basic Auth 密码至少 20 位，FRP/FastAPI 令牌至少 32 字节。明文只写入 Git 忽略的 `runtime/deployment/secrets.psd1`，输出时只向用户显示 Basic Auth 用户名和密码一次。

- [ ] **Step 2: 下载并校验官方 FRP v0.71.0**

从 `https://github.com/fatedier/frp/releases/tag/v0.71.0` 下载 Linux/Windows AMD64 资产；计算 SHA-256，必须与 Global Constraints 完全一致，否则停止。

- [ ] **Step 3: 渲染本机配置并验证**

运行 `render-config.ps1`，再执行 `frpc.exe verify -c runtime/deployment/frpc.toml`。Expected: exit 0，且配置只映射 `127.0.0.1:8765` 到 18765。

- [ ] **Step 4: 用生产变量运行全量测试与构建**

设置 `VITE_API_BASE=/api/v1` 和独立生产 `VITE_SESSION_TOKEN`；运行后端测试、前端测试和构建。Expected: 全部通过，`frontend/dist` 不含开发 API 地址。

- [ ] **Step 5: 打包并记录非秘密清单**

生成前端 ZIP、服务器 FRP 包和每个上传文件的 SHA-256；清单只记录版本、文件名、大小、哈希和构建提交，不记录令牌。

- [ ] **Step 6: 提交清单**

```bash
git add quality/hybrid-deployment-v2.3.0-manifest.md
git commit -m "build: prepare hybrid deployment v2.3.0"
```

### Task 4: 服务器变更前备份与可恢复基线

**Files:**

- Remote create: `/root/local-ai-mate-backup/<timestamp>/`
- Remote create: `/root/local-ai-mate-backup/<timestamp>/baseline.txt`

**Interfaces:**

- Consumes: 腾讯云 OrcaTerm root 会话和 Task 3 发布清单。
- Produces: Nginx、UFW、端口、进程、磁盘、证书和现有 vhost 的只读基线及相关配置备份。

- [ ] **Step 1: 重新确认资源与冲突**

记录 `free -h`、`df -h /`、`ss -ltnp`、运行服务、Nginx 实际二进制、主配置路径和 `nginx -T`。若可用内存低于 512MiB、磁盘低于 5GB、7000 或 18765 已占用，则停止执行并报告。

- [ ] **Step 2: 备份将修改的配置**

仅复制 Nginx 主配置引用关系、vhost 目录清单、UFW 状态和稍后新增文件的同名旧版本（若存在）；不得备份或读取其他站点证书私钥内容。

- [ ] **Step 3: 验证现有服务基线**

从公网确认根域名、`www.shipmaster.asia` 和 OpenClaw 当前响应；保存状态码与时间，不保存页面私密内容。

### Task 5: DNS 与双层防火墙

**Files:**

- External: 腾讯云 DNS 记录 `luna.shipmaster.asia A <server IPv4>`
- External: 腾讯云轻量防火墙 7000/TCP
- Remote modify: UFW 7000/TCP

**Interfaces:**

- Consumes: 已确认公网 IPv4。
- Produces: 指向服务器的独立子域名和 FRP 唯一新增公网控制端口。

- [ ] **Step 1: 创建 DNS A 记录**

主机记录 `luna`，记录类型 A，TTL 使用腾讯云默认值；不得修改 `@` 和 `www`。

- [ ] **Step 2: 验证 DNS**

等待公共解析返回服务器 IPv4；若 20 分钟内仍不一致，停止证书步骤但不重复创建记录。

- [ ] **Step 3: 新增腾讯云与 UFW 7000/TCP 规则**

规则备注 `LocalAIMate FRP TLS`。不得开放 18765、8765 或端口范围。

- [ ] **Step 4: 从外部验证端口边界**

7000 在 frps 启动前应拒绝连接；18765 和 8765 始终拒绝连接。

### Task 6: 安装并启动服务器 FRP

**Files:**

- Remote create: `/opt/frp/0.71.0/frps`
- Remote create: `/etc/frp/frps.toml`
- Remote create: `/etc/systemd/system/frps.service`

**Interfaces:**

- Consumes: Task 3 Linux 二进制、FRP 令牌和 Task 1 模板。
- Produces: 受 systemd 管理的 TLS FRP 控制端口，以及只绑定回环的代理端口能力。

- [ ] **Step 1: 上传并再次校验 Linux FRP SHA-256**

服务器计算值必须为 `84f27e39f11169f7adcef8e8b70c9329de17747b1f14dad9fb95eef5682ea716`；不一致立即删除上传文件并停止。

- [ ] **Step 2: 安装配置和 systemd 单元**

二进制 root 所有、0755；配置 root 所有、0600；服务配置只使用 `/etc/frp/frps.toml`。

- [ ] **Step 3: 验证配置后启动**

Run: `frps verify -c /etc/frp/frps.toml`、`systemctl daemon-reload`、`systemctl enable --now frps`。
Expected: verify exit 0，服务 active。

- [ ] **Step 4: 验证绑定地址**

7000 监听公网；尚无客户端时 18765 不存在，客户端连入后 18765 必须仅监听 127.0.0.1。

### Task 7: 本机生产后端与 FRP 客户端

**Files:**

- Local ignored: `runtime/deployment/frpc.toml`
- Local state: Windows 计划任务 `LocalAIMate-Backend`、`LocalAIMate-Tunnel`

**Interfaces:**

- Consumes: Task 2 脚本、Task 3 Windows frpc/秘密和 Task 6 frps。
- Produces: 本机回环 FastAPI 与可自动重连的 TLS 隧道。

- [ ] **Step 1: 停止当前手工开发服务**

只停止已识别的本项目 Uvicorn/Vite 进程；不停止 Ollama 或其他用户进程。

- [ ] **Step 2: 安装当前用户计划任务并启动**

后端环境包含生产会话令牌、`LOCAL_AI_MATE_ALLOWED_ORIGIN=https://luna.shipmaster.asia`、Ollama/SenseVoice/GPT-SoVITS 现有模型路径；frpc 使用独立配置。

- [ ] **Step 3: 验证本机与隧道**

本机 `/api/v1/health` 返回数据库/model/tts/stt 状态；服务器 `curl http://127.0.0.1:18765/api/v1/health` 可到达；公网直接访问 `:18765` 失败。

- [ ] **Step 4: 验证幂等和自动恢复**

重复运行启动脚本无重复进程；手工终止 frpc 后计划任务按策略恢复；恢复期间不影响本地数据库。

### Task 8: 发布静态前端、Nginx Basic Auth 与 HTTP 站点

**Files:**

- Remote create: `/www/wwwroot/luna.shipmaster.asia/releases/<timestamp>/`
- Remote create/update: `/www/wwwroot/luna.shipmaster.asia/current`
- Remote create: `/www/server/panel/vhost/nginx/luna.shipmaster.asia.conf`
- Remote create: `/www/server/passwd/luna.shipmaster.asia.htpasswd`

**Interfaces:**

- Consumes: Task 3 前端 ZIP、Basic Auth 凭据、Task 1 Nginx 模板和 Task 7 活跃回环 API。
- Produces: 受认证保护的 HTTP 验证站点，为 ACME WebRoot 做准备。

- [ ] **Step 1: 上传前端并校验 SHA-256**

解压到新时间戳目录，确认 `index.html`、背景资源和 JS/CSS 存在后原子切换 `current` 符号链接。

- [ ] **Step 2: 创建 bcrypt htpasswd**

文件权限 0640，仅 Nginx 运行用户可读；命令历史和日志中不得出现明文密码。

- [ ] **Step 3: 安装 HTTP 阶段 Nginx vhost**

先保留 `/.well-known/acme-challenge/` 无认证，其余路径启用 Basic Auth；`nginx -t` 成功后 reload。

- [ ] **Step 4: 验证认证与现有站点**

未认证 `/`、`/api/v1/health` 返回 401；正确认证后静态资源 200、API 200；根域名、www 和 OpenClaw 与基线一致。

### Task 9: HTTPS 证书与生产 Nginx 切换

**Files:**

- Remote create: `/etc/letsencrypt/live/luna.shipmaster.asia/`
- Remote modify: `/www/server/panel/vhost/nginx/luna.shipmaster.asia.conf`
- Remote create: 证书续期 reload hook

**Interfaces:**

- Consumes: 已生效 DNS、HTTP ACME 路径和 Task 8 vhost。
- Produces: 有效 HTTPS、HTTP 重定向、安全响应头和自动续期。

- [ ] **Step 1: 使用 WebRoot 签发单域名证书**

只申请 `luna.shipmaster.asia`；不得包含根域名或 www。失败时不启用伪证书或 HTTP 业务。

- [ ] **Step 2: 渲染 HTTPS Nginx 配置并测试**

启用 TLS 1.2/1.3、Basic Auth、静态 SPA、回环 API、流式代理、安全头和 180 秒超时；`nginx -t` 成功后 reload。

- [ ] **Step 3: 安装和演练续期 hook**

hook 先运行实际 Nginx `-t` 再 reload；执行 `certbot renew --dry-run`，Expected: success。

- [ ] **Step 4: 外部 TLS 与认证测试**

证书 SAN 只含目标域名且链有效；HTTP 301 到 HTTPS；HTTPS 未认证 401；认证后页面/API 200。

### Task 10: 真实端到端验收、回滚演练与收尾提交

**Files:**

- Create: `quality/hybrid-deployment-v2.3.0-verification.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-09-hybrid-private-deployment-design-v2.3.0.md`

**Interfaces:**

- Consumes: 完整公网链路和 v2.3.0 十项验收标准。
- Produces: 不含秘密的验证证据、用户访问地址和一次显示的登录凭据。

- [ ] **Step 1: 公网功能验收**

在新浏览器会话中验证 Basic Auth、全屏背景、关系设定、文字回复、SenseVoice、GPT-SoVITS 流式首段和 Piper 回退；确认浏览器网络请求只访问 `luna.shipmaster.asia`。

- [ ] **Step 2: 故障恢复验收**

短暂停止 frpc，确认静态页仍可访问且 API 明确离线；重新启动后无需 reload Nginx恢复。不得在语音生成中途执行该测试。

- [ ] **Step 3: 外部端口与隐私检查**

确认 18765/8765 公网关闭、7000 非 FRP 客户端无法代理、服务器无 SQLite/模型/参考音、日志无 Authorization/请求正文。

- [ ] **Step 4: 回滚演练**

验证前端符号链接可切回上一版本，Nginx 备份可恢复且 `nginx -t` 通过；演练后恢复 v2.3.0，不删除备份。

- [ ] **Step 5: 完成文档和最终测试**

记录各项状态、延迟、服务自启和既有站点对比；README 增加运行条件、访问方式与本机离线行为；设计状态改为已上线。再次运行后端/前端全量测试和构建。

- [ ] **Step 6: 最终提交**

```bash
git add README.md quality/hybrid-deployment-v2.3.0-verification.md docs/superpowers/specs/2026-10-09-hybrid-private-deployment-design-v2.3.0.md
git commit -m "docs: verify hybrid private deployment v2.3.0"
```
