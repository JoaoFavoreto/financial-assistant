from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CATEGORY_KEYWORDS = {
    "alimentacao": ["mercado", "supermercado", "almoco", "almoço", "janta", "lanche", "padaria", "ifood", "restaurante", "comida", "cantina"],
    "transporte": ["uber", "99", "taxi", "táxi", "gasolina", "onibus", "ônibus", "metro", "metrô", "transporte", "estacionamento"],
    "moradia": ["aluguel", "condominio", "condomínio", "energia", "agua", "água", "internet", "luz", "moradia"],
    "saude": ["farmacia", "farmácia", "medico", "médico", "consulta", "academia", "saude", "saúde", "remedio", "remédio"],
    "lazer": ["cinema", "bar", "show", "netflix", "spotify", "lazer"],
    "compras": ["roupa", "amazon", "compra", "shopping", "loja"],
    "educacao": ["curso", "livro", "faculdade", "educacao", "educação"],
}

MONTH_QUERY_PATTERNS = (
    "quanto gastei esse mes",
    "quanto gastei este mes",
    "resumo",
    "resumo do mes",
    "resumo desse mes",
    "resumo deste mes",
    "como esta meu mes",
)

WEEK_QUERY_PATTERNS = (
    "resumo da semana",
    "resumo semanal",
    "como foi minha semana",
    "como esta minha semana",
)

RECENT_QUERY_PATTERNS = (
    "ultimos gastos",
    "ultimos lancamentos",
    "gastos recentes",
    "ultimas despesas",
    "ultimos registros",
)


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


@dataclass
class LocalSearchFilter:
    category_hint: str | None = None
    description_hint: str | None = None
    period: str | None = None
    granularity: str | None = "total"
    limit: int | None = None


