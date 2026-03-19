from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_user_by_id, mark_onboarded
from app.dependencies import require_login
from app.services import BudgetService, CategoryService, ChatHistoryService, ExpenseService, cents_to_brl
from app.config import settings

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

MAX_HISTORY = 20

# Default onboarding categories
DEFAULT_ONBOARDING_CATEGORIES = [
    {"key": "alimentacao", "label": "Alimentação", "examples": ["mercado", "restaurante", "ifood", "lanche"], "sort_order": 0},
    {"key": "transporte", "label": "Transporte", "examples": ["uber", "gasolina", "onibus", "metro"], "sort_order": 1},
    {"key": "moradia", "label": "Moradia", "examples": ["aluguel", "condominio", "energia", "internet"], "sort_order": 2},
    {"key": "saude", "label": "Saúde", "examples": ["farmacia", "medico", "academia", "remedio"], "sort_order": 3},
    {"key": "lazer", "label": "Lazer", "examples": ["cinema", "bar", "netflix", "spotify"], "sort_order": 4},
    {"key": "compras", "label": "Compras", "examples": ["roupa", "amazon", "shopping"], "sort_order": 5},
    {"key": "educacao", "label": "Educação", "examples": ["curso", "livro", "faculdade"], "sort_order": 6},
    {"key": "outros", "label": "Outros", "examples": [], "sort_order": 7},
]


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    today = date.today()
    summary = ExpenseService().get_monthly_summary(today.year, today.month, user_id=user["user_id"])
    budgets = BudgetService().list_budgets(user_id=user["user_id"])
    chat_history = ChatHistoryService().load_recent(user["user_id"], limit=MAX_HISTORY)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "budgets": budgets,
            "chat_history": chat_history,
            "llm_mode": "OpenAI ativo" if settings.llm_enabled else "fallback por regras",
            "cents_to_brl": cents_to_brl,
        },
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    today = date.today()
    summary = ExpenseService().get_monthly_summary(today.year, today.month, user_id=user["user_id"])
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "cents_to_brl": cents_to_brl,
        },
    )


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_welcome(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    user_data = get_user_by_id(user["user_id"])
    if user_data and user_data["onboarded"]:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "onboarding_welcome.html",
        {"request": request, "user": user},
    )


@router.get("/onboarding/categories", response_class=HTMLResponse)
async def onboarding_categories_page(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    return templates.TemplateResponse(
        "onboarding.html",
        {
            "request": request,
            "user": user,
            "categories": DEFAULT_ONBOARDING_CATEGORIES,
        },
    )


@router.post("/onboarding/categories")
async def onboarding_categories_submit(request: Request, user: dict = Depends(require_login)) -> RedirectResponse:
    form = await request.form()
    user_id = user["user_id"]
    category_service = CategoryService()

    selected_categories = []
    for cat in DEFAULT_ONBOARDING_CATEGORIES:
        key = cat["key"]
        label_value = form.get(f"label_{key}", cat["label"])
        if form.get(f"enabled_{key}"):
            selected_categories.append({
                "key": key,
                "label": str(label_value),
                "examples": cat["examples"],
                "sort_order": cat["sort_order"],
            })

    # Always include "outros" as fallback
    has_outros = any(c["key"] == "outros" for c in selected_categories)
    if not has_outros:
        outros = next((c for c in DEFAULT_ONBOARDING_CATEGORIES if c["key"] == "outros"), None)
        if outros:
            selected_categories.append(outros)

    if selected_categories:
        category_service.bulk_insert(user_id, selected_categories)

    mark_onboarded(user_id)
    return RedirectResponse("/", status_code=303)
