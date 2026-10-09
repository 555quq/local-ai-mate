# 露娜本地伴侣 v1.7.0 验证记录

验证日期：2026-10-08  
验证设备：Windows、Python 3.12、CPU 本地 STT/TTS、Ollama `qwen3:1.7b`

## 结论

v1.7.0 的沉浸式对话、透明历史、S1 字幕、V3 半双工语音状态机、本地 STT、本地 TTS 和 Ollama 对话链路均已完成并通过自动化或真实运行时验证。应用未使用浏览器云端语音识别；健康接口报告 `cloud_outbound_enabled: false`。

## 安装状态

- 后端隔离环境：`D:\A\backend\.venv`
- faster-whisper：1.2.1
- CTranslate2：4.8.2
- PyAV：15.1.0（固定 `<16`，规避 PyAV 19 与 faster-whisper 1.2.1 的真实解码不兼容）
- Piper：1.8.0
- STT 模型：`D:\A\models\stt\faster-whisper-base`，6 个模型文件，约 148 MB
- TTS 模型：`D:\A\models\tts\piper\zh_CN-huayan-medium`
- 背景资源：`D:\A\frontend\src\assets\luna-background.png`，与 `D:\A\露娜纯净版.png` 字节一致

## 自动化验证

### 后端

命令：

```powershell
Set-Location D:\A\backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：23/23 通过。覆盖会话令牌、Origin、消息、记忆、提醒、隐私、Piper、STT、并发拒绝、音频大小/时长/媒体类型及安全错误映射。

### 前端

命令：

```powershell
Set-Location D:\A\frontend
npm test -- --run
npm run build
```

结果：22/22 通过；TypeScript 与 Vite 生产构建通过。覆盖 VAD、麦克风权限失败、资源释放、暂停/恢复、STT Blob、播放结束/阻止/失败/停止、透明历史、S1 字幕、键盘与无障碍名称。

## 真实运行时冒烟

1. `/api/v1/health`：数据库、Ollama、Piper 和 Faster-Whisper 最终均为 `ready`。
2. 本地文字消息：成功创建会话并收到 `event: completed`；回复由本机 `qwen3:1.7b` 生成。
3. Piper：真实合成返回 `audio/wav`、RIFF/WAVE 文件头，样例输出 331,820 字节。
4. Faster-Whisper：将 Piper 生成的真实中文 WAV 直接以内存请求提交，HTTP 200，语言 `zh`，识别时长约 2.1 秒；base 模型把“露娜”近音识别为“路那”，属于小模型词汇精度而非链路故障。
5. STT 响应带 `Cache-Control: no-store`；音频未写入数据库、日志或临时文件。
6. 前端开发服务运行于 `http://127.0.0.1:5173/`，后端只监听 `127.0.0.1:8765`。

## 人工设备检查

物理麦克风权限必须由用户在浏览器中点击中央光球后授予，无法由自动化测试代替。自动化已经验证拒绝权限、权限撤销、静音丢弃、停止、切页和卸载时的资源清理。首次实机说话建议使用一句 2–6 秒普通话；若专名识别偏差，可后续把 STT 模型升级为 `small`，代价是更高 CPU 和内存占用。

## 已知边界

- 当前 SQLite 是开发版明文存储，真实私密数据需等 SQLCipher/Windows 凭据保护完成后再长期使用。
- 华妍 Piper 模型卡标注原始数据集许可为 `Unknown`；公开分发或商用前需重新确认授权。
- v1.7.0 是半双工：露娜思考或朗读时暂停麦克风，不支持全双工打断或后台唤醒词。
