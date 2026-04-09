from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth import is_admin_username


async def require_login(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    username = request.session.get("username", "")
    return {"user_id": user_id, "username": username, "is_admin": is_admin_username(username)}


async def require_admin(request: Request) -> dict:
    user = await require_login(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Acesso restrito para admin.")
    return user
