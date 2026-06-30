"""Stdio transport bridge to an existing MCP SSE server."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable

import anyio
from mcp.client.sse import sse_client
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage

from telegram_mcp import cleanup
from telegram_mcp import session_log
from telegram_mcp import singleton

_PARENT_CHECK_SEC = float(os.getenv("TELEGRAM_MCP_BRIDGE_PARENT_CHECK_SEC", "5") or 5)
_IDLE_SEC = float(os.getenv("TELEGRAM_MCP_BRIDGE_IDLE_SEC", "1800") or 0)


def bridge_idle_sec() -> float:
    return _IDLE_SEC


def bridge_parent_check_sec() -> float:
    return _PARENT_CHECK_SEC


def _parent_launcher_orphaned() -> bool:
    """True when our parent is an orphaned ``uv`` launcher (Cursor session gone)."""
    ppid = os.getppid()
    if ppid <= 1:
        return True
    try:
        out = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "ppid=,command="],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        line = (out.stdout or "").strip()
        if not line:
            return False
        parts = line.split(None, 1)
        if len(parts) < 2:
            return False
        parent_ppid = int(parts[0])
        command = parts[1]
        return parent_ppid <= 1 and "uv" in command.lower()
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _should_exit_bridge(
    last_activity: float,
    *,
    now: float,
    is_alive: Callable[[int], bool] = singleton.is_process_alive,
) -> str | None:
    ppid = os.getppid()
    if ppid <= 1:
        return "orphaned_ppid"
    if _parent_launcher_orphaned():
        return "orphaned_launcher"
    if not is_alive(ppid):
        return "parent_dead"
    if _IDLE_SEC > 0 and now - last_activity >= _IDLE_SEC:
        return "idle_timeout"
    return None


async def _pipe(
    source: anyio.abc.ObjectReceiveStream[SessionMessage | Exception],
    dest: anyio.abc.ObjectSendStream[SessionMessage | Exception],
    *,
    on_activity: Callable[[], None] | None = None,
) -> None:
    async for item in source:
        if on_activity is not None:
            on_activity()
        await dest.send(item)


async def _bridge_watchdog(
    last_activity_holder: list[float],
    cancel_scope: anyio.CancelScope,
) -> None:
    while True:
        await anyio.sleep(_PARENT_CHECK_SEC)
        reason = _should_exit_bridge(
            last_activity_holder[0],
            now=time.monotonic(),
        )
        if reason:
            session_log.log_client("BRIDGE_WATCHDOG_EXIT", reason=reason)
            cancel_scope.cancel()
            return


async def run_stdio_sse_bridge(sse_url: str) -> None:
    """Relay MCP JSON-RPC between local stdio (Cursor/mcporter) and remote SSE."""
    last_activity = [time.monotonic()]
    cleanup.touch_bridge_heartbeat()

    def _mark_activity() -> None:
        last_activity[0] = time.monotonic()
        cleanup.touch_bridge_heartbeat()

    try:
        async with stdio_server() as (stdio_read, stdio_write):
            async with sse_client(sse_url) as (sse_read, sse_write):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(_bridge_watchdog, last_activity, tg.cancel_scope)
                    tg.start_soon(
                        _pipe,
                        stdio_read,
                        sse_write,
                        on_activity=_mark_activity,
                    )
                    tg.start_soon(
                        _pipe,
                        sse_read,
                        stdio_write,
                        on_activity=_mark_activity,
                    )
    finally:
        cleanup.remove_bridge_heartbeat()
