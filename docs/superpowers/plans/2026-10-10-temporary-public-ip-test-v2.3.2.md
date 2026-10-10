# 临时公网 IP 测试 v2.3.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改正式域名入口和本地 AI 架构的前提下，为 `https://49.234.57.214:8443` 建立可信、受认证保护且验收后 72 小时自动关闭的临时测试入口。

**Architecture:** 腾讯云运行一个与主 Nginx 隔离的 `luna-temp-edge` Nginx 进程，复用现有只读前端发布目录并将 `/api/*` 转发到既有服务器回环 `127.0.0.1:18765`。Certbot 5.4+ 通过主 Nginx 的临时精确 IP WebRoot 申请 Let’s Encrypt `shortlived` IP 证书；持久化 systemd calendar timer 在验收成功 72 小时后停止入口并清理临时凭据和证书。

**Tech Stack:** Ubuntu 24.04、宝塔 Nginx、Certbot 5.4+、Let’s Encrypt shortlived profile、systemd service/timer、UFW（仅在已启用时）、OpenSSH 反向隧道、FastAPI、pytest、PowerShell/CDP 腾讯云控制台。

**Spec:** `docs/superpowers/specs/2026-10-10-temporary-public-ip-test-design-v2.3.2.md`

## Global Constraints

- 临时公网地址固定为 `https://49.234.57.214:8443`，测试窗口从全部验收通过的 `T0` 开始精确计算 72 小时。
- 8443 使用独立 Nginx 进程、配置、PID、日志和 systemd 服务；不得长期加入主 Nginx 的 80/443 虚拟主机集合。
- 服务器内初验只绑定 `127.0.0.1:8443`；审计云防火墙并启用绝对时间 4 小时故障保护 timer 后，才允许切换到 `0.0.0.0:8443`。
- Certbot 必须为 5.4 或更高版本，并使用 `--preferred-profile shortlived --webroot --ip-address 49.234.57.214`；正式证书 SAN 必须精确包含该 IP。
- 正式证书剩余有效期少于 80 小时时不得开始测试；不得使用 HTTP、自签名证书或忽略浏览器证书警告作为替代。
- 临时 Basic Auth 凭据与正式凭据分离，密码至少 128 位随机熵，只显示一次，不进入 Git、日志或命令历史。
- FastAPI 仍只允许 `https://luna.shipmaster.asia` Origin；临时边缘只在反向代理请求中规范化 Origin，不重启或放宽后端。
- Basic Auth 是公网主认证边界；前端内置的 FastAPI 会话令牌不是独立秘密，测试结束后必须轮换并在正式域名发布前重建前端。
- 服务器不保存模型、SQLite、聊天内容、录音或 TTS 音频；18765 与 Windows 8765 继续只绑定回环地址。
- 不修改 `www.shipmaster.asia`、OpenClaw、宝塔面板、管理 SSH、正式 Basic Auth、正式域名证书或 DNS。
- 主 Nginx、独立 Nginx 或 systemd 配置验证失败时不得 reload/start；任何认证、Origin、TLS 或到期清理负向测试失败都阻止开放公网规则。
- 清理操作只允许固定的 `luna-temp-edge` 文件、服务、证书 lineage 和带项目注释的防火墙规则；不得使用未校验变量指向递归删除目标。

## Review Focus

- 公网 80 被腾讯云未备案策略拦截或返回非 ACME 内容：staging HTTP-01 必须先成功，否则不申请正式证书、不开放 8443。
- 宝塔 Nginx 二进制的编译路径与独立 prefix 冲突：独立配置必须显式设置 PID、日志和全部临时目录，并在启动前执行同一二进制的 `-t`。
- Basic Auth 的 `Authorization` 被转发给 FastAPI：代理必须清空该请求头，同时保留前端使用的 `X-Session-Token`。
- 非同源客户端借助无条件 Origin 改写调用 API：边缘必须先精确校验 IP Origin 与 `Sec-Fetch-Site`，再规范化后端 Origin。
- systemd 到期时服务器关机：timer 必须使用绝对 UTC `OnCalendar` 与 `Persistent=true`，开机后立即补执行清理。
- 部署会话在公网验收中断：4 小时故障保护 timer 必须已在公网绑定前启用，不能让入口处于无到期保护状态。
- 清理部分失败：停止/禁用边缘服务失败不得忽略；必须确认 inactive/disabled 且连续多次无 8443 监听，才能继续删除证书或文件。

