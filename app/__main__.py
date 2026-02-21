"""Entrypoint for `python -m app` (alternative to `python -m app.server`).

Transport selection is driven by MCP_TRANSPORT env var (default: stdio).
"""

from app.config import settings
from app.server import mcp

if settings.mcp_transport == "http":
    mcp.run(transport="http", host=settings.mcp_host, port=settings.mcp_port)
else:
    mcp.run()
