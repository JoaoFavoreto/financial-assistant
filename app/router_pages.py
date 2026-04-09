from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_user_by_id, mark_onboarded
from app.config import settings
from app.default_categories import DEFAULT_ONBOARDING_CATEGORIES
from app.dependencies import require_login
from app.overview import OverviewService
from app.services import BudgetService, CategoryService, ChatHistoryService, RecurringExpenseService, cents_to_brl, format_category

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

MAX_HISTORY = 20


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    today = date.today()
    user_id = user["user_id"]
    recurring_svc = RecurringExpenseService()
    generated = recurring_svc.generate_pending(user_id)
    overview_service = OverviewService()
    summary = overview_service.get_monthly_overview(user_id, today.year, today.month)
    weekly = overview_service.get_weekly_overview(user_id, anchor_date=today)
    budgets = BudgetService().list_budgets(user_id=user_id)
    chat_history = ChatHistoryService().load_recent(user_id, limit=MAX_HISTORY)
    recurring_expenses = recurring_svc.list_for_user(user_id)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "weekly": weekly,
            "budgets": budgets,
            "chat_history": chat_history,
            "llm_mode": "OpenAI ativo" if settings.llm_enabled else "fallback por regras",
            "cents_to_brl": cents_to_brl,
            "recurring_expenses": recurring_expenses,
            "generated_count": len(generated),
        },
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    today = date.today()
    user_id = user["user_id"]
    overview_service = OverviewService()
    summary = overview_service.get_monthly_overview(user_id, today.year, today.month)
    weekly = overview_service.get_weekly_overview(user_id, anchor_date=today)
    user_categories = CategoryService().list_for_user(user_id)
    for expense in summary.get("recent_expenses", []):
        expense["category_label"] = format_category(expense["category"], user_id)
    import_success = request.session.pop("import_success", None)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "weekly": weekly,
            "cents_to_brl": cents_to_brl,
            "user_categories": user_categories,
            "import_success": import_success,
        },
    )


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_welcome(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    user_data = get_user_by_id(user["user_id"])
    if user_data and user_data["onboarded"]:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "onboarding_welcome.html",
        {"request": request, "user": user},
    )


@router.get("/onboarding/categories", response_class=HTMLResponse)
async def onboarding_categories_page(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
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
            selected_categories.append(
                {
                    "key": key,
                    "label": str(label_value),
                    "examples": cat["examples"],
                    "sort_order": cat["sort_order"],
                }
            )

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
