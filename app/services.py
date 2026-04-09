from __future__ import annotations

import difflib
import json
import re
import uuid
from datetime import date, timedelta
from decimal import InvalidOperation

from app.db import get_connection
from app.parsing import ParsedBudget, ParsedExpense, parse_brl_to_cents


def resolve_period(period: str | None) -> tuple[str, str]:
    """Returns (start_date_iso, end_date_exclusive_iso) for the given period key."""
    today = date.today()

    if period == "today":
        start = today
        end = today + timedelta(days=1)
    elif period == "yesterday":
        start = today - timedelta(days=1)
        end = today
    elif period is None or period == "this_month":
        start = date(today.year, today.month, 1)
        end = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    elif period == "last_month":
        if today.month == 1:
            start = date(today.year - 1, 12, 1)
            end = date(today.year, 1, 1)
        else:
            start = date(today.year, today.month - 1, 1)
            end = date(today.year, today.month, 1)
    elif period == "this_week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    elif period == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=7)
    elif period and period.startswith("last_N_days:"):
        try:
            n = int(period.split(":")[1])
        except (ValueError, IndexError):
            n = 30
        start = today - timedelta(days=n)
        end = today + timedelta(days=1)
    elif period and len(period) == 7:  # "YYYY-MM"
        try:
            year, month = int(period[:4]), int(period[5:7])
            start = date(year, month, 1)
            end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        except ValueError:
            start = date(today.year, today.month, 1)
            end = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    else:
        start = date(today.year, today.month, 1)
        end = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)

    return start.isoformat(), end.isoformat()


class AmbiguousMatchError(Exception):
    def __init__(self, candidates: list[dict]) -> None:
        self.candidates = candidates
        super().__init__("Múltiplos gastos correspondentes encontrados.")


CATEGORY_LABELS = {
    "alimentacao": "Alimentação",
    "transporte": "Transporte",
    "moradia": "Moradia",
    "saude": "Saúde",
    "lazer": "Lazer",
    "compras": "Compras",
    "educacao": "Educação",
    "outros": "Outros",
}


def cents_to_brl(amount_cents: int) -> str:
    value = amount_cents / 100
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_money_to_cents(raw_value: str) -> int:
    return parse_brl_to_cents(raw_value)


def format_category(category: str, user_id: int | None = None) -> str:
    if user_id is not None:
        label = CategoryService().get_label(user_id, category)
        if label:
            return label
    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def format_expense_date(expense_date: date) -> str:
    today = date.today()
    if expense_date == today:
        return "hoje"
    if expense_date == today - timedelta(days=1):
        return "ontem"
    return expense_date.strftime("%d/%m/%Y")


