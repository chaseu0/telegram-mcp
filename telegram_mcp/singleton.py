"""Single-instance Telegram MCP server coordination.

Design principles:
- **Port listening is the source of truth** for "daemon is up".
- **PID file is advisory** and reaped when the process is dead.
- **flock is released by the kernel** when a process exits; separate spawn vs daemon
  lock files so a waiting bridge cannot block the daemon from starting.
- MCP stdio clients **bridge only** by default; at most one spawner wins auto-start.
  Set TELEGRAM_MCP_AUTO_SPAWN=0 to forbid auto-spawn (use telegram-mcp-serve / launchd).
"""

from __future__ import annotations

import atexit
import fcntl
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from telegram_mcp import session_log

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


def auto_spawn_enabled() -> bool:
    return os.getenv("TELEGRAM_MCP_AUTO_SPAWN", "1").strip().lower() in {
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


def daemon_lock_path() -> Path:
    return get_cache_dir() / f"daemon-{get_port()}.lock"


def spawn_lock_path() -> Path:
    return get_cache_dir() / f"spawn-{get_port()}.lock"


def pid_path() -> Path:
    return get_cache_dir() / f"daemon-{get_port()}.pid"


def serve_log_path() -> Path:
    """Deprecated; structured logs live under ``logs/daemon.log``."""
    return session_log.daemon_log_path()


# Backward-compatible name used in docs/tests
def lock_path() -> Path:
    return daemon_lock_path()


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_port_open(host: Optional[str] = None, port: Optional[int] = None) -> bool:
    host = host or get_host()
    port = port if port is not None else get_port()
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def is_daemon_healthy() -> bool:
    """True when the SSE port accepts connections (authoritative)."""
    return is_port_open()


def read_daemon_pid() -> Optional[int]:
    try:
        return int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def remove_pid_file() -> None:
    try:
        pid_path().unlink(missing_ok=True)
    except OSError:
        pass


def write_pid_file() -> None:
    get_cache_dir().mkdir(parents=True, exist_ok=True)
    pid_path().write_text(str(os.getpid()), encoding="utf-8")


def reconcile_stale_state() -> None:
    """Drop PID file when the recorded daemon process no longer exists."""
    pid = read_daemon_pid()
    if pid is not None and not is_process_alive(pid):
        remove_pid_file()


def daemon_status() -> dict:
    """Inspectable snapshot for logging and diagnostics."""
    pid = read_daemon_pid()
    alive = pid is not None and is_process_alive(pid)
    return {
        "sse_url": get_sse_url(),
        "port_open": is_port_open(),
        "pid": pid,
        "pid_alive": alive,
        "auto_spawn": auto_spawn_enabled(),
    }


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
        reconcile_stale_state()
        if is_port_open():
            time.sleep(0.3)
            return
        time.sleep(0.2)
    status = daemon_status()
    session_log.log_spawn(
        "WAIT_TIMEOUT",
        host=get_host(),
        port=get_port(),
        timeout_sec=timeout_sec,
        status=status,
    )
    raise RuntimeError(
        f"telegram-mcp daemon not ready at {get_host()}:{get_port()} within "
        f"{timeout_sec:.0f}s (status={status}). "
        f"Start once: `telegram-mcp-serve` or `uv run main.py --serve`. "
        f"Logs: {session_log.daemon_log_path()}, {session_log.spawn_log_path()}"
    )


def _spawn_daemon() -> None:
    cmd = [sys.executable, "-m", "telegram_mcp.runner", "--serve"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )
    session_log.log_spawn(
        "SPAWN_DAEMON",
        child_pid=proc.pid,
        cmd=" ".join(cmd),
        daemon_log=session_log.daemon_log_path(),
    )


def _try_spawn_daemon_once() -> bool:
    """Return True if this process spawned the daemon subprocess."""
    spawn_lock = FileLock(spawn_lock_path())
    try:
        spawn_lock.acquire(exclusive=True, nonblocking=True)
    except BlockingIOError:
        session_log.log_spawn("SPAWN_DEFERRED", reason="another_spawner_holds_lock")
        return False

    try:
        reconcile_stale_state()
        if is_port_open():
            session_log.log_spawn("SPAWN_SKIPPED", reason="port_already_open")
            return False
        session_log.log_spawn("SPAWN_ATTEMPT", spawn_lock=str(spawn_lock_path()))
        _spawn_daemon()
        return True
    finally:
        # Release before waiting so the child can acquire daemon.lock.
        spawn_lock.release()


def ensure_singleton_server_running(timeout_sec: float = _STARTUP_TIMEOUT_SEC) -> str:
    """Return SSE URL; start daemon only if auto-spawn allowed and port is down."""
    url = get_sse_url()
    reconcile_stale_state()

    if is_port_open():
        session_log.log_spawn("CONNECT_EXISTING", sse_url=url, port_open=True)
        return url

    if not auto_spawn_enabled():
        status = daemon_status()
        raise RuntimeError(
            f"No telegram-mcp daemon listening at {url} (status={status}). "
            "Parallel MCP clients must not each start a daemon. "
            "Start one shared instance: `telegram-mcp-serve` "
            "(recommended: login item / launchd), then reload MCP clients."
        )

    spawned = _try_spawn_daemon_once()
    if spawned:
        session_log.log_spawn(
            "WAIT_FOR_PORT",
            wait_role="spawner",
            timeout_sec=timeout_sec,
            daemon_log=session_log.daemon_log_path(),
        )
        print(
            f"Spawned telegram-mcp daemon (log: {session_log.daemon_log_path()})",
            file=sys.stderr,
        )
    else:
        session_log.log_spawn(
            "WAIT_FOR_PORT",
            wait_role="waiter",
            timeout_sec=timeout_sec,
        )
        print(
            "Waiting for another client to finish starting the shared daemon...",
            file=sys.stderr,
        )

    _wait_for_port(timeout_sec)
    session_log.log_spawn("PORT_READY", sse_url=url)
    return url


def acquire_daemon_lock(*, nonblocking: bool = False) -> FileLock:
    """Exclusive lock held for the lifetime of the SSE daemon process."""
    holder = FileLock(daemon_lock_path())
    holder.acquire(exclusive=True, nonblocking=nonblocking)
    return holder


def register_daemon_shutdown_hooks() -> None:
    """Ensure PID file is removed on normal exit or SIGTERM/SIGINT."""

    def _cleanup() -> None:
        remove_pid_file()

    atexit.register(_cleanup)

    def _signal_handler(signum, _frame) -> None:
        _cleanup()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            pass
