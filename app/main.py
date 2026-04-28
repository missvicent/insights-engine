import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import ai as ai_routes
from app.routes import emails as emails_routes
from app.routes import health as health_routes
from app.routes import insights as insights_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

def _parse_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(
    title="finance-insights-engine",
    description="A financial insights engine that uses AI to analyze transactions and provide insights.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(os.getenv("CORS_ORIGINS")),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(insights_routes.router)
app.include_router(ai_routes.router)
app.include_router(health_routes.router)
app.include_router(emails_routes.router)
