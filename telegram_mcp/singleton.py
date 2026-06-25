"""Single-instance Telegram MCP server coordination.

When TELEGRAM_MCP_SINGLETON is enabled (default), stdio launches connect to one
shared SSE server instead of each spawning their own Telethon session.
"""

from __future__ import annotations

import fcntl
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_PORT = 18765
DEFAULT_HOST = "127.0.0.1"
_STARTUP_TIMEOUT_SEC = float(os.getenv("TELEGRAM_MCP_STARTUP_TIMEOUT", "120"))


def singleton_enabled() -> bool:
    return os.getenv("TELEGRAM_MCP_SINGLETON", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_host() -> str:
    return os.getenv("TELEGRAM_MCP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST


def get_port() -> int:
    raw = os.getenv("TELEGRAM_MCP_PORT", str(DEFAULT_PORT)).strip()
    return int(raw)


def get_cache_dir() -> Path:
    raw = os.getenv("TELEGRAM_MCP_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "telegram-mcp"


def get_sse_url() -> str:
    return f"http://{get_host()}:{get_port()}/sse"


def lock_path() -> Path:
    return get_cache_dir() / f"singleton-{get_port()}.lock"


def pid_path() -> Path:
    return get_cache_dir() / f"singleton-{get_port()}.pid"


def serve_log_path() -> Path:
    return get_cache_dir() / f"serve-{get_port()}.log"


def is_port_open(host: Optional[str] = None, port: Optional[int] = None) -> bool:
    host = host or get_host()
    port = port if port is not None else get_port()
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


class FileLock:
    """POSIX advisory lock on a cache file."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: Optional[int] = None

    def acquire(self, *, exclusive: bool = True, nonblocking: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if nonblocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> FileLock:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _wait_for_port(timeout_sec: float = _STARTUP_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if is_port_open():
            # Brief settle time so Telethon init in the daemon can finish.
            time.sleep(0.4)
            return
        time.sleep(0.2)
    log_hint = serve_log_path()
    raise RuntimeError(
        f"telegram-mcp singleton server did not become ready on "
        f"{get_host()}:{get_port()} within {timeout_sec:.0f}s. "
        f"Check {log_hint} and mcp_errors.log."
    )


def _spawn_daemon() -> None:
    log_path = serve_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    cmd = [sys.executable, "-m", "telegram_mcp.runner", "--serve"]
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )


def ensure_singleton_server_running(timeout_sec: float = _STARTUP_TIMEOUT_SEC) -> str:
    """Start the shared SSE server if needed; return its SSE URL."""
    url = get_sse_url()
    if is_port_open():
        return url

    spawn_lock = FileLock(lock_path())
    try:
        spawn_lock.acquire(exclusive=True, nonblocking=True)
    except BlockingIOError:
        _wait_for_port(timeout_sec)
        return url

    try:
        if is_port_open():
            return url
        _spawn_daemon()
        _wait_for_port(timeout_sec)
        return url
    finally:
        spawn_lock.release()


def acquire_daemon_lock(*, nonblocking: bool = False) -> FileLock:
    """Lock held for the lifetime of the SSE daemon process."""
    holder = FileLock(lock_path())
    holder.acquire(exclusive=True, nonblocking=nonblocking)
    return holder


def write_pid_file() -> None:
    pid_path().parent.mkdir(parents=True, exist_ok=True)
    pid_path().write_text(str(os.getpid()), encoding="utf-8")


def remove_pid_file() -> None:
    try:
        pid_path().unlink(missing_ok=True)
    except OSError:
        pass


def read_daemon_pid() -> Optional[int]:
    try:
        return int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