---

### Task 1: 建立临时边缘部署合约与可复用资产

**Files:**

- Create: `deploy/server/temp-ip-edge/acme-http.conf.template`
- Create: `deploy/server/temp-ip-edge/nginx.conf.template`
- Create: `deploy/server/temp-ip-edge/luna-temp-edge.service.template`
- Create: `deploy/server/temp-ip-edge/luna-temp-edge-expiry.service.template`
- Create: `deploy/server/temp-ip-edge/luna-temp-edge-failsafe.timer.template`
- Create: `deploy/server/temp-ip-edge/luna-temp-edge-expiry.timer.template`
- Create: `deploy/server/temp-ip-edge/luna-temp-edge-retry.timer.template`
- Create: `deploy/server/temp-ip-edge/check-expiry.sh`
- Create: `deploy/server/temp-ip-edge/cleanup-due.sh`
- Create: `deploy/server/temp-ip-edge/cleanup.sh`
- Create: `backend/tests/test_temp_ip_deployment_contract.py`

**Interfaces:**

- Consumes: v2.3.2 固定 IP、端口、发布目录、回环上游、生产 Origin、服务名称和清理范围。
- Produces: `__PUBLIC_IP__`、`__LISTEN_ADDRESS__`、`__NGINX_BINARY__`、`__NGINX_USER__`、`__CERT_FULLCHAIN__`、`__CERT_PRIVKEY__`、`__TEMP_AUTH_FILE__`、`__FAILSAFE_AT_UTC__`、`__EXPIRES_AT_UTC__` 占位符模板，以及无秘密的幂等清理入口 `/usr/local/lib/luna-temp-edge/cleanup.sh`。

- [ ] **Step 1: 写部署合约测试并确认失败**

在 `test_temp_ip_deployment_contract.py` 断言：独立 Nginx 完整配置通过 `__LISTEN_ADDRESS__:8443` 控制监听；包含 TLS 1.2/1.3、临时 htpasswd、`client_max_body_size 9m`、180 秒超时、关闭响应/请求 buffering 与 cache、先校验 IP Origin/`Sec-Fetch-Site`、再执行 `proxy_set_header Origin https://luna.shipmaster.asia`、清空 `Authorization`、静态与 API 安全头、允许 `data:` 音频和 `access_log off`；ACME 模板只放行 challenge 且其他请求 404；两个 timer 均含 `Persistent=true` 与各自绝对时间占位符；启动守卫拒绝缺失/过期 `expires-at`；到期服务必须由独立 `cleanup-due.sh` `ExecCondition` 约束，未到当前截止时间不得进入清理；清理脚本严格停止服务并只引用固定项目路径。

Run: `.venv/Scripts/python.exe -m pytest tests/test_temp_ip_deployment_contract.py -q`（工作目录 `backend`）

Expected: FAIL，原因是临时边缘资产尚不存在。

- [ ] **Step 2: 实现 ACME 与独立 Nginx 模板**

`nginx.conf.template` 必须是可由 `nginx -p /var/lib/luna-temp-edge/nginx/ -c /etc/luna-temp-edge/nginx.conf` 独立加载的完整配置，显式声明 PID、error log、client/proxy temp 路径、单 worker、events/http 块和唯一 8443 server。静态根目录固定为 `/www/wwwroot/luna.shipmaster.asia/current`，API 上游固定为 `127.0.0.1:18765`。

- [ ] **Step 3: 实现 systemd 单元和固定作用域清理脚本**

