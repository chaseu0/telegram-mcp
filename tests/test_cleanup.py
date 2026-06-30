import os
import time
from unittest.mock import patch

import pytest

from telegram_mcp import cleanup


def test_parse_etime_formats():
    assert cleanup.parse_etime("45") == 45
    assert cleanup.parse_etime("03:45") == 3 * 60 + 45
    assert cleanup.parse_etime("01:02:03") == 3600 + 120 + 3
    assert cleanup.parse_etime("2-01:02:03") == 2 * 86400 + 3600 + 120 + 3


def test_is_bridge_and_daemon_commands():
    assert cleanup.is_daemon_command("python main.py --serve")
    assert cleanup.is_daemon_command("telegram-mcp-serve")
    assert not cleanup.is_bridge_command("python main.py --serve")

    assert cleanup.is_bridge_command("uv run main.py")
    assert cleanup.is_bridge_command("python main.py")
    assert cleanup.is_bridge_command("telegram-mcp")
    assert not cleanup.is_bridge_command("python unrelated.py")


def test_classify_stale_bridge_orphaned():
    proc = cleanup.ProcessInfo(
        pid=100,
        ppid=1,
        etime_sec=60,
        command="python main.py",
    )
    assert cleanup.classify_stale_bridge(proc, daemon_pid=200) == "orphaned_ppid"


def test_classify_stale_bridge_parent_dead():
    proc = cleanup.ProcessInfo(
        pid=100,
        ppid=50,
        etime_sec=60,
        command="python main.py",
    )
    with patch.object(cleanup.singleton, "is_process_alive", return_value=False):
        assert cleanup.classify_stale_bridge(proc, daemon_pid=200) == "parent_dead"


def test_classify_stale_bridge_skips_daemon():
    proc = cleanup.ProcessInfo(
        pid=200,
        ppid=1,
        etime_sec=99999,
        command="python -m telegram_mcp.runner --serve",
    )
    assert cleanup.classify_stale_bridge(proc, daemon_pid=200) is None


def test_classify_stale_bridge_max_age(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_BRIDGE_MAX_AGE_SEC", "100")
    proc = cleanup.ProcessInfo(
        pid=100,
        ppid=os.getpid(),
        etime_sec=200,
        command="python main.py",
    )
    assert cleanup.classify_stale_bridge(proc, daemon_pid=999, max_age_sec=100) == "max_age"


def test_classify_stale_bridge_idle_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    proc = cleanup.ProcessInfo(
        pid=321,
        ppid=os.getpid(),
        etime_sec=10,
        command="python main.py",
    )
    cleanup.touch_bridge_heartbeat(321)
    old = time.time() - 4000
    os.utime(cleanup.bridge_heartbeat_path(321), (old, old))
    assert (
        cleanup.classify_stale_bridge(proc, daemon_pid=999, idle_sec=1800, now=time.time())
        == "idle_heartbeat"
    )


def test_classify_stale_bridge_orphaned_launcher():
    parent = cleanup.ProcessInfo(
        pid=50,
        ppid=1,
        etime_sec=60,
        command="uv run main.py",
    )
    proc = cleanup.ProcessInfo(
        pid=100,
        ppid=50,
        etime_sec=60,
        command="python main.py",
    )
    assert (
        cleanup.classify_stale_bridge(proc, daemon_pid=200, parent=parent)
        == "orphaned_launcher"
    )


def test_find_stale_bridges_from_ps_output(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    procs = [
        cleanup.ProcessInfo(10, 1, 120, "uv run main.py"),
        cleanup.ProcessInfo(11, 10, 120, "python main.py"),
        cleanup.ProcessInfo(20, os.getpid(), 30, "python main.py"),
        cleanup.ProcessInfo(30, 1, 10, "python -m telegram_mcp.runner --serve"),
    ]
    stale = cleanup.find_stale_bridges(procs=procs, daemon_pid=30, max_age_sec=0, idle_sec=0)
    assert sorted(item.proc.pid for item in stale) == [10, 11]


def test_cleanup_stale_bridges_dry_run(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    procs = [cleanup.ProcessInfo(10, 1, 120, "python main.py")]
    with patch.object(cleanup, "list_processes", return_value=procs):
        with patch.object(cleanup, "terminate_process") as kill:
            removed = cleanup.cleanup_stale_bridges(dry_run=True, max_age_sec=0, idle_sec=0)
    assert len(removed) == 1
    kill.assert_not_called()


def test_cleanup_stale_bridges_kills(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    procs = [cleanup.ProcessInfo(10, 1, 120, "python main.py")]
    with patch.object(cleanup, "list_processes", return_value=procs):
        with patch.object(cleanup, "terminate_process", return_value=True) as kill:
            removed = cleanup.cleanup_stale_bridges(dry_run=False, max_age_sec=0, idle_sec=0)
    assert len(removed) == 1
    kill.assert_called_once_with(10)


def test_bridge_heartbeat_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    cleanup.touch_bridge_heartbeat(42)
    path = cleanup.bridge_heartbeat_path(42)
    assert path.is_file()
    cleanup.remove_bridge_heartbeat(42)
    assert not path.exists()
