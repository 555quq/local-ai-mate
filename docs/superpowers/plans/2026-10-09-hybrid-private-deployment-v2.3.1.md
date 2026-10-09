# 混合私有上线 v2.3.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地 AI 伴侣安全发布到 `https://luna.shipmaster.asia`，使用受约束的 OpenSSH 反向隧道连接腾讯云 Nginx 与 Windows 本机 AI 后端。

**Architecture:** 腾讯云静态托管前端，Nginx 对全站执行 Basic Auth 并将 `/api/*` 转发至服务器回环 `127.0.0.1:18765`。Windows 内置 `ssh.exe` 使用独立 Ed25519 密钥和固定主机指纹，将该回环端口反向映射到本机 FastAPI `127.0.0.1:8765`；服务器专用账号只能创建这一条远程转发。

**Tech Stack:** Ubuntu 24.04 OpenSSH Server、Windows OpenSSH 9.5、宝塔 Nginx、ACME/Certbot WebRoot、Windows PowerShell/计划任务、FastAPI、Vite、pytest/Vitest/Pester。

**Spec:** `docs/superpowers/specs/2026-10-09-hybrid-private-deployment-design-v2.3.1.md`

## Global Constraints

- 不添加 Windows Defender 排除项，不运行 FRP，不新增 7000/TCP 防火墙规则。
- 不修改、替换或迁移现有 `www.shipmaster.asia`、OpenClaw、宝塔面板、现有 SSH 管理账号和既有证书。
- 腾讯云不安装 AI 模型、项目数据库或常驻 Node/Vite 开发服务器。
- FastAPI 只绑定 Windows `127.0.0.1:8765`；SSH 远程监听只绑定服务器 `127.0.0.1:18765`。
- SSH 使用专用 `luna-tunnel` 用户、独立 Ed25519 密钥、固定主机指纹和 `permitlisten="127.0.0.1:18765"`。
- 所有秘密和私钥均在执行时生成，保存于 Git 忽略目录，不进入日志、文档或发布包。
- 每次 `sshd` 或 Nginx reload 前必须使用服务器实际二进制执行配置测试；失败时恢复备份。
- 未取得有效 HTTPS 证书、未验证认证边界、未验证隧道账号最小权限或破坏既有服务时，不得宣告上线完成。

## Review Focus

- `sshd_config.d` 未被主配置 Include：预检必须解析实际 Include 链，不能把未加载的 drop-in 当成约束已生效。
- `restrict` 与 `port-forwarding` 组合顺序或 `permitlisten` 格式错误：必须用目标用户上下文的 `sshd -T -C` 和真实负向连接测试验证。
- 服务器 SSH 主机指纹被中间人替换：控制台读取值与 `ssh-keyscan` 结果不一致时停止，不能自动接受。
- 18765 已被未知服务占用：`ExitOnForwardFailure=yes` 必须失败退出，不能终止未知进程或换端口。
- Windows 登录任务重复启动：PID 与完整命令行标记必须阻止第二个 Uvicorn 或隧道进程，并只停止本项目进程。

---

### Task 1: 用 OpenSSH 合约替换 FRP 部署资产

**Files:**

- Delete: `deploy/server/frps.toml.template`
- Delete: `deploy/server/frps.service`
- Delete: `deploy/windows/frpc.toml.template`
- Create: `deploy/server/sshd-luna-tunnel.conf.template`
- Create: `deploy/server/authorized_keys.template`
- Modify: `deploy/windows/secrets.psd1.example`
- Modify: `deploy/windows/generate-secrets.ps1`
- Modify: `backend/tests/test_deployment_contract.py`

**Interfaces:**

- Consumes: v2.3.1 的专用用户、端口、密钥、主机指纹和最小权限约束。
- Produces: 无秘密的 SSH 服务端模板，以及只生成 Basic Auth/FastAPI 凭据的本机秘密模板。

- [ ] **Step 1: 修改部署合约测试并确认失败**

