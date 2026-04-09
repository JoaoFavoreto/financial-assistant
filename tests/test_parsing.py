from datetime import date, timedelta
import unittest

from app.llm import LLMExpense, LLMMessageInterpretation, brl_to_cents, interpretation_to_result
from app.parsing import parse_brl_to_cents, parse_message
from app.services import parse_money_to_cents


class ParseBrlToCentsTests(unittest.TestCase):
    def test_integer(self) -> None:
        self.assertEqual(parse_brl_to_cents("12"), 1200)

    def test_decimal_comma(self) -> None:
        self.assertEqual(parse_brl_to_cents("12,34"), 1234)

    def test_thousands_separator(self) -> None:
        self.assertEqual(parse_brl_to_cents("1.234,56"), 123456)


class LocalFallbackParsingTests(unittest.TestCase):
    def test_parse_budget_message(self) -> None:
        parsed = parse_message("orçamento alimentação 1200")
        self.assertEqual(parsed["intent"], "set_budget")
        self.assertEqual(parsed["budget"].category, "alimentacao")
        self.assertEqual(parsed["budget"].amount_cents, 120000)

    def test_parse_multiple_expenses(self) -> None:
        parsed = parse_message("gastei 30 na cantina e 18 no uber hoje")
        self.assertEqual(parsed["intent"], "create_expenses")
        self.assertEqual(len(parsed["expenses"]), 2)
        self.assertEqual(parsed["expenses"][0].category, "alimentacao")
        self.assertEqual(parsed["expenses"][1].category, "transporte")

    def test_parse_monthly_summary(self) -> None:
        parsed = parse_message("quanto gastei esse mês?")
        self.assertEqual(parsed["intent"], "monthly_summary")

    def test_parse_weekly_summary(self) -> None:
        parsed = parse_message("como foi minha semana?")
        self.assertEqual(parsed["intent"], "weekly_summary")

    def test_parse_yesterday_search(self) -> None:
        parsed = parse_message("quanto gastei ontem?")
        self.assertEqual(parsed["intent"], "filtered_search")
        self.assertEqual(parsed["search_filter"].period, "yesterday")

    def test_parse_recent_search(self) -> None:
        parsed = parse_message("me mostra meus últimos gastos")
        self.assertEqual(parsed["intent"], "filtered_search")
        self.assertEqual(parsed["search_filter"].limit, 5)
        self.assertEqual(parsed["search_filter"].period, "last_N_days:30")

    def test_parse_multiple_budgets_same_message(self) -> None:
        parsed = parse_message("orçamento alimentação 1200 e orçamento transporte 500")
        self.assertEqual(parsed["intent"], "set_budget_many")
        self.assertEqual(len(parsed["budgets"]), 2)

    def test_parse_multiple_recurrings_same_message(self) -> None:
        parsed = parse_message("netflix 39,90 mensal e academia 120 todo mês dia 10")
        self.assertEqual(parsed["intent"], "create_recurring_expense_many")
        self.assertEqual(len(parsed["recurring_expenses"]), 2)

    def test_parse_card_installment_purchase(self) -> None:
        parsed = parse_message("gastei 120,00 em 10x no cartão no curso")
        self.assertEqual(parsed["intent"], "create_card_installment_purchase")
        self.assertEqual(parsed["card_purchase"].installment_total, 10)
        self.assertEqual(parsed["card_purchase"].installment_amount_cents, 12000)


class BrlToCentsTests(unittest.TestCase):
    def test_decimal_dot(self) -> None:
        self.assertEqual(brl_to_cents("42.50"), 4250)

    def test_integer_string(self) -> None:
        self.assertEqual(brl_to_cents("100"), 10000)

    def test_small_value(self) -> None:
        self.assertEqual(brl_to_cents("1.50"), 150)

    def test_decimal_comma_supported(self) -> None:
        self.assertEqual(brl_to_cents("1,50"), 150)


class ServicesMoneyParseTests(unittest.TestCase):
    def test_parse_money_to_cents_uses_brazilian_format(self) -> None:
        self.assertEqual(parse_money_to_cents("1.234,56"), 123456)


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
                expenses=[LLMExpense(amount_brl="50.00", category="alimentacao", description="almoço", expense_date="2026-03-15")],
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
        result = interpretation_to_result(LLMMessageInterpretation(intent="create_expenses", expenses=[]))
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
        result = interpretation_to_result(LLMMessageInterpretation(intent="set_budget"))
        self.assertEqual(result["intent"], "help")

    def test_monthly_summary(self) -> None:
        result = interpretation_to_result(LLMMessageInterpretation(intent="monthly_summary"))
        self.assertEqual(result["intent"], "monthly_summary")

    def test_weekly_summary(self) -> None:
        result = interpretation_to_result(LLMMessageInterpretation(intent="weekly_summary"))
        self.assertEqual(result["intent"], "weekly_summary")

    def test_category_summary(self) -> None:
        result = interpretation_to_result(LLMMessageInterpretation(intent="category_summary"))
        self.assertEqual(result["intent"], "category_summary")

    def test_reply_hint_propagated(self) -> None:
        result = interpretation_to_result(
            LLMMessageInterpretation(intent="help", reply_hint="Não entendi o valor.")
        )
        self.assertEqual(result["intent"], "help")
        self.assertEqual(result["reply_hint"], "Não entendi o valor.")


if __name__ == "__main__":
    unittest.main()