边缘服务以前台 `daemon off` 运行，启动前用同一二进制和 prefix 执行配置检查，并调用 `check-expiry.sh` 验证 root-owned、mode 0600、严格 UTC 格式的 `expires-at` 仍在未来。unit 以低权限 Web 用户运行，启用 `ProtectSystem=strict` 等沙箱，只读发布/配置/证书路径，只写固定 runtime/log/temp 路径。expiry service 先以 `ExecCondition=/usr/local/lib/luna-temp-edge/cleanup-due.sh` 严格验证同一 `expires-at`，只有当前 UTC 时间达到截止时间才调用 root-owned 清理脚本，并在真正失败时自动重试；未到期返回普通 condition skip。failsafe/expiry timer 分别使用 `OnCalendar=__FAILSAFE_AT_UTC__`、`OnCalendar=__EXPIRES_AT_UTC__` 与 `Persistent=true`；retry timer 使用 `OnBootSec` 与 `OnUnitInactiveSec`，跨重启周期调用同一受截止条件保护的服务。清理脚本先尝试精确撤销本项目 UFW 规则，再无条件严格 stop/disable edge、确认 inactive/disabled 并连续多次检查 `ss -ltn` 无 8443；UFW 状态/所有权异常必须保留证据，但只能在 edge 关闭验证后阻止删除秘密。安全关键失败或 timer 未确认 inactive/disabled 时保留 retry timer、到期守卫及 `expires-at`；所有 timer 已严格停用后，残留截止记录、unit 和脚本改为终态 best-effort 删除，失败只告警且不虚假声称仍能自动 retry。

- [ ] **Step 4: 增加 Review Focus 负向断言并运行测试**

测试必须额外拒绝：监听 80/443、模板固定公网监听而没有 `__LISTEN_ADDRESS__`、无条件 Origin 改写、代理 Authorization、请求缓冲仍开启、CSP 不允许 `data:` 音频、API 缺失安全头、缺少 `Persistent=true`、无 expires-at 启动或清理到期守卫、retry/旧 failsafe 在当前截止前进入清理、忽略 systemctl stop/disable 失败、清理失败后自删恢复组件、宽泛 `/etc/letsencrypt` 删除、未校验变量参与 `rm -rf`，以及模板内出现真实密码、会话令牌或私钥。

Run: `.venv/Scripts/python.exe -m pytest tests/test_temp_ip_deployment_contract.py -q`，随后 `.venv/Scripts/python.exe -m pytest tests -q`（工作目录 `backend`）

Expected: 新测试和后端全量测试全部 PASS。

- [ ] **Step 5: 提交部署资产**

```bash
git add deploy/server/temp-ip-edge backend/tests/test_temp_ip_deployment_contract.py
git commit -m "feat: add isolated temporary IP edge assets"
```

### Task 2: 服务器只读预检、基线与备份

**Files:**

- Remote create: `/root/local-ai-mate-backup/<timestamp>/temporary-ip-v2.3.2-baseline.txt`
- Remote backup: 主 Nginx 实际配置、站点 vhost、systemd 单元同名旧文件、UFW 状态和 Certbot lineage 清单

**Interfaces:**

- Consumes: 腾讯云 OrcaTerm root 会话、Task 1 无秘密资产、v2.3.1 现有部署。
- Produces: 实际 Nginx 二进制/用户/include 链、Certbot 版本与账户状态、80/8443/18765 端口状态、现有服务基线和精确回滚目录。

- [ ] **Step 1: 记录资源和既有服务基线**

记录 `free -h`、`df -h /`、`ss -ltnp`、主 Nginx PID/二进制/编译参数、`systemctl` 状态、当前 vhost、UFW 状态和服务器时间同步。若可用内存低于 384 MiB、磁盘低于 3 GiB、8443 已被未知进程占用或系统时间未同步，则停止部署。

- [ ] **Step 2: 核对前端与回环 API**

确认 `/www/wwwroot/luna.shipmaster.asia/current/index.html` 存在且 current 链接目标与 v2.3.1 清单一致；`127.0.0.1:18765/api/v1/health` 可到达本地后端。不得为了预检启动第二份前端或 FastAPI。

- [ ] **Step 3: 核对 Certbot 5.4+ 与 ACME 账户**