测试断言不再存在 FRP 服务文件；SSH drop-in 含 `Match User luna-tunnel`、`AllowTcpForwarding remote`、`GatewayPorts no`、`PasswordAuthentication no`、`PermitTTY no`；authorized key 模板含 `restrict,port-forwarding,permitlisten="127.0.0.1:18765"` 和公钥占位符；模板和示例不含真实 IP、密钥或令牌。

Run: `.venv/Scripts/python.exe -m pytest tests/test_deployment_contract.py -q`（工作目录 `backend`）  
Expected: FAIL，原因是 SSH 模板尚不存在且旧 FRP 文件仍存在。

- [ ] **Step 2: 实现最小 SSH 模板并移除 FRP 模板**

drop-in 只影响 `luna-tunnel`，不得改变全局管理用户。authorized key 模板使用 `__SSH_PUBLIC_KEY__` 占位符；秘密示例以 `SshUser = 'luna-tunnel'` 和部署时探测的 `SshPort` 替代 `FrpToken`。

- [ ] **Step 3: 运行部署合约与后端全量测试**

Run: `.venv/Scripts/python.exe -m pytest tests -q`（工作目录 `backend`）  
Expected: 全部 PASS。

- [ ] **Step 4: 提交**

```bash
git add deploy backend/tests/test_deployment_contract.py
git commit -m "refactor: replace FRP deployment with constrained SSH"
```

### Task 2: Windows SSH 隧道生命周期与自动恢复

**Files:**

- Modify: `deploy/windows/start-production.ps1`
- Modify: `deploy/windows/stop-production.ps1`
- Modify: `deploy/windows/install-autostart.ps1`
- Delete: `deploy/windows/render-config.ps1`
- Modify: `deploy/windows/tests/production-scripts.Tests.ps1`

**Interfaces:**

- Consumes: `runtime/deployment/secrets.psd1`、`runtime/deployment/ssh/id_ed25519`、项目独立 `known_hosts`。
- Produces: `Start-SshTunnel` 等价行为、受监督的 `ssh.exe -NT` 进程和 `LocalAIMate-Tunnel` 自动恢复任务。

- [ ] **Step 1: 更新 Pester 测试并确认失败**

锁定 `BatchMode=yes`、`StrictHostKeyChecking=yes`、项目 `UserKnownHostsFile`、`ExitOnForwardFailure=yes`、30/3 保活、`-R 127.0.0.1:18765:127.0.0.1:8765`、用户/端口来自秘密文件；断言脚本中无 `frpc` 或 `7000`。同时测试缺失私钥、known_hosts、无效端口时启动失败。

- [ ] **Step 2: 将隧道组件改为受监督 ssh.exe**

`start-production.ps1 -Component Tunnel -Supervise` 以前台子进程运行系统 `ssh.exe`。继续使用 PID 文件和完整命令行标记防重复；后端组件维持回环监听、生产 Origin 和 Ollama 配置。

- [ ] **Step 3: 收紧停止和计划任务行为**

停止脚本只在 PID 对应命令行同时包含项目 `known_hosts` 和固定 `-R` 映射时终止 `ssh.exe`。计划任务名称不变，失败重试间隔一分钟，现有用户登录后启动。

- [ ] **Step 4: 验证脚本、前端与构建**

Run: `Invoke-Pester deploy/windows/tests/production-scripts.Tests.ps1`；在 `frontend` 运行 `npm test -- --run` 与 `npm run build`。  
Expected: 全部 PASS，PowerShell 解析器无错误。

- [ ] **Step 5: 提交**

```bash
git add deploy/windows
git commit -m "feat: supervise a pinned OpenSSH reverse tunnel"
```

### Task 3: 生成 SSH 身份、生产凭据与可审计发布包

**Files:**

- Create (ignored): `runtime/deployment/secrets.psd1`
- Create (ignored): `runtime/deployment/ssh/id_ed25519`
- Create (ignored): `runtime/deployment/ssh/id_ed25519.pub`
- Create (ignored): `runtime/deployment/ssh/known_hosts`
- Create (ignored): `runtime/deployment/releases/<timestamp>/frontend.zip`
- Create: `quality/hybrid-deployment-v2.3.1-manifest.md`

