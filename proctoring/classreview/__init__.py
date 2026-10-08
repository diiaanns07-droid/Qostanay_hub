"""Qorgau Class — episodes, clips and teacher decisions (T03), protocol qorgau.class.v1.

Server side (mounted by the class server):
    from classreview import ReviewStore, ReviewConfig, create_review_router
    store = ReviewStore(ReviewConfig.from_env()); store.open()
    app.include_router(create_review_router(store, teacher_guard=..., student_resolver=..., status_provider=...))
    store.ingest_incident(student_id, incident_msg, class_session_id=...)   # from /ws/student
    store.note_clip_requested(student_id, incident_id, command_id)            # when sending request_clip
    store.note_command_ack(command_id, ok, error_ru)                          # on its ack
    store.on_change(lambda event: broadcast_to_ws_teacher(event))

Teacher UI module: classreview/ui/ (slot "history" of the T02 class panel registry, or the DEV host page).
"""

from .config import ReviewConfig
from .router import AuthError, create_review_router
from .store import IngestResult, ReviewError, ReviewStore

UI_DIR = __import__("pathlib").Path(__file__).resolve().parent / "ui"

__all__ = ["AuthError", "IngestResult", "ReviewConfig", "ReviewError", "ReviewStore", "UI_DIR", "create_review_router"]
