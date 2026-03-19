from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


@dataclass
class ParsedExpense:
    description: str
    category: str
    amount_cents: int
    expense_date: date
    source_type: str = "text"


@dataclass
class ParsedBudget:
    category: str
    amount_cents: int


def parse_brl_to_cents(raw_value: str) -> int:
    cleaned = raw_value.strip().replace("r$", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Valor invalido: {raw_value}") from exc
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
