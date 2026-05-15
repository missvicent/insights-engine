import logging

from fastapi import APIRouter, HTTPException

from app.db.client import ping

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/health/db")
def health_db() -> dict[str, str]:
    try:
        ping()
    except Exception as e:
        logger.warning("health/db failed: %s", e)
        raise HTTPException(status_code=503, detail="db unreachable") from None
    return {"db": "ok"}
