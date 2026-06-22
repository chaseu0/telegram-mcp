import json

import pytest
from telethon.tl.types import Channel, ChatInvite, User

from telegram_mcp import runtime
from telegram_mcp.tools import chats


def test_parse_invite_hash_variants():
    assert runtime.parse_invite_hash("https://t.me/+AbCdEf123") == "AbCdEf123"
    assert runtime.parse_invite_hash("https://t.me/joinchat/AbCdEf123") == "AbCdEf123"
    assert runtime.parse_invite_hash("+AbCdEf123") == "AbCdEf123"
    assert runtime.parse_invite_hash("AbCdEf123") == "AbCdEf123"


def test_classify_preview_target():
    assert runtime.classify_preview_target("https://t.me/+HASH") == ("invite", "HASH")
    assert runtime.classify_preview_target("@mychannel") == ("username", "mychannel")
    assert runtime.classify_preview_target("https://t.me/mychannel") == ("username", "mychannel")
    assert runtime.classify_preview_target("-100123") == ("id", -100123)
    assert runtime.classify_preview_target(-100123) == ("id", -100123)


def test_format_entity_includes_channel_username():
    channel = Channel(
        id=3,
        title="Channel",
        photo=None,
        date=None,
        creator=False,
        left=False,
        broadcast=True,
        verified=False,
        megagroup=False,
        restricted=False,
        signatures=False,
        min=False,
        scam=False,
        has_link=False,
        has_geo=False,
        slowmode_enabled=False,
        call_active=False,
        call_not_empty=False,
        fake=False,
        gigagroup=False,
        noforwards=False,
        join_to_send=False,
        join_request=False,
        forum=False,
        stories_hidden=False,
        stories_hidden_min=False,
        stories_unavailable=False,
        access_hash=1,
        username="pubchan",
    )
    assert runtime.format_entity(channel) == {
        "id": -1000000000003,
        "name": "Channel",
        "type": "channel",
        "username": "pubchan",
    }


class _PreviewClient:
    def __init__(self, invite=None, resolved=None, full=None, messages=None):
        self.invite = invite
        self.resolved = resolved
        self.full = full
        self.messages = messages or []

    async def __call__(self, request):
        name = type(request).__name__
        if name == "CheckChatInviteRequest":
            return self.invite
        if name == "ResolveUsernameRequest":
            return self.resolved
        if name == "GetFullChannelRequest":
            return self.full
        raise AssertionError(f"unexpected request {name}")

    async def get_messages(self, entity, limit=5):
        return self.messages[:limit]


@pytest.mark.asyncio
async def test_preview_chat_invite_without_join(monkeypatch):
    invite = ChatInvite(
        title="Secret Group",
        photo=None,
        participants_count=42,
        color=0,
        about="about text",
        channel=True,
        broadcast=False,
        public=False,
        megagroup=True,
        request_needed=False,
    )

    async def noop(_client):
        return None

    monkeypatch.setattr(chats, "get_client", lambda account=None: _PreviewClient(invite=invite))
    monkeypatch.setattr(chats, "ensure_connected", noop)

    raw = await chats.preview_chat("https://t.me/+AbCdEf123", message_limit=0)
    data = json.loads(raw)
    assert data["preview_type"] == "invite"
    assert data["title"] == "Secret Group"
    assert data["participants_count"] == 42
    assert data["already_member"] is False


@pytest.mark.asyncio
async def test_preview_chat_public_username(monkeypatch):
    channel = Channel(
        id=10,
        title="Teach Channel",
        photo=None,
        date=None,
        creator=False,
        left=False,
        broadcast=True,
        verified=False,
        megagroup=False,
        restricted=False,
        signatures=False,
        min=False,
        scam=False,
        has_link=False,
        has_geo=False,
        slowmode_enabled=False,
        call_active=False,
        call_not_empty=False,
        fake=False,
        gigagroup=False,
        noforwards=False,
        join_to_send=False,
        join_request=False,
        forum=False,
        stories_hidden=False,
        stories_hidden_min=False,
        stories_unavailable=False,
        access_hash=9,
        username="teach",
    )
    resolved = type("Resolved", (), {"chats": [channel], "users": []})()
    full_chat = type("FullChat", (), {"about": "learn cvv", "participants_count": 9})()
    full = type("Full", (), {"chats": [channel], "full_chat": full_chat})()

    async def noop(_client):
        return None

    monkeypatch.setattr(
        chats,
        "get_client",
        lambda account=None: _PreviewClient(resolved=resolved, full=full),
    )
    monkeypatch.setattr(chats, "ensure_connected", noop)

    raw = await chats.preview_chat("teach", message_limit=0)
    data = json.loads(raw)
    assert data["preview_type"] == "public_username"
    assert data["username"] == "teach"
    assert data["about"] == "learn cvv"


@pytest.mark.asyncio
async def test_preview_chat_bare_id_without_cache_returns_message(monkeypatch):
    async def noop(_client):
        return None

    async def fail_resolve(_identifier, _client=None):
        raise ValueError("cold cache")

    monkeypatch.setattr(chats, "get_client", lambda account=None: _PreviewClient())
    monkeypatch.setattr(chats, "ensure_connected", noop)
    monkeypatch.setattr(chats, "resolve_entity", fail_resolve)

    raw = await chats.preview_chat(-1002007133883, message_limit=0)
    assert "Cannot preview chat id" in raw
    assert "@username" in raw
