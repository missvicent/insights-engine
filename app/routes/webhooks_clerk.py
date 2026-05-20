import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.db.client import (
    build_service_role_client,
    call_delete_user_data,
    enqueue_email,
    fetch_profile_for_deletion,
    record_webhook_event,
)
from app.services.email_service import try_send_pending_email

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks-clerk"])


@router.post(
    "/webhooks/clerk",
    status_code=204,
    responses={401: {"description": "Invalid Svix signature"}},
)
async def clerk_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> None:
    payload = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }

    try:
        Webhook(get_settings().clerk_webhook_secret).verify(payload, headers)
    except WebhookVerificationError as err:
        raise HTTPException(status_code=401, detail="Invalid signature") from err

    sr_client = build_service_role_client()
    if not record_webhook_event(sr_client, headers["svix-id"]):
        logger.info("Duplicate webhook svix-id=%s; skipping", headers["svix-id"])
        return

    event = json.loads(payload)
    event_type = event.get("type")
    logger.info("Received Clerk %s webhook event", event_type)

    if event_type == "user.created":
        data = event.get("data", {})
        primary_id = data.get("primary_email_address_id")
        primary_email = next(
            (
                e["email_address"]
                for e in data.get("email_addresses", [])
                if e.get("id") == primary_id
            ),
            None,
        )
        if primary_email is None:
            logger.warning(
                "user.created with no primary email: user_id=%s",
                data.get("id"),
            )
            return
        row_id = enqueue_email(
            sr_client,
            template="welcome",
            to_email=primary_email,
            payload={"first_name": data.get("first_name")},
        )
        background_tasks.add_task(try_send_pending_email, row_id)
        return

    if event_type == "user.deleted":
        data = event.get("data", {})
        user_id = data.get("id")
        if not user_id:
            logger.warning("user.deleted: no user_id in event data")
            return
        profile = fetch_profile_for_deletion(sr_client, user_id)
        call_delete_user_data(sr_client, user_id)
        if profile:
            email, full_name = profile
            row_id = enqueue_email(
                sr_client,
                template="account_deleted",
                to_email=email,
                payload={"first_name": full_name},
            )
            background_tasks.add_task(try_send_pending_email, row_id)
        return

    logger.info("Unhandled Clerk event type=%s; returning 204", event_type)
