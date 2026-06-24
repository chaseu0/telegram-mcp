from types import SimpleNamespace

from telegram_mcp.tools.messages import (
    collect_message_links,
    extract_inline_buttons,
    extract_message_entities,
    message_to_dict,
    parse_telegram_target,
)


class MessageEntityTextUrl:
    pass


class MessageEntityUrl:
    pass


def test_parse_telegram_target_variants():
    assert parse_telegram_target("tg://resolve?domain=foo_bar") == {
        "kind": "username",
        "value": "foo_bar",
    }
    assert parse_telegram_target("https://t.me/+AbCdEf") == {
        "kind": "invite",
        "value": "AbCdEf",
    }
    assert parse_telegram_target("@jisou") == {"kind": "username", "value": "jisou"}


def test_extract_message_entities_text_url_and_url():
    text_url = MessageEntityTextUrl()
    text_url.offset = 0
    text_url.length = 2
    text_url.url = "tg://resolve?domain=sponsor1"
    text_url2 = MessageEntityTextUrl()
    text_url2.offset = 3
    text_url2.length = 2
    text_url2.url = "tg://resolve?domain=huaduo1211"
    plain = MessageEntityTextUrl()
    plain.offset = 6
    plain.length = 4
    url_entity = MessageEntityUrl()
    url_entity.offset = 11
    url_entity.length = 22

    msg = SimpleNamespace(
        message="赞助 花朵 自然结果 https://t.me/example",
        entities=[text_url, text_url2, plain, url_entity],
    )

    entities = extract_message_entities(msg)
    assert len(entities) == 4
    assert entities[0]["url"] == "tg://resolve?domain=sponsor1"
    assert entities[0]["parsed"]["value"] == "sponsor1"
    assert entities[3]["url"] == "https://t.me/example"

    links = collect_message_links(msg)
    urls = {item["url"] for item in links}
    assert "tg://resolve?domain=sponsor1" in urls
    assert "https://t.me/example" in urls


def test_extract_inline_buttons_with_urls():
    msg = SimpleNamespace(
        buttons=[
            [
                SimpleNamespace(text="打开群", url="tg://resolve?domain=testgroup"),
                SimpleNamespace(text="回调", data=b"\x01\x02"),
            ]
        ]
    )
    rows = extract_inline_buttons(msg)
    assert rows[0][0]["url"] == "tg://resolve?domain=testgroup"
    assert rows[0][0]["parsed"]["value"] == "testgroup"
    assert rows[0][1]["data"] == "0102"


def test_message_to_dict_includes_structured_fields():
    entity = MessageEntityTextUrl()
    entity.offset = 0
    entity.length = 2
    entity.url = "tg://resolve?domain=huaduo1211"

    msg = SimpleNamespace(
        id=492,
        sender=SimpleNamespace(first_name="极搜", username="jisou"),
        sender_id=123,
        date="2026-06-20",
        message="花朵",
        out=False,
        entities=[entity],
        buttons=None,
        views=100,
        forwards=2,
        reactions=SimpleNamespace(
            results=[SimpleNamespace(reaction=SimpleNamespace(emoticon="👍"), count=3)]
        ),
        reply_to=None,
        fwd_from=None,
        via_bot_id=None,
        edit_date=None,
        pinned=False,
        replies=None,
        action=None,
        ttl_period=None,
        grouped_id=None,
        web_preview=None,
        sticker=None,
        photo=None,
        voice=None,
        video_note=None,
        video=None,
        audio=None,
        gif=None,
        document=None,
        contact=None,
        geo=None,
        poll=None,
        media=None,
    )

    record = message_to_dict(msg)
    assert record["id"] == 492
    assert record["sender_username"] == "jisou"
    assert record["entities"][0]["url"] == "tg://resolve?domain=huaduo1211"
    assert record["links"][0]["parsed"]["value"] == "huaduo1211"
    assert record["engagement"]["views"] == 100
    assert record["engagement"]["reactions_detail"][0]["emoji"] == "👍"
