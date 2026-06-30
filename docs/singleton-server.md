# 单实例 MCP 服务器（Singleton）

多个 Cursor Agent / 会话 / mcporter **互相不知道对方的存在**，但会同时 `spawn` MCP。设计目标：

1. **全局只有一个** Telethon 守护进程（`--serve`）
2. **所有 MCP 客户端只做 stdio 桥接**，不各自登录 Telegram
3. **不以「僵死 pid / 锁文件」冒充运行中** — 以 **端口是否在监听** 为准

## 架构

```
Agent A ──► main.py (桥接) ──┐
Agent B ──► main.py (桥接) ──┼──► http://127.0.0.1:18765/sse ──► main.py --serve (唯一 Telethon)
Agent C ──► main.py (桥接) ──┘
```

| 进程 | 命令行 | Telethon |
|------|--------|----------|
| 守护进程 | `main.py --serve` | **是**（全机唯一） |
| MCP 客户端 | `main.py`（无 `--serve`） | **否** |

## 真相来源（避免不一致状态）

| 信号 | 含义 |
|------|------|
| **`127.0.0.1:PORT` 可连接** | 守护进程 **正在运行**（权威） |
| `daemon-{port}.pid` | 辅助记录；启动时写入，退出 / SIGTERM 时删除 |
| `daemon-{port}.lock` | 守护进程存活期间 flock；**进程死亡后内核自动释放** |
| `spawn-{port}.lock` | 仅用于「谁有权 subprocess 拉起守护进程」，**拉起后立即释放** |

每次桥接或 `--serve` 启动前会执行 `reconcile_stale_state()`：**若 pid 文件中进程已不存在，自动删除 pid 文件**。

> 旧版 bug：桥接进程与守护进程共用同一把锁，桥接等待端口时持有锁，守护进程永远拿不到锁 → 120s 超时。现已拆分为 `spawn` / `daemon` 两把锁。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TELEGRAM_MCP_SINGLETON` | `1` | 启用桥接模式 |
| `TELEGRAM_MCP_AUTO_SPAWN` | `1` | `0` = 客户端**只连接、不拉起**守护进程（多 Agent 推荐） |
| `TELEGRAM_MCP_PORT` | `18765` | SSE 端口 |
| `TELEGRAM_MCP_HOST` | `127.0.0.1` | 绑定地址 |
| `TELEGRAM_MCP_CACHE_DIR` | `~/.cache/telegram-mcp` | 锁、pid、日志 |
| `TELEGRAM_MCP_STARTUP_TIMEOUT` | `120` | 等待端口就绪秒数 |

### 多 Agent 推荐配置

**先单独起一个守护进程**（登录项、tmux、launchd 均可），MCP 客户端禁止自动拉起：

```json
"env": {
  "TELEGRAM_MCP_SINGLETON": "1",
  "TELEGRAM_MCP_AUTO_SPAWN": "0",
  "TELEGRAM_MCP_PORT": "18765"
}
```

守护进程启动一次：

```bash
cd /path/to/telegram-mcp && set -a && source .env && set +a
uv run telegram-mcp-serve
```

之后任意数量的 Agent 只会桥接到已有 SSE，**不会**各自 `Popen --serve`。

若 `AUTO_SPAWN=0` 且守护进程未运行，桥接会明确报错并提示执行 `telegram-mcp-serve`，而不是静默死锁。

### 单用户 / 自动拉起（默认）

`TELEGRAM_MCP_AUTO_SPAWN=1` 时：第一个连上的客户端通过 `spawn.lock` **尝试拉起一次**守护进程；其他并发客户端只 **等待端口**，不会重复 spawn。

## 运行时文件（端口 18765）

日志目录：`~/.cache/telegram-mcp/logs/`（或 `$TELEGRAM_MCP_CACHE_DIR/logs/`）

| 路径 | 说明 |
|------|------|
| `daemon-18765.pid` | 守护进程 PID（进程死后会被 reconcile 清掉） |
| `daemon-18765.lock` | 守护进程 flock |
| `spawn-18765.lock` | 短暂 spawn 协调 |
| `logs/daemon.log` | 守护进程生命周期（每次 `--serve` 新会话会截断重写） |
| `logs/clients.log` | stdio 桥接连接/断开（同一会话内追加） |
| `logs/spawn.log` | 自动拉起 / 等待端口协调 |
| 仓库内 `mcp_errors.log` | **仅** MCP 工具 RPC 错误（不含连接事件） |

每行格式：`ISO时间 [DAEMON|CLIENT|SPAWN] pid=… 事件 key=value …`

旧版 `serve-*.log`、`singleton-*.pid` / `singleton-*.lock` 在守护进程新会话启动时自动清理。

## 验证

```bash
# 应只有 1 行含 --serve
ps -ww -ax -o pid,rss,command | grep 'main.py'

lsof -iTCP:18765 -sTCP:LISTEN

# pid 应对应存活进程
cat ~/.cache/telegram-mcp/daemon-18765.pid
kill -0 $(cat ~/.cache/telegram-mcp/daemon-18765.pid) && echo alive
```

## 清理与重启

```bash
pkill -f 'telegram-mcp.*main.py' || true
rm -f ~/.cache/telegram-mcp/daemon-18765.pid   # 可选；reconcile 也会清僵死 pid

cd /path/to/telegram-mcp && set -a && source .env && set +a
uv run telegram-mcp-serve
```

Cursor：**Reload MCP**。

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| pid 存在但进程不在 | 守护进程被 kill -9 | 已自动 reconcile；或手动删 pid 后重启 serve |
| `daemon not ready` 120s | 旧版锁死锁 / 守护启动失败 | 更新到最新代码；看 `logs/daemon.log`、`logs/spawn.log` |
| 多个 `--serve` | 旧代码或 AUTO_SPAWN 竞态 | 只保留一个；多 Agent 用 `AUTO_SPAWN=0` |
| `No daemon listening` + AUTO_SPAWN=0 | 未先起 serve | `uv run telegram-mcp-serve` |
