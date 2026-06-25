"""Stdio transport bridge to an existing MCP SSE server."""

from __future__ import annotations

import anyio
from mcp.client.sse import sse_client
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage


async def _pipe(
    source: anyio.abc.ObjectReceiveStream[SessionMessage | Exception],
    dest: anyio.abc.ObjectSendStream[SessionMessage | Exception],
) -> None:
    async for item in source:
        await dest.send(item)


async def run_stdio_sse_bridge(sse_url: str) -> None:
    """Relay MCP JSON-RPC between local stdio (Cursor/mcporter) and remote SSE."""
    async with stdio_server() as (stdio_read, stdio_write):
        async with sse_client(sse_url) as (sse_read, sse_write):
            async with anyio.create_task_group() as tg:
                tg.start_soon(_pipe, stdio_read, sse_write)
                tg.start_soon(_pipe, sse_read, stdio_write)
