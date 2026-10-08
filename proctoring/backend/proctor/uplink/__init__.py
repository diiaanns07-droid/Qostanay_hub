"""Student uplink to the class server, protocol qorgau.class.v1 (owner: C2).

Started by the backend lifespan (proctor.app) only when QORGAU_CLASS_SERVER and QORGAU_CLASS_CODE are
set; otherwise ``start_uplink`` returns None and the backend works locally as before. A broken or
unreachable class server never stops the local exam: the uplink runs in its own thread.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from .backend_view import BackendView
from .client import ClassStateMsg, Uplink
from .config import UplinkConfig, config_from_env

log = logging.getLogger("proctor.uplink")

__all__ = ["ClassStateMsg", "Uplink", "UplinkConfig", "config_from_env", "start_uplink"]


def start_uplink(settings: Any, manager_getter: Callable[[], Any], hub: Any, environ: dict[str, str] | None = None) -> Uplink | None:
    """Create and start the uplink, or return None (not configured / invalid config — logged, never raised)."""
    try:
        cfg = config_from_env(Path(settings.data_dir), environ)
    except ValueError as exc:
        log.error("class uplink disabled: %s", exc)
        return None
    if cfg is None:
        return None
    try:
        uplink = Uplink(cfg, BackendView(manager_getter), publish=lambda msg: hub.publish(msg, None))
        uplink.start()
    except Exception:
        log.exception("class uplink could not start; the exam continues locally")
        return None
    log.info("class uplink started: server %s", cfg.server)
    return uplink
