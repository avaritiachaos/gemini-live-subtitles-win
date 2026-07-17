# Live Translate — Gemini 实时同传字幕（Windows）

利用 Google AI Studio 免费 API key + `gemini-3.5-live-translate-preview` 模型，
把系统正在播放的任何声音（YouTube、会议、纪录片…）实时翻译成中文悬浮字幕。

- **免费**：AI Studio 免费层该模型不限 RPM/RPD，只限 TPM，个人使用足够
- **免虚拟声卡**：直接用 Windows WASAPI loopback 抓系统声音
- **70+ 语言 → 中文**（目标语言可在设置中切换）

## 使用步骤

1. 安装 [Python 3.11+](https://www.python.org/downloads/)（勾选 *Add Python to PATH*）
2. 到 [aistudio.google.com/apikey](https://aistudio.google.com/apikey) 免费创建一个 API key
3. 双击 `run.bat`（首次会自动建虚拟环境并装依赖）
4. 点击字幕条上的 ⚙ 填入 API key，点 ▶ 开始
5. 播放任意外语视频，字幕条实时出中文

字幕条可拖动到任意位置；鼠标悬停显示控制按钮（▶/■ 开始停止、⚙ 设置、✕ 退出）。

## 常见问题

**国内网络连不上？**
需要代理。程序通过 `websockets` 库读取系统环境变量，启动前设置：
```bat
set HTTPS_PROXY=http://127.0.0.1:7890
run.bat
```
（端口按你的代理软件修改）

**提示 "API key 无效"？**
确认 key 来自 AI Studio（`AIza` 开头），且账号所在区域支持 Gemini API。

**字幕突然停了？**
免费层有 TPM（每分钟 token）限制，触发限流后程序会自动退避重连，稍等即可恢复。

**想翻译自己说的话？**
设置中把音频来源切换为"麦克风"。

## 技术架构

```
WASAPI loopback 采集 → 重采样 16kHz PCM16
  → WebSocket (BidiGenerateContent) → Gemini Live Translate
  → outputTranscription 增量文本 → PySide6 悬浮字幕 HUD
```

| 文件 | 职责 |
|---|---|
| `main.py` | 入口与装配 |
| `config.py` | 配置持久化（`%APPDATA%\live-translate\config.json`） |
| `audio_capture.py` | WASAPI loopback / 麦克风采集 + 重采样 |
| `gemini_client.py` | Live API WebSocket 客户端（自动重连、背压丢帧） |
| `subtitle_hud.py` | 悬浮字幕条 |
| `settings_dialog.py` | 设置窗口 |
