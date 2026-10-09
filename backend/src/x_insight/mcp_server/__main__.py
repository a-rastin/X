"""Private stdio entry point: ``python -m x_insight.mcp_server``.

Protocol goes to stdout only; diagnostics go to stderr (stderr can never
corrupt the stdout protocol). No arguments carry secrets; the grant comes
from ``X_INSIGHT_MCP_GRANT`` in the protected worker environment.
"""

from __future__ import annotations

import logging
import sys

import anyio

from x_insight.mcp_server.server import build_server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    async def _run() -> None:
        from mcp.server.stdio import stdio_server

        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(_run)


if __name__ == "__main__":
    main()
