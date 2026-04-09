from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path

from app import db
from app.auth import create_user
from app.overview import OverviewService
from app.parsing import ParsedBudget, ParsedExpense
from app.services import BudgetService, ExpenseService


class OverviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / f".tmpdb_{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        db.DATA_DIR = self.temp_dir
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.ensure_database(force=True)
        self.user_id = create_user(f"user_{uuid.uuid4().hex[:8]}", "senha123")
        self.expense_service = ExpenseService()
        self.budget_service = BudgetService()
        self.overview_service = OverviewService()

    def tearDown(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_monthly_overview_adds_budget_alerts(self) -> None:
        today = date.today()
        self.expense_service.create_expense(
            ParsedExpense(
                description="mercado",
                category="alimentacao",
                amount_cents=5000,
                expense_date=today,
            ),
            user_id=self.user_id,
        )
        self.budget_service.upsert_budget(
            ParsedBudget(category="alimentacao", amount_cents=6000),
            user_id=self.user_id,
        )

        overview = self.overview_service.get_monthly_overview(self.user_id, today.year, today.month)

        self.assertEqual(overview["budget_alerts"][0]["level"], "budget_warning")
        self.assertIn("Alimentação", overview["budget_alerts"][0]["message"])
        self.assertTrue(overview["insights"])

    def test_weekly_overview_compares_with_previous_week(self) -> None:
        today = date.today()
        self.expense_service.create_expense(
            ParsedExpense(
                description="uber",
                category="transporte",
                amount_cents=4000,
                expense_date=today,
            ),
            user_id=self.user_id,
        )
        self.expense_service.create_expense(
            ParsedExpense(
                description="uber semana passada",
                category="transporte",
                amount_cents=1000,
                expense_date=today - timedelta(days=7),
            ),
            user_id=self.user_id,
        )

        overview = self.overview_service.get_weekly_overview(self.user_id, anchor_date=today)

        self.assertEqual(overview["total_cents"], 4000)
        self.assertEqual(overview["top_category"]["category"], "transporte")
        self.assertEqual(overview["comparison"]["direction"], "up")
        self.assertIn("semana passada", overview["comparison"]["text"])


if __name__ == "__main__":
    unittest.main()
