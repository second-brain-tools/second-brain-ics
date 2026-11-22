"""MCP tool annotations for this server's tools.

Clients use these hints, e.g. to skip a confirmation prompt for a read-only tool.
"""

from mcp.types import ToolAnnotations

# Reads and changes nothing. Unlike second-brain-core's READ_ONLY, it is "open
# world": a call can fetch a feed from its server on the internet, which is not
# this machine.
READ_ONLY_FEED = ToolAnnotations(read_only_hint=True, open_world_hint=True)
