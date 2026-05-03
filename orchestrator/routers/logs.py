"""Log streaming routes."""

import logging
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["logs"])

logger = logging.getLogger("orchestrator")


@router.get("/logs")
async def get_logs(level: str = "", limit: int = 100):
    """Get recent orchestrator log lines, optionally filtered by level."""
    from log_config import get_recent_logs
    return {"lines": get_recent_logs(level=level, limit=limit)}
