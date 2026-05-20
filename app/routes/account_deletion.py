from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.config import get_settings
from app.context import UserContext
from app.db.client import build_service_role_client
from app.routes.deps import get_user_ctx
from app.services.deletion_service import ClerkDeleteFailed
from app.services.deletion_service import delete_account as delete_account_service
from app.services.email_service import try_send_pending_email

router = APIRouter(tags=["account-deletion"])


@router.post("/account/delete", status_code=204)
def delete_account_(
    user_ctx: Annotated[UserContext, Depends(get_user_ctx)],
    background_tasks: BackgroundTasks,
) -> None:
    if not get_settings().account_deletion_enabled:
        raise HTTPException(status_code=503, detail="account deletion not yet enabled")
    sr_client = build_service_role_client()
    try:
        row_id = delete_account_service(user_ctx, sr_client)
    except ClerkDeleteFailed as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if row_id is not None:
        background_tasks.add_task(try_send_pending_email, row_id)
