# openclaw-compressor

可插拔的上下文压缩 MCP Server，适用于任何基于 MCP 协议的 AI 编程助手。

支持的宿主环境：
- **OpenClaw** (`~/.openclaw/`)
- **Claude Code** (`~/.claude/`)
- **Cline** (`~/.cline/`)
- 任意 MCP 宿主（通过环境变量 `OPENCLAW_COMPRESSOR_SESSION_DIR` 指定 session 目录）

通过 MCP 协议接入，零侵入，不修改任何宿主程序代码。

---

## 快速开始

### 1. 安装

```bash
# 从源码安装
git clone https://github.com/EvilJul/openclaw-compressor.git
cd openclaw-compressor
pip install -e .

# 按需安装 LLM provider
pip install -e ".[anthropic]"   # Anthropic (Claude 系列)
pip install -e ".[openai]"     # OpenAI (GPT / o 系列)
pip install -e ".[llm]"        # 全部 provider
```

安装后验证命令可用：

```bash
openclaw-compressor --help
```

如果提示 `command not found`，用 `python3 -m openclaw_compressor.server` 替代。

### 2. 自动配置（推荐）

运行 setup 命令，自动检测已安装的宿主环境并注册 MCP Server：

```bash
openclaw-compressor setup
```

setup 会：
1. 扫描系统中已安装的 MCP 宿主（OpenClaw、Claude Code、Cline）
2. 显示检测结果和生成的配置
3. 询问是否自动写入对应宿主的配置文件

### 3. 手动配置

如果 setup 不适用，手动编辑宿主的配置文件。

**OpenClaw** — 编辑 `~/.openclaw/settings.json`：

```json
{
  "mcpServers": {
    "context-compressor": {
      "command": "openclaw-compressor"
    }
  }
}
```

**Claude Code** — 编辑 `~/.claude/settings.json`：

```json
{
  "mcpServers": {
    "context-compressor": {
      "command": "openclaw-compressor"
    }
  }
}
```

**Cline** — 编辑 `~/.cline/settings.json`：

```json
{
  "mcpServers": {
    "context-compressor": {
      "command": "openclaw-compressor"
    }
  }
}
```

**其他 MCP 宿主** — 在宿主的 MCP 配置中添加同样的 server 定义，并设置环境变量指定 session 目录：

```json
{
  "mcpServers": {
    "context-compressor": {
      "command": "openclaw-compressor",
      "env": {
        "OPENCLAW_COMPRESSOR_SESSION_DIR": "/path/to/your/sessions"
      }
    }
  }
}
```

备选：用 python 直接启动

```json
{
  "mcpServers": {
    "context-compressor": {
      "command": "python3",
      "args": ["-m", "openclaw_compressor.server"]
    }
  }
}
```

使用 LLM 策略时需传入对应 provider 的 API Key，可选配置默认模型：

```json
{
  "mcpServers": {
    "context-compressor": {
      "command": "openclaw-compressor",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "OPENAI_API_KEY": "sk-...",
        "OPENCLAW_COMPRESSOR_MODEL": "claude-sonnet-4-20250514"
      }
    }
  }
}
```

### 环境变量

| 变量 | 说明 |
|------|------|
| OPENCLAW_COMPRESSOR_SESSION_DIR | 自定义 session 目录路径（优先级最高，适用于非标准宿主） |
| ANTHROPIC_API_KEY | 使用 Claude 系列模型时必填 |
| OPENAI_API_KEY | 使用 GPT / o 系列模型时必填 |
| OPENCLAW_COMPRESSOR_MODEL | 可选，LLM 策略的默认模型（优先级高于工具参数） |
| OPENCLAW_COMPRESSOR_PROVIDER | 可选，强制指定 provider（anthropic / openai），通常无需设置 |

### 4. 让 AI 自动调用压缩（推荐）

MCP 协议本身没有自动触发机制，需要通过项目级指令让 AI 主动调用。根据你使用的宿主，将以下内容添加到对应的指令文件中：