def pluralize(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


class ExpenseService:
    def create_expense(self, expense: ParsedExpense, user_id: int | None = None) -> int:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO expenses (
                    description, category, amount_cents, expense_date, source_type, user_id,
                    expense_kind, installment_group_id, installment_index, installment_total,
                    installment_purchase_total_cents, card_label
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expense.description,
                    expense.category,
                    expense.amount_cents,
                    expense.expense_date.isoformat(),
                    expense.source_type,
                    user_id,
                    "normal",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def create_expenses(self, expenses: list[ParsedExpense], user_id: int | None = None) -> list[int]:
        ids: list[int] = []
        with get_connection() as connection:
            for expense in expenses:
                cursor = connection.execute(
                    """
                    INSERT INTO expenses (
                        description, category, amount_cents, expense_date, source_type, user_id,
                        expense_kind, installment_group_id, installment_index, installment_total,
                        installment_purchase_total_cents, card_label
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        expense.description,
                        expense.category,
                        expense.amount_cents,
                        expense.expense_date.isoformat(),
                        expense.source_type,
                        user_id,
                        "normal",
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            connection.commit()
        return ids

    def create_card_installment_purchase(
        self,
        *,
        user_id: int,
        description: str,
        category: str,
        installment_amount_cents: int,
        installment_total: int,
        purchase_total_cents: int,
        first_expense_date: date,
        card_label: str | None = None,
    ) -> list[int]:
        group_id = str(uuid.uuid4())
        ids: list[int] = []
        with get_connection() as connection:
            for idx in range(installment_total):
                installment_date = _add_months(first_expense_date, idx)
                cursor = connection.execute(
                    """
                    INSERT INTO expenses (
                        description, category, amount_cents, expense_date, source_type, user_id,
                        expense_kind, installment_group_id, installment_index, installment_total,
                        installment_purchase_total_cents, card_label
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        description,
                        category,
                        installment_amount_cents,
                        installment_date.isoformat(),
                        "text",
                        user_id,
                        "card_installment",
                        group_id,
                        idx + 1,
                        installment_total,
                        purchase_total_cents,
                        card_label,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            connection.commit()
        return ids

    def list_expenses_for_month(self, year: int, month: int, user_id: int | None = None) -> list[dict]:
        start_date = date(year, month, 1).isoformat()
        if month == 12:
            end_date = date(year + 1, 1, 1).isoformat()
        else:
            end_date = date(year, month + 1, 1).isoformat()

        with get_connection() as connection:
            if user_id is not None:
                rows = connection.execute(
                    """
                    SELECT id, description, category, amount_cents, expense_date, source_type, created_at, expense_kind,
                           installment_group_id, installment_index, installment_total, installment_purchase_total_cents, card_label
                    FROM expenses
                    WHERE expense_date >= ? AND expense_date < ? AND user_id = ?
                    ORDER BY expense_date DESC, id DESC
                    """,
                    (start_date, end_date, user_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, description, category, amount_cents, expense_date, source_type, created_at, expense_kind,
                           installment_group_id, installment_index, installment_total, installment_purchase_total_cents, card_label
                    FROM expenses
                    WHERE expense_date >= ? AND expense_date < ?
                    ORDER BY expense_date DESC, id DESC
                    """,
                    (start_date, end_date),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_monthly_summary(self, year: int, month: int, user_id: int | None = None) -> dict:
        expenses = self.list_expenses_for_month(year, month, user_id=user_id)
        total_cents = sum(expense["amount_cents"] for expense in expenses)
        categories: dict[str, int] = {}
        for expense in expenses:
            categories.setdefault(expense["category"], 0)
            categories[expense["category"]] += expense["amount_cents"]

        ordered_categories = [
            {
                "category": category,
                "category_label": format_category(category, user_id),
                "amount_cents": amount_cents,
                "amount_brl": cents_to_brl(amount_cents),
            }
            for category, amount_cents in sorted(categories.items(), key=lambda item: item[1], reverse=True)
        ]

        budgets = BudgetService().list_budgets(user_id=user_id)
        budget_map = {budget["category"]: budget["amount_cents"] for budget in budgets}
        budget_status = []
        for category, actual in categories.items():
            planned = budget_map.get(category, 0)
            budget_status.append(
                {
                    "category": category,
                    "category_label": format_category(category, user_id),
                    "planned_cents": planned,
                    "planned_brl": cents_to_brl(planned),
                    "actual_cents": actual,
                    "actual_brl": cents_to_brl(actual),
                    "difference_cents": planned - actual,
                    "difference_brl": cents_to_brl(planned - actual),
                }
            )

        recent_expenses = []
        for expense in expenses[:10]:
            enriched = dict(expense)
            enriched["category_label"] = format_category(expense["category"], user_id)
            recent_expenses.append(enriched)

        return {
            "total_cents": total_cents,
            "total_brl": cents_to_brl(total_cents),
            "expense_count": len(expenses),
            "categories": ordered_categories,
            "budgets": budget_status,
            "recent_expenses": recent_expenses,
        }

    def _score_candidates(
        self,
        candidates: list[dict],
        target: "LLMExpenseTarget",  # type: ignore[name-defined]  # noqa: F821
    ) -> list[tuple[float, dict]]:
        scored: list[tuple[float, dict]] = []
        for expense in candidates:
            score = 0.0

            if target.description_hint:
                ratio = difflib.SequenceMatcher(
                    None, target.description_hint.lower(), expense["description"].lower()
                ).ratio()
                score += ratio * 3

            if target.category_hint and expense["category"] == target.category_hint.lower():
                score += 1.0

            if target.amount_brl_hint:
                try:
                    target_cents = parse_money_to_cents(target.amount_brl_hint)
                    if expense["amount_cents"] == target_cents:
                        score += 2.0
                    elif abs(expense["amount_cents"] - target_cents) <= target_cents * 0.05:
                        score += 1.0
                except (ValueError, InvalidOperation):
                    pass

            if target.date_hint:
                today = date.today()
                if target.date_hint == "today":
                    target_date = today.isoformat()
                elif target.date_hint == "yesterday":
                    target_date = (today - timedelta(days=1)).isoformat()
                elif target.date_hint == "last":
                    target_date = None
                else:
                    target_date = target.date_hint
                if target_date and expense["expense_date"] == target_date:
                    score += 1.5

            if score > 0:
                scored.append((score, expense))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def find_best_match(
        self,
        user_id: int,
        target: "LLMExpenseTarget",  # type: ignore[name-defined]  # noqa: F821
        within_days: int = 90,
    ) -> dict | None:
        """
        Returns the best unique match, or None if not found.
        Raises AmbiguousMatchError if two candidates score too close.
        """
        cutoff = (date.today() - timedelta(days=within_days)).isoformat()
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, description, category, amount_cents, expense_date, source_type, created_at, expense_kind,
                       installment_group_id, installment_index, installment_total, installment_purchase_total_cents, card_label
                FROM expenses
                WHERE user_id = ? AND expense_date >= ?
                ORDER BY expense_date DESC, id DESC
                """,
                (user_id, cutoff),
            ).fetchall()

        candidates = [dict(row) for row in rows]
        if not candidates:
            return None

        # No hints at all — can't match
        has_hints = any([
            target.description_hint,
            target.category_hint,
            target.amount_brl_hint,
            target.date_hint,
        ])
        if not has_hints:
            return None

        scored = self._score_candidates(candidates, target)
        if not scored:
            return None

        best_score, best = scored[0]
        if best_score < 0.1:
            return None

        # Ambiguous: two strong candidates within 5% of each other
        if len(scored) >= 2:
            second_score = scored[1][0]
            if second_score >= 0.1 and (best_score - second_score) / best_score < 0.05:
                raise AmbiguousMatchError([exp for _, exp in scored[:5]])

        return best

    def search_expenses(
        self,
        user_id: int,
        *,
        start_date: str,
        end_date: str,
        category: str | None = None,
        description_hint: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        query = """
            SELECT id, description, category, amount_cents, expense_date, source_type,
                   expense_kind, installment_group_id, installment_index, installment_total,
                   installment_purchase_total_cents, card_label
            FROM expenses
            WHERE user_id = ? AND expense_date >= ? AND expense_date < ?
        """
        params: list = [user_id, start_date, end_date]
        if category:
            query += " AND category = ?"
            params.append(category.lower())
        if description_hint:
            query += " AND LOWER(description) LIKE ?"
            params.append(f"%{description_hint.lower()}%")
        query += " ORDER BY expense_date DESC, id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with get_connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def find_recent_expenses(self, user_id: int, limit: int = 5, within_days: int = 90) -> list[dict]:
        cutoff = (date.today() - timedelta(days=within_days)).isoformat()
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, description, category, amount_cents, expense_date, expense_kind,
                       installment_group_id, installment_index, installment_total
                FROM expenses
                WHERE user_id = ? AND expense_date >= ?
                ORDER BY expense_date DESC, id DESC
                LIMIT ?
                """,
                (user_id, cutoff, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_expense(self, user_id: int, expense_id: int) -> dict:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id, description, category, amount_cents, expense_date, expense_kind,
                       installment_group_id, installment_index, installment_total
                FROM expenses WHERE id = ? AND user_id = ?
                """,
                (expense_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError(f"Gasto {expense_id} não encontrado para este usuário.")
            connection.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id))
            connection.commit()
        return dict(row)

    def delete_installment_series(self, user_id: int, expense_id: int) -> int:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT installment_group_id
                FROM expenses
                WHERE id = ? AND user_id = ?
                """,
                (expense_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError(f"Gasto {expense_id} não encontrado para este usuário.")
            group_id = row["installment_group_id"]
            if not group_id:
                connection.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id))
                connection.commit()
                return 1
            deleted = connection.execute(
                "DELETE FROM expenses WHERE installment_group_id = ? AND user_id = ?",
                (group_id, user_id),
            )
            connection.commit()
            return int(deleted.rowcount or 0)

    def update_expense(
        self,
        user_id: int,
        expense_id: int,
        *,
        amount_cents: int | None = None,
        category: str | None = None,
        description: str | None = None,
        expense_date: str | None = None,
        apply_to_series: bool = False,
    ) -> dict:
        with get_connection() as connection:
            old = connection.execute(
                """
                SELECT id, description, category, amount_cents, expense_date, expense_kind, installment_group_id
                FROM expenses WHERE id = ? AND user_id = ?
                """,
                (expense_id, user_id),
            ).fetchone()
            if not old:
                raise ValueError(f"Gasto {expense_id} não encontrado para este usuário.")

            new_amount = amount_cents if amount_cents is not None else old["amount_cents"]
            new_category = category if category is not None else old["category"]
            new_description = description if description is not None else old["description"]
            new_date = expense_date if expense_date is not None else old["expense_date"]

            if apply_to_series and old["installment_group_id"]:
                connection.execute(
                    """
                    UPDATE expenses
                    SET amount_cents = ?, category = ?, description = ?
                    WHERE installment_group_id = ? AND user_id = ?
                    """,
                    (new_amount, new_category, new_description, old["installment_group_id"], user_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE expenses
                    SET amount_cents = ?, category = ?, description = ?, expense_date = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (new_amount, new_category, new_description, new_date, expense_id, user_id),
                )
            connection.commit()

            updated = connection.execute(
                "SELECT id, description, category, amount_cents, expense_date FROM expenses WHERE id = ?",
                (expense_id,),
            ).fetchone()
        return dict(updated)


class BudgetService:
    def upsert_budget(self, budget: ParsedBudget, user_id: int | None = None) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO budgets (user_id, category, amount_cents)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, category) DO UPDATE SET amount_cents = excluded.amount_cents
                """,
                (user_id, budget.category, budget.amount_cents),
            )
            connection.commit()

    def list_budgets(self, user_id: int | None = None) -> list[dict]:
        with get_connection() as connection:
            if user_id is not None:
                rows = connection.execute(
                    "SELECT category, amount_cents, created_at FROM budgets WHERE user_id = ? ORDER BY category ASC",
                    (user_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT category, amount_cents, created_at FROM budgets ORDER BY category ASC"
                ).fetchall()
        budgets = []
        for row in rows:
            b = dict(row)
            b["category_label"] = format_category(b["category"])
            budgets.append(b)
        return budgets


class ChatHistoryService:
    MAX_STORED = 200

    def append(self, user_id: int, role: str, content: str) -> None:
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO chat_messages (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )
            # Prune oldest rows beyond MAX_STORED
            connection.execute(
                """
                DELETE FROM chat_messages
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM chat_messages WHERE user_id = ?
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (user_id, user_id, self.MAX_STORED),
            )
            connection.commit()

    def load_recent(self, user_id: int, limit: int = 20) -> list[dict]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM chat_messages
                WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return list(reversed([dict(r) for r in rows]))


class CategoryService:
    def list_for_user(self, user_id: int) -> list[dict]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT id, user_id, key, label, examples, sort_order FROM user_categories WHERE user_id = ? ORDER BY sort_order ASC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def bulk_insert(self, user_id: int, categories: list[dict]) -> None:
        with get_connection() as connection:
            for cat in categories:
                examples = cat.get("examples", [])
                examples_str = json.dumps(examples, ensure_ascii=False) if isinstance(examples, list) else str(examples)
                connection.execute(
                    """
                    INSERT INTO user_categories (user_id, key, label, examples, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, key) DO UPDATE SET label = excluded.label, examples = excluded.examples
                    """,
                    (user_id, cat["key"], cat["label"], examples_str, cat.get("sort_order", 0)),
                )
            connection.commit()

    def add_category(self, user_id: int, key: str, label: str, examples: list[str] | None = None) -> None:
        if not re.match(r"^[a-z][a-z0-9_]{1,29}$", key):
            raise ValueError("Chave inválida. Use apenas letras minúsculas, números e underscores (ex.: saude_mental).")
        examples_str = json.dumps(examples or [], ensure_ascii=False)
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) FROM user_categories WHERE user_id = ? AND key = ?",
                (user_id, key),
            ).fetchone()[0]
            if existing:
                raise ValueError(f"Categoria '{key}' já existe.")
            max_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM user_categories WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO user_categories (user_id, key, label, examples, sort_order) VALUES (?, ?, ?, ?, ?)",
                (user_id, key, label, examples_str, max_order + 1),
            )
            connection.commit()

    def update_category(self, user_id: int, key: str, label: str) -> None:
        with get_connection() as connection:
            connection.execute(
                "UPDATE user_categories SET label = ? WHERE user_id = ? AND key = ?",
                (label, user_id, key),
            )
            connection.commit()

    def delete_category(self, user_id: int, key: str) -> None:
        if key == "outros":
            raise ValueError("A categoria 'outros' é obrigatória e não pode ser removida.")
        with get_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM expenses WHERE user_id = ? AND category = ?",
                (user_id, key),
            ).fetchone()[0]
            if count > 0:
                raise ValueError(f"Esta categoria possui {count} gasto(s). Reclassifique-os antes de excluir.")
            connection.execute(
                "DELETE FROM user_categories WHERE user_id = ? AND key = ?",
                (user_id, key),
            )
            connection.commit()

    def get_label(self, user_id: int, key: str) -> str:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT label FROM user_categories WHERE user_id = ? AND key = ?",
                (user_id, key),
            ).fetchone()
        return row["label"] if row else ""


class RecurringExpenseService:
    def create(
        self,
        user_id: int,
        description: str,
        category: str,
        amount_cents: int,
        frequency: str,
        day: int,
    ) -> dict:
        if frequency not in ("monthly", "weekly"):
            raise ValueError("frequency deve ser 'monthly' ou 'weekly'.")
        if frequency == "monthly" and not (1 <= day <= 28):
            raise ValueError("day_of_month deve ser entre 1 e 28.")
        if frequency == "weekly" and not (0 <= day <= 6):
            raise ValueError("day_of_week deve ser entre 0 (seg) e 6 (dom).")

        day_of_month = day if frequency == "monthly" else None
        day_of_week = day if frequency == "weekly" else None

        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO recurring_expenses
                    (user_id, description, category, amount_cents, frequency, day_of_month, day_of_week)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, description, category, amount_cents, frequency, day_of_month, day_of_week),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM recurring_expenses WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def list_for_user(self, user_id: int) -> list[dict]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM recurring_expenses WHERE user_id = ? AND active = 1 ORDER BY id ASC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def deactivate(self, user_id: int, recurring_id: int) -> dict:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM recurring_expenses WHERE id = ? AND user_id = ? AND active = 1",
                (recurring_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError(f"Recorrente {recurring_id} não encontrado.")
            connection.execute(
                "UPDATE recurring_expenses SET active = 0 WHERE id = ?",
                (recurring_id,),
            )
            connection.commit()
        return dict(row)

    def find_by_description_hint(self, user_id: int, hint: str) -> list[dict]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM recurring_expenses WHERE user_id = ? AND active = 1",
                (user_id,),
            ).fetchall()
        candidates = [dict(r) for r in rows]
        if not hint:
            return candidates
        hint_lower = hint.lower()
        return [c for c in candidates if hint_lower in c["description"].lower()]

    def generate_pending(self, user_id: int) -> list[dict]:
        today = date.today()
        recurrings = self.list_for_user(user_id)
        generated = []

        for rec in recurrings:
            last_iso: str | None = rec["last_generated"]
            last = date.fromisoformat(last_iso) if last_iso else None

            if rec["frequency"] == "monthly":
                dom = rec["day_of_month"]
                # Start of current month
                month_start = date(today.year, today.month, 1)
                # Due date this month (clamped to 28)
                due_day = min(dom, 28)
                due_this_month = date(today.year, today.month, due_day)

                if today >= due_this_month and (last is None or last < month_start):
                    expense_date = due_this_month
                    self._insert_and_update(rec, user_id, expense_date, today)
                    generated.append(rec)

            elif rec["frequency"] == "weekly":
                dow = rec["day_of_week"]
                # Find all due dates since last_generated (or up to 4 weeks back)
                if last is None:
                    # Generate once for the most recent past occurrence
                    days_ago = (today.weekday() - dow) % 7
                    due = today - timedelta(days=days_ago)
                    if due <= today:
                        self._insert_and_update(rec, user_id, due, today)
                        generated.append(rec)
                else:
                    # Generate for each week that has passed since last
                    next_due = last + timedelta(days=1)
                    # Advance to the next matching weekday
                    days_until = (dow - next_due.weekday()) % 7
                    next_due = next_due + timedelta(days=days_until)
                    while next_due <= today:
                        self._insert_and_update(rec, user_id, next_due, today)
                        generated.append(rec)
                        next_due += timedelta(weeks=1)

        return generated

    def _insert_and_update(self, rec: dict, user_id: int, expense_date: date, today: date) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO expenses (description, category, amount_cents, expense_date, source_type, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rec["description"],
                    rec["category"],
                    rec["amount_cents"],
                    expense_date.isoformat(),
                    "recurring",
                    user_id,
                ),
            )
            connection.execute(
                "UPDATE recurring_expenses SET last_generated = ? WHERE id = ?",
                (today.isoformat(), rec["id"]),
            )
            connection.commit()


def _add_months(base: date, months: int) -> date:
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    day = min(base.day, 28)
    return date(year, month, day)
