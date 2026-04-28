import json
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.db.client import Settings, get_settings
from app.services.email_service import send_welcome_email

router = APIRouter()

logger = logging.getLogger(__name__)


@router.post("/emails/welcome")
async def welcome_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
):
    payload = await request.body()

    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }

    try:
        wh = Webhook(settings.clerk_webhook_secret)
        wh.verify(payload, headers)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

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
        else:
            background_tasks.add_task(
                send_welcome_email,
                primary_email,
                data.get("first_name"),
            )
    return {"status": 200, "message": "Welcome email sent"}
