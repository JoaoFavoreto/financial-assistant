from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import settings
from app.parsing import ParsedBudget, ParsedExpense
from app.skills import finance_extraction_skill


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


class LLMMessageInterpretation(BaseModel):
    intent: Literal[
        "create_expenses",
        "set_budget",
        "monthly_summary",
        "category_summary",
        "edit_expense",
        "delete_expense",
        "help",
        "empty",
    ]
    expenses: list[LLMExpense] = []
    budget_category: str | None = None
    budget_amount_brl: str | None = None
    reply_hint: str | None = None
    expense_target: LLMExpenseTarget | None = None
    expense_edit: LLMExpenseEdit | None = None


class OpenAIInterpreter:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key, timeout=settings.economia_llm_timeout)
        self.model = settings.openai_model

    def parse_message(self, message: str, user_id: int | None = None) -> dict:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": finance_extraction_skill(user_id=user_id)},
                {"role": "user", "content": message},
            ],
            text_format=LLMMessageInterpretation,
        )
        return interpretation_to_result(response.output_parsed)


def interpretation_to_result(payload: LLMMessageInterpretation) -> dict:
    if payload.intent in {"monthly_summary", "category_summary", "help", "empty"}:
        result: dict = {"intent": payload.intent}
        if payload.reply_hint:
            result["reply_hint"] = payload.reply_hint
        return result

    if payload.intent == "set_budget":
        if not payload.budget_category or not payload.budget_amount_brl:
            return {"intent": "help", "reply_hint": "Nao consegui identificar categoria e valor do orcamento."}
        return {
            "intent": "set_budget",
            "budget": ParsedBudget(
                category=payload.budget_category.lower(),
                amount_cents=brl_to_cents(payload.budget_amount_brl),
            ),
        }

    if payload.intent == "create_expenses":
        if not payload.expenses:
            return {"intent": "help", "reply_hint": "Nao consegui identificar nenhum gasto."}
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
    return int((Decimal(raw_value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
