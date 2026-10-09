# 本地伴侣前端 MVP

这是根据设计方案 v1.6.0 实现的 React + TypeScript + Vite 本地控制台原型。它只向同源本地后端发起 `/api/v1/*` 请求，浏览器端不会直接调用云端模型、第三方分析或遥测服务。

## 已实现范围

- 对话页：消息来源标签、本地/云端增强路由选择、输入校验、错误保留。
- 本地语音输出：助手完整回复后默认自动朗读；支持单条播放/停止和全局自动朗读开关。WAV Blob 只在当前页面内存中缓存，卸载时撤销。
- 云端同意卡：后端签发一次性同意记录，再以 `request_id` 与 `cloud_consent_id` 提交；前端不持有云端 API 密钥。
- 记忆候选审核：批准或批准为仅本地使用。
- 隐私页：明确标出后端尚未实现的持久化控制，避免界面伪造已保存、已导出或已删除的状态。

## 运行

要求 Node.js 20+。在此目录运行：

```powershell
npm install
npm run dev
```

开发服务器固定绑定 `127.0.0.1:5173`，并将同源 `/api` 请求转发到 `127.0.0.1:8765` 的本地后端；浏览器不会直接跨域访问端口 8765。生产启动器应以同样方式提供同源本地代理，而不要把云端端点暴露给浏览器。

集成时可在本机 `.env.local` 设置 `VITE_SESSION_TOKEN`。`VITE_API_BASE` 默认应保持 `/api/v1`，只在受控同源部署中调整。令牌只用于本机后端的写入请求（`X-Session-Token`）；不要填入云端 API 密钥，也不要将 `.env.local` 提交到版本库。

构建检查：

```powershell
npm run build
```

## 本地 API 契约

客户端封装位于 `src/api/client.ts`，适配后端的 `{ request_id, server_time, data }` JSON 信封、消息 SSE 流和 `/tts/synthesize` 的二进制 WAV 响应。核心端点为：`/health`、`/conversations`、`/conversations/:id/messages`、`/tts/synthesize`、`/cloud/consents`、`/conversations/:id/memory-candidates` 与 `/memory-candidates/:id/approve`。所有云端增强均必须经过本地后端：同意记录返回 `request_id` 与 `consent_id` 后，才能附在本地消息请求里。
