"""Detect and terminate stale stdio bridge processes in singleton mode.

Only targets MCP bridge clients (``main.py`` / ``telegram-mcp`` without ``--serve``).
Never kills the shared daemon (``telegram-mcp-serve`` / ``--serve``).
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from telegram_mcp import session_log
from telegram_mcp import singleton

_DEFAULT_MAX_AGE_SEC = float(os.getenv("TELEGRAM_MCP_BRIDGE_MAX_AGE_SEC", "7200") or 0)
_DEFAULT_IDLE_SEC = float(os.getenv("TELEGRAM_MCP_BRIDGE_IDLE_SEC", "1800") or 0)
_DAEMON_MARKERS = ("--serve", "telegram-mcp-serve")
_BRIDGE_MARKERS = (
    "main.py",
    "telegram_mcp.runner",
    "telegram-mcp",
    "telegram_mcp",
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    etime_sec: float
    command: str


@dataclass(frozen=True)
class StaleBridge:
    proc: ProcessInfo
    reason: str


def bridge_heartbeat_path(pid: Optional[int] = None) -> Path:
    pid = pid if pid is not None else os.getpid()
    return singleton.get_cache_dir() / "bridges" / f"{pid}.heartbeat"


def touch_bridge_heartbeat(pid: Optional[int] = None) -> None:
    path = bridge_heartbeat_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def remove_bridge_heartbeat(pid: Optional[int] = None) -> None:
    try:
        bridge_heartbeat_path(pid).unlink(missing_ok=True)
    except OSError:
        pass


def is_daemon_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _DAEMON_MARKERS)


def is_bridge_command(command: str) -> bool:
    if is_daemon_command(command):
        return False
    lowered = command.lower()
    if "--stdio-direct" in lowered:
        return True
    return any(marker.lower() in lowered for marker in _BRIDGE_MARKERS)


def parse_etime(etime: str) -> float:
    """Parse ``ps`` elapsed time (``MM:SS``, ``HH:MM:SS``, or ``D-HH:MM:SS``)."""
    text = etime.strip()
    day_sec = 0.0
    if "-" in text:
        days_part, text = text.split("-", 1)
        day_sec = int(days_part) * 86400
    parts = text.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = (int(p) for p in parts)
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = (int(p) for p in parts)
    elif len(parts) == 1:
        return day_sec + int(parts[0])
    else:
        raise ValueError(f"unrecognized etime: {etime!r}")
    return day_sec + hours * 3600 + minutes * 60 + seconds


def _parse_ps_line(line: str) -> Optional[ProcessInfo]:
    line = line.strip()
    if not line:
        return None
    match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.+)$", line)
    if not match:
        return None
    pid = int(match.group(1))
    ppid = int(match.group(2))
    etime = match.group(3)
    command = match.group(4).strip()
    try:
        etime_sec = parse_etime(etime)
    except ValueError:
        etime_sec = 0.0
    return ProcessInfo(pid=pid, ppid=ppid, etime_sec=etime_sec, command=command)


def list_processes(
    *,
    ps_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[ProcessInfo]:
    result = ps_runner(
        ["ps", "-ww", "-ax", "-o", "pid=,ppid=,etime=,command="],
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
    )
    procs: list[ProcessInfo] = []
    for line in (result.stdout or "").splitlines():
        proc = _parse_ps_line(line)
        if proc is not None:
            procs.append(proc)
    return procs


def _heartbeat_idle_sec(pid: int, *, now: Optional[float] = None) -> Optional[float]:
    path = bridge_heartbeat_path(pid)
    if not path.is_file():
        return None
    now = now if now is not None else time.time()
    return max(0.0, now - path.stat().st_mtime)


def classify_stale_bridge(
    proc: ProcessInfo,
    *,
    daemon_pid: Optional[int],
    max_age_sec: float = _DEFAULT_MAX_AGE_SEC,
    idle_sec: float = _DEFAULT_IDLE_SEC,
    now: Optional[float] = None,
    is_alive: Callable[[int], bool] = singleton.is_process_alive,
    parent: Optional[ProcessInfo] = None,
) -> Optional[str]:
    if daemon_pid is not None and proc.pid == daemon_pid:
        return None
    if not is_bridge_command(proc.command):
        return None
    if is_daemon_command(proc.command):
        return None

    if proc.ppid <= 1:
        return "orphaned_ppid"
    if parent is not None and parent.ppid <= 1 and "uv" in parent.command.lower():
        return "orphaned_launcher"
    if not is_alive(proc.ppid):
        return "parent_dead"

    if max_age_sec > 0 and proc.etime_sec >= max_age_sec:
        return "max_age"

    if idle_sec > 0:
        heartbeat_idle = _heartbeat_idle_sec(proc.pid, now=now)
        if heartbeat_idle is not None and heartbeat_idle >= idle_sec:
            return "idle_heartbeat"

    return None


def _index_by_pid(procs: list[ProcessInfo]) -> dict[int, ProcessInfo]:
    return {proc.pid: proc for proc in procs}


def find_stale_bridges(
    *,
    max_age_sec: float = _DEFAULT_MAX_AGE_SEC,
    idle_sec: float = _DEFAULT_IDLE_SEC,
    procs: Optional[list[ProcessInfo]] = None,
    daemon_pid: Optional[int] = None,
) -> list[StaleBridge]:
    if procs is None:
        procs = list_processes()
    if daemon_pid is None:
        singleton.reconcile_stale_state()
        daemon_pid = singleton.read_daemon_pid()

    by_pid = _index_by_pid(procs)
    stale: list[StaleBridge] = []
    for proc in procs:
        parent = by_pid.get(proc.ppid)
        reason = classify_stale_bridge(
            proc,
            daemon_pid=daemon_pid,
            max_age_sec=max_age_sec,
            idle_sec=idle_sec,
            parent=parent,
        )
        if reason:
            stale.append(StaleBridge(proc=proc, reason=reason))
    return stale


def terminate_process(pid: int, *, sig: int = signal.SIGTERM) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def cleanup_stale_bridges(
    *,
    dry_run: bool = False,
    max_age_sec: float = _DEFAULT_MAX_AGE_SEC,
    idle_sec: float = _DEFAULT_IDLE_SEC,
) -> list[StaleBridge]:
    stale = find_stale_bridges(max_age_sec=max_age_sec, idle_sec=idle_sec)
    for item in stale:
        session_log.log_spawn(
            "BRIDGE_CLEANUP",
            action="dry_run" if dry_run else "kill",
            target_pid=item.proc.pid,
            ppid=item.proc.ppid,
            reason=item.reason,
            command=item.proc.command[:200],
        )
        if not dry_run:
            terminate_process(item.proc.pid)
    return stale


def reconcile_stale_bridges_on_startup() -> list[StaleBridge]:
    """Best-effort cleanup when the daemon starts (does not raise)."""
    try:
        removed = cleanup_stale_bridges(dry_run=False)
        if removed:
            session_log.log_daemon(
                "BRIDGE_RECONCILE",
                count=len(removed),
                pids=[item.proc.pid for item in removed],
            )
        return removed
    except Exception as exc:
        session_log.log_daemon("BRIDGE_RECONCILE_FAILED", error=str(exc))
        return []


def cleanup_entry(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Terminate stale telegram-mcp stdio bridge processes (never the --serve daemon).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List stale bridges without sending SIGTERM",
    )
    parser.add_argument(
        "--max-age-sec",
        type=float,
        default=_DEFAULT_MAX_AGE_SEC,
        help="Kill bridges at least this old (0=disable; env TELEGRAM_MCP_BRIDGE_MAX_AGE_SEC)",
    )
    parser.add_argument(
        "--idle-sec",
        type=float,
        default=_DEFAULT_IDLE_SEC,
        help="Kill when heartbeat idle exceeds this (0=disable; env TELEGRAM_MCP_BRIDGE_IDLE_SEC)",
    )
    args = parser.parse_args(argv)

    stale = cleanup_stale_bridges(
        dry_run=args.dry_run,
        max_age_sec=args.max_age_sec,
        idle_sec=args.idle_sec,
    )
    if not stale:
        print("No stale bridge processes found.", file=sys.stderr)
        return

    verb = "Would terminate" if args.dry_run else "Terminated"
    for item in stale:
        print(
            f"{verb} pid={item.proc.pid} ppid={item.proc.ppid} "
            f"reason={item.reason} etime={item.proc.etime_sec:.0f}s "
            f"cmd={item.proc.command[:120]}",
            file=sys.stderr,
        )
    print(f"{verb} {len(stale)} bridge process(es).", file=sys.stderr)
