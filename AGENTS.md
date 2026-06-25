# telegram-mcp — Agent 指引

本仓库为 Telegram MCP 服务器（Telethon + MCP）。Agent 接入前请先读本文。

## 必读文档

| 文档 | 内容 |
|------|------|
| [README.md](README.md) | 安装、MCP 配置、环境变量 |
| [docs/singleton-server.md](docs/singleton-server.md) | **单实例守护进程**（并行任务必开，默认已启用） |
| [docs/structured-messages.md](docs/structured-messages.md) | `get_messages` 等返回的 JSON 字段 |
| [skills/README.md](skills/README.md) | 任务型 Agent Skills 索引 |

## 默认行为（重要）

1. **`TELEGRAM_MCP_SINGLETON=1`（默认）** — 仅一个 Telethon 守护进程；MCP 客户端为 stdio 桥接。并行 Agent **不要**关掉此选项。
2. **优先 `TELEGRAM_SESSION_STRING`** — 避免文件 session 锁。
3. **读 bot 消息用 `get_messages` / `list_messages`** — 含 `entities`、`links`，可解析 `tg://` 隐藏链接。
4. **错误信息已结构化** — 工具返回含 `Type`、`Guidance`；详情见 `mcp_errors.log`。
5. **写操作遇 `UserRestrictedError`** — 账号风控，联系 @SpamBot，勿重试建群/发消息。

## 任务 Skills

- **极搜找群**：先读 [skills/telegram-jisou-group-search/SKILL.md](skills/telegram-jisou-group-search/SKILL.md)

## 本地调用示例

```bash
# 守护进程（可选，首次 MCP 连接会自动拉起）
uv run main.py --serve

# 工具调用
mcporter call telegram-mcp.get_me 2>/dev/null
mcporter call telegram-mcp.get_messages chat_id=jisou page=1 page_size=5 2>/dev/null
```

## 分支（fork 工作流）

开发在 `feat-*` 分支；勿在 `master` / 直接改 `test` 提交。见上游协作约定。
