# 结构化消息字段（get_messages / list_messages）

`get_messages`、`list_messages`、`get_history`、`get_pinned_messages` 返回 JSON，不再丢失内嵌链接与互动数据。

## 顶层

```json
{
  "results": [ { "...message" } ],
  "metadata": { "page": 1, "page_size": 20, "count": 5 }
}
```

## 单条 message

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 消息 ID |
| `sender` / `sender_id` / `sender_username` | | 发送者 |
| `date` | datetime | 发送时间 |
| `text` | string | 可见纯文本（**不含**隐藏 URL 文本背后的真实链接） |
| `entities` | array | Telegram `MessageEntity` 列表 |
| `links` | array | 聚合链接（entity + inline_button），含 `parsed` 便于 `preview_chat` |
| `link_urls` | string[] | 仅 URL 列表 |
| `inline_buttons` | array[][] | 按钮行：`text` / `url` / `data` / `parsed` |
| `buttons` | string[] | 按钮文案扁平列表（兼容） |
| `engagement.views` | int | 频道浏览量 |
| `engagement.forwards` | int | 转发数 |
| `engagement.reactions` | int | 反应总数 |
| `engagement.reactions_detail` | array | `{emoji, count}` |
| `media` | string | 附件类型标签 |
| `reply_to` | int | 回复的消息 ID |

## entity 示例（机器人隐藏链接）

极搜等 bot 常用 `MessageEntityTextUrl`：界面显示群名，点击跳转 `tg://resolve?domain=...`。

```json
{
  "type": "MessageEntityTextUrl",
  "offset": 0,
  "length": 4,
  "text": "花朵",
  "url": "tg://resolve?domain=huaduo1211",
  "parsed": { "kind": "username", "value": "huaduo1211" }
}
```

## links 示例

```json
{
  "url": "tg://resolve?domain=huaduo1211",
  "source": "entity",
  "label": "花朵",
  "parsed": { "kind": "username", "value": "huaduo1211" }
}
```

探测：`mcporter call telegram-mcp.preview_chat target=huaduo1211 message_limit=3`

## parsed.kind 含义

| kind | preview_chat target 示例 |
|------|--------------------------|
| `username` | `@name` 或 `name` |
| `invite` | `https://t.me/+hash` |
| `channel_id` | 通常需结合搜索/dialog，不推荐裸 id |
| `tg_scheme` | 原样传入或人工解析 |

## 调用示例

```bash
mcporter call telegram-mcp.get_messages chat_id=jisou page=1 page_size=5 2>/dev/null
mcporter call telegram-mcp.list_messages chat_id=jisou limit=10 2>/dev/null
```