优先使用隔离的官方 Snap Certbot，不替换宝塔或系统现有 ACME 客户端。若 `/snap/bin/certbot --version` 低于 5.4 或不存在，安装/刷新官方 Certbot Snap 后复核；若没有可用 ACME 账户，暂停取得用户用于 Let’s Encrypt 注册的邮箱，不使用未获同意的邮箱。

- [ ] **Step 4: 创建精确备份并校验**

备份将修改的主 Nginx include/vhost 和所有同名旧单元；保存文件 mode/owner/SHA-256。备份路径必须解析为 `/root/local-ai-mate-backup/<timestamp>` 的直接子目录，不能覆盖 2026-10-09 的现有备份。

### Task 3: 验证 HTTP-01 并签发可信公网 IP 证书

**Files:**

- Remote create: `/var/lib/luna-temp-edge/acme/.well-known/acme-challenge/`
- Remote temporary create: 主 Nginx include 中的精确 IP ACME server block
- Remote create: `/etc/letsencrypt/live/luna-temp-ip-49-234-57-214/`

**Interfaces:**

- Consumes: Task 1 ACME 模板、Task 2 主 Nginx include 链与 Certbot 5.4+。
- Produces: SAN 为 `49.234.57.214`、shortlived profile、剩余有效期至少 80 小时的正式证书。

- [ ] **Step 1: 安装精确 IP ACME 临时 server block**

渲染 `server_name 49.234.57.214`，challenge 根固定为 `/var/lib/luna-temp-edge/acme`，其他路径返回 404。用宝塔实际 Nginx 二进制执行 `-t`，成功后仅平滑 reload；从公网请求随机不存在 challenge 必须返回 404 且现有域名站点状态不变。

- [ ] **Step 2: 先申请 staging 证书**

Run（敏感邮箱从 root-only 环境变量读取，不回显）：

```bash
/snap/bin/certbot certonly --staging --non-interactive --agree-tos \
  --email "$ACME_EMAIL" \
  --preferred-profile shortlived --webroot \
  --webroot-path /var/lib/luna-temp-edge/acme \
  --ip-address 49.234.57.214 --cert-name luna-temp-ip-staging
```

Expected: HTTP-01 成功，staging 证书 SAN 含目标 IP。失败时恢复 ACME 配置并停止，不尝试 HTTP 业务入口。

- [ ] **Step 3: 申请正式受信任证书**

移除 staging lineage 后，以相同参数去掉 `--staging`，把 `--cert-name` 改为 `luna-temp-ip-49-234-57-214`。使用 `openssl x509` 验证 issuer、SAN、notBefore/notAfter 与至少 80 小时剩余有效期；不得只依赖 Certbot 成功退出码。

- [ ] **Step 4: 移除 ACME 临时配置**

删除本版本临时 IP server block，用主 Nginx `-t` 成功后平滑 reload；再次确认域名站点、OpenClaw 和宝塔面板与基线一致。保留空的 challenge 根到最终清理，但公网不再有专用 IP HTTP 路由。

### Task 4: 安装隔离边缘、临时认证与自动关闭

**Files:**

- Create (ignored): `runtime/deployment/temp-ip-v2.3.2-secrets.psd1`
- Remote create: `/etc/luna-temp-edge/nginx.conf`
- Remote create: `/etc/luna-temp-edge/htpasswd`
- Remote create: `/etc/luna-temp-edge/tls/fullchain.pem`、`privkey.pem`
- Remote create: `/usr/local/lib/luna-temp-edge/check-expiry.sh`
- Remote create: `/usr/local/lib/luna-temp-edge/cleanup-due.sh`
- Remote create: `/usr/local/lib/luna-temp-edge/cleanup.sh`
- Remote create: `/etc/systemd/system/luna-temp-edge.service`
- Remote create: `/etc/systemd/system/luna-temp-edge-expiry.service`
- Remote create before public binding: `/etc/systemd/system/luna-temp-edge-failsafe.timer`
- Remote create before public binding: `/etc/systemd/system/luna-temp-edge-retry.timer`
- Remote create after acceptance: `/etc/systemd/system/luna-temp-edge-expiry.timer`
- Remote state: `/var/lib/luna-temp-edge/expires-at`

