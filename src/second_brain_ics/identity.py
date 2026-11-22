"""How second-brain-ics describes itself to the shared library (second-brain-core)."""

from second_brain_core.server import Server

from .errors import CalendarError

SERVER = Server(
    name="second-brain-ics",
    prefix="SBICS_",
    package="second_brain_ics",
    default_port=8777,  # one above second-brain-imap's default
)

# This server's own failures: a tool turns them into a ToolError, and the audit
# line names them instead, so an operator can count outages from it.
NAMED_CAUSES = (CalendarError,)