**Interfaces:**

- Consumes: Task 1/2 脚本、生产域名与服务器预检后得到的 SSH 端口/主机指纹。
- Produces: 四项相互隔离的认证材料：Basic Auth 凭据、FastAPI 令牌、SSH 客户端私钥、服务器主机指纹固定文件；以及无秘密发布清单。

- [ ] **Step 1: 安全迁移现有忽略凭据**

保留尚未展示的 Basic Auth 用户名/密码和 FastAPI 会话令牌；移除不再使用的 FRP 令牌，增加 `SshUser` 与预检确认后的 `SshPort`。不在终端输出明文。

- [ ] **Step 2: 生成独立 Ed25519 密钥并限制 ACL**

Run: Windows 系统 `ssh-keygen.exe -t ed25519 -N "" -f runtime/deployment/ssh/id_ed25519 -C local-ai-mate-tunnel`。  
Expected: 私钥只允许当前用户和 SYSTEM 读取；不得写入用户全局 `.ssh`。

- [ ] **Step 3: 固定并验证服务器主机指纹**

从腾讯云可信终端读取服务器 Ed25519 SHA-256 指纹；本机 `ssh-keyscan -p <实际端口>` 写入临时文件并由 `ssh-keygen -lf` 计算指纹。完全一致后才原子写入项目 `known_hosts`。

- [ ] **Step 4: 用生产变量执行全量测试与构建**

设置 `VITE_API_BASE=/api/v1` 和独立 `VITE_SESSION_TOKEN`；运行后端全量测试、前端全量测试和 `npm run build`。  
Expected: 全部通过，`frontend/dist` 不含开发端口或 FRP 字样。

- [ ] **Step 5: 打包并记录非秘密清单**

生成时间戳前端 ZIP，记录文件名、大小、SHA-256、构建提交、OpenSSH 客户端版本；不得记录密码、令牌、私钥、公钥正文或主机公钥正文。

- [ ] **Step 6: 提交清单**

```bash
git add quality/hybrid-deployment-v2.3.1-manifest.md deploy/windows/generate-secrets.ps1
git commit -m "build: prepare SSH hybrid deployment v2.3.1"
```

### Task 4: 服务器预检、备份与 SSH 可信基线

**Files:**

- Remote create: `/root/local-ai-mate-backup/<timestamp>/baseline.txt`
- Remote backup: 实际 `sshd_config`、Include 文件清单、目标 Nginx vhost（若存在）和 UFW 状态

**Interfaces:**

- Consumes: 腾讯云 OrcaTerm root 会话。
- Produces: 实际 SSH 端口、Ed25519 主机指纹、配置加载链、资源与既有服务基线。

- [ ] **Step 1: 记录资源、端口和进程基线**

记录 `free -h`、`df -h /`、`ss -ltnp`、系统服务、宝塔 Nginx 实际二进制和主配置。若可用内存低于 512MiB、磁盘低于 5GB 或 18765 已占用，则停止并报告。

- [ ] **Step 2: 解析 SSH 实际配置**

读取 `sshd -T`、systemd 单元、监听端口和主配置 Include 链；确认腾讯云防火墙与 UFW 已允许该现有端口，不创建新规则。

- [ ] **Step 3: 记录可信主机指纹**

