# Virtual AI Companion （虚拟 AI 伙伴）

一个基于 Live2D 的桌面 AI 伙伴，拥有虚拟形象、语音交互、屏幕感知和桌面操控能力。

> 🎭 默认角色：**芙宁娜**（Furina，来自《原神》），带有完整的 Live2D 模型、表情和动作动画。

---

## ✨ 功能特性

| 功能                        | 说明                                                                 |
| --------------------------- | -------------------------------------------------------------------- |
| 🎭**Live2D 虚拟形象** | 实时渲染的 Live2D 角色，支持表情切换、口型同步、待机动画、眨眼       |
| 🎤**语音输入**        | 麦克风 + 系统音频采集，内置 VAD（语音活动检测）自动识别说话段落      |
| 🧠**语音识别 (STT)**  | 基于本地 faster-whisper 模型，支持中英文，无需联网                   |
| 🤖**智能对话 (LLM)**  | 支持 DeepSeek API / OpenAI API / 本地 Ollama 多种后端                |
| 🔊**语音合成 (TTS)**  | 基于 edge-tts，音色自然，附带口型同步数据驱动 Live2D 嘴唇            |
| 🖥️**屏幕感知**      | 截屏 + OCR 文字提取，AI 可以「看到」你屏幕上的内容并据此回复         |
| 🖱️**桌面操控**      | AI 可通过输出标签执行：打开应用、网页搜索、打字、按键、鼠标点击/滚动 |
| 💾**长期记忆**        | SQLite 存储对话历史 + ChromaDB 语义搜索，自动提取用户信息            |
| 💬**文字聊天**        | 内置聊天面板，支持语音/纯文字模式切换                                |
| 🪟**透明悬浮窗**      | 无边框、置顶、半透明窗口，Click-through 穿透点击                     |

---

## 🏗️ 项目架构

```
virtual_ai_companion/
├── main.py                 # 程序入口，初始化和生命周期管理
├── config.yaml             # 全局配置文件
├── requirements.txt        # Python 依赖
│
├── core/
│   ├── controller.py       # 主控制器，异步状态机（IDLE→LISTENING→THINKING→EXECUTING→SPEAKING）
│   └── actions.py          # 桌面操控执行器（打开应用、搜索、点击、按键等）
│
├── ai/
│   ├── llm.py              # LLM 客户端，支持 DeepSeek/OpenAI/Ollama
│   ├── stt.py              # 语音识别 (faster-whisper)
│   ├── tts.py              # 语音合成 (edge-tts) + 口型同步
│   └── memory.py           # 长期记忆（SQLite + ChromaDB 语义搜索）
│
├── capture/
│   ├── audio.py            # 音频采集 + 能量型 VAD
│   ├── screen.py           # 屏幕截图 (mss)
│   └── ocr.py              # 屏幕文字提取 (easyocr)
│
├── live2d/
│   ├── window.py           # 透明悬浮窗（PyQt5 + QWebEngineView）
│   ├── bridge.py           # Python ↔ JavaScript 双向通信桥 (QWebChannel)
│   └── web/
│       ├── index.html      # 前端页面
│       ├── app.js          # Live2D 渲染控制器 (PIXI.js + Cubism SDK)
│       ├── pixi.min.js     # PIXI.js v7 渲染引擎
│       ├── pixi-live2d-display.min.js  # Live2D PIXI 插件
│       ├── live2dcubismcore.min.js     # Live2D Cubism Core SDK
│       └── qwebchannel.js  # Qt WebChannel JS 库
│
├── models/
│   ├── Furina/             # 芙宁娜 Live2D 模型（模型文件、纹理、物理、动作、表情）
│   └── whisper-medium/     # 本地 Whisper 语音识别模型
│
└── data/
    ├── memory.db           # SQLite 对话记忆数据库
    └── chroma/             # ChromaDB 向量语义搜索
```

### 核心交互流程

```
麦克风/文字 ──→ STT / 跳过 ──→ 记忆召回 ──→ LLM 推理 ──→ 动作解析 ──→ TTS + 口型同步
     ↑                              │                  │               │
     │                        屏幕OCR上下文        [ACTION:xxx]      Live2D表情+嘴唇
     │                              │                  │               │
     └────────────────────────── 循环返回 IDLE ─────────────────────────┘
```

---

## 🚀 快速开始

### 1. 环境要求

