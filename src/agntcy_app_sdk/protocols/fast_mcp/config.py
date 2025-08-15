import os

FASTMCP_HOST = os.getenv("FAST_MCP_HOST", "localhost")
FASTMCP_PORT = int(os.getenv("FAST_MCP_PORT", 8000))