在控制台执行 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256`，仅将指纹值带回本机用于比对，不读取主机私钥。

- [ ] **Step 4: 备份将修改的配置并验证既有服务**

备份 SSH 主配置引用关系和稍后新增文件的同名旧版本；从公网记录根域名、`www.shipmaster.asia`、OpenClaw 与 SSH 管理登录状态码/可达性，不保存页面内容或凭据。

### Task 5: 创建受约束的服务器隧道账号

**Files:**

- Remote create: `/home/luna-tunnel/.ssh/authorized_keys`
- Remote create: SSH 实际 Include 目录中的 `90-local-ai-mate-tunnel.conf`

**Interfaces:**

- Consumes: Task 1 模板、Task 3 公钥、Task 4 实际 SSH 配置。
- Produces: 只能监听 `127.0.0.1:18765` 的 `luna-tunnel` 公钥账号。

- [ ] **Step 1: 创建无密码、无 sudo 的专用用户**

用户 home 只保存 `.ssh`，不放项目文件。目录/文件所有者均为该用户，`.ssh` 0700、`authorized_keys` 0600。

- [ ] **Step 2: 安装受约束公钥与 Match User drop-in**

authorized key 使用 `restrict,port-forwarding,permitlisten="127.0.0.1:18765"`；drop-in 仅匹配 `luna-tunnel` 并限制为远程转发、无密码、无 PTY、`GatewayPorts no`。

- [ ] **Step 3: 离线验证后 reload SSH**

Run: 实际 `sshd -t` 与 `sshd -T -C user=luna-tunnel,host=localhost,addr=127.0.0.1`。  
Expected: 语法成功，目标用户为远程转发、密码关闭、PTY 关闭、GatewayPorts 关闭；保持当前 OrcaTerm 会话，再 reload 而非 restart。

- [ ] **Step 4: 执行正向和负向权限测试**

正确映射可连接；尝试 Shell、PTY、正向转发、监听 `0.0.0.0:18765` 或其他端口必须失败。任何负向测试成功都视为部署失败并回滚 SSH 改动。

### Task 6: 本机生产后端、SSH 隧道与自动恢复

**Files:**

- Local state: `runtime/deployment/ssh/known_hosts`
- Local state: Windows 计划任务 `LocalAIMate-Backend`、`LocalAIMate-Tunnel`

**Interfaces:**

- Consumes: Task 2 脚本、Task 3 密钥/凭据、Task 5 专用账号。
- Produces: 本机回环 FastAPI 和可自动恢复的固定指纹 SSH 隧道。

- [ ] **Step 1: 停止当前本项目开发服务**

只停止命令行可确认属于 `D:\A` 的 Uvicorn/Vite；不停止 Ollama、模型进程或其他用户服务。

- [ ] **Step 2: 安装当前用户计划任务并启动**

启动后端与隧道；后端 Origin 固定为 `https://luna.shipmaster.asia`，SSH 使用项目私钥和 known_hosts。

- [ ] **Step 3: 验证回环链路**

本机 `/api/v1/health` 返回状态；服务器 `curl http://127.0.0.1:18765/api/v1/health` 到达本机；公网访问 18765/8765 失败，7000 无本项目监听。

- [ ] **Step 4: 验证幂等、占用冲突和自动恢复**

重复启动无重复进程；预占 18765 时隧道失败且不终止占用者；终止项目 `ssh.exe` 后计划任务按策略恢复；伪造主机指纹时连接拒绝。

### Task 7: DNS、静态前端、Basic Auth 与 HTTP 站点

**Files:**

- External: DNS A 记录 `luna.shipmaster.asia`
- Remote create: `/www/wwwroot/luna.shipmaster.asia/releases/<timestamp>/`
- Remote update: `/www/wwwroot/luna.shipmaster.asia/current`
- Remote create: `/www/server/panel/vhost/nginx/luna.shipmaster.asia.conf`
- Remote create: `/www/server/passwd/luna.shipmaster.asia.htpasswd`

**Interfaces:**

- Consumes: Task 3 前端包和 Basic Auth 凭据、Task 6 活跃回环 API。
- Produces: 受认证保护的 HTTP 验证站点和 ACME WebRoot。

- [ ] **Step 1: 创建并验证 DNS A 记录**

只创建主机记录 `luna`，不修改 `@` 或 `www`。等待公共解析指向当前服务器；20 分钟不一致时停止证书步骤。

- [ ] **Step 2: 上传前端并原子切换 current**

校验 ZIP SHA-256，解压到新时间戳目录，确认 `index.html`、背景图片、JS/CSS 后切换符号链接；保留上一版本。

