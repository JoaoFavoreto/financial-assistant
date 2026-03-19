from __future__ import annotations

import difflib
import json
from datetime import date, timedelta

from app.db import get_connection
from app.parsing import ParsedBudget, ParsedExpense


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
                INSERT INTO expenses (description, category, amount_cents, expense_date, source_type, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    expense.description,
                    expense.category,
                    expense.amount_cents,
                    expense.expense_date.isoformat(),
                    expense.source_type,
                    user_id,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def create_expenses(self, expenses: list[ParsedExpense], user_id: int | None = None) -> list[int]:
        ids = []
        for expense in expenses:
            ids.append(self.create_expense(expense, user_id=user_id))
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
                    SELECT id, description, category, amount_cents, expense_date, source_type, created_at
                    FROM expenses
                    WHERE expense_date >= ? AND expense_date < ? AND user_id = ?
                    ORDER BY expense_date DESC, id DESC
                    """,
                    (start_date, end_date, user_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, description, category, amount_cents, expense_date, source_type, created_at
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

    def find_best_match(
        self,
        user_id: int,
        target: "LLMExpenseTarget",  # type: ignore[name-defined]  # noqa: F821
        within_days: int = 90,
    ) -> dict | None:
        """
        Find the best matching expense for a given target.
        Returns None if no match found or if ambiguous (two candidates with very close scores).
        """
        cutoff = (date.today() - timedelta(days=within_days)).isoformat()
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, description, category, amount_cents, expense_date, source_type, created_at
                FROM expenses
                WHERE user_id = ? AND expense_date >= ?
                ORDER BY expense_date DESC, id DESC
                """,
                (user_id, cutoff),
            ).fetchall()

        candidates = [dict(row) for row in rows]
        if not candidates:
            return None

        scored: list[tuple[float, dict]] = []
        for expense in candidates:
            score = 0.0

            if target.description_hint:
                ratio = difflib.SequenceMatcher(
                    None, target.description_hint.lower(), expense["description"].lower()
                ).ratio()
                score += ratio * 3  # description weighted more

            if target.category_hint and expense["category"] == target.category_hint.lower():
                score += 1.0

            if target.amount_brl_hint:
                try:
                    from app.llm import brl_to_cents
                    target_cents = brl_to_cents(target.amount_brl_hint)
                    if expense["amount_cents"] == target_cents:
                        score += 2.0
                    elif abs(expense["amount_cents"] - target_cents) <= target_cents * 0.05:
                        score += 1.0
                except Exception:
                    pass

            if target.date_hint:
                today = date.today()
                if target.date_hint == "today":
                    target_date = today.isoformat()
                elif target.date_hint == "yesterday":
                    target_date = (today - timedelta(days=1)).isoformat()
                elif target.date_hint == "last":
                    target_date = None  # just boost recent
                else:
                    target_date = target.date_hint
                if target_date and expense["expense_date"] == target_date:
                    score += 1.5

            if score > 0:
                scored.append((score, expense))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]

        if len(scored) >= 2:
            second_score = scored[1][0]
            # Ambiguous if second candidate is within 15% of best
            if second_score > 0 and (best_score - second_score) / best_score < 0.15:
                return None  # ambiguous

        if best_score < 0.3:
            return None

        return best

    def delete_expense(self, user_id: int, expense_id: int) -> dict:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, description, category, amount_cents, expense_date FROM expenses WHERE id = ? AND user_id = ?",
                (expense_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError(f"Gasto {expense_id} não encontrado para este usuário.")
            connection.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id))
            connection.commit()
        return dict(row)

    def update_expense(
        self,
        user_id: int,
        expense_id: int,
        *,
        amount_cents: int | None = None,
        category: str | None = None,
        description: str | None = None,
        expense_date: str | None = None,
    ) -> dict:
        with get_connection() as connection:
            old = connection.execute(
                "SELECT id, description, category, amount_cents, expense_date FROM expenses WHERE id = ? AND user_id = ?",
                (expense_id, user_id),
            ).fetchone()
            if not old:
                raise ValueError(f"Gasto {expense_id} não encontrado para este usuário.")

            new_amount = amount_cents if amount_cents is not None else old["amount_cents"]
            new_category = category if category is not None else old["category"]
            new_description = description if description is not None else old["description"]
            new_date = expense_date if expense_date is not None else old["expense_date"]

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

    def get_label(self, user_id: int, key: str) -> str:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT label FROM user_categories WHERE user_id = ? AND key = ?",
                (user_id, key),
            ).fetchone()
        return row["label"] if row else ""
