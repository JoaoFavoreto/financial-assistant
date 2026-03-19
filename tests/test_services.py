from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path

from app import db
from app.auth import create_user
from app.parsing import ParsedBudget, ParsedExpense
from app.services import BudgetService, ExpenseService


class ServicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / f".tmpdb_{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        db.DATA_DIR = self.temp_dir
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.ensure_database()
        self.user_id = create_user("testuser", "senha123")
        self.expense_service = ExpenseService()
        self.budget_service = BudgetService()

    def tearDown(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_monthly_summary_aggregates_by_category(self) -> None:
        self.expense_service.create_expense(
            ParsedExpense(
                description="mercado",
                category="alimentacao",
                amount_cents=5000,
                expense_date=date.today(),
            ),
            user_id=self.user_id,
        )
        self.expense_service.create_expense(
            ParsedExpense(
                description="uber",
                category="transporte",
                amount_cents=2300,
                expense_date=date.today(),
            ),
            user_id=self.user_id,
        )
        self.budget_service.upsert_budget(
            ParsedBudget(category="alimentacao", amount_cents=12000),
            user_id=self.user_id,
        )

        summary = self.expense_service.get_monthly_summary(
            date.today().year, date.today().month, user_id=self.user_id
        )
        budget_map = {item["category"]: item for item in summary["budgets"]}

        self.assertEqual(summary["total_cents"], 7300)
        self.assertEqual(summary["expense_count"], 2)
        self.assertEqual(summary["categories"][0]["category"], "alimentacao")
        self.assertEqual(budget_map["alimentacao"]["planned_cents"], 12000)

    def test_create_multiple_expenses_in_one_message(self) -> None:
        expenses = [
            ParsedExpense(
                description="cantina",
                category="alimentacao",
                amount_cents=3000,
                expense_date=date.today(),
            ),
            ParsedExpense(
                description="uber",
                category="transporte",
                amount_cents=1800,
                expense_date=date.today(),
            ),
        ]
        self.expense_service.create_expenses(expenses, user_id=self.user_id)
        summary = self.expense_service.get_monthly_summary(
            date.today().year, date.today().month, user_id=self.user_id
        )

        self.assertEqual(summary["expense_count"], 2)
        self.assertEqual(summary["total_cents"], 4800)

    def test_data_isolation_between_users(self) -> None:
        user2_id = create_user("otheruser", "senha456")
        self.expense_service.create_expense(
            ParsedExpense(
                description="almoço",
                category="alimentacao",
                amount_cents=2500,
                expense_date=date.today(),
            ),
            user_id=self.user_id,
        )
        summary2 = self.expense_service.get_monthly_summary(
            date.today().year, date.today().month, user_id=user2_id
        )
        self.assertEqual(summary2["expense_count"], 0)


if __name__ == "__main__":
    unittest.main()