- [ ] **Step 3: 安全创建 Basic Auth 哈希**

使用 bcrypt htpasswd，明文密码不进入 shell 历史或日志；文件 0640 且只允许 Nginx 运行用户读取。

- [ ] **Step 4: 安装 HTTP Nginx vhost**

ACME 路径免认证，其余静态和 `/api` 均认证。运行实际 Nginx `-t`，成功后 reload。

- [ ] **Step 5: 验证认证边界与既有站点**

未认证 `/` 和 `/api/v1/health` 均 401；认证后静态与 API 均 200；根域名、www、OpenClaw 与基线一致。

### Task 8: HTTPS、自动续期与浏览器安全

**Files:**

- Remote create: `/etc/letsencrypt/live/luna.shipmaster.asia/`
- Remote modify: `/www/server/panel/vhost/nginx/luna.shipmaster.asia.conf`
- Remote create: 证书续期 Nginx 验证/reload hook

**Interfaces:**

- Consumes: 已生效 DNS、HTTP ACME 路径和 Nginx HTTPS 模板。
- Produces: 有效 TLS、HTTP 重定向、安全响应头和自动续期。

- [ ] **Step 1: WebRoot 签发单域名证书**

只申请 `luna.shipmaster.asia`，失败时不启用自签证书或把 HTTP 业务视为完成。

- [ ] **Step 2: 切换 HTTPS vhost**

渲染实际证书路径，执行 Nginx `-t` 后 reload；保持 Basic Auth、回环 API、流式代理、180 秒超时和 9m 上传限制。

- [ ] **Step 3: 安装续期 hook 并 dry-run**

hook 先执行实际 Nginx `-t` 再 reload；`certbot renew --dry-run` 必须成功。

- [ ] **Step 4: 外部 TLS 和浏览器权限测试**

证书链/SAN 正确，HTTP 301，HTTPS 未认证 401，认证后页面/API 200，麦克风权限只授予本站。

### Task 9: 真实端到端验收、回滚演练与收尾提交

**Files:**

- Create: `quality/hybrid-deployment-v2.3.1-verification.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-09-hybrid-private-deployment-design-v2.3.1.md`

**Interfaces:**

- Consumes: 完整公网链路和 v2.3.1 十一项验收标准。
- Produces: 不含秘密的验收证据、访问地址与一次性显示的 Basic Auth 凭据。

- [ ] **Step 1: 公网功能验收**

在全新浏览器会话验证背景、关系设定、文字回复、SenseVoice、GPT-SoVITS 流式首段和 Piper 回退；浏览器网络请求只访问目标域名。

- [ ] **Step 2: 隧道故障与恢复验收**

非语音生成期间停止隧道，确认静态页可访问且 API 显示离线；计划任务恢复后无需 reload Nginx。

- [ ] **Step 3: SSH 权限、端口和隐私复核**

重复 Task 5 负向权限测试；确认 18765/8765 公网关闭、7000 未新增、服务器无模型/SQLite/参考音、日志无 Authorization 或请求正文。

- [ ] **Step 4: 回滚演练**

验证前端符号链接、Nginx 备份和 SSH drop-in 备份可恢复且配置测试通过；演练后恢复 v2.3.1，不删除备份。

- [ ] **Step 5: 文档与最终测试**

记录状态、延迟、自启、主机指纹验证结果和既有服务对比；README 增加访问和本机离线行为；设计状态改为已上线。再次运行后端/前端/Pester全量测试和生产构建。

- [ ] **Step 6: 最终提交并显示凭据一次**

```bash
git add README.md quality/hybrid-deployment-v2.3.1-verification.md docs/superpowers/specs/2026-10-09-hybrid-private-deployment-design-v2.3.1.md
git commit -m "docs: verify SSH hybrid deployment v2.3.1"
```

仅在所有验收通过后向用户显示 `https://luna.shipmaster.asia`、Basic Auth 用户名和密码一次；不得显示 FastAPI 令牌、私钥或完整公钥。
