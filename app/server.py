from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import ensure_database
from app.router_auth import router as router_auth
from app.router_chat import router as router_chat
from app.router_pages import router as router_pages


def create_app() -> FastAPI:
    ensure_database()
    application = FastAPI(title="EconomIA")
    application.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
    application.include_router(router_auth)
    application.include_router(router_pages)
    application.include_router(router_chat)
    return application


app = create_app()
