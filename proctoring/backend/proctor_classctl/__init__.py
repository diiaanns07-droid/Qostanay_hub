"""T04: exam sessions, policies and teacher commands for Qorgau Class (qorgau.class.v1, teacher side).

Pure service code that the class server (T01/C1) mounts; it owns no sockets. The server feeds it
student connections/messages and gives it a transport to send `command` messages:

    control = ClassControl(transport=server_transport)            # one per class server
    control.student_connected(student_id, hello_message)          # after `welcome`
    control.handle_student_message(student_id, message)           # ack / status / command_progress
    control.student_disconnected(student_id)
    control.tick()                                                # ~every 500 ms (timeouts, expiry)
    app.include_router(create_teacher_router(control, get_principal), prefix="/api/teacher")

A command is shown as done only after the student client confirmed it (ack), never because the
server accepted the request. Expired commands are never delivered. See handoffs/T04/.
"""

from __future__ import annotations

from .access import AccessDenied, Principal, Role
from .clock import Clock, FakeClock, SystemClock
from .service import ClassControl

__all__ = ["AccessDenied", "ClassControl", "Clock", "FakeClock", "Principal", "Role", "SystemClock"]
