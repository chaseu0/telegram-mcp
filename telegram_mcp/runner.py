"""Application entrypoints for the Telegram MCP server."""

from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution

try:
    assert_safe_distribution()
except UnsafeInstallationError as exc:
    raise SystemExit(str(exc)) from None

from telegram_mcp import runtime as _runtime
from telegram_mcp.bridge import run_stdio_sse_bridge
from telegram_mcp.runtime import *
from telegram_mcp import session_log
from telegram_mcp import singleton
from telegram_mcp import session_log
import telegram_mcp.tools  # noqa: F401 - registers MCP tools via decorators

_RUNNER_FLAGS = frozenset({"--serve", "--stdio-direct"})


async def _connect_authorized_client(label, client) -> None:
    await client.connect()
    if await client.is_user_authorized():
        return

    raise RuntimeError(
        f"Telegram client '{label}' is not authorized. Interactive phone login "
        "is disabled for the MCP server because it runs over stdio. Generate a "
        "session string with `uv run session_string_generator.py`, then set "
        "TELEGRAM_SESSION_STRING or TELEGRAM_SESSION_STRING_<LABEL> in .env. "
        "For existing file sessions, run the login outside the MCP server first."
    )


async def _bootstrap_telegram_clients() -> None:
    labels = ", ".join(clients.keys())
    print(f"Starting {len(clients)} Telegram client(s) ({labels})...", file=sys.stderr)
    await asyncio.gather(
        *(_connect_authorized_client(label, cl) for label, cl in clients.items())
    )

    print("Warming entity caches (background)...", file=sys.stderr)

    async def _warm_caches() -> None:
        try:
            await asyncio.gather(*(cl.get_dialogs() for cl in clients.values()))
            print("Entity caches warmed.", file=sys.stderr)
        except Exception as warm_exc:
            print(f"Entity cache warm failed: {warm_exc}", file=sys.stderr)

    asyncio.create_task(_warm_caches())
    print(f"Telegram client(s) started ({labels}).", file=sys.stderr)


async def _disconnect_clients() -> None:
    try:
        await asyncio.gather(*(cl.disconnect() for cl in clients.values()), return_exceptions=True)
    except Exception:
        pass


def _handle_startup_error(exc: Exception) -> None:
    print(f"Error starting client: {exc}", file=sys.stderr)
    if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc):
        print(
            "Database lock detected. Enable TELEGRAM_MCP_SINGLETON=1 (default) so only "
            "one Telethon session runs, or stop duplicate telegram-mcp processes.",
            file=sys.stderr,
        )
    sys.exit(1)


async def _main_stdio_direct() -> None:
    """Legacy mode: Telethon + MCP stdio in this process (one client per spawn)."""
    try:
        await _bootstrap_telegram_clients()
        print("Running MCP server (stdio, direct)...", file=sys.stderr)
        await mcp.run_stdio_async()
    except Exception as exc:
        _handle_startup_error(exc)
    finally:
        await _disconnect_clients()


