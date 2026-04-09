from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.auth import create_user, get_user_by_username, verify_password
from app.default_categories import DEFAULT_ONBOARDING_CATEGORIES
from app.services import CategoryService

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_salt"], user["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": "Usuário ou senha inválidos."},
            status_code=401,
        )
    request.session["user_id"] = user["id"]
    request.session["username"] = user["username"]
    if not user["onboarded"]:
        return RedirectResponse("/onboarding", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "register.html",
        {"request": request, "error": None, "categories": DEFAULT_ONBOARDING_CATEGORIES},
    )


@router.post("/register", response_model=None)
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    if password != confirm_password:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"request": request, "error": "As senhas não coincidem.", "categories": DEFAULT_ONBOARDING_CATEGORIES},
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"request": request, "error": "A senha deve ter pelo menos 6 caracteres.", "categories": DEFAULT_ONBOARDING_CATEGORIES},
            status_code=400,
        )
    try:
        user_id = create_user(username, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"request": request, "error": str(exc), "categories": DEFAULT_ONBOARDING_CATEGORIES},
            status_code=400,
        )

    selected_categories = []
    for cat in DEFAULT_ONBOARDING_CATEGORIES:
        key = cat["key"]
        label_value = form.get(f"label_{key}", cat["label"])
        if form.get(f"enabled_{key}", "on"):
            selected_categories.append(
                {
                    "key": key,
                    "label": str(label_value),
                    "examples": cat["examples"],
                    "sort_order": cat["sort_order"],
                }
            )

    custom_categories_raw = str(form.get("custom_categories", "")).strip()
    if custom_categories_raw:
        next_sort = len(selected_categories)
        for line in custom_categories_raw.splitlines():
            label = line.strip()
            if not label:
                continue
            key = label.lower().replace(" ", "_")
            key = "".join(ch for ch in key if ch.isalnum() or ch == "_")[:30]
            if not key or any(c["key"] == key for c in selected_categories):
                continue
            selected_categories.append(
                {"key": key, "label": label[:40], "examples": [], "sort_order": next_sort}
            )
            next_sort += 1

    if not any(c["key"] == "outros" for c in selected_categories):
        outros = next((c for c in DEFAULT_ONBOARDING_CATEGORIES if c["key"] == "outros"), None)
        if outros:
            selected_categories.append(outros)

    if selected_categories:
        CategoryService().bulk_insert(user_id, selected_categories)
    request.session["user_id"] = user_id
    request.session["username"] = username
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
