import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.db.client import (
    build_service_role_client,
    call_delete_user_data,
    fetch_profile_for_deletion,
    record_webhook_event,
)
from app.services.email_service import (
    send_account_deleted_email,
    send_welcome_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks-clerk"])


@router.post(
    "/webhooks/clerk/delete_account",
    status_code=204,
    responses={401: {"description": "Invalid Svix signature"}},
)
async def webhooks_delete_account(request: Request) -> None:
    payload = await request.body()

    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }

    try:
        wh = Webhook(get_settings().clerk_webhook_secret)
        wh.verify(payload, headers)
    except WebhookVerificationError as err:
        raise HTTPException(status_code=401, detail="Invalid signature") from err

    sr_client = build_service_role_client()
    if not record_webhook_event(sr_client, headers["svix-id"]):
        logger.info("Duplicate webhook svix-id=%s; skipping", headers["svix-id"])
        return

    event = json.loads(payload)
    event_type = event.get("type")
    logger.info("Received Clerk %s webhook event", event_type)

    if event_type == "user.deleted":
        user_id = event.get("data", {}).get("id")
        if not user_id:
            logger.warning("user.deleted: no user_id in event data")
            return

        profile = fetch_profile_for_deletion(sr_client, user_id)
        call_delete_user_data(sr_client, user_id)
        if profile:
            email, full_name = profile
            send_account_deleted_email(email, full_name)


@router.post(
    "/webhooks/clerk/welcome",
    status_code=204,
    responses={401: {"description": "Invalid Svix signature"}},
)
async def welcome_webhook(
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
        wh = Webhook(get_settings().clerk_webhook_secret)
        wh.verify(payload, headers)
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
        data = event["data"]
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

        background_tasks.add_task(
            send_welcome_email,
            primary_email,
            data.get("first_name"),
        )
