import os
import socket
from unittest.mock import patch

import pytest

from telegram_mcp import singleton


def test_singleton_enabled_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_MCP_SINGLETON", raising=False)
    assert singleton.singleton_enabled() is True
    monkeypatch.setenv("TELEGRAM_MCP_SINGLETON", "0")
    assert singleton.singleton_enabled() is False


def test_get_sse_url_and_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_MCP_PORT", "19999")
    monkeypatch.setenv("TELEGRAM_MCP_HOST", "127.0.0.1")
    assert singleton.get_sse_url() == "http://127.0.0.1:19999/sse"
    assert singleton.lock_path() == tmp_path / "singleton-19999.lock"
    assert singleton.pid_path() == tmp_path / "singleton-19999.pid"


def test_is_port_open_false():
    with patch.object(socket, "create_connection", side_effect=OSError("nope")):
        assert singleton.is_port_open("127.0.0.1", 1) is False


def test_file_lock_exclusive(tmp_path):
    path = tmp_path / "test.lock"
    first = singleton.FileLock(path)
    second = singleton.FileLock(path)
    first.acquire(exclusive=True, nonblocking=True)
    with pytest.raises(BlockingIOError):
        second.acquire(exclusive=True, nonblocking=True)
    first.release()
    second.acquire(exclusive=True, nonblocking=True)
    second.release()


def test_ensure_singleton_reuses_running_server(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_MCP_PORT", "20001")
    with patch.object(singleton, "is_port_open", return_value=True):
        assert singleton.ensure_singleton_server_running() == "http://127.0.0.1:20001/sse"


def test_ensure_singleton_spawns_daemon(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_MCP_PORT", "20002")
    calls = {"spawn": 0}

    def fake_spawn():
        calls["spawn"] += 1

    with patch.object(singleton, "is_port_open", side_effect=[False, False, True]):
        with patch.object(singleton, "_spawn_daemon", fake_spawn):
            url = singleton.ensure_singleton_server_running(timeout_sec=2.0)
    assert calls["spawn"] == 1
    assert url == "http://127.0.0.1:20002/sse"