| 宿主 | 指令文件 |
|------|----------|
| OpenClaw | 项目根目录 `AGENTS.md`（或 `~/clawd/AGENTS.md`） |
| Claude Code | 项目根目录 `CLAUDE.md` |
| Cline | 项目根目录 `.clinerules` |

将以下内容复制到对应文件中：

```markdown
## Auto Context Compression（自动上下文压缩）

本项目配置了 openclaw-compressor MCP Server。你必须遵守以下自动压缩规则：

### 自动触发条件（满足任一即执行）

- 对话消息数超过 20 条
- 你感知到上下文变长、响应变慢、或接近上下文窗口限制
- 用户开始全新的任务方向，旧上下文已不再相关

### 压缩流程

1. 调用 mcp__context-compressor__analyze_context 检查 token 用量
   - 参数 session_path：当前 session 文件路径或 ID
2. 如果返回 RECOMMENDATION: Compress now，直接调用 mcp__context-compressor__compress_session 执行压缩
   - 参数 strategy：smart_local（默认，零成本零延迟）
   - 复杂多步推理场景可用 llm 策略，需传入 model 参数
3. 压缩完成后简要告知用户压缩结果和压缩比
4. 如果 analyze_context 返回 OK（有余量），跳过压缩，不要打扰用户

### 规则

- 不要在压缩前询问用户是否需要压缩，直接执行
- 压缩保留最近 4 条消息，不会丢失当前工作上下文
- 优先使用 smart_local 策略
```

完成配置后，重启会话即可生效。AI 会在对话变长时自动检测并压缩，无需手动触发。

---

## Session 路径解析

工具接受 `session_path` 参数，支持以下格式：

1. **绝对路径** — 直接指向 session 文件，如 `/home/user/.openclaw/sessions/abc123.json`
2. **Session ID** — 自动在已知目录中搜索，如 `abc123`

搜索顺序：
1. `OPENCLAW_COMPRESSOR_SESSION_DIR` 环境变量指定的目录（最高优先级）
2. `~/.openclaw/sessions/`（OpenClaw）
3. `~/.claude/sessions/`（Claude Code）
4. `~/.cline/sessions/`（Cline）

只搜索实际存在的目录。如果所有已知目录都不存在，工具会返回明确的错误提示，引导用户设置 `OPENCLAW_COMPRESSOR_SESSION_DIR` 或传入绝对路径。

---

## 工作原理

```
用户 <-> AI 编程助手 <-> MCP Protocol (stdio) <-> openclaw-compressor
                                                        |
                                                  读写 session.json
                                                  (直接操作磁盘文件)
```

插件作为独立进程运行，通过 MCP stdio 协议与宿主通信。当模型调用压缩工具时，插件直接读取 session JSON 文件，执行压缩算法，将结果写回磁盘。整个过程不经过宿主 runtime，不修改任何原生代码。

Session 文件位置取决于宿主环境，通过多目录探测自动发现。

---

## MCP 工具

### analyze_context

分析 session 的上下文使用情况。返回消息数、估算 token 数、角色分布、使用的工具列表，以及是否建议压缩。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_path | string | 是 | - | session 文件路径或 session ID |
| max_estimated_tokens | integer | 否 | 10000 | 压缩建议的 token 阈值 |

返回示例：

```
Session: abc123.json
Messages: 47
Estimated tokens: 15,230
Roles: user=12, assistant=18, tool=17
Tools: Bash, Read, Edit
RECOMMENDATION: Compress now (tokens 15,230 >= threshold 10,000)
```

### compress_session

执行压缩。将旧消息替换为结构化摘要，保留最近 N 条消息，写回磁盘。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_path | string | 是 | - | session 文件路径或 session ID |
| strategy | string | 否 | smart_local | 压缩策略：local / smart_local / llm |
| model | string | 否 | - | LLM 模型 ID（llm 策略时使用，如 claude-sonnet-4-20250514、gpt-4o） |
| preserve_recent_messages | integer | 否 | 4 | 保留最近消息数 |
| max_estimated_tokens | integer | 否 | 10000 | 触发压缩的 token 阈值 |

> model 优先级：环境变量 `OPENCLAW_COMPRESSOR_MODEL` > 工具参数 `model`。使用 llm 策略时至少需要提供其中一个。