**Interfaces:**

- Consumes: Task 1 模板、Task 2 实际 Nginx 路径/用户、Task 3 正式证书、现有前端与回环 API。
- Produces: 仅本机可达的初始 8443 HTTPS 服务、一次性临时 Basic Auth 凭据、已启用的 4 小时故障保护 timer 和待验收后启用的 72 小时 timer。

- [ ] **Step 1: 生成不落命令历史的独立临时凭据**

在 Windows 使用系统 CSPRNG 生成随机用户名和至少 24 字节密码，保存到 Git 已忽略的 `runtime/deployment/temp-ip-v2.3.2-secrets.psd1`，ACL 只允许当前用户与 SYSTEM；不在终端回显。只把 bcrypt/apr1 哈希传到 `/etc/luna-temp-edge/htpasswd`；服务器文件 owner 为 root、group 为实际 Nginx 用户组、mode 0640，服务器不保存明文。

- [ ] **Step 2: 渲染并离线验证独立 Nginx**

把正式 lineage 的证书和私钥复制到独立 0640 root/Web组 TLS 目录，再以 `__LISTEN_ADDRESS__=127.0.0.1` 渲染 Nginx；创建显式 PID、日志和临时目录并限制 owner/mode。Run：实际 Nginx 二进制加独立 `-p`/`-c` 的 `-t`。Expected：PASS，且配置展开结果只监听 `127.0.0.1:8443`、不监听 80/443、不包含正式 htpasswd 路径；systemd unit 展开后只读访问发布/配置/TLS，只写固定运行目录。

- [ ] **Step 3: 安装边缘和清理 systemd 单元**

运行 `systemd-analyze verify` 检查 service、expiry service、failsafe timer 与 retry timer；计算当前 UTC 加 4 小时的绝对时间，把它以 root:0600 严格 UTC 单行格式原子写入 `expires-at`，启用 failsafe 与 retry timers，并确认触发时间、周期及 retry 提前触发只产生 condition skip 后才启动 loopback edge。若 UFW 已 active，记录前态并添加带注释 `luna-temp-edge-v2.3.2` 的唯一 8443/TCP 规则及其完整 tuple/编号；若 UFW inactive，不启用 UFW。

- [ ] **Step 4: 做服务器内正向与负向测试**

使用服务器回环 IP HTTPS 验证未认证根/API 均为 401；正确临时凭据留到 Task 5 从 Windows 受限秘密文件通过浏览器验证，禁止在 shell 参数或历史中出现明文。`openssl s_client` 证实 TLS 1.2/1.3 与 IP SAN；检查代理展开配置含 Origin/`Sec-Fetch-Site` 校验、清空 Authorization、规范化 Origin、保留 `X-Session-Token` 及 API 全部安全头。确认 `ss -ltnp` 只新增 `127.0.0.1:8443`，failsafe timer active，删除/过期 expires-at 后服务无法启动。

- [ ] **Step 5: 在隔离替代根中验证清理幂等性**

用临时目录和显式 mock 的 systemctl/ss/ufw/sleep/certbot 命令执行两次 `cleanup.sh`；TEST_ROOT 模式缺少任一 mock 时必须拒绝运行，不能回退到宿主真实命令。两次成功目标仅限模拟的 `luna-temp-edge` 文件。通过 expiry service 等价调用证明未来 `expires-at` 下 retry 与迟到的旧 failsafe 都只跳过、到期后才进入清理；非法日期或安全元数据必须 fail closed。分别注入 UFW status 失败、所有权不匹配、stop 失败、服务仍 active、端口间歇重现和证书删除失败；UFW 故障用例必须断言 edge 仍被 stop/disable 且完成端口检查。安全关键或 timer 停用失败均禁止删除可重试截止状态并返回非零；证书删除失败时保留 retry timer、unit、`cleanup-due.sh` 和 cleanup，模拟重启后仍会再次调用清理。另注入 timer 全部停用后的 daemon-reload 失败，必须明确作为 terminal best-effort 告警并返回安全完成，不得声称不存在的自动 retry。

