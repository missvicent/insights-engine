import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import insights as insights_routes


def _parse_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="finance-insights-engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(os.getenv("CORS_ORIGINS")),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(insights_routes.router)
