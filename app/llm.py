from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Literal

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
from pydantic import BaseModel, Field

from app.config import settings
from app.parsing import ParsedBudget, ParsedExpense, parse_brl_to_cents
from app.skills import finance_extraction_skill
from app.token_usage import TokenLimitError, TokenUsageService


class LLMExpense(BaseModel):
    amount_brl: str
    category: str
    description: str
    expense_date: str = Field(default="today", description="ISO date YYYY-MM-DD or 'today'/'yesterday'")


class LLMExpenseTarget(BaseModel):
    description_hint: str | None = None
    category_hint: str | None = None
    amount_brl_hint: str | None = None
    date_hint: str | None = None  # ISO or "today"/"yesterday"/"last"


class LLMExpenseEdit(BaseModel):
    target: LLMExpenseTarget
    new_amount_brl: str | None = None
    new_category: str | None = None
    new_description: str | None = None
    new_date: str | None = None


class LLMBudgetItem(BaseModel):
    category: str
    amount_brl: str


class LLMSearchFilter(BaseModel):
    category_hint: str | None = None
    description_hint: str | None = None
    period: str | None = None  # "today"|"yesterday"|"this_month"|"last_month"|"this_week"|"last_week"|"last_N_days:N"|"YYYY-MM"
    granularity: str | None = None  # "total"|"by_day"|"by_category"
    limit: int | None = None


class LLMRecurringExpense(BaseModel):
    description: str
    category: str
    amount_brl: str
    frequency: str  # 'monthly' | 'weekly'
    day_of_month: int | None = None  # 1-28
    day_of_week: int | None = None  # 0-6


class LLMCardInstallmentPurchase(BaseModel):
    description: str
    category: str
    installment_amount_brl: str
    installments: int
    first_expense_date: str = "today"
    card_label: str | None = None


class LLMMessageInterpretation(BaseModel):
    intent: Literal[
        "create_expenses",
        "set_budget",
        "monthly_summary",
        "weekly_summary",
        "category_summary",
        "filtered_search",
        "edit_expense",
        "delete_expense",
        "create_recurring_expense",
        "create_card_installment_purchase",
        "list_recurring_expenses",
        "delete_recurring_expense",
        "help",
        "empty",
    ]
    expenses: list[LLMExpense] = []
    budget_category: str | None = None
    budget_amount_brl: str | None = None
    budget_items: list[LLMBudgetItem] = []
    reply_hint: str | None = None
    expense_target: LLMExpenseTarget | None = None
    expense_edit: LLMExpenseEdit | None = None
    search_filter: LLMSearchFilter | None = None
    recurring_expense: LLMRecurringExpense | None = None
    recurring_expenses: list[LLMRecurringExpense] = []
    card_installment_purchase: LLMCardInstallmentPurchase | None = None
    recurring_target: str | None = None  # hint para delete


HISTORY_CONTEXT_WINDOW = 10
logger = logging.getLogger(__name__)


class _BatchCategoryItem(BaseModel):
    index: int
    category: str  # key from user's categories


class _BatchCategorizationResponse(BaseModel):
    items: list[_BatchCategoryItem]