返回示例：

```
Compressed session: abc123.json
Strategy: smart_local
Messages: 47 -> 5
Removed: 43 messages
Preserved: 4 recent messages
Tokens: 15,230 -> 2,100 (86% reduction)
```

### preview_compression

干跑模式。返回压缩预览（生成的摘要和统计），不修改任何文件。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| session_path | string | 是 | - | session 文件路径或 session ID |
| strategy | string | 否 | smart_local | 压缩策略 |
| model | string | 否 | - | LLM 模型 ID（llm 策略时使用） |
| preserve_recent_messages | integer | 否 | 4 | 保留最近消息数 |

---

## 压缩策略

### local

完全复刻 OpenClaw 内置 compact.rs 的逻辑。确定性，零成本，无 LLM 调用。

- 最近 3 条用户请求，截断到 160 字符
- 最多 8 个关键文件（仅 rs/ts/tsx/js/json/md）
- 5 个待办关键词（todo/next/pending/follow up/remaining）
- 逐条消息时间线，每条截断 160 字符

### smart_local（推荐）

增强版本地策略，摘要信息量更大、结构更清晰。

- 最近 5 条用户请求（vs 内置 3 条）
- 最多 12 个关键文件（vs 内置 8 个）
- 13 种文件扩展名（增加 py/go/java/toml/yaml/yml/jsx）
- 7 个待办关键词（增加 fixme/hack）
- 工具调用链分组：`call(Read) -> [ok] file content...`
- 错误检测：标记失败的工具调用
- 当前工作推断截断到 300 字符（vs 内置 200 字符）

### llm

先用 smart_local 提取结构化信息，再发给 LLM 生成语义级摘要。支持 Anthropic（Claude 系列）和 OpenAI（GPT / o 系列）模型，根据模型名自动推断 provider。

- 理解对话意图，摘要质量最高
- 需要对应 provider 的 API Key 环境变量
- 需要安装对应 provider 依赖：`pip install openclaw-compressor[anthropic]` 或 `pip install openclaw-compressor[openai]`
- 模型通过工具参数 `model` 或环境变量 `OPENCLAW_COMPRESSOR_MODEL` 指定
- 原始对话超过 20,000 字符时自动截断

---

## Session 文件格式

插件支持两种 session 文件格式，自动检测：

### JSON 格式（Claude Code / Cline）

文件扩展名 `.json`，整个文件是一个 JSON 对象：

```json
{
  "version": 1,
  "messages": [
    {
      "role": "user",
      "blocks": [
        {"type": "text", "text": "帮我修复 login 页面的 bug"}
      ]
    },
    {
      "role": "assistant",
      "blocks": [
        {"type": "text", "text": "Let me read the file."},
        {"type": "tool_use", "id": "tool-1", "name": "Read", "input": "{\"path\":\"src/login.ts\"}"}
      ],
      "usage": {
        "input_tokens": 150,
        "output_tokens": 30,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0
      }
    },
    {
      "role": "tool",
      "blocks": [
        {
          "type": "tool_result",
          "tool_use_id": "tool-1",
          "tool_name": "Read",
          "output": "export function login()...",
          "is_error": false
        }
      ]
    }
  ]
}
```

### JSONL 格式（OpenClaw 2026.3.13+）

文件扩展名 `.jsonl`，每行一个独立 JSON 对象：

```jsonl
{"type":"session","id":"abc123","cwd":"/project","timestamp":"2026-03-13T10:00:00Z"}
{"type":"message","message":{"role":"user","content":"帮我修复 login 页面的 bug"}}
{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"Let me read the file."},{"type":"tool_use","id":"tool-1","name":"Read","input":"{}"}]}}
{"type":"compaction","summary":"用户请求修复 login 页面 bug，助手读取了 src/login.ts 并完成修复。"}
{"type":"message","message":{"role":"user","content":"继续下一个任务"}}
```

JSONL 格式中的 `compaction` 条目会在压缩 round-trip 中保真还原，不会丢失类型信息。

压缩后第一条消息变为 system 角色的摘要，后面跟着原样保留的最近 N 条消息。

---

