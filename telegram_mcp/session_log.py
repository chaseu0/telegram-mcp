"""Structured session logging for singleton daemon and stdio bridge clients.

Log layout under ``{TELEGRAM_MCP_CACHE_DIR}/logs/`` (default ``~/.cache/telegram-mcp/logs/``):

- ``daemon.log`` — daemon lifecycle (truncated on each fresh ``--serve`` start)
- ``clients.log`` — stdio bridge connect/disconnect (append within a daemon session)
- ``spawn.log`` — auto-spawn / wait-for-port coordination

MCP tool RPC errors stay in the repo ``mcp_errors.log`` only (see ``runtime.py``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_PORT = 18765


def get_cache_dir() -> Path:
    raw = os.getenv("TELEGRAM_MCP_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "telegram-mcp"


def get_port() -> int:
    raw = os.getenv("TELEGRAM_MCP_PORT", str(DEFAULT_PORT)).strip()
    return int(raw)


def get_logs_dir() -> Path:
    return get_cache_dir() / "logs"


def daemon_log_path() -> Path:
    return get_logs_dir() / "daemon.log"


def clients_log_path() -> Path:
    return get_logs_dir() / "clients.log"


def spawn_log_path() -> Path:
    return get_logs_dir() / "spawn.log"


def serve_log_path() -> Path:
    """Deprecated alias; prefer :func:`daemon_log_path`."""
    return daemon_log_path()


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def format_log_line(role: str, event: str, **fields: Any) -> str:
    """Build one log line: ISO time + [ROLE] pid=… event key=value …"""
    pid = os.getpid()
    parts = [iso_timestamp(), f"[{role}]", f"pid={pid}", event]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ").replace("\r", " ")
        parts.append(f"{key}={text}")
    return " ".join(parts)


def _ensure_logs_dir() -> None:
    get_logs_dir().mkdir(parents=True, exist_ok=True)


def _write_lines(path: Path, lines: list[str], *, mode: str) -> None:
    _ensure_logs_dir()
    with open(path, mode, encoding="utf-8") as fh:
        for line in lines:
            fh.write(line)
            if not line.endswith("\n"):
                fh.write("\n")


def append_log(path: Path, line: str) -> None:
    _write_lines(path, [line], mode="a")


def truncate_log(path: Path, lines: list[str]) -> None:
    _write_lines(path, lines, mode="w")


def get_parent_process_info() -> dict[str, Optional[int | str]]:
    ppid = os.getppid()
    info: dict[str, Optional[int | str]] = {"ppid": ppid}
    if ppid <= 1:
        return info
    try:
        out = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        name = (out.stdout or "").strip()
        if name:
            info["parent_comm"] = name
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def cleanup_legacy_cache_files() -> list[str]:
    """Remove pre-structured-log artifacts from the cache directory."""
    removed: list[str] = []
    cache = get_cache_dir()
    if not cache.is_dir():
        return removed

    patterns = ("serve-*.log", "singleton-*.pid", "singleton-*.lock")
    for pattern in patterns:
        for path in cache.glob(pattern):
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                pass
    return removed


def archive_large_mcp_errors_log(
    repo_root: Optional[Path] = None,
    *,
    min_bytes: int = 50_000,
) -> Optional[str]:
    """Archive repo ``mcp_errors.log`` when large; leave a pointer note in place."""
    root = repo_root or Path(__file__).resolve().parent.parent
    log_path = root / "mcp_errors.log"
    if not log_path.is_file():
        return None
    size = log_path.stat().st_size
    if size < min_bytes:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = root / f"mcp_errors.log.{stamp}.bak"
    shutil.move(str(log_path), str(archive))
    note = (
        f"# Archived {iso_timestamp()} — previous log ({size} bytes) moved to "
        f"{archive.name}\n"
        f"# MCP tool RPC errors only; connection events go to "
        f"{daemon_log_path()} and {clients_log_path()}\n"
    )
    log_path.write_text(note, encoding="utf-8")
    return str(archive)


def begin_daemon_session(
    *,
    port: Optional[int] = None,
    sse_url: Optional[str] = None,
    host: Optional[str] = None,
) -> None:
    """Fresh ``--serve`` start: clean legacy files and truncate ``daemon.log``."""
    cleanup_legacy_cache_files()
    port = port if port is not None else get_port()
    if host is None:
        host = os.getenv("TELEGRAM_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if sse_url is None:
        sse_url = f"http://{host}:{port}/sse"

    banner = format_log_line(
        "DAEMON",
        "SESSION_START",
        port=port,
        host=host,
        sse_url=sse_url,
        daemon_log=daemon_log_path(),
        clients_log=clients_log_path(),
        spawn_log=spawn_log_path(),
    )
    separator = "=" * 72
    truncate_log(
        daemon_log_path(),
        [
            separator,
            banner,
            format_log_line("DAEMON", "LOG_LAYOUT", note="daemon=daemon.log clients=clients.log spawn=spawn.log"),
            separator,
        ],
    )


def log_daemon(event: str, **fields: Any) -> None:
    append_log(daemon_log_path(), format_log_line("DAEMON", event, **fields))


def log_client(event: str, **fields: Any) -> None:
    append_log(clients_log_path(), format_log_line("CLIENT", event, **fields))


def log_spawn(event: str, **fields: Any) -> None:
    append_log(spawn_log_path(), format_log_line("SPAWN", event, **fields))
