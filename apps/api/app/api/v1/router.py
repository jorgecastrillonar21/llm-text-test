from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import ai, sessions, worlds

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(ai.router)
api_router.include_router(worlds.router)
api_router.include_router(sessions.router)