## 项目结构

```
openclaw-compressor/
├── pyproject.toml                  # 包定义、依赖、CLI 入口点
├── LICENSE                # MIT
├── README.md
├── .gitignore
├── openclaw_compressor/            # 主包
│   ├── __init__.py                 # 版本号
│   ├── hosts.py                    # 多宿主探测、session 路径解析、自动配置
│   ├── session.py                  # Session/Message/ContentBlock 数据结构
│   ├── strategies.py               # 压缩策略（Local / SmartLocal / LLM）
│   └── server.py                   # MCP Server 入口
└── tests/
    ├── __init__.py
    ├── test_hosts.py               # 多宿主探测、路径解析测试
    ├── test_session.py             # session 读写、序列化测试
    └── test_strategies.py          # 压缩策略、阈值判断、摘要内容测试
```

### 模块职责

**hosts.py** — 宿主适配层
- 多宿主 session 目录探测（OpenClaw / Claude Code / Cline / 自定义）
- session 路径解析：支持绝对路径和 session ID，自动搜索已知目录
- 自动配置：检测宿主环境，生成并写入 MCP 配置
- setup 交互式命令

**session.py** — 数据层
- ContentBlock / Message / Session 三层数据结构
- 支持 JSON（Claude Code / Cline）和 JSONL（OpenClaw 2026.3.13+）两种格式，自动检测
- JSONL compaction 条目 round-trip 保真
- Token 估算：字符数 / 4 + 1（与 Rust 端一致）

**strategies.py** — 算法层
- CompactionStrategy 抽象基类，定义 summarize() + compact() 模板方法
- compact() 流程：判断阈值 -> 切分消息 -> summarize() -> 包装延续消息 -> 组装新 Session
- 三种策略实现 + get_strategy() 工厂函数

**server.py** — 接口层
- 基于 mcp 库的 stdio server
- 三个工具注册：analyze_context / compress_session / preview_compression
- session 路径解析委托给 hosts.py
- 支持 `setup` 子命令

---

## 开发

```bash
git clone https://github.com/EvilJul/openclaw-compressor.git
cd openclaw-compressor

# 安装开发依赖
pip install -e ".[dev,llm]"

# 运行测试
pytest

# 语法检查
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['openclaw_compressor/hosts.py','openclaw_compressor/session.py','openclaw_compressor/strategies.py','openclaw_compressor/server.py']]"
```

### 自定义策略

继承 CompactionStrategy，实现 summarize() 方法即可：

```python
from openclaw_compressor.strategies import CompactionStrategy
from openclaw_compressor.session import Message

class MyStrategy(CompactionStrategy):
    def summarize(self, messages: list[Message]) -> str:
        return "My custom summary..."
```

compact() 模板方法自动处理阈值判断、消息切分、延续消息包装。

---

## 故障排查

**插件未被识别**

```bash
which openclaw-compressor          # 确认命令可用
pip show openclaw-compressor       # 检查安装位置
```

**Session 文件找不到**

```bash
# 检查各宿主的 session 目录是否存在
ls ~/.openclaw/sessions/           # OpenClaw
ls ~/.claude/sessions/             # Claude Code
ls ~/.cline/sessions/              # Cline

# 或设置自定义 session 目录
export OPENCLAW_COMPRESSOR_SESSION_DIR=/path/to/sessions

# 运行 setup 查看检测结果
openclaw-compressor setup
```

**LLM 策略报错**

```bash
# 确认安装了对应 provider 依赖
pip install openclaw-compressor[anthropic]   # Claude 系列
pip install openclaw-compressor[openai]      # GPT / o 系列

# 确认 API Key 已设置
echo $ANTHROPIC_API_KEY
echo $OPENAI_API_KEY

# 确认指定了模型（二选一）
echo $OPENCLAW_COMPRESSOR_MODEL              # 环境变量方式
# 或在工具调用时传入 model 参数
```

**压缩后行为异常**

```bash
# 建议压缩前备份
cp ~/.openclaw/sessions/<id>.json ~/.openclaw/sessions/<id>.json.bak
# 或先用 preview_compression 预览
```

## License

MIT
