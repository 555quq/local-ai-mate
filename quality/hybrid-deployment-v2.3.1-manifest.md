# 混合私有上线 v2.3.1 发布清单

日期：2026-10-09  
版本：v2.3.1  
方案：Nginx + OpenSSH 受约束反向隧道

## 构建

- 构建提交：`f99e802b089bc2d87098ece041177cf05a9fa6ea`
- 发布目录标识：`20261009-190002`
- 前端包：`frontend.zip`
- 文件大小：1,597,410 bytes
- SHA-256：`091597a5374aa89b9b4df254c9df5373ea55f904e8dc5b3fbab04b46914ab242`
- Windows SSH 客户端：OpenSSH_for_Windows_9.5p2 / LibreSSL 3.8.2

## 构建验证

- 后端：91 项 pytest 通过。
- 前端：38 项 Vitest 通过。
- Windows 生命周期：5 项 Pester 通过。
- TypeScript 与 Vite 生产构建通过。
- 生产包不含开发服务器地址、FRP 配置或 FRP 令牌标识。

## 安全材料状态

- Basic Auth 用户名、密码和 FastAPI 会话令牌已分别生成并保存在 Git 忽略目录。
- 项目独立 Ed25519 身份已生成，私钥 ACL 只允许当前 Windows 用户和 SYSTEM。
- 服务器 Ed25519 主机公钥已通过腾讯云可信终端与公网握手指纹比对，项目 `known_hosts` 已固定。
- 专用服务器账号只能使用公钥认证和远程端口转发；禁止密码、PTY、代理、X11、用户 rc 与本地转发。
- 本清单不记录密码、令牌、私钥、公钥正文或服务器主机公钥正文。

## 端口约束

- Windows FastAPI：`127.0.0.1:8765`
- 服务器 Nginx 上游：`127.0.0.1:18765`
- SSH：复用服务器既有 22/TCP
- 7000/TCP：未使用、未新增规则
