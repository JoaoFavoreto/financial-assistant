from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import require_login
from app.services import CategoryService

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(prefix="/settings")


@router.get("/categories", response_class=HTMLResponse)
async def settings_categories(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    cats = CategoryService().list_for_user(user["user_id"])
    error = request.session.pop("settings_error", None)
    return templates.TemplateResponse(
        "settings_categories.html",
        {"request": request, "user": user, "categories": cats, "error": error},
    )


@router.post("/categories/update")
async def settings_categories_update(
    request: Request,
    user: dict = Depends(require_login),
) -> RedirectResponse:
    form = await request.form()
    svc = CategoryService()
    cats = svc.list_for_user(user["user_id"])
    for cat in cats:
        new_label = str(form.get(f"label_{cat['key']}", cat["label"])).strip()
        if new_label and new_label != cat["label"]:
            svc.update_category(user["user_id"], cat["key"], new_label)
    return RedirectResponse("/settings/categories", status_code=303)


@router.post("/categories/add")
async def settings_categories_add(
    request: Request,
    user: dict = Depends(require_login),
    key: str = Form(...),
    label: str = Form(...),
) -> RedirectResponse:
    try:
        CategoryService().add_category(user["user_id"], key.strip().lower(), label.strip())
    except ValueError as exc:
        request.session["settings_error"] = str(exc)
    return RedirectResponse("/settings/categories", status_code=303)


@router.post("/categories/delete")
async def settings_categories_delete(
    request: Request,
    user: dict = Depends(require_login),
    key: str = Form(...),
) -> RedirectResponse:
    try:
        CategoryService().delete_category(user["user_id"], key)
    except ValueError as exc:
        request.session["settings_error"] = str(exc)
    return RedirectResponse("/settings/categories", status_code=303)
