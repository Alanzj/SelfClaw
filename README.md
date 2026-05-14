# SelfClaw · 聚合智能系统

> 多模型聚合、自动降级、CCR 闭环智能体的 AI 对话系统

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│                  Streamlit WebUI (8502)              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ 对话引擎  │ │ 模型管理  │ │ 工具系统  │ │  CCR   │ │
│  │chat_engine│ │model_mgr │ │  tools   │ │ ccr.py │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ │
│       └─────────────┴────────────┴────────────┘      │
│                        │                             │
│              ┌─────────▼──────────┐                  │
│              │   Gateway (19999)   │                  │
│              │   gateway.py        │                  │
│              └─────────┬──────────┘                  │
│                        │                             │
│  ┌─────────┬───────────┼───────────┬─────────┐      │
│  ▼         ▼           ▼           ▼         ▼      │
│ DeepSeek  SiliconFlow  NVIDIA    ZenMux    Mistral  │
│ 智谱GLM   Kimi         MiMo      ...                │
└─────────────────────────────────────────────────────┘
```

## 功能特性

- **多模型聚合** — 支持 DeepSeek、NVIDIA、SiliconFlow、ZenMux、智谱、Kimi、MiMo、Mistral 等多家模型
- **自动降级轮询** — 模型失败自动切换，临时错误两轮重试，永久故障 24h 黑名单，全网崩溃 3min 冷却
- **CCR 闭环智能体** — 集成工具调用、记忆管理、反思修复、策略进化
- **文件操作** — 读取/写入/列出目录，支持 PDF、Word、Excel 解析
- **联网搜索** — 必应+百度双引擎搜索
- **语音交互** — 支持 edge-tts 语音播报 + speech_recognition 语音输入
- **模型管理** — 可视化增删改查、拖拽排序、属地判断
- **多主题** — 深色 / 柔灰 / 浅色三种主题切换
- **CCR HTTP 代理** — 兼容 Claude Code 请求格式，自动转发到 OpenClaw 网关

## 目录结构

```
MyAI_Model/
├── DS/                    # Streamlit UI 模块
│   ├── main.py            # 入口文件
│   ├── config.py          # 配置管理
│   ├── chat_engine.py     # 对话引擎（单次/降级轮询）
│   ├── model_manager.py   # 模型增删改查、黑名单、日志
│   ├── tools.py           # 文件操作、搜索、记忆、插件
│   └── ui.py              # 完整 UI 渲染（CSS + 组件）
├── CCR/                   # CCR 闭环智能体
│   └── ccr.py             # Hermes 智能体 + HTTP 代理
├── gateway.py             # FastAPI 网关（多模型路由）
├── openclaw.json          # 模型池 & Provider 配置
├── start.bat              # Windows 一键启动脚本
├── requirements.txt       # Python 依赖
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

创建 `keys.env` 文件，填入你的 API Key：

```env
DEEPSEEK_API_KEY=sk-xxx
SILICONFLOW_API_KEY=sk-xxx
NVIDIA_API_KEY=nvapi-xxx
ZENMUX_API_KEY=sk-xxx
ZHIPU_API_KEY=xxx
KIMI_API_KEY=sk-xxx
MIMO_API_KEY=sk-xxx
MISTRAL_API_KEY=sk-xxx
```

### 3. 启动

**Windows（推荐）：**
```cmd
start.bat
```

**手动启动：**
```bash
# 启动网关
python gateway.py

# 启动 WebUI
streamlit run DS/main.py --server.port 8502
```

### 4. 访问

打开浏览器访问 `http://127.0.0.1:8502`

## 配置说明

### 模型池 (`openclaw.json`)

```json
{
  "id": "deepseek:v4-flash-paid",
  "actual": "deepseek-v4-flash",
  "provider": "deepseek",
  "label": "DeepSeek V4 Flash",
  "type": "auto",
  "order": 1
}
```

- `id` — 模型唯一标识
- `actual` — 实际模型名称（发给 Provider）
- `provider` — Provider 名称（对应 providers 配置）
- `type` — `auto`（自动降级池）或 `manual`（手动选择）
- `order` — 优先级排序

### Provider 配置

```json
{
  "deepseek": {
    "baseUrl": "https://api.deepseek.com/v1",
    "apiKey": "${DEEPSEEK_API_KEY}",
    "proxy": ""
  }
}
```

- `apiKey` 支持环境变量引用 `${VAR_NAME}`
- `proxy` 为空则使用系统代理

## 技术栈

- **前端**: Streamlit + 自定义 CSS
- **网关**: FastAPI + Uvicorn
- **AI**: OpenAI SDK (兼容多家 Provider)
- **语音**: edge-tts / pyttsx3 / SpeechRecognition
- **搜索**: 必应 + 百度双引擎

## License

MIT