async def _serve_main() -> None:
    """Singleton daemon: one Telethon session + MCP over SSE."""
    singleton.reconcile_stale_state()

    if singleton.is_port_open():
        pid = singleton.read_daemon_pid()
        session_log.log_daemon(
            "DUPLICATE_SERVE_ATTEMPT",
            sse_url=singleton.get_sse_url(),
            existing_pid=pid,
            port_open=True,
        )
        print(
            f"telegram-mcp daemon already listening on {singleton.get_sse_url()} "
            f"(pid {pid or 'unknown'})",
            file=sys.stderr,
        )
        return

    daemon_lock = None
    try:
        daemon_lock = singleton.acquire_daemon_lock(nonblocking=True)
    except BlockingIOError:
        session_log.log_daemon(
            "DUPLICATE_SERVE_ATTEMPT",
            reason="daemon_lock_held",
            sse_url=singleton.get_sse_url(),
        )
        print(
            "Daemon lock held by another process; waiting for SSE port...",
            file=sys.stderr,
        )
        try:
            singleton._wait_for_port()
        except RuntimeError as exc:
            session_log.log_daemon("STARTUP_FAILED", error=str(exc))
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        return

    session_log.begin_daemon_session(
        port=singleton.get_port(),
        sse_url=singleton.get_sse_url(),
        host=singleton.get_host(),
    )
    session_log.archive_large_mcp_errors_log()
    session_log.log_daemon("DAEMON_START", auto_spawn=singleton.auto_spawn_enabled())
    singleton.register_daemon_shutdown_hooks()
    try:
        session_log.log_daemon("TELETHON_BOOTSTRAP_BEGIN", clients=list(clients.keys()))
        await _bootstrap_telegram_clients()
        session_log.log_daemon("TELETHON_BOOTSTRAP_OK", clients=list(clients.keys()))
        singleton.write_pid_file()
        session_log.log_daemon(
            "SSE_LISTENING",
            sse_url=singleton.get_sse_url(),
            pid_file=str(singleton.pid_path()),
        )
        print(
            f"telegram-mcp daemon SSE at {singleton.get_sse_url()} (pid {os.getpid()})",
            file=sys.stderr,
        )
        await mcp.run_sse_async()
    except Exception as exc:
        session_log.log_daemon("STARTUP_FAILED", error=str(exc))
        _handle_startup_error(exc)
    finally:
        session_log.log_daemon("DAEMON_SHUTDOWN")
        singleton.remove_pid_file()
        if daemon_lock is not None:
            daemon_lock.release()
        await _disconnect_clients()


async def _main_singleton_stdio() -> None:
    """Connect stdio to the shared singleton SSE server (no local Telethon)."""
    parent = session_log.get_parent_process_info()
    session_log.log_client(
        "CLIENT_CONNECT",
        auto_spawn=singleton.auto_spawn_enabled(),
        **parent,
    )
    try:
        singleton.reconcile_stale_state()
        sse_url = singleton.ensure_singleton_server_running()
        status = singleton.daemon_status()
        session_log.log_client(
            "BRIDGE_START",
            sse_url=sse_url,
            daemon_status=status,
            **parent,
        )
        print(
            f"Bridging stdio to shared daemon at {sse_url} (status={status})",
            file=sys.stderr,
        )
        await run_stdio_sse_bridge(sse_url)
        session_log.log_client("BRIDGE_END", sse_url=sse_url, reason="normal", **parent)
    except Exception as exc:
        session_log.log_client("BRIDGE_END", reason="error", error=str(exc), **parent)
        print(f"Error connecting to shared daemon: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        session_log.log_client("CLIENT_DISCONNECT", **parent)


def _split_runner_argv(argv: list[str]) -> tuple[bool, bool, list[str]]:
    serve = "--serve" in argv
    stdio_direct = "--stdio-direct" in argv
    filtered = [arg for arg in argv if arg not in _RUNNER_FLAGS]
    return serve, stdio_direct, filtered


def serve_entry() -> None:
    """Console entry for `telegram-mcp-serve` (explicit singleton daemon)."""
    serve, _stdio_direct, filtered_argv = _split_runner_argv(sys.argv[1:])
    _configure_allowed_roots_from_cli(filtered_argv)
    _runtime._apply_exposed_tools_mode()
    nest_asyncio.apply()
    asyncio.run(_serve_main())


def main() -> None:
    serve, stdio_direct, filtered_argv = _split_runner_argv(sys.argv[1:])
    _configure_allowed_roots_from_cli(filtered_argv)
    _runtime._apply_exposed_tools_mode()
    nest_asyncio.apply()

    if serve:
        asyncio.run(_serve_main())
    elif stdio_direct or not singleton.singleton_enabled():
        asyncio.run(_main_stdio_direct())
    else:
        asyncio.run(_main_singleton_stdio())


if __name__ == "__main__":
    main()
