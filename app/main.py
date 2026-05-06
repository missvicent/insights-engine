import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import resend
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import ai as ai_routes
from app.routes import emails as emails_routes
from app.routes import health as health_routes
from app.routes import insights as insights_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.resend_api_key:
        resend.api_key = settings.resend_api_key
    yield


app = FastAPI(
    title="finance-insights-engine",
    description=(
        "A financial insights engine that uses AI to analyze"
        " transactions and provide insights."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(insights_routes.router)
app.include_router(ai_routes.router)
app.include_router(health_routes.router)
app.include_router(emails_routes.router)
