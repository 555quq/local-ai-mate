# 本地 AI 伴侣混合私有上线设计 v2.3.1

日期：2026-10-09  
状态：已选择 OpenSSH 反向隧道，待书面规格复核  
适用项目：露娜本地 AI 伴侣

## 1. 版本变更

本版本替代 v2.3.0 的 FRP 隧道。Windows Defender 将官方 FRP v0.71.0 识别为 `PUA:Win32/FRProxy` 并阻止运行；即使压缩包 SHA-256 与官方发布值完全一致，也不通过添加杀毒排除项继续使用。v2.3.1 改用 Windows 11 自带的 OpenSSH 9.5 客户端，通过现有腾讯云 SSH 服务建立受约束的反向隧道。

以下内容保持不变：`luna.shipmaster.asia`、Nginx 静态前端、Basic Auth、HTTPS、FastAPI 会话令牌、精确 Origin、本机 AI 推理与数据存储、服务器回环上游 `127.0.0.1:18765`。

## 2. 目标与边界

用户通过 `https://luna.shipmaster.asia` 访问露娜。腾讯云只提供 HTTPS、认证、静态资源和流量转发；Ollama、SenseVoiceSmall、GPT-SoVITS、Piper、SQLite 与记忆数据继续留在 Windows 本机。服务器仍不安装 AI 模型、项目数据库或常驻 Node/Vite 服务。

这是单用户私有部署，不提供注册、多用户账户或公网 API。Windows 关机、休眠或断网时，静态页面仍可访问，但界面显示“本地 AI 主机暂时离线”。

## 3. 架构

```text
用户浏览器
  │ HTTPS + Nginx Basic Auth
  ▼
luna.shipmaster.asia
  ├─ /、静态资源 ──► 腾讯云静态发布目录
  └─ /api/* ──► Nginx ──► 127.0.0.1:18765
                                  ▲
                                  │ OpenSSH 反向隧道
                                  │ -R 127.0.0.1:18765:127.0.0.1:8765
                                  │
                   Windows FastAPI 127.0.0.1:8765
                                  │
             Ollama / SenseVoice / GPT-SoVITS / Piper / SQLite
```

OpenSSH 连接由 Windows 主动发起并复用服务器现有 SSH 监听端口。腾讯云防火墙和 UFW 不新增 7000/TCP；18765 只由 `sshd` 在服务器回环地址创建，8765 只在 Windows 回环地址监听。

## 4. SSH 身份与最小权限

服务器创建专用系统用户 `luna-tunnel`，不设置密码，不加入 sudo 组，不授予交互式 Shell。该用户只接受独立 Ed25519 公钥认证，不能执行远程命令、申请 PTY、转发代理、转发 X11 或读取项目数据。

专用 `authorized_keys` 条目使用 OpenSSH 约束组合：

- `restrict` 默认关闭代理、PTY、用户 rc、X11 和端口转发能力；
- `port-forwarding` 只重新开启端口转发能力；
- `permitlisten="127.0.0.1:18765"` 只允许指定服务器监听地址和端口；
- `sshd_config` 的 `Match User luna-tunnel` 再限制为 `AllowTcpForwarding remote`、`GatewayPorts no`、`PasswordAuthentication no` 和 `PermitTTY no`。

两层限制必须同时生效。部署前使用服务器实际 `sshd -T`/`sshd -T -C user=luna-tunnel,...` 验证最终配置；语法失败或约束未生效时不得 reload SSH 服务。

## 5. 密钥和主机指纹

Windows 为本项目生成独立 Ed25519 密钥，私钥无口令以支持无人值守登录，但仅当前 Windows 用户和 SYSTEM 可读取。私钥、部署令牌和 `known_hosts` 均放在 Git 忽略目录 `runtime/deployment/ssh/`，不复用用户现有 `~/.ssh` 私钥。

服务器 Ed25519 主机公钥指纹先在腾讯云控制台的可信终端中读取，再与 Windows `ssh-keyscan` 获取的公钥进行 SHA-256 指纹比对。只有完全一致才写入项目独立 `known_hosts`。客户端固定使用：

- `BatchMode=yes`，禁止回退到密码交互；
- `StrictHostKeyChecking=yes`，主机指纹变化时立即失败；
- `UserKnownHostsFile=<项目独立 known_hosts>`，不修改用户全局文件；
- `ExitOnForwardFailure=yes`，18765 无法绑定时不伪装为连接成功；
- `ServerAliveInterval=30`、`ServerAliveCountMax=3`，约 90 秒内发现失联；
- `-NT`，不执行远程命令、不分配终端。

## 6. Windows 生命周期

现有 `LocalAIMate-Backend` 任务继续运行 FastAPI。原 `LocalAIMate-Tunnel` 任务改为监督 `ssh.exe` 前台进程；异常退出后由计划任务按一分钟间隔重试。启动脚本使用 PID 文件和命令行标记保证幂等，停止脚本只终止本项目记录且命令行匹配的进程。

生产后端固定：

- `127.0.0.1:8765`；
- `LOCAL_AI_MATE_ALLOWED_ORIGIN=https://luna.shipmaster.asia`；
- 独立 `LOCAL_AI_MATE_SESSION_TOKEN`；
- 现有本机 Ollama、SenseVoice 和 GPT-SoVITS 模型路径。

