---
name: telegram-jisou-buttons
description: >-
  极搜 @jisou 机器人内联按钮语义、callback 解码、press_inline_button 自动化、
  筛选与翻页循环。找群任务必须先按类型筛选再翻页。Use when automating jisou
  inline keyboards, pagination, type filters (👥📢), or decoding callback data.
---

# 极搜 @jisou 内联按钮

极搜每条搜索结果回复都带 **两行内联按钮**。筛选与翻页通过 **同一条消息原地编辑**（`edited` 时间戳变化），不会新发消息。必须用 `press_inline_button` + `get_messages` 读编辑后的内容。

完整找群流程见 [telegram-jisou-group-search/SKILL.md](../telegram-jisou-group-search/SKILL.md)。

## MCP 工具

| 工具 | 用途 |
|------|------|
| `press_inline_button` | 点击 callback 按钮（`button_text` 或 `button_index`） |
| `list_inline_buttons` | 列出某条消息全部按钮文本/索引 |
| `get_messages` | 读编辑后的结构化正文（`entities` / `links`） |

### press_inline_button 参数

```bash
mcporter call telegram-mcp.press_inline_button \
  chat_id=jisou message_id=531 button_text='👥' 2>/dev/null
```

- `message_id`：**必须**指定 bot 回复的消息 ID（筛选/翻页后 ID 不变）
- `button_text`：与按钮可见文案完全一致（emoji 敏感）
- 点击后等待 **2–3 秒**，再 `get_messages` 读同 ID 消息

## 按钮布局

```
行 1（类型筛选）: 👥  📢  🎬  🏞  🎧  💬  🤖  📁
行 2（排序/翻页）: 🆕最新  🔞过滤  下一页
                  — 或 —  防失联  ⏮️  ➡️   （第 2 页起）
```

第 1 页行 2 为 `下一页`；翻到第 2 页后变为 `⏮️` + `➡️`，**自动化必须同时识别两种翻页文案**。

## 按钮语义表

callback `data` 为 hex，解码为空格分隔的 ASCII。格式：

```
/s <nsfw_flag> 0 <type> <sort> <page> 18 10
```

| 按钮 | 解码示例 | 含义 |
|------|----------|------|
| 👥 | `/s 0 0 group none 0 18 10` | **仅群组**（找群必点） |
| 📢 | `/s 0 0 channel none 0 18 10` | 仅频道 |
| 🎬 | `/s 0 0 Video none 0 18 10` | 视频 |
| 🏞 | `/s 0 0 Photo none 0 18 10` | 图片 |
| 🎧 | `/s 0 0 Audio none 0 18 10` | 音频 |
| 💬 | `/s 0 0 GText none 0 18 10` | 讨论帖/频道讨论区内容（非用户评论） |
| 🤖 | `/s 0 0 bot none 0 18 10` | 机器人 |
| 📁 | `/s 0 0 Document none 0 18 10` | 文档/文件 |
| 🆕最新 | `/s 0 0 allMsg createTime 0 18 10` | 按创建时间排序 |
| 🔞过滤 | `/s 1 0 allGroup none 0 18 10` | 成人内容过滤开关（`1` = 开启） |
| 下一页 | `/s 0 0 allGroup none 1 18 10` | 下一页（首页，页码 `1`） |
| ⏮️ | `/s 0 0 allGroup none 0 18 10` | 上一页 |
| ➡️ | `/s 0 0 allGroup none 2 18 10` | 下一页（已在第 2 页时，页码递增） |
| 防失联 | URL 按钮 | 打开 `t.me/jisou2` 等，非 callback |

hex 解码示例：

```bash
python3 -c "print(bytes.fromhex('2f73203020302067726f7570206e6f6e652030203138203130').decode())"
# /s 0 0 group none 0 18 10
```

## 标准自动化流程

### 1. 发关键词 → 锁定 bot 消息

```bash
mcporter call telegram-mcp.send_message chat_id=jisou message='币圈' 2>/dev/null
sleep 3
mcporter call telegram-mcp.get_messages chat_id=jisou page=1 page_size=5 2>/dev/null
```

