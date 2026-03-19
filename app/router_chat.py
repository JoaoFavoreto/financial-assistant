from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.dependencies import require_login
from app.llm import LLMExpenseTarget, OpenAIInterpreter
from app.overview import OverviewService
from app.parsing import parse_message
from app.replies import (
    reply_ambiguous_match,
    reply_budget_set,
    reply_category_summary,
    reply_confirm_delete,
    reply_empty,
    reply_expense_deleted,
    reply_expense_not_found,
    reply_expense_updated,
    reply_expenses_created,
    reply_filtered_search,
    reply_help,
    reply_monthly_summary,
    reply_recurring_created,
    reply_recurring_deleted,
    reply_recurring_list,
    reply_weekly_summary,
)
from app.services import AmbiguousMatchError, BudgetService, ChatHistoryService, ExpenseService, RecurringExpenseService, resolve_period

router = APIRouter()

MAX_HISTORY = 20


def _build_filter_description(sf: "LLMSearchFilter") -> str:  # type: ignore[name-defined]  # noqa: F821
    parts = []
    limit = getattr(sf, "limit", None)
    if limit:
        parts.append("seus últimos gastos")
    if sf.description_hint:
        parts.append(sf.description_hint)
    if sf.category_hint:
        from app.services import CATEGORY_LABELS

        parts.append(CATEGORY_LABELS.get(sf.category_hint, sf.category_hint))
    if sf.period:
        period_labels = {
            "today": "hoje",
            "yesterday": "ontem",
            "this_month": "este mês",
            "last_month": "mês passado",
            "this_week": "esta semana",
            "last_week": "semana passada",
        }
        if sf.period in period_labels:
            parts.append(period_labels[sf.period])
        elif sf.period.startswith("last_N_days:"):
            n = sf.period.split(":")[1]
            parts.append(f"últimos {n} dias")
        elif len(sf.period) == 7:
            parts.append(sf.period)
    return " / ".join(parts) if parts else "filtro aplicado"


def _interpret_message(message: str, user_id: int, history_for_llm: list[dict]) -> dict:
    if not settings.llm_enabled:
        return parse_message(message)

    try:
        return OpenAIInterpreter().parse_message(message, user_id=user_id, history=history_for_llm)
    except Exception:
        return parse_message(message)


