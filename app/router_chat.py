from __future__ import annotations

import logging
from datetime import date, timedelta

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
    reply_budgets_set,
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
    reply_recurring_created_many,
    reply_recurring_deleted,
    reply_recurring_list,
    reply_weekly_summary,
)
from app.token_usage import TokenLimitError
from app.services import (
    AmbiguousMatchError,
    BudgetService,
    ChatHistoryService,
    ExpenseService,
    RecurringExpenseService,
    parse_money_to_cents,
    resolve_period,
)

router = APIRouter()
logger = logging.getLogger(__name__)

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


def _handle_create_expenses(parsed: dict, user_id: int, today: date) -> str:
    expenses = parsed["expenses"]
    ExpenseService().create_expenses(expenses, user_id=user_id)
    monthly_overview = OverviewService().get_monthly_overview(user_id, today.year, today.month)
    affected_categories = {expense.category for expense in expenses}
    budget_alerts = [alert for alert in monthly_overview.get("budget_alerts", []) if alert["category"] in affected_categories]
    return reply_expenses_created(expenses, budget_alerts=budget_alerts)


def _handle_set_budget(parsed: dict, user_id: int) -> str:
    budget = parsed["budget"]
    BudgetService().upsert_budget(budget, user_id=user_id)
    return reply_budget_set(budget)


def _handle_set_budget_many(parsed: dict, user_id: int) -> str:
    budgets = parsed["budgets"]
    svc = BudgetService()
    for budget in budgets:
        svc.upsert_budget(budget, user_id=user_id)
    return reply_budgets_set(budgets)


def _handle_delete_expense(parsed: dict, user_id: int, request: Request) -> str:
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


def _handle_edit_expense(parsed: dict, user_id: int) -> str:
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
        try:
            kwargs["amount_cents"] = parse_money_to_cents(expense_edit.new_amount_brl)
        except ValueError:
            return reply_help("Valor inválido para edição. Use formato como 10,50.")
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


def _interpret_message(message: str, user_id: int, history_for_llm: list[dict]) -> dict:
    if not settings.llm_enabled:
        return parse_message(message)

    try:
        return OpenAIInterpreter().parse_message(message, user_id=user_id, history=history_for_llm, route="/api/chat")
    except TokenLimitError as exc:
        logger.warning("LLM bloqueado por limite de tokens para user_id=%s: %s", user_id, exc)
        return parse_message(message)
    except Exception as exc:
        logger.warning("Falha no parser LLM para user_id=%s; fallback para parser local: %s", user_id, exc)
        return parse_message(message)


def _handle_intent(parsed: dict, user_id: int, request: Request) -> str:
    intent = parsed["intent"]
    today = date.today()
    overview_service = OverviewService()

    if intent == "empty":
        return reply_empty()

    if intent == "create_expenses":
        return _handle_create_expenses(parsed, user_id, today)

    if intent == "set_budget":
        return _handle_set_budget(parsed, user_id)

    if intent == "set_budget_many":
        return _handle_set_budget_many(parsed, user_id)

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
        return _handle_delete_expense(parsed, user_id, request)

    if intent == "edit_expense":
        return _handle_edit_expense(parsed, user_id)

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
        frequency = rec_data.frequency if rec_data.frequency in ("monthly", "weekly") else "monthly"
        day = getattr(rec_data, "day_of_month", None) if frequency == "monthly" else getattr(rec_data, "day_of_week", None)
        if day is None and hasattr(rec_data, "day"):
            day = rec_data.day
        if day is None:
            day = 1
        try:
            amount_cents = (
                rec_data.amount_cents
                if hasattr(rec_data, "amount_cents")
                else parse_money_to_cents(rec_data.amount_brl)
            )
            rec = RecurringExpenseService().create(
                user_id=user_id,
                description=rec_data.description,
                category=rec_data.category.lower(),
                amount_cents=amount_cents,
                frequency=frequency,
                day=day,
            )
        except ValueError as exc:
            return reply_help(str(exc))
        return reply_recurring_created(rec)

    if intent == "create_recurring_expense_many":
        created: list[dict] = []
        for rec_data in parsed["recurring_expenses"]:
            frequency = rec_data.frequency if rec_data.frequency in ("monthly", "weekly") else "monthly"
            day = getattr(rec_data, "day_of_month", None) if frequency == "monthly" else getattr(rec_data, "day_of_week", None)
            if day is None and hasattr(rec_data, "day"):
                day = rec_data.day
            if day is None:
                day = 1
            try:
                amount_cents = (
                    rec_data.amount_cents
                    if hasattr(rec_data, "amount_cents")
                    else parse_money_to_cents(rec_data.amount_brl)
                )
                rec = RecurringExpenseService().create(
                    user_id=user_id,
                    description=rec_data.description,
                    category=rec_data.category.lower(),
                    amount_cents=amount_cents,
                    frequency=frequency,
                    day=day,
                )
                created.append(rec)
            except ValueError:
                continue
        if not created:
            return reply_help("Não consegui criar os recorrentes informados.")
        return reply_recurring_created_many(created)

    if intent == "create_card_installment_purchase":
        card = parsed["card_purchase"]
        try:
            first_date = date.today()
            raw_first = getattr(card, "first_expense_date", "today")
            if raw_first not in {"today", "yesterday"}:
                first_date = date.fromisoformat(raw_first)
            elif raw_first == "yesterday":
                first_date = date.today() - timedelta(days=1)
            ids = ExpenseService().create_card_installment_purchase(
                user_id=user_id,
                description=card.description,
                category=card.category.lower(),
                installment_amount_cents=parse_money_to_cents(card.installment_amount_brl),
                installment_total=card.installments,
                purchase_total_cents=parse_money_to_cents(card.installment_amount_brl) * card.installments,
                first_expense_date=first_date,
                card_label=card.card_label,
            )
        except (ValueError, TypeError) as exc:
            return reply_help(f"Não consegui criar o parcelamento no cartão: {exc}")
        return (
            f"Perfeito! Registrei compra no cartão em {card.installments}x.\n\n"
            f"- {card.description} ({card.category})\n"
            f"- Parcela mensal: {card.installment_amount_brl}\n"
            f"- Total de lançamentos gerados: {len(ids)}"
        )

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


