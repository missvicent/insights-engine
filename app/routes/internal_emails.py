"""Internal endpoints called by infrastructure (currently: pg_cron).

Auth is a shared bearer secret (`CRON_SHARED_SECRET`), not a Clerk JWT.
The single endpoint here drains the pending_emails outbox.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db.client import build_service_role_client, fetch_ready_pending_emails
from app.routes.deps import verify_cron_secret
from app.services.email_service import try_send_pending_email

router = APIRouter(tags=["internal"])

MAX_BATCH_SIZE = 200


class FlushRequest(BaseModel):
    # No upper bound here; we clamp to MAX_BATCH_SIZE in the handler so an
    # over-spec caller silently gets a sensible cap instead of a 422.
    batch_size: int = Field(default=50, ge=1)


class FlushResponse(BaseModel):
    scanned: int
    sent: int
    failed: int


@router.post(
    "/internal/emails/flush",
    response_model=FlushResponse,
    dependencies=[Depends(verify_cron_secret)],
)
def flush_emails(body: FlushRequest | None = None) -> FlushResponse:
    batch_size = min((body or FlushRequest()).batch_size, MAX_BATCH_SIZE)
    sr_client = build_service_role_client()
    rows = fetch_ready_pending_emails(sr_client, batch_size)

    sent = 0
    failed = 0
    for row in rows:
        if try_send_pending_email(int(row["id"])):
            sent += 1
        else:
            failed += 1

    return FlushResponse(scanned=len(rows), sent=sent, failed=failed)
