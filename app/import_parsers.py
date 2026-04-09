from __future__ import annotations

import csv
import difflib
import io
import re
import unicodedata
from datetime import date, timedelta
from decimal import InvalidOperation

from app.parsing import ParsedExpense, canonicalize, detect_category, parse_brl_to_cents


class ImportParseError(Exception):
    """Raised when a file cannot be parsed as expense data."""


# ─── Column mapping ──────────────────────────────────────────────────────────

COLUMN_ALIASES: dict[str, list[str]] = {
    "date": [
        "data", "date", "dt", "data lancamento", "data transacao",
        "data da compra", "data compra", "data pagamento",
    ],
    "description": [
        "descricao", "description", "historico", "titulo", "title",
        "estabelecimento", "memo", "nome", "lancamento",
    ],
    "amount": [
        "valor", "amount", "value", "montante", "vlr",
        "valor (r$)", "valor brl", "preco", "total",
    ],
    "category": ["categoria", "category"],
}

# Lines to skip: payments, credits, fees
_IGNORE_PATTERN = re.compile(
    r"\b(pagamento|credito|cr[eé]dito|iof|juros|encargo|anuidade"
    r"|tarifa|multa|estorno|cashback|saldo|devolucao|devolução)\b",
    re.IGNORECASE,
)

# Instalment suffix: "AMAZON 2/12"
_INSTALMENT_PATTERN = re.compile(r"\s+(\d{1,2}/\d{1,2})\s*$")

# Date at start of line (PDF text extraction)
_LINE_DATE_PATTERN = re.compile(r"^(\d{2}/\d{2}(?:/\d{2,4})?)\s+")

