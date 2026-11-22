"""Turning a failure inside a tool into the error an MCP client sees.

second-brain-core's rules (``second_brain_core.failure``), bound to this server: a
planned failure (a :class:`~..errors.CalendarError`) carries its own sentence,
and anything unplanned gets a fixed one, since an exception's own text can hold
a feed's address or an event's text.
"""

from second_brain_core import failure

from ..identity import SERVER

guarded = failure.guarded(SERVER)
INTERNAL_MESSAGE = failure.internal_message(SERVER)
