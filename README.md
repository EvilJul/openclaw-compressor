# openclaw-compressor

可插拔的上下文压缩 MCP Server，适用于 OpenClaw / Claude Code 等基于 MCP 协议的 AI 编程助手。

通过 MCP 协议接入，零侵入，不修改任何宿主程序代码。

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

### 2. 注册 MCP Server

编辑 `~/.claude/settings.json`（全局）或 `.claude/settings.json`（项目级）：

```json
{
  "mcpServers": {
    "context-compressor": {
      "command": "openclaw-compressor"
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

环境变量说明：

| 变量 | 说明 |
|------|------|
| ANTHROPIC_API_KEY | 使用 Claude 系列模型时必填 |
| OPENAI_API_KEY | 使用 GPT / o 系列模型时必填 |
| OPENCLAW_COMPRESSOR_MODEL | 可选，LLM 策略的默认模型（优先级高于工具参数） |
| OPENCLAW_COMPRESSOR_PROVIDER | 可选，强制指定 provider（anthropic / openai），通常无需设置，会根据模型名自动推断 |

### 3. 在 CLAUDE.md 中添加使用指引

在项目根目录的 `CLAUDE.md` 中添加以下内容，让模型知道何时、如何调用压缩工具：

```markdown
## Context Compression

本项目配置了 openclaw-compressor MCP Server，提供智能上下文压缩能力。

当对话变长、上下文接近限制时，按以下步骤操作：

1. 调用 mcp__context-compressor__analyze_context 检查当前 session 的 token 用量
   - 参数 session_path：当前 session 文件路径
2. 如果返回建议压缩，先调用 mcp__context-compressor__preview_compression 预览摘要
   - 参数 strategy：推荐 smart_local
3. 确认摘要合理后，调用 mcp__context-compressor__compress_session 执行压缩
   - 参数 strategy：smart_local（默认）或 llm（复杂场景）
   - 使用 llm 策略时，传入 model 参数指定模型（如 claude-sonnet-4-20250514、gpt-4o）

注意事项：
- 压缩后旧消息被替换为结构化摘要，最近 4 条消息原样保留
- smart_local 策略零成本零延迟，适合绝大多数场景
- llm 策略调用 LLM 生成语义级摘要，支持 Anthropic 和 OpenAI 模型，适合复杂多步推理场景
- 压缩前务必先 preview_compression 确认摘要质量
```

完成以上 3 步后，重启会话即可生效。

---

## 工作原理

```
用户 <-> AI 编程助手 <-> MCP Protocol (stdio) <-> openclaw-compressor
                                                        |
                                                  读写 session.json
                                                  (直接操作磁盘文件)
```

插件作为独立进程运行，通过 MCP stdio 协议与宿主通信。当模型调用压缩工具时，插件直接读取 session JSON 文件，执行压缩算法，将结果写回磁盘。整个过程不经过宿主 runtime，不修改任何原生代码。

Session 文件默认位置：`~/.claude/sessions/<session-id>.json`

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
Tools: Bash, Read, EditRECOMMENDATION: Compress now (tokens 15,230 >= threshold 10,000)
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

## Session JSON 格式

插件直接读写的 session 文件格式，与 OpenClaw Rust 端 session.rs 完全对齐：

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

压缩后第一条消息变为 system 角色的摘要，后面跟着原样保留的最近 N 条消息。

---

## 项目结构

```
openclaw-compressor/
├── pyproject.toml                  # 包定义、依赖、CLI 入口点
├── LICENSE                         # MIT
├── README.md
├── .gitignore
├── openclaw_compressor/            # 主包
│   ├── __init__.py                 # 版本号
│   ├── session.py                  # Session/Message/ContentBlock 数据结构
│   ├── strategies.py               # 压缩策略（Local / SmartLocal / LLM）
│   └── server.py                   # MCP Server 入口
└── tests/
    ├── __init__.py
    ├── test_session.py             # session 读写、序列化测试
    └── test_strategies.py          # 压缩策略、阈值判断、摘要内容测试
```

### 模块职责

**session.py** — 数据层
- ContentBlock / Message / Session 三层数据结构
- JSON 序列化/反序列化，与 Rust session.rs 格式对齐
- Token 估算：字符数 / 4 + 1（与 Rust 端一致）

**strategies.py** — 算法层
- CompactionStrategy 抽象基类，定义 summarize() + compact() 模板方法
- compact() 流程：判断阈值 -> 切分消息 -> summarize() -> 包装延续消息 -> 组装新 Session
- 三种策略实现 + get_strategy() 工厂函数

**server.py** — 接口层
- 基于 mcp 库的 stdio server
- 三个工具注册：analyze_context / compress_session / preview_compression
- session 路径解析：支持绝对路径和 session ID

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
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['openclaw_compressor/session.py','openclaw_compressor/strategies.py','openclaw_compressor/server.py']]"
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
ls ~/.claude/sessions/             # 列出所有 session
# 在 OpenClaw 中输入 /status 查看当前 session 路径
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
cp ~/.claude/sessions/<id>.json ~/.claude/sessions/<id>.json.bak
# 或先用 preview_compression 预览
```

## License

MIT
