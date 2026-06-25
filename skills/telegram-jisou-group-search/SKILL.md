---
name: telegram-jisou-group-search
description: >-
  Search Telegram groups/channels via @jisou bot, parse structured bot replies
  (tg:// links, entities), probe each result with preview_chat for accessibility
  and quality scoring. Use when the user asks to 找群, search groups via jisou/极搜,
  validate search results, or audit sponsored vs organic listings.
---

# Telegram 极搜 @jisou 找群

通过 **telegram-mcp**（mcporter 或 Cursor MCP）向 `@jisou` 发关键词，解析机器人回复中的内嵌链接，逐条探测可访问性与质量。

## 前置

- 已配置 [单实例 MCP](../../docs/singleton-server.md)（默认开启），避免并行任务 session 冲突
- 调用：`mcporter call telegram-mcp.<tool> ... 2>/dev/null`（stderr 为启动日志，可隐藏）
- 写操作（`send_message`）需账号未被 `UserRestrictedError` 限制
- 探测优先用 `preview_chat`（无需加群）；每条探测间隔 ≥ 2s
- 读消息必须用结构化工具：见 [structured-messages.md](../../docs/structured-messages.md)

## 工作流

```
进度:
- [ ] 1. 发关键词给 @jisou
- [ ] 2. 读取结构化 bot 回复
- [ ] 3. 解析 links / entities
- [ ] 4. 逐条 preview_chat 探测
- [ ] 5. 输出质量报告
```

### 1. 发送关键词

```bash
mcporter call telegram-mcp.send_message chat_id=jisou message='cvv' 2>/dev/null
```

等待 bot 回复（通常 < 2s）。确认新消息：

```bash
mcporter call telegram-mcp.list_messages chat_id=jisou limit=3 2>/dev/null
```

### 2. 读取结构化消息

**必须用** `get_messages` 或 `list_messages`（返回 JSON），不要用旧版纯文本行格式。

```bash
mcporter call telegram-mcp.get_messages chat_id=jisou page=1 page_size=5 2>/dev/null
```

每条消息关键字段：

| 字段 | 用途 |
|------|------|
| `text` | 可见标题/描述 |
| `entities` | 内嵌 `MessageEntityTextUrl`，含 `url`（如 `tg://resolve?domain=xxx`） |
| `links` | 扁平化链接列表，`parsed.kind` = username / invite |
| `inline_buttons` | 按钮 URL / callback |
| `engagement` | views / forwards / `reactions_detail` |

极搜典型回复：约 **9 条赞助置顶 + 9 条自然结果**（单条消息内多 entity，如 msg #492）。

### 3. 解析目标

从 `links` 或 `entities` 提取探测目标：

- `parsed.kind == "username"` → `preview_chat target=@value`
- `parsed.kind == "invite"` → `preview_chat target='https://t.me/+value'`
- `url` 含 `tg://resolve?domain=` → `preview_chat target=@domain`

去重：同一 `parsed.value` 只探测一次。

### 4. 探测每条结果

```bash
mcporter call telegram-mcp.preview_chat target=@username message_limit=3 2>/dev/null
```

失败时记录 RPC 错误类型（`ChannelPrivateError`、`InviteHashExpiredError` 等）。

### 5. 质量评分（每条）

| 维度 | 信号 | 权重 |
|------|------|------|
| 可访问 | `preview_chat` 成功 | 必须 |
| 类型 | channel / supergroup / group | 信息 |
| 规模 | `participants_count`（若有） | 高 |
| 活跃度 | 最近 `message_limit` 条消息日期 | 高 |
| 来源 | 赞助区 vs 自然区（按消息内顺序/标记） | 中 |
| 完整度 | 有 username、有简介 about | 低 |

输出模板见 [report-template.md](report-template.md)。

## 风控与错误

MCP 错误包含 `Type` / `Message` / `Guidance`（详见 [singleton-server.md](../../docs/singleton-server.md)）：

| RPC 类型 | 含义 | 动作 |
|----------|------|------|
| `FloodWaitError` | 频率限制 | 等待 `flood_wait_seconds` |
| `UserRestrictedError` | 账号被限制 | 停写操作，联系 @SpamBot |
| `PeerFloodError` | 陌生人操作过多 | 降频 |
| `ChannelPrivateError` | 私有群/需邀请 | 标记不可公开访问 |

完整 traceback 在 `mcp_errors.log`。

## 导出

建议目录：`exports/jisou_<keyword>_<YYYYMMDD>/`

- `bot_reply.json` — `get_messages` 原始结构化输出
- `targets/<username>/preview.json` — 每条探测结果
- `report.md` — 汇总表（见 report-template.md）

## 禁止

- 不要高频轮询 @jisou（同一关键词间隔 ≥ 30s）
- 不要对不可访问目标反复 `join_chat` 撞墙
- 不要用裸数字 chat id 探测（无 access_hash 会失败）
- 不要 `TELEGRAM_MCP_SINGLETON=0` 下并行多 Agent 共用同一 session

## 参考

- [structured-messages.md](../../docs/structured-messages.md)
- [report-template.md](report-template.md)
