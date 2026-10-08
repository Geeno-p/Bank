"""Start the server as an MCP client would, list tools, call one.  python scripts/mcp_smoke.py"""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def text(res) -> str:
    return "\n".join(getattr(c, "text", "") for c in res.content)


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "regintel.mcp_server.server"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        print("tools:    ", [t.name for t in (await s.list_tools()).tools])
        print("resources:", [str(x.uri) for x in (await s.list_resources()).resources])
        print("prompts:  ", [p.name for p in (await s.list_prompts()).prompts])
        print("\nget_gaps(band=HIGH):\n", text(await s.call_tool("get_gaps", {"band": "HIGH", "limit": 3}))[:800])


asyncio.run(main())