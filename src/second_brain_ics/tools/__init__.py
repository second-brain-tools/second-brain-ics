"""MCP tool registration.

Every tool reads. There is no write tool to register: this server has no code
that changes a calendar, and a feed has no way to be written to.
"""

from fastmcp import FastMCP

from ..store import Store
from . import read


def register_tools(mcp: FastMCP, store: Store) -> None:
    """Register the MCP tools on ``mcp``, reading through ``store``."""
    read.register(mcp, store)
