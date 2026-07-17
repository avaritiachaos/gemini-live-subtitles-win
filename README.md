# Live Translate — Gemini 实时同传字幕（Windows）

利用 Google AI Studio 免费 API key + `gemini-3.5-live-translate-preview` 模型，
把系统正在播放的任何声音（YouTube、会议、纪录片…）实时翻译成中文悬浮字幕。

- **免费**：AI Studio 免费层该模型不限 RPM/RPD，只限 TPM，个人使用足够
- **免虚拟声卡**：直接用 Windows WASAPI loopback 抓系统声音
- **70+ 语言 → 中文**（目标语言可在设置中切换）

## 功能

| | |
|---|---|
| 🎧 音源 | 系统声音（可指定输出设备）或麦克风 |
| 🌐 双语字幕 | 可选同时显示原文转写 + 译文 |
| 📜 历史记录 | 全部字幕带时间戳，可导出 TXT / SRT |
| 🔇 静音门控 | 无声片段不上传，节省免费配额（VAD） |
| 🖱️ 鼠标穿透 | 字幕条不挡操作，托盘/快捷键解除 |
| 🎨 外观 | 字体大小、字幕颜色、背景不透明度可调 |
| ⌨️ 全局快捷键 | `Ctrl+Alt+T` 开始/停止，`Ctrl+Alt+L` 鼠标穿透 |
| 🔁 稳定性 | 会话到期无缝续连、断网指数退避重连、限流自动等待 |

## 使用步骤

**方式一（推荐小白）**：到 [Releases](../../releases) 下载 `LiveTranslate.exe` 直接运行。

**方式二（从源码）**：
1. 安装 [Python 3.11+](https://www.python.org/downloads/)（勾选 *Add Python to PATH*）
2. 双击 `run.bat`（首次自动建虚拟环境装依赖）

然后：
1. 到 [aistudio.google.com/apikey](https://aistudio.google.com/apikey) 免费创建 API key
2. 点字幕条上的 ⚙ 填入 key，点 ▶ 开始
3. 播放任意外语视频，字幕条实时出中文

字幕条可拖动；鼠标悬停出控制按钮：▶/■ 开始停止、📜 历史、🔒 穿透、⚙ 设置、✕ 退出。
托盘图标提供同样入口。

## 静音观看（不外放声音也要字幕）

字幕来自"系统实际播放的声音"，静音就没有信号。两个办法：

1. 把 Windows 默认输出切到听不见的设备（如没接喇叭的显示器 HDMI）
2. 装免费的 [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)，在 Windows
   "应用音量和设备首选项"里把浏览器输出指到 CABLE Input，
   然后在本程序设置的"监听设备"里选 CABLE Input——喇叭完全安静，字幕照出

## 常见问题

**国内网络连不上？**
需要代理。程序读取系统环境变量，启动前：
```bat
set HTTPS_PROXY=http://127.0.0.1:7890
run.bat
```

**提示 "API key 无效"？**
确认 key 来自 AI Studio（`AIza` 开头），且账号所在区域支持 Gemini API。

**字幕突然显示"X 秒后重连"？**
免费层 TPM 限流或网络抖动，程序会自动退避重连，稍等即可；开启"静音门控"能显著减少限流。

## 看日语直播的推荐设置

1. 设置里的**翻译提示词**填上下文，例如：
   > 音频来自日语直播，输出口语化中文，人名与专有名词保留常用译名，语气词可省略。
2. 直播 BGM 常年不断，**静音门控**基本不会触发，可以关掉（不影响效果）
3. 网络抖动时程序只保留最近 1 秒音频、优先追最新进度，字幕会自动跟上直播，
   不会越落越远
4. 长时间挂机没问题：会话到期自动续连，历史记录照常累积，随时可导出

**进阶（省配额）**：`%APPDATA%\live-translate\config.json` 里把
`response_modality` 改成 `"TEXT"` 可让模型不再生成（本来就没在用的）翻译语音，
显著节省 TPM；若改完连不上说明模型不支持，改回 `"AUDIO"` 即可。

## 技术架构

```
WASAPI loopback / 麦克风 → VAD 门控 → 重采样 16kHz PCM16
  → WebSocket (BidiGenerateContent) → Gemini Live Translate
  → input/outputTranscription 增量文本 → PySide6 悬浮 HUD + 历史存储
```

| 文件 | 职责 |
|---|---|
| `main.py` | 入口、托盘、全局快捷键、装配 |
| `config.py` | 配置持久化（`%APPDATA%\live-translate\config.json`） |
| `audio_capture.py` | WASAPI loopback / 麦克风采集 + VAD + 重采样 |
| `gemini_client.py` | Live API WebSocket 客户端（无缝续连、退避重连、背压丢帧） |
| `subtitle_hud.py` | 悬浮字幕条（双语、穿透、自定义外观） |
| `history.py` | 历史存储 + 查看窗口 + TXT/SRT 导出 |
| `settings_dialog.py` | 设置窗口 |

## 打包 exe

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconsole --onefile --name LiveTranslate main.py
```
产物在 `dist\LiveTranslate.exe`。
