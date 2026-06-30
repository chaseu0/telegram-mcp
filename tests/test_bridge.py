import os
from unittest.mock import patch

from telegram_mcp.bridge import _should_exit_bridge


def test_should_exit_orphaned_ppid():
    with patch("telegram_mcp.bridge.os.getppid", return_value=1):
        assert _should_exit_bridge(0.0, now=1.0) == "orphaned_ppid"


def test_should_exit_parent_dead():
    with patch("telegram_mcp.bridge.os.getppid", return_value=99):
        with patch("telegram_mcp.bridge.singleton.is_process_alive", return_value=False):
            assert _should_exit_bridge(0.0, now=1.0) == "parent_dead"


def test_should_exit_orphaned_launcher():
    with patch("telegram_mcp.bridge.os.getppid", return_value=50):
        with patch("telegram_mcp.bridge.singleton.is_process_alive", return_value=True):
            with patch(
                "telegram_mcp.bridge._parent_launcher_orphaned",
                return_value=True,
            ):
                assert _should_exit_bridge(0.0, now=1.0) == "orphaned_launcher"


def test_should_exit_none_when_healthy(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_BRIDGE_IDLE_SEC", "0")
    import importlib
    import telegram_mcp.bridge as bridge_mod

    importlib.reload(bridge_mod)
    with patch.object(bridge_mod.os, "getppid", return_value=os.getpid()):
        assert bridge_mod._should_exit_bridge(1.0, now=2.0) is None


def test_should_exit_idle_timeout(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_BRIDGE_IDLE_SEC", "60")
    import importlib
    import time

    import telegram_mcp.bridge as bridge_mod

    importlib.reload(bridge_mod)
    with patch.object(bridge_mod.os, "getppid", return_value=os.getpid()):
        reason = bridge_mod._should_exit_bridge(
            last_activity=time.monotonic() - 120,
            now=time.monotonic(),
            is_alive=lambda _pid: True,
        )
    assert reason == "idle_timeout"