### Task 5: 腾讯云防火墙与公网端到端验收

**Files:**

- External modify: 腾讯云 Lighthouse 入站防火墙规则 `TCP:8443`
- Create: `quality/temporary-public-ip-test-v2.3.2-verification.md`

**Interfaces:**

- Consumes: Task 4 本机已验证但尚未对公网开放的临时入口。
- Produces: 通过公网浏览器验证的文字、STT、TTS、认证、离线恢复与既有服务隔离证据。

- [ ] **Step 1: 审计规则、切换公网监听并仅开放 8443/TCP**

先通过腾讯云控制台确认不存在 8443 或全端口入站放行规则。确认 4 小时 failsafe timer active 后，将独立 Nginx 配置原子改为 `__LISTEN_ADDRESS__=0.0.0.0`，执行 `-t` 并仅重启独立 edge；随后为当前 Lighthouse 实例新增单端口 TCP 8443 入站规则。不开放 18765、8765 或端口范围，不修改 80/443/SSH 规则。截图/记录规则 ID 与时间，但不记录账号 Cookie 或访问令牌。

- [ ] **Step 2: 验证公网 TLS 与双层认证边界**

从非服务器网络访问 `https://49.234.57.214:8443`：浏览器无证书警告；未认证和错误 Basic Auth 均 401；正确 Basic Auth 从本机受限秘密文件注入浏览器，不进入 URL、终端参数、历史或截图；错误/缺失 `X-Session-Token` 的写操作失败；前端 token 的同源写操作成功；错误 Origin 或跨站 `Sec-Fetch-Site` 返回 403。

- [ ] **Step 3: 验证文字与历史对话**

完成一次文字发送、流式回复和历史记录打开/关闭。记录首字符时间与完成状态，不记录对话正文。浏览器网络只应访问 `49.234.57.214:8443`，不得直接访问 18765、8765 或第三方模型 API。

- [ ] **Step 4: 验证麦克风、STT 与 TTS**

确认浏览器把 IP HTTPS 识别为安全上下文并出现麦克风授权；完成一次本地语音识别、GPT-SoVITS 流式首段播放和一次 Piper 回退。记录状态、耗时和错误码，不保存录音或生成音频。

- [ ] **Step 5: 验证隧道断开与自动恢复**

在非生成阶段只停止本项目 SSH 隧道，确认静态页仍可访问、API 显示本地主机离线；计划任务恢复隧道后无需重启 edge。不得停止 Ollama、模型进程或未知 SSH 会话。

- [ ] **Step 6: 对比既有服务与公网端口**

复核域名站点、OpenClaw、宝塔面板、管理 SSH 与 Task 2 基线一致；公网扫描只新增 8443，18765/8765 不可达。把非秘密结果写入 verification 文档。

### Task 6: 启动 72 小时窗口、更新文档并提交

**Files:**

- Modify: `quality/temporary-public-ip-test-v2.3.2-verification.md`
- Modify: `docs/superpowers/specs/2026-10-10-temporary-public-ip-test-design-v2.3.2.md`
- Modify: `README.md`
- Remote create: `/etc/systemd/system/luna-temp-edge-expiry.timer`
- Remote remove after replacement: `/etc/systemd/system/luna-temp-edge-failsafe.timer`
- Remote create: `/var/lib/luna-temp-edge/expires-at`

**Interfaces:**

- Consumes: Task 5 全部通过的验收记录。
- Produces: 从验收成功时刻开始的 72 小时持久化自动关闭、可审计状态、一次性显示的临时访问凭据和最终提交。

- [ ] **Step 1: 计算并固化绝对到期时间**

