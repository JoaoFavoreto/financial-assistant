from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.auth import create_user, get_user_by_username, verify_password

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_salt"], user["password_hash"]):
        return templates.TemplateResponse(
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
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@router.post("/register", response_model=None)
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    if password != confirm_password:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "As senhas não coincidem."},
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "A senha deve ter pelo menos 6 caracteres."},
            status_code=400,
        )
    try:
        user_id = create_user(username, password)
    except ValueError as exc:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": str(exc)},
            status_code=400,
        )
    request.session["user_id"] = user_id
    request.session["username"] = username
    return RedirectResponse("/onboarding", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
