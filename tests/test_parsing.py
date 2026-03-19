from datetime import date, timedelta
import unittest

from app.llm import LLMExpense, LLMMessageInterpretation, brl_to_cents, interpretation_to_result
from app.parsing import parse_brl_to_cents


class ParseBrlToCentsTests(unittest.TestCase):
    def test_integer(self) -> None:
        self.assertEqual(parse_brl_to_cents("12"), 1200)

    def test_decimal_comma(self) -> None:
        self.assertEqual(parse_brl_to_cents("12,34"), 1234)

    def test_thousands_separator(self) -> None:
        self.assertEqual(parse_brl_to_cents("1.234,56"), 123456)


class BrlToCentsTests(unittest.TestCase):
    def test_decimal_dot(self) -> None:
        self.assertEqual(brl_to_cents("42.50"), 4250)

    def test_integer_string(self) -> None:
        self.assertEqual(brl_to_cents("100"), 10000)

    def test_small_value(self) -> None:
        self.assertEqual(brl_to_cents("1.50"), 150)


class InterpretationToResultTests(unittest.TestCase):
    def test_single_expense(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(
                intent="create_expenses",
                expenses=[
                    LLMExpense(amount_brl="42.50", category="alimentacao", description="mercado", expense_date="today")
                ],
            )
        )
        self.assertEqual(result["intent"], "create_expenses")
        self.assertEqual(len(result["expenses"]), 1)
        self.assertEqual(result["expenses"][0].amount_cents, 4250)
        self.assertEqual(result["expenses"][0].category, "alimentacao")
        self.assertEqual(result["expenses"][0].expense_date, date.today())

    def test_multiple_expenses(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(
                intent="create_expenses",
                expenses=[
                    LLMExpense(amount_brl="30.00", category="alimentacao", description="cantina", expense_date="today"),
                    LLMExpense(amount_brl="18.00", category="transporte", description="uber", expense_date="today"),
                    LLMExpense(amount_brl="45.00", category="alimentacao", description="mercado", expense_date="today"),
                ],
            )
        )
        self.assertEqual(result["intent"], "create_expenses")
        self.assertEqual(len(result["expenses"]), 3)
        self.assertEqual(result["expenses"][0].amount_cents, 3000)
        self.assertEqual(result["expenses"][1].category, "transporte")
        self.assertEqual(result["expenses"][2].category, "alimentacao")

    def test_expense_yesterday(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(
                intent="create_expenses",
                expenses=[LLMExpense(amount_brl="89.00", category="transporte", description="uber", expense_date="yesterday")],
            )
        )
        self.assertEqual(result["expenses"][0].expense_date, date.today() - timedelta(days=1))

    def test_expense_iso_date(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(
                intent="create_expenses",
                expenses=[LLMExpense(amount_brl="50.00", category="alimentacao", description="almoco", expense_date="2026-03-15")],
            )
        )
        self.assertEqual(result["expenses"][0].expense_date, date(2026, 3, 15))

    def test_expense_invalid_date_falls_back_to_today(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(
                intent="create_expenses",
                expenses=[LLMExpense(amount_brl="10.00", category="outros", description="teste", expense_date="invalido")],
            )
        )
        self.assertEqual(result["expenses"][0].expense_date, date.today())

    def test_empty_expenses_returns_help(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(intent="create_expenses", expenses=[])
        )
        self.assertEqual(result["intent"], "help")

    def test_set_budget(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(
                intent="set_budget",
                budget_category="transporte",
                budget_amount_brl="350.00",
            )
        )
        self.assertEqual(result["intent"], "set_budget")
        self.assertEqual(result["budget"].category, "transporte")
        self.assertEqual(result["budget"].amount_cents, 35000)

    def test_set_budget_missing_fields_returns_help(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(intent="set_budget")
        )
        self.assertEqual(result["intent"], "help")

    def test_monthly_summary(self) -> None:
        result = interpretation_to_result(LLMMessageInterpretation(intent="monthly_summary"))
        self.assertEqual(result["intent"], "monthly_summary")

    def test_category_summary(self) -> None:
        result = interpretation_to_result(LLMMessageInterpretation(intent="category_summary"))
        self.assertEqual(result["intent"], "category_summary")

    def test_reply_hint_propagated(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(intent="help", reply_hint="Nao entendi o valor.")
        )
        self.assertEqual(result["intent"], "help")
        self.assertEqual(result["reply_hint"], "Nao entendi o valor.")


if __name__ == "__main__":
    unittest.main()
