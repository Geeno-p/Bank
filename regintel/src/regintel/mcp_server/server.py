"""RegIntel MCP server (stdio): read-only compliance tools for any MCP client.
  python -m regintel.mcp_server.server"""
from __future__ import annotations

import json
import logging
import sys

from mcp.server.mcpserver import MCPServer

from regintel.tools import readonly

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)   # stdout carries the protocol; never print()

mcp = MCPServer("regintel", instructions=(
    "Read-only access to a bank's regulatory compliance knowledge (synthetic demo bank). "
    "Start with overview, then get_gaps / search_obligations / get_traceability. "
    "Cite gap IDs, control IDs and clause refs. These tools cannot change data."))

for fn in readonly.TOOLS.values():        # name, docstring and signature become the MCP tool schema
    mcp.add_tool(fn)


@mcp.resource("regintel://overview")
def overview_resource() -> str:
    """Current compliance posture (JSON)."""
    return json.dumps(readonly.overview(), indent=2)


@mcp.resource("regintel://changes")
def changes_resource() -> str:
    """Detected regulatory changes (JSON)."""
    return json.dumps(readonly.get_changes(), indent=2, default=str)


@mcp.prompt()
def gap_briefing(band: str = "HIGH") -> str:
    """Briefing on gaps of one risk band."""
    return (f"Using the regintel tools, brief me on all {band} gaps: for each give the obligation (regulation + "
            f"clause), the control, why it is a gap, the risk factors (explain_risk) and the evidence. "
            f"End with the top 3 priorities.")


def main() -> None:
    mcp.run()          # stdio transport


if __name__ == "__main__":
    main()