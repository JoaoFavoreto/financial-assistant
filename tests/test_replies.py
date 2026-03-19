from __future__ import annotations

import unittest
from datetime import date

from app.parsing import ParsedBudget, ParsedExpense
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
    reply_help,
    reply_monthly_summary,
    reply_weekly_summary,
)


class RepliesTests(unittest.TestCase):
    def test_reply_empty_contains_persona(self) -> None:
        text = reply_empty()
        self.assertIn("EconomIA", text)
        self.assertIn("WhatsApp", text)
        self.assertIn("você", text)

    def test_reply_expenses_created_single(self) -> None:
        expenses = [
            ParsedExpense(
                description="mercado",
                category="alimentacao",
                amount_cents=4250,
                expense_date=date.today(),
            )
        ]
        text = reply_expenses_created(expenses)
        self.assertIn("42,50", text)
        self.assertIn("mercado", text)
        self.assertIn("semana", text.lower())

    def test_reply_expenses_created_multiple_with_alert(self) -> None:
        expenses = [
            ParsedExpense(description="uber", category="transporte", amount_cents=1800, expense_date=date.today()),
            ParsedExpense(description="padaria", category="alimentacao", amount_cents=900, expense_date=date.today()),
        ]
        text = reply_expenses_created(
            expenses,
            budget_alerts=[{"message": "Alimentação já consumiu 80% do orçamento do mês."}],
        )
        self.assertIn("2 gastos", text)
        self.assertIn("27,00", text)
        self.assertIn("80%", text)

    def test_reply_budget_set(self) -> None:
        budget = ParsedBudget(category="alimentacao", amount_cents=120000)
        text = reply_budget_set(budget)
        self.assertIn("1.200,00", text)
        self.assertIn("Alimentação", text)

    def test_reply_monthly_summary_empty(self) -> None:
        summary = {"expense_count": 0, "total_brl": "R$ 0,00", "categories": [], "budgets": []}
        text = reply_monthly_summary(summary)
        self.assertIn("limpinho", text)

    def test_reply_monthly_summary_with_data(self) -> None:
        summary = {
            "expense_count": 3,
            "total_brl": "R$ 150,00",
            "categories": [{"category_label": "Alimentação", "amount_brl": "R$ 100,00"}],
            "top_category": {"category_label": "Alimentação", "amount_brl": "R$ 100,00"},
            "budget_alerts": [{"message": "Alimentação já consumiu 83% do orçamento do mês."}],
            "insights": ["R$ 20,00 a mais em relação a mês passado."],
        }
        text = reply_monthly_summary(summary)
        self.assertIn("3 gastos", text)
        self.assertIn("150,00", text)
        self.assertIn("Alimentação", text)
        self.assertIn("Leitura rápida", text)

    def test_reply_weekly_summary_with_data(self) -> None:
        summary = {
            "expense_count": 2,
            "total_brl": "R$ 70,00",
            "top_category": {"category_label": "Transporte", "amount_brl": "R$ 40,00"},
            "comparison": {"text": "R$ 20,00 a mais em relação a semana passada."},
            "budget_alerts": [],
            "insights": ["Transporte lidera seus gastos nesta semana, com R$ 40,00."],
        }
        text = reply_weekly_summary(summary)
        self.assertIn("Nesta semana", text)
        self.assertIn("70,00", text)
        self.assertIn("semana passada", text)

    def test_reply_category_summary_empty(self) -> None:
        summary = {"categories": []}
        text = reply_category_summary(summary)
        self.assertIn("não encontrei", text.lower())

    def test_reply_category_summary_with_data(self) -> None:
        summary = {
            "categories": [
                {"category_label": "Transporte", "amount_brl": "R$ 80,00"},
                {"category_label": "Lazer", "amount_brl": "R$ 50,00"},
            ]
        }
        text = reply_category_summary(summary)
        self.assertIn("Transporte", text)
        self.assertIn("80,00", text)

    def test_reply_expense_deleted(self) -> None:
        expense = {
            "description": "uber para o trabalho",
            "category": "transporte",
            "amount_cents": 2300,
            "expense_date": "2026-03-18",
        }
        text = reply_expense_deleted(expense)
        self.assertIn("23,00", text)
        self.assertIn("Transporte", text)

    def test_reply_expense_not_found(self) -> None:
        text = reply_expense_not_found("uber de ontem")
        self.assertIn("uber de ontem", text)

    def test_reply_ambiguous_match(self) -> None:
        candidates = [
            {"description": "uber 1", "category": "transporte", "amount_cents": 1500, "expense_date": "2026-03-18"},
            {"description": "uber 2", "category": "transporte", "amount_cents": 1800, "expense_date": "2026-03-17"},
        ]
        text = reply_ambiguous_match(candidates)
        self.assertIn("mais de um", text.lower())
        self.assertIn("você", text.lower())

    def test_reply_confirm_delete(self) -> None:
        expense = {
            "description": "ifood",
            "category": "alimentacao",
            "amount_cents": 5500,
            "expense_date": "2026-03-18",
        }
        text = reply_confirm_delete(expense)
        self.assertIn("55,00", text)
        self.assertIn("sim", text.lower())

    def test_reply_help_default(self) -> None:
        text = reply_help()
        self.assertIn("categorias", text.lower())
        self.assertIn("orçamento", text.lower())

    def test_reply_expense_updated(self) -> None:
        old = {"description": "uber", "category": "transporte", "amount_cents": 1500, "expense_date": "2026-03-18"}
        new = {"description": "uber corrigido", "category": "transporte", "amount_cents": 2000, "expense_date": "2026-03-18"}
        text = reply_expense_updated(old, new)
        self.assertIn("20,00", text)
        self.assertIn("15,00", text)


if __name__ == "__main__":
    unittest.main()