def _process_chat(request: Request, message: str, user_id: int) -> str:
    chat_svc = ChatHistoryService()
    pending_delete_id = request.session.get("pending_delete_id")
    if pending_delete_id and message.strip().lower() in {"sim", "s", "yes", "y"}:
        try:
            deleted = ExpenseService().delete_expense(user_id, pending_delete_id)
            reply = reply_expense_deleted(deleted)
        except ValueError as exc:
            logger.info("Falha ao deletar gasto pendente user_id=%s expense_id=%s: %s", user_id, pending_delete_id, exc)
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
    return reply


@router.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    user: dict = Depends(require_login),
) -> RedirectResponse:
    _process_chat(request, message, user["user_id"])
    return RedirectResponse("/", status_code=303)


@router.post("/api/chat")
async def api_chat(
    request: Request,
    message: str = Form(...),
    user: dict = Depends(require_login),
) -> JSONResponse:
    if not message.strip():
        return JSONResponse({"ok": False, "error": "Mensagem vazia.", "details": None}, status_code=422)
    reply = _process_chat(request, message, user["user_id"])
    return JSONResponse({"ok": True, "reply": reply, "error": None, "details": None})


@router.post("/api/expenses/{expense_id}/delete")
async def api_expense_delete(
    expense_id: int,
    apply_to_series: str | None = Form(None),
    user: dict = Depends(require_login),
) -> JSONResponse:
    try:
        if apply_to_series:
            ExpenseService().delete_installment_series(user["user_id"], expense_id)
        else:
            ExpenseService().delete_expense(user["user_id"], expense_id)
        return JSONResponse({"ok": True, "error": None, "details": None})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "details": None}, status_code=404)


@router.post("/api/expenses/{expense_id}/edit")
async def api_expense_edit(
    expense_id: int,
    user: dict = Depends(require_login),
    description: str = Form(...),
    category: str = Form(...),
    amount_brl: str = Form(...),
    expense_date: str = Form(...),
    apply_to_series: str | None = Form(None),
) -> JSONResponse:
    if not description.strip():
        return JSONResponse({"ok": False, "error": "Descrição não pode ficar vazia.", "details": None}, status_code=422)
    try:
        amount_cents = parse_money_to_cents(amount_brl)
        updated = ExpenseService().update_expense(
            user["user_id"],
            expense_id,
            description=description[:120],
            category=category.lower(),
            amount_cents=amount_cents,
            expense_date=expense_date,
            apply_to_series=apply_to_series is not None,
        )
        return JSONResponse({"ok": True, "error": None, "details": {"expense": updated}})
    except ValueError as exc:
        logger.info("Falha ao editar gasto user_id=%s expense_id=%s: %s", user["user_id"], expense_id, exc)
        status = 404 if "não encontrado" in str(exc).lower() else 422
        return JSONResponse({"ok": False, "error": str(exc), "details": None}, status_code=status)
    except Exception as exc:
        logger.exception("Erro inesperado ao editar gasto user_id=%s expense_id=%s", user["user_id"], expense_id)
        return JSONResponse(
            {"ok": False, "error": "Erro interno ao editar gasto.", "details": str(exc)},
            status_code=500,
        )


@router.get("/api/summary")
async def api_summary(user: dict = Depends(require_login)) -> JSONResponse:
    today = date.today()
    summary = OverviewService().get_monthly_overview(user["user_id"], today.year, today.month)
    return JSONResponse({"ok": True, "error": None, "details": summary})