隧道固定映射 `127.0.0.1:18765` 到 `127.0.0.1:8765`，不允许模板或运行时配置覆盖这两个地址。用户现有 SSH 会话、SSH 配置和密钥不被读取或修改。

## 7. Nginx、域名和 HTTPS

v2.3.0 的 Nginx 设计继续适用：

- 新建 `luna.shipmaster.asia` A 记录，不修改根域名或 `www`；
- `/api/*` 代理到 `http://127.0.0.1:18765`；
- 静态页面和 API 均由 Nginx Basic Auth 保护；
- ACME WebRoot 路径不要求认证；
- HTTP 重定向 HTTPS，TLS 1.2/1.3；
- `proxy_buffering off`、超时 180 秒、`client_max_body_size 9m`；
- 麦克风权限仅限本站，SPA 路由回退不影响 `/api`；
- 每次 reload 前执行宝塔实际 Nginx 二进制的配置测试。

Nginx 不感知隧道实现，因此 FRP 替换为 OpenSSH 不改变前端构建、Basic Auth、证书或 API 路径。

## 8. 防火墙与端口

不新增 FRP 的 7000/TCP 规则。复用服务器当前已存在并经腾讯云控制台允许的 SSH 端口，端口号在部署预检中读取，不在设计中假定为 22。

上线后端口边界：

- 80/443：Nginx 公网入口；
- 现有 SSH 端口：仅用于管理和 `luna-tunnel` 公钥连接；
- 18765：服务器回环监听，公网不可达；
- 8765：Windows 回环监听，局域网和公网不可达；
- 7000：本项目不监听、不新增规则。

若服务器当前 SSH 配置允许密码登录，本项目不擅自全局关闭，以免影响既有管理方式；`luna-tunnel` 用户仍必须单独禁用密码认证。

## 9. 故障处理

- 主机指纹不匹配：立即停止，不自动接受新指纹。
- SSH 专用账号约束验证失败：恢复 `sshd_config` 备份，不启动隧道。
- 18765 已占用：客户端因 `ExitOnForwardFailure` 退出，不终止未知进程。
- 隧道断开：计划任务重试；Nginx 返回网关错误，前端显示离线，不调用云模型。
- SSH 服务 reload 失败：通过腾讯云控制台保留的终端恢复备份，不重启服务器。
- 本机密钥 ACL 无法限制：不安装自启动任务，不复制私钥到其他位置。
- DNS、证书或 Nginx 失败：不把明文 HTTP 或自签证书标记为上线成功。

## 10. 隐私与日志

浏览器至腾讯云使用 HTTPS，腾讯云至 Windows 使用 SSH 加密。对话内容只在 Nginx 内存转发和 Windows 后端处理；服务器不保存 SQLite、模型、参考音频或 TTS 结果。

Nginx 不记录请求正文、Authorization 或会话令牌。SSH 日志只保留认证、连接和转发故障元数据。设计文档、Git、发布包和验证报告不得出现 Basic Auth 密码、FastAPI 会话令牌、私钥或完整公钥内容。

## 11. 验收标准

1. `https://luna.shipmaster.asia` 使用有效证书，HTTP 自动跳转 HTTPS。
2. 未认证访问 `/` 与 `/api/v1/health` 均返回 401。
3. 正确认证后能加载背景、关系设定，并完成一次本机文字回复。
4. SenseVoice、GPT-SoVITS 流式首段和 Piper 回退各完成一次公网测试。
5. `luna-tunnel` 无法获得 Shell、PTY、正向转发或监听 18765 以外端口。
6. 主机指纹错误时客户端拒绝连接，正确指纹下反向隧道成功。
7. 18765 仅监听 `127.0.0.1`，公网无法访问 18765/8765；7000 未新增监听或规则。
8. 终止 SSH 隧道后静态页仍可打开并显示离线；计划任务自动恢复后无需 reload Nginx。
9. 服务器重启后 Nginx/SSH 正常，Windows 重新登录后后端与隧道恢复。
10. `www.shipmaster.asia`、OpenClaw、宝塔面板和既有管理 SSH 在部署前后行为一致。
11. Git 与部署报告不包含密码、会话令牌、私钥、聊天数据或音频内容。

## 12. 回滚

1. 禁用并删除 Windows 的 `LocalAIMate-Tunnel` 计划任务，停止 PID 文件标识的本项目 `ssh.exe`。
2. 从专用用户的 `authorized_keys` 删除本项目公钥，删除 `Match User luna-tunnel` 独立配置并通过 `sshd -t` 后 reload。
3. 需要时锁定或删除无数据的 `luna-tunnel` 用户；不修改其他用户的密钥。
4. 移除 `luna.shipmaster.asia` Nginx 虚拟主机并在语法检查通过后 reload。
5. 删除该子域名 DNS 记录；保留加密密钥备份之外的发布与配置备份直至确认无需恢复。

回滚不修改现有 SSH 端口、防火墙规则、`www.shipmaster.asia`、OpenClaw、宝塔或其他证书。

## 13. 资源结论

OpenSSH 反向隧道复用系统现有服务，不需要额外常驻服务端程序或新公网端口，资源占用低于 FRP 方案。2 核 2GB 服务器足以承载单用户静态站点、Nginx 代理和 SSH 隧道。主要可用性条件仍是 Windows 电脑保持开机、联网并避免自动休眠。
