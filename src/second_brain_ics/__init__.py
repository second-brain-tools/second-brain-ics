"""second-brain-ics: a read-only MCP server over calendar feeds.

It reads named iCalendar feeds (a share link such as Proton Calendar's), expands
repeating events, and answers in a named time zone. It cannot write: there is no
code here that changes a calendar.
"""

# Before anything imports fastmcp: stops FastMCP reading its settings, the
# transport among them, from a .env in the working directory.
import second_brain_core  # noqa: F401

__version__ = "0.1.0"
