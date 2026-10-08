import asyncio

from regintel.mcp_server.server import mcp
from regintel.tools.readonly import TOOLS


def test_all_tools_registered():
    assert set(TOOLS) <= {t.name for t in asyncio.run(mcp.list_tools())}