- **Python** ≥ 3.12
- **Windows 10/11**（Live2D 透明窗口依赖 Win32 API）
- **语音输入**：麦克风设备
- **LLM API**：DeepSeek API Key（[免费注册](https://platform.deepseek.com)），或本地 Ollama

### 2. 安装依赖

```bash
cd virtual_ai_companion
pip install -r requirements.txt
```

> 💡 `pyaudio` 在 Windows 上通过 pip 直接安装 wheel 包，无需手动安装 PortAudio。

### 3. 配置

编辑 `config.yaml`，主要修改：

```yaml
llm:
  provider: "openai"                          # 或 "ollama"
  api_key: "sk-your-deepseek-api-key"         # DeepSeek API Key
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-flash"                  # 或 deepseek-chat
```

其他可选配置见 [config.yaml](config.yaml) 中的完整注释。

### 4. 运行

```bash
python main.py
```

启动后：

- 桌面右上角会出现芙宁娜的 Live2D 悬浮窗
- 对着麦克风说话，AI 会自动识别并回复（语音 + 口型同步）
- 点击左下角 💬 按钮可打开文字聊天面板
- 点击角色可选择模型（拖拽移动/滚轮缩放），点击空白区域可选择窗口

---

## 🎮 操作指南

| 操作              | 方式                           |
| ----------------- | ------------------------------ |
| 语音对话          | 直接对着麦克风说话（自动 VAD） |
| 文字对话          | 点击 💬 → 输入文字 → 发送    |
| 语音/文字模式切换 | 聊天面板中点击 🔊/📝 按钮      |
| 移动角色          | 点击角色选中 → 拖拽/方向键    |
| 缩放角色          | 选中角色 → 滚轮 / +/- 键      |
| 移动窗口          | 点击空白选中窗口 → 拖拽       |
| 调整窗口大小      | 选中窗口 → 滚轮               |
| 重置              | 选中后按`0`                  |
| 取消选中          | 按`Esc`                      |

---

## 🔧 配置说明

### LLM 提供商选择

| 提供商                     | 优点                     | 缺点                  |
| -------------------------- | ------------------------ | --------------------- |
| **DeepSeek**（推荐） | 中文优秀、便宜、无需 GPU | 需要 API Key 和网络   |
| **Ollama**           | 完全本地免费、离线可用   | 需要 GPU 加速、模型大 |
| **OpenAI**           | 效果最好                 | 较贵                  |

切换到 Ollama：

```yaml
llm:
  provider: "ollama"
  host: "http://localhost:11434"
  model: "llava:13b"
  vision_enabled: true
```

### 语音识别模型

默认使用本地 `models/whisper-medium/`，也可自动下载：

```yaml
stt:
  model_path: null          # 设为 null 自动下载
  model_size: "small"       # tiny/base/small/medium/large-v3
  device: "cpu"             # 或 "cuda"（需要 CUDA 12）
  language: "zh"            # 中文识别
```

### 桌面操控

AI 可以通过 `[ACTION:type:args]` 标签操控桌面。默认**开启但需要确认**：

```yaml
computer_control:
  enabled: true
  confirm_before_execute: true   # 设为 false 自动执行
```

示例对话：

- 用户："帮我打开记事本" → AI 输出：`好的~ [ACTION:open_app:notepad]`
- 用户："搜索天气预报" → AI 输出：`帮你查！[ACTION:search_web:天气预报]`

---

## 📦 模型文件

### Live2D 模型

本项目使用芙宁娜（Furina）Live2D 模型，位于 `models/Furina/`，包含：

- 模型本体 (`.moc3`)
- 纹理贴图 (`.png`)
- 物理模拟 (`.physics3.json`)
- 17 个表情（星星、哭、生气、汗、猫猫嘴 等）
- 多个动作动画（待机、摊手、变芒、变荒）

### Whisper 模型

语音识别使用 faster-whisper 本地模型，可放置在 `models/whisper-medium/`。

---

## 🛠️ 技术栈

| 层级       | 技术                                   |
| ---------- | -------------------------------------- |
| GUI 框架   | PyQt5 + QWebEngineView                 |
| 2D 渲染    | PIXI.js v7 + Live2D Cubism SDK 4       |
| 前后端通信 | QWebChannel (Qt ↔ JavaScript)         |
| 语音识别   | faster-whisper (CTranslate2)           |
| 语音合成   | edge-tts + ffmpeg (via imageio-ffmpeg) |
| LLM        | DeepSeek API / OpenAI API / Ollama     |
| OCR        | easyocr (PyTorch)                      |
| 向量记忆   | ChromaDB + SQLite WAL                  |
| 屏幕截图   | mss                                    |
| 桌面操控   | pyautogui                              |
| 音频       | pyaudio + sounddevice                  |

---

## ⚠️ 注意事项

1. **首次运行 OCR 较慢**：easyocr 初始化需要 10-30 秒，首次运行请耐心等待
2. **DeepSeek API 需网络**：默认使用云端 LLM，需保持网络连接
3. **CUDA 冲突**：STT 的 CTranslate2 和 OCR 的 PyTorch 不建议同时使用 GPU，推荐都用 CPU
4. **安全警告**：开启 `computer_control` 后 AI 可以操控你的电脑，建议保持 `confirm_before_execute: true`
5. **管理员权限**：部分桌面操控功能（如点击其他窗口）可能需要管理员权限
