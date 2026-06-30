---
name: telegram-jisou-group-search
description: >-
  Search Telegram groups/channels via @jisou bot, parse structured bot replies
  (tg:// links, entities), probe each result with preview_chat for accessibility
  and quality scoring. Use when the user asks to 找群, search groups via jisou/极搜,
  validate search results, or audit sponsored vs organic listings.
---

# Telegram 极搜 @jisou 找群

通过 **telegram-mcp**（mcporter 或 Cursor MCP）向 `@jisou` 发关键词，**先按 👥 筛选群组、再翻页采集**，解析内嵌链接后逐条 `preview_chat` 探测质量。

> 按钮筛选与翻页细节见 [telegram-jisou-buttons/SKILL.md](../telegram-jisou-buttons/SKILL.md)

## 前置

- 已配置 [单实例 MCP](../../docs/singleton-server.md)（默认开启），避免并行任务 session 冲突
- 调用：`mcporter call telegram-mcp.<tool> ... 2>/dev/null`（stderr 为启动日志，可隐藏）
- 写操作（`send_message`、`press_inline_button`）需账号未被 `UserRestrictedError` 限制
- 探测优先用 `preview_chat`（无需加群）；每条探测间隔 ≥ 2s
- 读消息必须用结构化工具：见 [structured-messages.md](../../docs/structured-messages.md)

## 工作流（必须顺序）

```
进度:
- [ ] 1. 发关键词给 @jisou
- [ ] 2. get_messages 锁定 bot 回复 message_id
- [ ] 3. press_inline_button 👥 筛选群组
- [ ] 4. 循环 下一页/➡️ 翻页直到无新增或达上限
- [ ] 5. 解析 entities/links 去重
- [ ] 6. 逐条 preview_chat 探测
- [ ] 7. 输出质量报告
```

### 1. 发送关键词

```bash
mcporter call telegram-mcp.send_message chat_id=jisou message='币圈' 2>/dev/null
sleep 3
mcporter call telegram-mcp.get_messages chat_id=jisou page=1 page_size=5 2>/dev/null
```

从 `results` 取极搜 bot 消息（`sender_id: 5762373625`），记下 `id`。

### 2. 👥 筛选（找群必做）

```bash
mcporter call telegram-mcp.press_inline_button chat_id=jisou message_id=531 button_text='👥' 2>/dev/null
sleep 3
mcporter call telegram-mcp.get_messages chat_id=jisou page=1 page_size=3 2>/dev/null
```

未筛选的首页混合 👥 群与 📢 频道；关键词「币圈」首页约 17 条链接中仅 ~3 条为群，翻 3 页无筛选共 ~36 条链接、仅 ~6 条群标记。点 👥 后每页应几乎全是群组，翻 10+ 页可获 100+ 群链接。

### 3. 翻页采集

```bash
# 第 1 页 → 第 2 页
mcporter call telegram-mcp.press_inline_button chat_id=jisou message_id=531 button_text='下一页' 2>/dev/null
sleep 3
# 第 2 页起用 ➡️（不是「下一页」）
mcporter call telegram-mcp.press_inline_button chat_id=jisou message_id=531 button_text='➡️' 2>/dev/null
```

停止条件：本页无新链接 / 无翻页按钮 / 达到 `max_pages`（建议 ≥ 10）。

每条消息关键字段：

| 字段 | 用途 |
|------|------|
| `text` | 可见标题；`👥` 行 = 群组结果 |
| `entities` | 内嵌 `MessageEntityTextUrl`，含真实 `url` |
| `links` | 扁平化链接，`parsed.kind` = username / invite |
| `inline_buttons` / `buttons` | 筛选与翻页按钮 |
| `edited` | 按钮按压后消息编辑时间 |
| `engagement` | views / forwards / `reactions_detail` |

### 4. 解析目标

从 `links` 或 `entities` 提取探测目标：

- `parsed.kind == "username"` → `preview_chat target=@value`
- `parsed.kind == "invite"` → `preview_chat target='https://t.me/+value'`
- 跳过 `jisou1Bot` 热搜跳转、`?start=` 广告 bot

去重：同一 `parsed.value` 只探测一次。

### 5. 探测每条结果

```bash
mcporter call telegram-mcp.preview_chat target=@username message_limit=3 2>/dev/null
```

失败时记录 RPC 错误类型（`ChannelPrivateError`、`InviteHashExpiredError` 等）。

### 6. 质量评分（每条）

| 维度 | 信号 | 权重 |
|------|------|------|
| 可访问 | `preview_chat` 成功 | 必须 |
| 类型 | channel / supergroup / group | 信息 |
| 规模 | `participants_count`（若有） | 高 |
| 活跃度 | 最近 `message_limit` 条消息日期 | 高 |
| 来源 | 赞助区 vs 自然区（按消息内顺序/标记） | 中 |
| 完整度 | 有 username、有简介 about | 低 |

输出模板见 [report-template.md](report-template.md)。

## 评论与群内发言

| 能力 | 支持 |
|------|------|
| 极搜按群/频道类型筛选 | ✅ 👥 / 📢 |
| 极搜翻页 | ✅ 下一页 / ➡️ |
| 过滤极搜结果中的「用户评论」 | ❌ 无此按钮 |
| 读目标群近期发言 | ✅ 对每条结果 `preview_chat` |
| 读 @jisou 对话上下文 | ✅ `get_message_context`（仅你与 bot 的 DM） |

💬 按钮是 GText 讨论帖类型筛选，不是群内热评过滤。评估群质量须在 `preview_chat` 后分析 `recent_messages`。

## 一键脚本

```bash
python3 scripts/jisou_search.py 币圈 --max-pages 15
python3 scripts/jisou_search.py 币圈 --no-filter --max-pages 3   # 对照：不筛选
```

导出：`exports/jisou_<keyword>_<date>/summary.json` + 每页快照。

## 风控与错误

| RPC 类型 | 含义 | 动作 |
|----------|------|------|
| `FloodWaitError` | 频率限制 | 等待 `flood_wait_seconds` |
| `UserRestrictedError` | 账号被限制 | 停写操作，联系 @SpamBot |
| `PeerFloodError` | 陌生人操作过多 | 降频 |
| `ChannelPrivateError` | 私有群/需邀请 | 标记不可公开访问 |

完整 traceback 在 `mcp_errors.log`。

## 导出

建议目录：`exports/jisou_<keyword>_<YYYYMMDD>/`

- `summary.json` — 去重后的全部目标
- `bot_replies/<keyword>_pN.json` — 每页原始消息
- `targets/<username>/preview.json` — 每条探测结果
- `report.md` — 汇总表（见 report-template.md）

## 禁止

- **不要**跳过 👥 直接翻页采集（频道与广告会淹没群链接）
- **不要**只认「下一页」忽略「➡️」（第 2 页起按钮文案变化）
- 不要高频轮询 @jisou（同一关键词间隔 ≥ 30s）
- 不要对不可访问目标反复 `join_chat` 撞墙
- 不要用裸数字 chat id 探测（无 access_hash 会失败）
- 不要 `TELEGRAM_MCP_SINGLETON=0` 下并行多 Agent 共用同一 session

## 参考

- [telegram-jisou-buttons/SKILL.md](../telegram-jisou-buttons/SKILL.md) — 按钮语义与翻页循环
- [structured-messages.md](../../docs/structured-messages.md)
- [report-template.md](report-template.md)
