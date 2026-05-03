"""Log streaming routes."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["logs"])

logger = logging.getLogger("orchestrator")


class TouchEventLog(BaseModel):
    msg: str


@router.get("/logs")
async def get_logs(level: str = "", limit: int = 100):
    """Get recent orchestrator log lines, optionally filtered by level."""
    from log_config import get_recent_logs
    return {"lines": get_recent_logs(level=level, limit=limit)}


@router.post("/logs/touch-events")
async def log_touch_event(event: TouchEventLog):
    """Log touch/gesture events from frontend (e-ink mode)."""
    import os
    log_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "logs", "touch-events.log"
    )

    with open(log_path, "a") as f:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        f.write(f"[{ts}] {event.msg}\n")

    return {"ok": True}
