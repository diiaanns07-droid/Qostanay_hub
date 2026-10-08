"""Qorgau class server (owner: T01). Entry point: `python -m classroom.server`; app factory: create_app()."""

from .config import SERVER_VERSION, ServerConfig

__all__ = ["SERVER_VERSION", "ServerConfig"]