从 `results` 取 `sender_id == 5762373625` 或 `sender` 含 `极搜` 的消息，记下 `id`。

### 2. 先筛选 👥，再翻页

```bash
MID=531
mcporter call telegram-mcp.press_inline_button chat_id=jisou message_id=$MID button_text='👥' 2>/dev/null
sleep 3
mcporter call telegram-mcp.get_messages chat_id=jisou page=1 page_size=3 2>/dev/null
```

**禁止**在未筛选时直接翻页采集——混合结果含大量 📢 频道与赞助广告，且群链接占比极低。

### 3. 翻页循环

```bash
# 首页用「下一页」，第 2 页起用「➡️」
mcporter call telegram-mcp.press_inline_button chat_id=jisou message_id=$MID button_text='下一页' 2>/dev/null
# 或
mcporter call telegram-mcp.press_inline_button chat_id=jisou message_id=$MID button_text='➡️' 2>/dev/null
sleep 3
```

停止条件（满足任一）：

- 本页 `entities` 解析出的新链接数为 0
- 行 2 无 `下一页` / `➡️` 按钮
- 达到 `max_pages`（建议 ≥ 10）
- 正文含「没有更多」类提示

### 4. 等待编辑完成

`press_inline_button` 成功后，bot **编辑原消息**而非新发。检测方式：

- `get_messages` 返回同 `id`，`edited` 字段更新
- `text` 中 `👥` 行数变化（筛选后应全部为群）
- 行尾出现 `【第N页】` 提示

可选：对比按压前后 `text` 的 hash，不变则 `sleep 2` 重试一次。

### 5. 解析链接

从 `entities` / `links` 提取 `parsed.value`，跳过：

- `jisou1Bot` / `jisou2` / `jisou3` 热搜跳转
- `?start=` 广告 bot 链接

去重键：`parsed.kind` + `parsed.value`。

## 评论 / 回复过滤（能力边界）

| 需求 | 是否支持 | 说明 |
|------|----------|------|
| 极搜结果按「群 vs 频道」筛选 | ✅ | 点 👥 / 📢 |
| 极搜结果翻页采集 | ✅ | 下一页 / ➡️ |
| 成人内容过滤 | ✅ | 🔞过滤 开关 |
| 按最新排序 | ✅ | 🆕最新 |
| 过滤「真实用户评论」 | ❌ | 极搜无此按钮；💬 是 GText 讨论帖类型，不是群内热评 |
| 读某群内的用户发言 | 间接 | 对单条结果 `preview_chat message_limit=N`，在目标群内判断 |
| 读 @jisou 对话上下文 | ✅ | `get_message_context chat_id=jisou message_id=<id>` — 仅你与 bot 的消息，不含结果群内容 |
| 读 bot 回复的 thread 评论 | ❌ | 极搜 DM 无讨论串 |

若要评估群质量（广告帖、活跃发言者），在采集链接后逐条 `preview_chat`，勿指望极搜按钮过滤群内热评。

## 常见错误

| 现象 | 原因 | 处理 |
|------|------|------|
| 翻页后内容不变 | 只按了 `下一页`，第 2 页起应点 `➡️` | 检查 `buttons` 数组选正确文案 |
| 链接很少 | 未点 👥，混有频道 | 先 `press_inline_button button_text='👥'` |
| `Button not found` | emoji 不匹配或消息 ID 错 | `list_inline_buttons` 确认 |
| `FloodWaitError` | 搜索过频 | 等待 `flood_wait_seconds`，关键词间隔 ≥ 30s |
| 解析到 jisou 热搜链接 | 正文底部「热搜」区块 | 过滤 `parsed.value` 含 `jisou` |

## 参考脚本

仓库 `scripts/jisou_search.py`：关键词 → 👥 筛选 → 翻页 → 去重 → 导出 JSON。

## 参考

- [telegram-jisou-group-search/SKILL.md](../telegram-jisou-group-search/SKILL.md)
- [structured-messages.md](../../docs/structured-messages.md)
- 样本：`exports/jisou_*/bot_replies/币圈.json`
