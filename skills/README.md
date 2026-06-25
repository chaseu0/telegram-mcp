# Agent Skills（本仓库）

供 Cursor / Codex / 其他 Agent 直接引用的任务说明。安装或 clone 本仓库后，让 Agent **读取对应 `SKILL.md`** 再执行。

| Skill | 路径 | 触发场景 |
|-------|------|----------|
| 极搜 @jisou 找群 | [telegram-jisou-group-search/SKILL.md](telegram-jisou-group-search/SKILL.md) | 找群、极搜、关键词搜索、验证群链接是否可访问 |

## 在 Cursor 中挂载（可选）

将 skill 链到个人 skills 目录：

```bash
ln -sf /path/to/telegram-mcp/skills/telegram-jisou-group-search \
  ~/.cursor/skills/telegram-jisou-group-search
```

或在 Agent 提示中写明：

> 执行 Telegram 找群任务前，先阅读仓库 `skills/telegram-jisou-group-search/SKILL.md`。

## 相关文档

- [单实例 MCP 服务器](../docs/singleton-server.md)
- [结构化消息字段](../docs/structured-messages.md)
