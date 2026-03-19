from __future__ import annotations

from fastapi import HTTPException, Request


async def require_login(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return {"user_id": user_id, "username": request.session.get("username", "")}