def strip_accents(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", text) if unicodedata.category(char) != "Mn")


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def canonicalize(text: str) -> str:
    return normalize_text(strip_accents(text))


def normalize_category(raw_value: str) -> str:
    candidate = canonicalize(raw_value)
    for category, keywords in CATEGORY_KEYWORDS.items():
        if candidate == category:
            return category
        if candidate.startswith(category[:4]):
            return category
        if candidate in {canonicalize(keyword) for keyword in keywords}:
            return category
    return candidate or "outros"


def parse_message(text: str) -> dict:
    normalized = normalize_text(text)
    canonical = canonicalize(text)
    if not normalized:
        return {"intent": "empty"}

    budget = parse_budget(normalized)
    if budget:
        return {"intent": "set_budget", "budget": budget}

    search_filter = parse_search_filter(canonical)
    if _looks_like_weekly_summary_query(canonical, search_filter):
        return {"intent": "weekly_summary"}

    if normalized.startswith("categorias") or "por categoria" in canonical:
        return {"intent": "category_summary"}

    if any(pattern in canonical for pattern in MONTH_QUERY_PATTERNS):
        if search_filter and any([search_filter.category_hint, search_filter.description_hint, search_filter.limit]):
            return {"intent": "filtered_search", "search_filter": search_filter}
        return {"intent": "monthly_summary"}

    if _looks_like_expense_entry(normalized):
        expenses = parse_expenses(normalized)
        if expenses:
            return {"intent": "create_expenses", "expenses": expenses}

    if search_filter and _looks_like_filtered_search(canonical, search_filter):
        return {"intent": "filtered_search", "search_filter": search_filter}

    expenses = parse_expenses(normalized)
    if expenses:
        return {"intent": "create_expenses", "expenses": expenses}

    return {"intent": "help"}


def parse_budget(normalized_text: str) -> ParsedBudget | None:
    match = re.search(r"(?:orcamento|orçamento|definir orcamento|definir orçamento)\s+([a-zA-Z\u00c0-\u017f_]+)\s+([\d.,]+)", normalized_text)
    if not match:
        return None
    return ParsedBudget(
        category=normalize_category(match.group(1)),
        amount_cents=parse_brl_to_cents(match.group(2)),
    )


def _looks_like_expense_entry(normalized_text: str) -> bool:
    if not re.search(r"\d", normalized_text):
        return False
    if re.search(r"\b(gastei|paguei|custou|foi)\b", normalized_text):
        return True
    return bool(re.match(r"^\d+[\d.,]*\b", normalized_text))


def parse_expenses(normalized_text: str) -> list[ParsedExpense]:
    amount_matches = list(re.finditer(r"(\d+[\d.,]*)", normalized_text))
    if not amount_matches:
        return []

    expenses: list[ParsedExpense] = []
    default_date = detect_date(normalized_text)

    for index, match in enumerate(amount_matches):
        start = 0 if index == 0 else match.start()
        end = len(normalized_text) if index == len(amount_matches) - 1 else amount_matches[index + 1].start()
        segment = normalized_text[start:end].strip(" ,;-")
        expense = parse_expense_segment(segment, default_date)
        if expense:
            expenses.append(expense)

    return expenses


def parse_expense_segment(segment_text: str, default_date: date) -> ParsedExpense | None:
    amount_match = re.search(r"(\d+[\d.,]*)", segment_text)
    if not amount_match:
        return None

    amount_cents = parse_brl_to_cents(amount_match.group(1))
    category = detect_category(segment_text)
    expense_date = detect_date(segment_text, default_date)
    description = build_description(segment_text, amount_match.group(1), category)
    return ParsedExpense(
        description=description,
        category=category,
        amount_cents=amount_cents,
        expense_date=expense_date,
    )


def build_description(normalized_text: str, amount_token: str, category: str) -> str:
    stripped = normalized_text.replace(amount_token, " ")
    stripped = re.sub(r"\b(gastei|paguei|foi|custou|hoje|ontem|no|na|em|e|de|do|da|com)\b", " ", stripped, flags=re.IGNORECASE)
    stripped = " ".join(stripped.split()).strip(" -.,")
    return stripped[:120] if stripped else f"gasto em {category}"


def detect_category(text: str) -> str:
    candidate = canonicalize(text)
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category in candidate:
            return category
        if any(canonicalize(keyword) in candidate for keyword in keywords):
            return category
    return "outros"


def detect_date(text: str, default_date: date | None = None) -> date:
    candidate = canonicalize(text)
    today = date.today()
    if "ontem" in candidate:
        return today - timedelta(days=1)
    if "hoje" in candidate:
        return today
    return default_date or today


def _looks_like_weekly_summary_query(canonical_text: str, search_filter: LocalSearchFilter | None) -> bool:
    if any(pattern in canonical_text for pattern in WEEK_QUERY_PATTERNS):
        return True
    if not search_filter or search_filter.period != "this_week":
        return False
    if search_filter.category_hint or search_filter.description_hint:
        return False
    return any(
        phrase in canonical_text
        for phrase in ("quanto gastei", "o que gastei", "esta semana", "essa semana", "minha semana")
    )


def _looks_like_filtered_search(canonical_text: str, search_filter: LocalSearchFilter) -> bool:
    if search_filter.limit is not None:
        return True

    prefixes = (
        "gastos de ",
        "gastos com ",
        "quanto gastei",
        "o que gastei",
        "me mostra",
        "mostra meus",
        "quais foram",
    )
    if canonical_text.startswith(prefixes):
        return True

    if search_filter.category_hint or search_filter.description_hint:
        return True

    if search_filter.period in {"today", "yesterday", "last_week", "last_month"}:
        return True

    return False


def parse_search_filter(canonical_text: str) -> LocalSearchFilter | None:
    category_hint = None
    description_hint = None

    for category, keywords in CATEGORY_KEYWORDS.items():
        if category in canonical_text:
            category_hint = category
            break
        for keyword in keywords:
            keyword_canonical = canonicalize(keyword)
            if keyword_canonical in canonical_text:
                if keyword_canonical in {"uber", "ifood", "mercado", "cantina", "padaria", "amazon", "netflix", "spotify"}:
                    description_hint = keyword_canonical
                else:
                    category_hint = category
                break
        if category_hint or description_hint:
            break

    recent_requested = any(pattern in canonical_text for pattern in RECENT_QUERY_PATTERNS)

    period = None
    if "ontem" in canonical_text:
        period = "yesterday"
    elif "hoje" in canonical_text and any(token in canonical_text for token in ("gastei", "gastos", "despesas", "lancamentos")):
        period = "today"
    elif "esse mes" in canonical_text or "este mes" in canonical_text:
        period = "this_month"
    elif "mes passado" in canonical_text:
        period = "last_month"
    elif "essa semana" in canonical_text or "esta semana" in canonical_text:
        period = "this_week"
    elif "semana passada" in canonical_text:
        period = "last_week"
    elif recent_requested:
        period = "last_N_days:30"

    limit = 5 if recent_requested else None

    if not any([category_hint, description_hint, period, limit]):
        return None

    return LocalSearchFilter(
        category_hint=category_hint,
        description_hint=description_hint,
        period=period,
        granularity="total",
        limit=limit,
    )


def parse_brl_to_cents(raw_value: str) -> int:
    cleaned = raw_value.strip().replace("r$", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Valor inválido: {raw_value}") from exc
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))



