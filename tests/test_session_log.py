import re
from pathlib import Path

import pytest

from telegram_mcp import session_log


def test_log_paths_under_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_MCP_PORT", "19999")
    logs = tmp_path / "logs"
    assert session_log.get_logs_dir() == logs
    assert session_log.daemon_log_path() == logs / "daemon.log"
    assert session_log.clients_log_path() == logs / "clients.log"
    assert session_log.spawn_log_path() == logs / "spawn.log"
    assert session_log.serve_log_path() == logs / "daemon.log"


def test_format_log_line_includes_role_pid_event():
    line = session_log.format_log_line(
        "DAEMON",
        "SESSION_START",
        port=18765,
        sse_url="http://127.0.0.1:18765/sse",
    )
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z \[DAEMON\] pid=\d+ SESSION_START ",
        line,
    )
    assert "port=18765" in line
    assert "sse_url=http://127.0.0.1:18765/sse" in line


def test_begin_daemon_session_truncates_daemon_log(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    daemon = session_log.daemon_log_path()
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_text("stale line\n", encoding="utf-8")

    session_log.begin_daemon_session(port=18765, sse_url="http://127.0.0.1:18765/sse")

    text = daemon.read_text(encoding="utf-8")
    assert "stale line" not in text
    assert "SESSION_START" in text
    assert "port=18765" in text


def test_append_log_roles(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    session_log.log_client("CLIENT_CONNECT", sse_url="http://127.0.0.1:1/sse")
    session_log.log_spawn("PORT_READY", sse_url="http://127.0.0.1:1/sse")

    clients = session_log.clients_log_path().read_text(encoding="utf-8")
    spawn = session_log.spawn_log_path().read_text(encoding="utf-8")
    assert "[CLIENT]" in clients
    assert "CLIENT_CONNECT" in clients
    assert "[SPAWN]" in spawn
    assert "PORT_READY" in spawn


def test_cleanup_legacy_cache_files(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_CACHE_DIR", str(tmp_path))
    (tmp_path / "serve-18765.log").write_text("old\n", encoding="utf-8")
    (tmp_path / "singleton-18765.pid").write_text("1\n", encoding="utf-8")
    (tmp_path / "singleton-18765.lock").touch()
    (tmp_path / "daemon-18765.pid").write_text("2\n", encoding="utf-8")

    removed = session_log.cleanup_legacy_cache_files()

    assert len(removed) == 3
    assert not (tmp_path / "serve-18765.log").exists()
    assert not (tmp_path / "singleton-18765.pid").exists()
    assert not (tmp_path / "singleton-18765.lock").exists()
    assert (tmp_path / "daemon-18765.pid").exists()


def test_archive_large_mcp_errors_log(tmp_path):
    log_path = tmp_path / "mcp_errors.log"
    log_path.write_text("x" * 60_000, encoding="utf-8")

    archive = session_log.archive_large_mcp_errors_log(tmp_path, min_bytes=50_000)

    assert archive is not None
    assert Path(archive).is_file()
    assert log_path.is_file()
    assert "Archived" in log_path.read_text(encoding="utf-8")


def test_archive_skips_small_log(tmp_path):
    log_path = tmp_path / "mcp_errors.log"
    log_path.write_text("small\n", encoding="utf-8")
    assert session_log.archive_large_mcp_errors_log(tmp_path) is None
    assert log_path.read_text(encoding="utf-8") == "small\n"