class OpenAIInterpreter:
    def __init__(self) -> None:
        if OpenAI is None:
            raise RuntimeError("Pacote 'openai' não instalado no ambiente.")
        self.client = OpenAI(api_key=settings.openai_api_key, timeout=settings.economia_llm_timeout)
        self.simple_model = settings.openai_model_simple
        self.complex_model = settings.openai_model_complex
        self.token_usage = TokenUsageService()

    @staticmethod
    def _extract_usage(response: object) -> tuple[int, int, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return (0, 0, 0)
        prompt = int(getattr(usage, "input_tokens", 0) or 0)
        completion = int(getattr(usage, "output_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", prompt + completion) or 0)
        return (prompt, completion, total)

    def categorize_batch(
        self,
        items: list[tuple[int, str]],
        user_id: int | None = None,
    ) -> dict[int, str]:
        """
        Categorize a batch of expense descriptions.
        items: list of (original_index, description)
        Returns: dict of original_index → category_key
        """
        from app.skills import finance_extraction_skill

        if not items:
            return {}

        lines = "\n".join(f"{idx}. {desc}" for idx, desc in items)
        prompt = (
            "Categorize each expense description below using only the category keys "
            "available in the system prompt. Return JSON with an 'items' array where "
            "each element has 'index' (int) and 'category' (string key).\n\n"
            f"{lines}"
        )
        try:
            self.token_usage.check_limits(user_id, requested_tokens=300)
        except TokenLimitError as exc:
            self.token_usage.log_usage(
                user_id=user_id,
                route="/import/upload",
                task_type="categorize_batch",
                model=self.simple_model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="blocked",
                error_message=str(exc),
            )
            raise
        response = self.client.responses.parse(
            model=self.simple_model,
            input=[
                {"role": "system", "content": finance_extraction_skill(user_id=user_id)},
                {"role": "user", "content": prompt},
            ],
            text_format=_BatchCategorizationResponse,
        )
        prompt_t, completion_t, total_t = self._extract_usage(response)
        self.token_usage.log_usage(
            user_id=user_id,
            route="/import/upload",
            task_type="categorize_batch",
            model=self.simple_model,
            prompt_tokens=prompt_t,
            completion_tokens=completion_t,
            total_tokens=total_t,
            status="allowed",
        )
        suggestions = {item.index: item.category.lower() for item in response.output_parsed.items}
        if user_id is not None:
            from app.services import CategoryService

            allowed = {c["key"] for c in CategoryService().list_for_user(user_id)}
            if allowed:
                suggestions = {idx: cat for idx, cat in suggestions.items() if cat in allowed}
        return suggestions

    def parse_message(
        self,
        message: str,
        user_id: int | None = None,
        history: list[dict] | None = None,
        route: str = "/api/chat",
    ) -> dict:
        messages: list[dict] = [
            {"role": "system", "content": finance_extraction_skill(user_id=user_id)},
        ]
        if history:
            for turn in history[-HISTORY_CONTEXT_WINDOW:]:
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": message})

        try:
            self.token_usage.check_limits(user_id, requested_tokens=500)
        except TokenLimitError as exc:
            self.token_usage.log_usage(
                user_id=user_id,
                route=route,
                task_type="parse_message",
                model=self.complex_model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="blocked",
                error_message=str(exc),
            )
            raise
        try:
            response = self.client.responses.parse(
                model=self.complex_model,
                input=messages,
                text_format=LLMMessageInterpretation,
            )
        except TokenLimitError:
            raise
        except Exception as exc:
            self.token_usage.log_usage(
                user_id=user_id,
                route=route,
                task_type="parse_message",
                model=self.complex_model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error_message=str(exc),
            )
            raise
        prompt_t, completion_t, total_t = self._extract_usage(response)
        self.token_usage.log_usage(
            user_id=user_id,
            route=route,
            task_type="parse_message",
            model=self.complex_model,
            prompt_tokens=prompt_t,
            completion_tokens=completion_t,
            total_tokens=total_t,
            status="allowed",
        )
        return interpretation_to_result(response.output_parsed)


def interpretation_to_result(payload: LLMMessageInterpretation) -> dict:
    if payload.intent in {"monthly_summary", "weekly_summary", "category_summary", "help", "empty"}:
        result: dict = {"intent": payload.intent}
        if payload.reply_hint:
            result["reply_hint"] = payload.reply_hint
        return result

    if payload.intent == "set_budget":
        if payload.budget_items:
            return {
                "intent": "set_budget_many",
                "budgets": [
                    ParsedBudget(category=item.category.lower(), amount_cents=brl_to_cents(item.amount_brl))
                    for item in payload.budget_items
                ],
            }
        if not payload.budget_category or not payload.budget_amount_brl:
            return {"intent": "help", "reply_hint": "Não consegui identificar categoria e valor do orçamento."}
        return {
            "intent": "set_budget",
            "budget": ParsedBudget(
                category=payload.budget_category.lower(),
                amount_cents=brl_to_cents(payload.budget_amount_brl),
            ),
        }

    if payload.intent == "create_expenses":
        if not payload.expenses:
            return {"intent": "help", "reply_hint": "Não consegui identificar nenhum gasto."}
        return {
            "intent": "create_expenses",
            "expenses": [_build_expense(e) for e in payload.expenses],
        }

    if payload.intent == "delete_expense":
        return {
            "intent": "delete_expense",
            "expense_target": payload.expense_target or LLMExpenseTarget(),
        }

    if payload.intent == "edit_expense":
        return {
            "intent": "edit_expense",
            "expense_edit": payload.expense_edit or LLMExpenseEdit(target=LLMExpenseTarget()),
        }

    if payload.intent == "filtered_search":
        return {
            "intent": "filtered_search",
            "search_filter": payload.search_filter or LLMSearchFilter(),
        }

    if payload.intent == "create_recurring_expense":
        if payload.recurring_expenses:
            return {
                "intent": "create_recurring_expense_many",
                "recurring_expenses": payload.recurring_expenses,
            }
        rec = payload.recurring_expense
        if not rec:
            return {"intent": "help", "reply_hint": "Não consegui identificar os dados do gasto recorrente."}
        return {
            "intent": "create_recurring_expense",
            "recurring_expense": rec,
        }

    if payload.intent == "create_card_installment_purchase":
        card = payload.card_installment_purchase
        if not card:
            return {"intent": "help", "reply_hint": "Não consegui identificar os dados do parcelamento no cartão."}
        return {
            "intent": "create_card_installment_purchase",
            "card_purchase": card,
        }

    if payload.intent == "list_recurring_expenses":
        return {"intent": "list_recurring_expenses"}

    if payload.intent == "delete_recurring_expense":
        return {
            "intent": "delete_recurring_expense",
            "recurring_target": payload.recurring_target,
        }

    return {"intent": "help"}


def _build_expense(item: LLMExpense) -> ParsedExpense:
    today = date.today()
    if item.expense_date == "yesterday":
        expense_date = today - timedelta(days=1)
    else:
        try:
            expense_date = date.fromisoformat(item.expense_date)
        except (ValueError, TypeError):
            expense_date = today

    return ParsedExpense(
        description=(item.description or "gasto registrado").strip()[:120],
        category=item.category.lower(),
        amount_cents=brl_to_cents(item.amount_brl),
        expense_date=expense_date,
        source_type="text",
    )


def brl_to_cents(raw_value: str) -> int:
    try:
        return parse_brl_to_cents(raw_value)
    except ValueError as exc:
        logger.info("Valor BRL inválido retornado no fluxo LLM: %s", raw_value)
        raise exc
