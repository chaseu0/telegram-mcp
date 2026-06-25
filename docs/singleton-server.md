# 单实例 MCP 服务器（Singleton）

并行任务（多个 Cursor Agent、mcporter、MCP 客户端）若各自 `spawn` 一个 `telegram-mcp`，会各自连接 Telethon，共用同一 session 时常见：

- `sqlite3.OperationalError: database is locked`（文件 session）
- Auth key 冲突、消息状态错乱
- `ps` 中出现大量 `python main.py` 进程

**默认开启单实例模式**（`TELEGRAM_MCP_SINGLETON=1`）：全局只有 **一个** 带 Telethon 的守护进程；每个 MCP 客户端进程只做 **stdio ↔ SSE 桥接**，不再重复登录 Telegram。

## 架构

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Cursor #1   │  │ Cursor #2   │  │ mcporter    │
│ main.py     │  │ main.py     │  │ main.py     │
│ (stdio桥接) │  │ (stdio桥接) │  │ (stdio桥接) │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       └────────────────┼────────────────┘
                        │ HTTP SSE
                        ▼
              ┌─────────────────────┐
              │ main.py --serve     │
              │ Telethon + MCP SSE  │
              │ 127.0.0.1:18765     │
              └─────────────────────┘
```

| 进程命令行 | 角色 | 是否连接 Telethon |
|-----------|------|-------------------|
| `python main.py --serve` | 守护进程 | **是**（唯一） |
| `python main.py`（无 `--serve`） | stdio 桥接 | **否**（转发到 SSE） |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TELEGRAM_MCP_SINGLETON` | `1` | `1` 启用单实例；`0` 恢复旧行为（每进程独立 Telethon，易冲突） |
| `TELEGRAM_MCP_PORT` | `18765` | SSE 监听端口 |
| `TELEGRAM_MCP_HOST` | `127.0.0.1` | 绑定地址（仅本机） |
| `TELEGRAM_MCP_CACHE_DIR` | `~/.cache/telegram-mcp` | 锁文件、pid、守护进程日志 |
| `TELEGRAM_MCP_STARTUP_TIMEOUT` | `120` | 等待守护进程就绪的秒数 |

运行时文件（以默认端口为例）：

| 路径 | 内容 |
|------|------|
| `~/.cache/telegram-mcp/singleton-18765.lock` | 守护进程持有 flock |
| `~/.cache/telegram-mcp/singleton-18765.pid` | 守护进程 PID |
| `~/.cache/telegram-mcp/serve-18765.log` | 守护进程 stdout/stderr |

## MCP 客户端配置（推荐）

`mcp.json` **无需改成 URL**，继续用 `command` + `uv run main.py` 即可。首次连接会自动拉起守护进程。

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/full/path/to/telegram-mcp",
        "run",
        "main.py"
      ],
      "env": {
        "TELEGRAM_API_ID": "your_api_id",
        "TELEGRAM_API_HASH": "your_api_hash",
        "TELEGRAM_SESSION_STRING": "your_session_string",
        "TELEGRAM_MCP_SINGLETON": "1",
        "TELEGRAM_MCP_PORT": "18765"
      }
    }
  }
}
```

建议 **优先使用 `TELEGRAM_SESSION_STRING`**（StringSession），避免文件 session 与多进程历史问题。

## 手动运维

### 启动守护进程（可选）

首次有 MCP 客户端连接时会自动拉起；也可手动先起：

```bash
cd /path/to/telegram-mcp
set -a && source .env && set +a
uv run main.py --serve
# 或
uv run telegram-mcp-serve
```

stderr 示例：

```
telegram-mcp singleton SSE server at http://127.0.0.1:18765/sse (pid 61459)
```

### 验证进程形态

```bash
ps -ww -ax -o pid,rss,command | grep 'telegram-mcp\|MCP-AI/telegram-mcp.*main.py'
lsof -iTCP:18765 -sTCP:LISTEN
cat ~/.cache/telegram-mcp/singleton-18765.pid
```

**健康形态**：

- **恰好 1 个** 含 `--serve` 的 `main.py`（RSS 通常 30–50 MB，含 Telethon）
- 若干 **无** `--serve` 的 `main.py`（桥接，RSS 通常 10–20 MB）
- 端口 `18765` 仅被守护进程 PID 监听

### 清理并重启

```bash
# 停掉全部实例（含守护进程与桥接）
pkill -f 'telegram-mcp.*main.py' || true
pkill -f 'MCP-AI/telegram-mcp' || true

# 重新拉起守护进程
cd /path/to/telegram-mcp && set -a && source .env && set +a && uv run main.py --serve
```

然后在 Cursor 中 **Reload MCP** 或重启窗口。

### 关闭单实例（调试用）

```bash
TELEGRAM_MCP_SINGLETON=0 uv run main.py
# 或
uv run main.py --stdio-direct
```

每个 MCP 客户端将独立连接 Telethon——**不要**在并行任务下对同一 session 使用此模式。

## mcporter

```bash
mcporter call telegram-mcp.get_me 2>/dev/null
```

mcporter 配置的 `telegram-mcp` 命令与 Cursor 相同；单实例逻辑在 `main.py` 入口统一处理。

## 错误与 RPC 反馈

工具失败时返回多行可读信息（不再只有 `GEN-ERR-xxx`）：

```
Error in create_channel (code: GEN-ERR-050)
Type: UserRestrictedError
Message: You're spamreported, you can't create channels or chats.
Guidance: 账号被 Telegram 限制...请联系 @SpamBot...
```

完整 traceback 在仓库目录下的 `mcp_errors.log`。

常见 RPC 与处理见 [skills/telegram-jisou-group-search/SKILL.md](../skills/telegram-jisou-group-search/SKILL.md) 风控表，或 Telegram [Spam FAQ](https://telegram.org/faq_spam)。

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 多个 `main.py` 且都无 `--serve` | 旧进程未清、或未更新到含 singleton 的代码 | `pkill` 后重装/指向最新 fork，`Reload MCP` |
| 多个 `--serve` | 端口冲突或重复手动启动 | 只保留一个，改 `TELEGRAM_MCP_PORT` |
| `singleton server did not become ready` | 守护进程启动失败 | 查看 `serve-<port>.log`、`mcp_errors.log` |
| `database is locked` | 仍有多 Telethon 或文件 session 冲突 | 确认 singleton=1，换 StringSession |
| 桥接正常但工具超时 | 守护进程 Telethon 冷启动 / FloodWait | 先手动 `--serve` 预热，降低调用频率 |
