#!/usr/bin/env python3
"""极搜 @jisou 关键词搜索：👥 筛选 → 翻页 → 去重 → 导出 JSON。

用法:
  python3 scripts/jisou_search.py 币圈
  python3 scripts/jisou_search.py 币圈 --max-pages 15 --no-filter
  MCP_URL=http://127.0.0.1:18765/sse python3 scripts/jisou_search.py 广告群
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

MCP = os.environ.get("MCP_URL", "http://127.0.0.1:18765/sse")
JISOU_SENDER_ID = 5762373625
# Numeric ID avoids ResolveUsernameRequest FloodWait on chat_id=jisou
JISOU_CHAT_ID = os.environ.get("JISOU_CHAT_ID", str(JISOU_SENDER_ID))
BUTTON_WAIT = 3.0
SEARCH_WAIT = 4.0
SKIP_USERNAMES = re.compile(r"jisou\d*bot", re.I)


def mcporter(tool: str, **kwargs) -> dict | list | str | None:
    cmd = ["mcporter", "call", f"{MCP}.{tool}", "--allow-http"]
    for k, v in kwargs.items():
        if v is None:
            continue
        s = str(v).replace("'", "'\\''")
        cmd.append(f"{k}='{s}'" if (" " in s or any(c in s for c in "'\"\\$")) else f"{k}={s}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        raw = (out.stdout or "").strip()
        if not raw:
            return {"_error": (out.stderr or "empty output").strip(), "_tool": tool}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            if start >= 0:
                try:
                    return json.loads(raw[start:])
                except json.JSONDecodeError:
                    pass
            if raw.startswith("["):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    pass
            return {"_raw": raw, "_tool": tool}
    except subprocess.TimeoutExpired:
        return {"_error": "timeout", "_tool": tool}
    except Exception as e:
        return {"_error": str(e), "_tool": tool}


def find_bot_msg(msgs: dict | None) -> dict | None:
    if not isinstance(msgs, dict):
        return None
    for m in msgs.get("results", []):
        if m.get("sender_id") == JISOU_SENDER_ID or "极搜" in (m.get("sender") or ""):
            return m
    for m in msgs.get("results", []):
        if m.get("entities") and len(m.get("entities", [])) > 5:
            return m
    return None


def text_hash(msg: dict | None) -> str:
    if not msg:
        return ""
    return hashlib.md5((msg.get("text") or "").encode()).hexdigest()


def parse_targets(msg: dict | None) -> list[dict]:
    if not msg:
        return []
    seen: set[str] = set()
    targets: list[dict] = []
    for ent in msg.get("entities") or []:
        parsed = ent.get("parsed") or {}
        value = parsed.get("value")
        if not value or value in seen:
            continue
        url = ent.get("url") or ""
        if SKIP_USERNAMES.search(value) or "bot?" in url.lower():
            continue
        seen.add(value)
        targets.append({
            "kind": parsed.get("kind", "username"),
            "value": value,
            "label": (ent.get("text") or "").strip(),
            "url": url,
        })
    return targets


def next_page_button(msg: dict | None) -> str | None:
    buttons = (msg or {}).get("buttons") or []
    for label in ("下一页", "➡️"):
        if label in buttons:
            return label
    return None


def press_button(message_id: int, button_text: str) -> dict:
    return mcporter(
        "press_inline_button",
        chat_id=JISOU_CHAT_ID,
        message_id=message_id,
        button_text=button_text,
    )  # type: ignore[return-value]


def fetch_bot_message() -> dict | None:
    msgs = mcporter("get_messages", chat_id=JISOU_CHAT_ID, page=1, page_size=5)
    return find_bot_msg(msgs if isinstance(msgs, dict) else None)


def wait_for_edit(message_id: int, before_hash: str, retries: int = 5) -> dict | None:
    for _ in range(retries):
        time.sleep(BUTTON_WAIT)
        msg = fetch_bot_message()
        if msg and msg.get("id") == message_id:
            if text_hash(msg) != before_hash:
                return msg
            if msg.get("edited"):
                return msg
    return fetch_bot_message()


def paginate_keyword(
    keyword: str,
    *,
    max_pages: int = 10,
    group_filter: bool = True,
    out_dir: Path | None = None,
) -> dict:
    print(f"[search] keyword={keyword!r} filter={'👥' if group_filter else 'none'} max_pages={max_pages}")
    send = mcporter("send_message", chat_id=JISOU_CHAT_ID, message=keyword)
    if isinstance(send, dict) and ("_error" in send or "FloodWait" in str(send)):
        return {"keyword": keyword, "error": send, "targets": []}

    time.sleep(SEARCH_WAIT)
    bot_msg = fetch_bot_message()
    if not bot_msg:
        return {"keyword": keyword, "error": "no bot reply", "targets": []}

    message_id = bot_msg["id"]
    pages: list[dict] = []
    all_targets: dict[str, dict] = {}

    if group_filter:
        h0 = text_hash(bot_msg)
        press_button(message_id, "👥")
        edited = wait_for_edit(message_id, h0)
        if edited:
            bot_msg = edited

    for page_num in range(1, max_pages + 1):
        targets = parse_targets(bot_msg)
        new_count = 0
        for t in targets:
            key = t["value"].lower()
            if key not in all_targets:
                all_targets[key] = {**t, "page": page_num, "keyword": keyword}
                new_count += 1

        page_rec = {
            "page": page_num,
            "message_id": message_id,
            "targets_on_page": len(targets),
            "new_targets": new_count,
            "total_unique": len(all_targets),
            "buttons": bot_msg.get("buttons", []),
            "edited": bot_msg.get("edited"),
            "group_markers": (bot_msg.get("text") or "").count("👥"),
            "channel_markers": (bot_msg.get("text") or "").count("📢"),
        }
        pages.append(page_rec)
        print(
            f"  page {page_num}: on_page={len(targets)} new={new_count} "
            f"total={len(all_targets)} 👥={page_rec['group_markers']}"
        )

        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            snap = {
                "keyword": keyword,
                "page": page_num,
                "message": bot_msg,
                "targets": targets,
            }
            suffix = f"_p{page_num}" if page_num > 1 else ""
            (out_dir / f"{keyword}{suffix}.json").write_text(
                json.dumps(snap, ensure_ascii=False, indent=2)
            )

        if page_num >= max_pages:
            break

        btn = next_page_button(bot_msg)
        if not btn:
            print(f"  no pagination button on page {page_num}, stop")
            break
        if new_count == 0 and page_num > 1:
            print("  no new targets, stop")
            break

        h_before = text_hash(bot_msg)
        press_button(message_id, btn)
        edited = wait_for_edit(message_id, h_before)
        if not edited:
            print("  edit timeout after button press, stop")
            break
        bot_msg = edited

    result = {
        "keyword": keyword,
        "message_id": message_id,
        "group_filter": group_filter,
        "max_pages": max_pages,
        "pages": pages,
        "targets": list(all_targets.values()),
        "total_unique": len(all_targets),
        "exported_at": datetime.now().isoformat(),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="极搜 @jisou 筛选+翻页采集")
    parser.add_argument("keyword", help="搜索关键词")
    parser.add_argument("--max-pages", type=int, default=10, help="最大翻页数（默认 10）")
    parser.add_argument("--no-filter", action="store_true", help="不点 👥，采集混合结果（对照用）")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="导出目录（默认 exports/jisou_<keyword>_<date>/）",
    )
    args = parser.parse_args()

    out_dir = args.out
    if out_dir is None:
        date = datetime.now().strftime("%Y%m%d")
        repo = Path(__file__).resolve().parents[1]
        out_dir = repo / "exports" / f"jisou_{args.keyword}_{date}"

    result = paginate_keyword(
        args.keyword,
        max_pages=args.max_pages,
        group_filter=not args.no_filter,
        out_dir=out_dir,
    )

    summary_path = out_dir / "summary.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"\n[done] unique_targets={result.get('total_unique', 0)} -> {summary_path}")
    if result.get("error"):
        print(f"[error] {result['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