使用服务器同步后的 UTC 当前时间作为 `T0`，计算 `T0 + 72 hours`，同时记录 UTC 与 Asia/Shanghai 时间。渲染 timer 的 `OnCalendar=<UTC timestamp> UTC`、`Persistent=true`；在 `expires-at` 同目录创建 root-owned mode 0600 临时文件，写入同一严格 UTC 时间并 fsync 后以原子 rename 替换当前 4 小时截止。替换后立即运行 `check-expiry.sh`，并验证 `cleanup-due.sh` 返回未到期 condition skip。

- [ ] **Step 2: 验证并启用 timer**

运行 `systemd-analyze calendar` 与 `systemd-analyze verify`；`systemctl enable --now luna-temp-edge-expiry.timer` 后，`systemctl list-timers` 的 NEXT 必须与记录的 UTC 时间一致。只有确认 72 小时 timer active、NEXT 正确且 retry timer active 后，才禁用并删除 4 小时 failsafe timer。模拟旧 failsafe 在切换后迟到触发以及 retry 周期触发，二者都必须因当前 72 小时 `expires-at` 未到而跳过；再用替代根测试到期清理、开机补执行和清理失败后跨重启周期重试语义，不提前触发真实清理。

- [ ] **Step 3: 完成安全与秘密扫描**

运行后端全量 pytest、前端 Vitest/build、Pester，并对 Git diff、verification、README、systemd unit 展开文本执行秘密扫描。确认没有临时密码、FastAPI token、私钥、Authorization 值、聊天正文、录音路径或证书私钥。

- [ ] **Step 4: 更新状态文档**

把规格状态改为“72 小时临时测试已开始”，README 增加临时入口的短期性质、Windows 必须在线和到期行为；verification 记录版本、提交、证书 SAN/有效期、T0、到期时间、timer 状态、测试项和既有服务对比，不记录秘密。

- [ ] **Step 5: 提交验收记录**

```bash
git add README.md quality/temporary-public-ip-test-v2.3.2-verification.md \
  docs/superpowers/specs/2026-10-10-temporary-public-ip-test-design-v2.3.2.md
git commit -m "docs: verify temporary IP test v2.3.2"
```

- [ ] **Step 6: 一次性显示访问资料**

仅在证书、认证、文字、STT、TTS、端口隔离和 timer 全部通过后，向用户显示一次 URL、临时用户名、临时密码、Asia/Shanghai 到期时间。不得显示 FastAPI 会话令牌、SSH 私钥、完整公钥或证书私钥。

### Task 7: 到期结果与人工云规则收尾

**Files:**

- Remote state: systemd journal 中不含秘密的 expiry 结果
- Modify after expiry: `quality/temporary-public-ip-test-v2.3.2-verification.md`
- External delete after expiry: 腾讯云 Lighthouse `TCP:8443` 入站规则

**Interfaces:**

- Consumes: Task 6 到期时间和腾讯云规则 ID。
- Produces: 8443 无监听、临时服务/凭据/证书已清理、云防火墙规则已删除的最终关闭证据。

- [ ] **Step 1: 到期后验证自动关闭**

确认 edge service disabled/inactive、8443 无监听、临时 htpasswd 不存在、唯一 Certbot lineage 已删除、UFW 项目标记规则不存在。若清理有警告，保持服务关闭并只修复固定项目目标。

- [ ] **Step 2: 删除腾讯云 8443 规则**

通过控制台删除 Task 5 记录的单条规则，不修改其他规则；从公网确认 8443 超时/拒绝，域名站点与 SSH 仍正常。

- [ ] **Step 3: 轮换临时测试期间可见的 FastAPI 会话令牌**

在 Windows 生成新的生产会话令牌并更新 Git 忽略的秘密文件；安全重启本项目后端并验证回环健康与 SSH 隧道恢复。正式域名解除备案拦截前必须以新 token 重新构建前端；不得继续发布包含旧 token 的现有测试包。

- [ ] **Step 4: 补记最终关闭结果并提交**

在 verification 文档中增加实际清理时间、自动/人工步骤结果和既有服务复核，不写入凭据或日志正文。

```bash
git add quality/temporary-public-ip-test-v2.3.2-verification.md
git commit -m "docs: close temporary IP test v2.3.2"
```