def _handle_intent(parsed: dict, user_id: int, request: Request) -> str:
    intent = parsed["intent"]
    today = date.today()
    overview_service = OverviewService()

    if intent == "empty":
        return reply_empty()

    if intent == "create_expenses":
        expenses = parsed["expenses"]
        ExpenseService().create_expenses(expenses, user_id=user_id)
        monthly_overview = overview_service.get_monthly_overview(user_id, today.year, today.month)
        affected_categories = {expense.category for expense in expenses}
        budget_alerts = [
            alert for alert in monthly_overview.get("budget_alerts", []) if alert["category"] in affected_categories
        ]
        return reply_expenses_created(expenses, budget_alerts=budget_alerts)

    if intent == "set_budget":
        budget = parsed["budget"]
        BudgetService().upsert_budget(budget, user_id=user_id)
        return reply_budget_set(budget)

    if intent == "monthly_summary":
        summary = overview_service.get_monthly_overview(user_id, today.year, today.month)
        return reply_monthly_summary(summary)

    if intent == "weekly_summary":
        summary = overview_service.get_weekly_overview(user_id, anchor_date=today)
        return reply_weekly_summary(summary)

    if intent == "category_summary":
        summary = overview_service.get_monthly_overview(user_id, today.year, today.month)
        return reply_category_summary(summary)

    if intent == "delete_expense":
        target: LLMExpenseTarget = parsed["expense_target"]
        service = ExpenseService()
        try:
            match = service.find_best_match(user_id, target)
        except AmbiguousMatchError as exc:
            return reply_ambiguous_match(exc.candidates)
        if match is None:
            hint = target.description_hint or target.category_hint or None
            if hint is None:
                recent = service.find_recent_expenses(user_id, limit=5)
                if recent:
                    return reply_ambiguous_match(recent)
                return reply_expense_not_found("gasto não identificado")
            return reply_expense_not_found(hint)
        request.session["pending_delete_id"] = match["id"]
        return reply_confirm_delete(match)

    if intent == "edit_expense":
        expense_edit = parsed["expense_edit"]
        target = expense_edit.target
        service = ExpenseService()
        try:
            match = service.find_best_match(user_id, target)
        except AmbiguousMatchError as exc:
            return reply_ambiguous_match(exc.candidates)
        if match is None:
            hint = target.description_hint or target.category_hint or "gasto não identificado"
            return reply_expense_not_found(hint)

        kwargs: dict = {}
        if expense_edit.new_amount_brl:
            from app.llm import brl_to_cents

            kwargs["amount_cents"] = brl_to_cents(expense_edit.new_amount_brl)
        if expense_edit.new_category:
            kwargs["category"] = expense_edit.new_category.lower()
        if expense_edit.new_description:
            kwargs["description"] = expense_edit.new_description[:120]
        if expense_edit.new_date:
            kwargs["expense_date"] = expense_edit.new_date
        if not kwargs:
            return reply_help("Não consegui identificar o que você quer mudar no gasto.")
        old = dict(match)
        new = service.update_expense(user_id, match["id"], **kwargs)
        return reply_expense_updated(old, new)

    if intent == "filtered_search":
        sf = parsed["search_filter"]
        start_date, end_date = resolve_period(sf.period)
        expenses = ExpenseService().search_expenses(
            user_id,
            start_date=start_date,
            end_date=end_date,
            category=sf.category_hint,
            description_hint=sf.description_hint,
            limit=getattr(sf, "limit", None),
        )
        total_cents = sum(e["amount_cents"] for e in expenses)
        filter_desc = _build_filter_description(sf)
        return reply_filtered_search(expenses, total_cents, filter_desc)

    if intent == "create_recurring_expense":
        rec_data = parsed["recurring_expense"]
        from app.llm import brl_to_cents

        frequency = rec_data.frequency if rec_data.frequency in ("monthly", "weekly") else "monthly"
        day = rec_data.day_of_month if frequency == "monthly" else rec_data.day_of_week
        if day is None:
            day = 1
        try:
            rec = RecurringExpenseService().create(
                user_id=user_id,
                description=rec_data.description,
                category=rec_data.category.lower(),
                amount_cents=brl_to_cents(rec_data.amount_brl),
                frequency=frequency,
                day=day,
            )
        except ValueError as exc:
            return reply_help(str(exc))
        return reply_recurring_created(rec)

    if intent == "list_recurring_expenses":
        recurrings = RecurringExpenseService().list_for_user(user_id)
        return reply_recurring_list(recurrings)

    if intent == "delete_recurring_expense":
        hint = parsed.get("recurring_target") or ""
        svc = RecurringExpenseService()
        candidates = svc.find_by_description_hint(user_id, hint)
        if not candidates:
            return reply_expense_not_found(hint or "recorrente não identificado")
        if len(candidates) > 1:
            lines = "\n".join(f"- {c['description']} ({c['frequency']})" for c in candidates[:5])
            return reply_help(f"Encontrei mais de um recorrente parecido:\n{lines}\n\nSeja mais específico.")
        rec = svc.deactivate(user_id, candidates[0]["id"])
        return reply_recurring_deleted(rec)

    return reply_help(parsed.get("reply_hint"))


@router.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    user_id = user["user_id"]
    chat_svc = ChatHistoryService()

    pending_delete_id = request.session.get("pending_delete_id")
    if pending_delete_id and message.strip().lower() in {"sim", "s", "yes", "y"}:
        try:
            deleted = ExpenseService().delete_expense(user_id, pending_delete_id)
            reply = reply_expense_deleted(deleted)
        except Exception:
            reply = reply_help("Não consegui remover o gasto. Ele pode já ter sido apagado.")
        request.session.pop("pending_delete_id", None)
    else:
        if pending_delete_id:
            request.session.pop("pending_delete_id", None)

        history_for_llm = chat_svc.load_recent(user_id, limit=MAX_HISTORY)
        parsed = _interpret_message(message, user_id, history_for_llm)
        reply = _handle_intent(parsed, user_id, request)

    chat_svc.append(user_id, "user", message)
    chat_svc.append(user_id, "assistant", reply)

    return RedirectResponse("/", status_code=303)


@router.get("/api/summary")
async def api_summary(user: dict = Depends(require_login)) -> JSONResponse:
    today = date.today()
    summary = OverviewService().get_monthly_overview(user["user_id"], today.year, today.month)
    return JSONResponse(summary)