# BRL amount (last match in a line wins)
_AMOUNT_PATTERN = re.compile(r"([\d]{1,3}(?:\.\d{3})*,\d{2})")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _decode_bytes(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportParseError("Não foi possível decodificar o arquivo. Verifique a codificação.")


def _normalize_col(name: str) -> str:
    text = name.strip().lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(text.split())


def _detect_column_mapping(fieldnames: list[str]) -> dict[str, str]:
    """Returns logical_key → actual_csv_column_name."""
    normalized = {_normalize_col(f): f for f in fieldnames}
    result: dict[str, str] = {}
    for logical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                result[logical] = normalized[alias]
                break
    return result


def _parse_amount(raw: str) -> int | None:
    """Convert BRL string to cents. Returns None if unparseable or negative."""
    cleaned = raw.strip().replace("R$", "").replace("r$", "").strip()
    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-+").strip()
    if not cleaned:
        return None
    try:
        cents = parse_brl_to_cents(cleaned)
    except (ValueError, InvalidOperation):
        return None
    return -cents if negative else cents


def _parse_date(raw: str, reference_month: date | None = None) -> date | None:
    raw = raw.strip()
    # ISO: 2025-02-15
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    # dd/mm/yyyy or dd/mm/yy
    m = re.match(r"^(\d{2})/(\d{2})/(\d{2,4})$", raw)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    # dd/mm — infer year from reference_month
    m = re.match(r"^(\d{2})/(\d{2})$", raw)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        ref = reference_month or date.today()
        try:
            return date(ref.year, month, day)
        except ValueError:
            return None
    return None


def _apply_instalment(description: str) -> str:
    m = _INSTALMENT_PATTERN.search(description)
    if m:
        description = description[: m.start()] + f" ({m.group(1)})"
    return description[:120]


# ─── CSV ─────────────────────────────────────────────────────────────────────

def parse_csv(file_bytes: bytes, reference_month: date | None = None) -> list[ParsedExpense]:
    """Parse expense CSV file. Returns list of ParsedExpense with source_type='import'."""
    text = _decode_bytes(file_bytes)
    if not text.strip():
        raise ImportParseError("O arquivo CSV está vazio.")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;|\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        raise ImportParseError("O CSV não tem cabeçalho reconhecível.")

    col_map = _detect_column_mapping(fieldnames)
    if "amount" not in col_map:
        raise ImportParseError(
            f"Não encontrei coluna de valor no CSV. "
            f"Colunas encontradas: {', '.join(fieldnames)}"
        )

    results: list[ParsedExpense] = []
    for row in reader:
        amount_raw = row.get(col_map["amount"], "").strip()
        if not amount_raw:
            continue

        amount_cents = _parse_amount(amount_raw)
        if amount_cents is None or amount_cents <= 0:
            continue

        description = row.get(col_map.get("description", ""), "").strip()
        if not description:
            description = "gasto importado"
        if _IGNORE_PATTERN.search(description):
            continue
        description = _apply_instalment(description)

        date_raw = row.get(col_map.get("date", ""), "").strip()
        expense_date = _parse_date(date_raw, reference_month) if date_raw else None
        if expense_date is None:
            expense_date = reference_month or date.today()

        category = detect_category(description)

        results.append(ParsedExpense(
            description=description,
            category=category,
            amount_cents=amount_cents,
            expense_date=expense_date,
            source_type="import",
        ))

    if not results:
        raise ImportParseError(
            "Nenhum gasto encontrado no CSV. "
            "Verifique se o arquivo tem colunas de data, descrição e valor."
        )
    return results


# ─── PDF ─────────────────────────────────────────────────────────────────────

def parse_pdf(file_bytes: bytes, reference_month: date | None = None) -> list[ParsedExpense]:
    """Parse expense PDF (credit card invoice). Returns list of ParsedExpense."""
    try:
        import pdfplumber  # type: ignore[import]
    except ImportError:
        raise ImportParseError(
            "A biblioteca pdfplumber não está instalada. "
            "Execute: pip install pdfplumber"
        )

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                raise ImportParseError("O PDF está vazio.")

            if reference_month is None:
                reference_month = _detect_pdf_reference_month(pdf)

            all_rows: list[dict] = []
            for page in pdf.pages:
                rows = _extract_from_tables(page)
                if not rows:
                    text = page.extract_text() or ""
                    rows = _extract_from_text(text, reference_month)
                all_rows.extend(rows)
    except ImportParseError:
        raise
    except Exception as exc:
        raise ImportParseError(f"Erro ao ler o PDF: {exc}") from exc

    if not all_rows:
        raise ImportParseError(
            "Nenhum gasto encontrado no PDF. "
            "O arquivo pode ser um PDF escaneado (imagem) ou ter um formato não suportado."
        )
    return _rows_to_parsed_expenses(all_rows, reference_month)


def _detect_pdf_reference_month(pdf: object) -> date | None:
    """Scan first 2 pages for a vencimento/fatura date to infer the reference month."""
    patterns = [
        re.compile(r"(?:vencimento|fatura)[:\s]+(\d{2}/\d{2}/\d{4})", re.IGNORECASE),
        re.compile(r"(?:vencimento|fatura)[:\s]+(\d{2}/\d{2})", re.IGNORECASE),
    ]
    for page in pdf.pages[:2]:  # type: ignore[attr-defined]
        text = page.extract_text() or ""
        for pattern in patterns:
            m = pattern.search(text)
            if m:
                d = _parse_date(m.group(1))
                if d:
                    return d.replace(day=1)
    return None


def _looks_like_expense_table(rows: list[list]) -> bool:
    if not rows or len(rows) < 2:
        return False
    if any(len(row or []) < 3 for row in rows[:3] if row):
        return False
    for row in rows[1:6]:
        if row and any(_AMOUNT_PATTERN.search(str(cell or "")) for cell in row):
            return True
    return False


def _map_table_columns(header_row: list) -> dict[str, int]:
    result: dict[str, int] = {}
    has_header = False
    for i, cell in enumerate(header_row or []):
        norm = _normalize_col(str(cell or ""))
        if any(alias in norm for alias in ("data", "date")):
            result["date"] = i
            has_header = True
        elif any(alias in norm for alias in ("descric", "titulo", "estabele", "historico", "lancamento")):
            result["description"] = i
            has_header = True
        elif any(alias in norm for alias in ("valor", "amount", "montante")):
            result["amount"] = i
            has_header = True
    if not has_header:
        result.setdefault("date", 0)
        result.setdefault("description", 1)
        result.setdefault("amount", len(header_row) - 1 if header_row else 2)
    result["_has_header"] = 1 if has_header else 0
    return result


def _extract_from_tables(page: object) -> list[dict]:
    try:
        tables = page.extract_tables()  # type: ignore[attr-defined]
    except Exception:
        return []

    results: list[dict] = []
    for table in tables:
        if not _looks_like_expense_table(table):
            continue
        col_map = _map_table_columns(table[0] if table else [])
        start = 1 if col_map.get("_has_header") else 0
        for row in table[start:]:
            if not row:
                continue
            parsed = _parse_table_row(row, col_map)
            if parsed:
                results.append(parsed)
    return results


def _parse_table_row(row: list, col_map: dict[str, int]) -> dict | None:
    def get(key: str) -> str:
        idx = col_map.get(key, -1)
        if idx < 0 or idx >= len(row):
            return ""
        return str(row[idx] or "").strip()

    description = get("description")
    amount_raw = get("amount")
    if not amount_raw or not description:
        return None
    if _IGNORE_PATTERN.search(description):
        return None
    amount_cents = _parse_amount(amount_raw)
    if amount_cents is None or amount_cents <= 0:
        return None
    return {"date": get("date"), "description": description, "amount_cents": amount_cents}


def _extract_from_text(text: str, reference_month: date | None) -> list[dict]:
    results: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        date_match = _LINE_DATE_PATTERN.match(line)
        if not date_match:
            continue
        amount_matches = list(_AMOUNT_PATTERN.finditer(line))
        if not amount_matches:
            continue
        amount_raw = amount_matches[-1].group(1)
        amount_cents = _parse_amount(amount_raw)
        if amount_cents is None or amount_cents <= 0:
            continue
        description = line[date_match.end(): amount_matches[-1].start()].strip()
        if not description:
            continue
        if _IGNORE_PATTERN.search(description):
            continue
        results.append({
            "date": date_match.group(1),
            "description": description[:120],
            "amount_cents": amount_cents,
        })
    return results


def _rows_to_parsed_expenses(rows: list[dict], reference_month: date | None) -> list[ParsedExpense]:
    results: list[ParsedExpense] = []
    for row in rows:
        expense_date = _parse_date(row.get("date", ""), reference_month)
        if expense_date is None:
            expense_date = reference_month or date.today()
        description = _apply_instalment(row.get("description", "gasto importado").strip())
        amount_cents = row.get("amount_cents", 0)
        if amount_cents <= 0:
            continue
        results.append(ParsedExpense(
            description=description,
            category=detect_category(description),
            amount_cents=amount_cents,
            expense_date=expense_date,
            source_type="import",
        ))
    return results


# ─── Duplicate detection ─────────────────────────────────────────────────────

def detect_duplicates(
    user_id: int,
    candidates: list[ParsedExpense],
    tolerance_days: int = 1,
) -> list[bool]:
    """Returns list of booleans (True = likely duplicate) same length as candidates."""
    from app.db import get_connection

    if not candidates:
        return []

    dates = [c.expense_date for c in candidates]
    start = (min(dates) - timedelta(days=tolerance_days)).isoformat()
    end = (max(dates) + timedelta(days=tolerance_days + 1)).isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT description, amount_cents, expense_date
            FROM expenses
            WHERE user_id = ? AND expense_date >= ? AND expense_date < ?
            """,
            (user_id, start, end),
        ).fetchall()

    existing = [dict(r) for r in rows]
    results: list[bool] = []

    for candidate in candidates:
        is_dup = False
        for ex in existing:
            try:
                ex_date = date.fromisoformat(ex["expense_date"])
            except (ValueError, TypeError):
                continue
            if abs((ex_date - candidate.expense_date).days) > tolerance_days:
                continue
            if ex["amount_cents"] != candidate.amount_cents:
                continue
            ratio = difflib.SequenceMatcher(
                None,
                ex["description"].lower(),
                candidate.description.lower(),
            ).ratio()
            if ratio >= 0.85:
                is_dup = True
                break
        results.append(is_dup)

    return results
