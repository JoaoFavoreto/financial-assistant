from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.dependencies import require_login
from app.llm import LLMExpenseTarget, OpenAIInterpreter
from app.replies import (
    reply_ambiguous_match,
    reply_category_summary,
    reply_confirm_delete,
    reply_empty,
    reply_expense_deleted,
    reply_expense_not_found,
    reply_expense_updated,
    reply_expenses_created,
    reply_help,
    reply_monthly_summary,
    reply_budget_set,
)
from app.services import AmbiguousMatchError, BudgetService, ExpenseService

router = APIRouter()

MAX_HISTORY = 20


def _cap_history(history: list) -> list:
    return history[-MAX_HISTORY:]


def _handle_intent(parsed: dict, user_id: int, request: Request) -> str:
    intent = parsed["intent"]
    today = date.today()

    if intent == "empty":
        return reply_empty()

    if intent == "create_expenses":
        expenses = parsed["expenses"]
        ExpenseService().create_expenses(expenses, user_id=user_id)
        return reply_expenses_created(expenses)

    if intent == "set_budget":
        budget = parsed["budget"]
        BudgetService().upsert_budget(budget, user_id=user_id)
        return reply_budget_set(budget)

    if intent == "monthly_summary":
        summary = ExpenseService().get_monthly_summary(today.year, today.month, user_id=user_id)
        return reply_monthly_summary(summary)

    if intent == "category_summary":
        summary = ExpenseService().get_monthly_summary(today.year, today.month, user_id=user_id)
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
                # No hints at all — list recent expenses to help user be specific
                recent = service.find_recent_expenses(user_id, limit=5)
                if recent:
                    return reply_ambiguous_match(recent)
                return reply_expense_not_found("gasto não identificado")
            return reply_expense_not_found(hint)
        # Save pending delete in session
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
        # Build update kwargs
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

    return reply_help(parsed.get("reply_hint"))


@router.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    user_id = user["user_id"]
    history: list[dict] = request.session.get("chat_history", [])
    history.append({"role": "user", "content": message})

    # Check for pending delete confirmation
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

        if not settings.llm_enabled:
            parsed: dict = {"intent": "help", "reply_hint": "Configure OPENAI_API_KEY no arquivo .env para usar o EconomIA."}
        else:
            try:
                parsed = OpenAIInterpreter().parse_message(message, user_id=user_id)
            except Exception:
                parsed = {"intent": "help", "reply_hint": "Serviço de interpretação indisponível no momento. Tente novamente em instantes."}

        reply = _handle_intent(parsed, user_id, request)

    history.append({"role": "assistant", "content": reply})
    request.session["chat_history"] = _cap_history(history)

    return RedirectResponse("/", status_code=303)


@router.get("/api/summary")
async def api_summary(user: dict = Depends(require_login)) -> JSONResponse:
    today = date.today()
    summary = ExpenseService().get_monthly_summary(today.year, today.month, user_id=user["user_id"])
    return JSONResponse(summary)
