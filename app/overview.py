from __future__ import annotations

from datetime import date, timedelta

from app.services import BudgetService, ExpenseService, cents_to_brl, format_category


_ALERT_PRIORITY = {
    "over_budget": 3,
    "budget_reached": 2,
    "budget_warning": 1,
}


class OverviewService:
    def __init__(self) -> None:
        self.expense_service = ExpenseService()
        self.budget_service = BudgetService()

    def get_monthly_overview(self, user_id: int, year: int, month: int) -> dict:
        summary = self.expense_service.get_monthly_summary(year, month, user_id=user_id)
        raw_expenses = self.expense_service.list_expenses_for_month(year, month, user_id=user_id)
        prev_year, prev_month = _previous_month(year, month)
        previous = self.expense_service.get_monthly_summary(prev_year, prev_month, user_id=user_id)

        summary["top_category"] = summary["categories"][0] if summary["categories"] else None
        summary["source_totals"] = _source_totals(raw_expenses)
        summary["source_counts"] = _source_counts(raw_expenses)
        summary["comparison"] = _build_comparison(
            summary["total_cents"],
            previous["total_cents"],
            reference_label="mês passado",
        )
        summary["budget_alerts"] = self._build_budget_alerts(summary["budgets"])
        summary["insights"] = self._build_insights(summary, period_phrase="neste mês")
        return summary

    def get_weekly_overview(self, user_id: int, anchor_date: date | None = None) -> dict:
        anchor = anchor_date or date.today()
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
        previous_start = start - timedelta(days=7)
        previous_end = start

        current_expenses = self.expense_service.search_expenses(
            user_id,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        previous_expenses = self.expense_service.search_expenses(
            user_id,
            start_date=previous_start.isoformat(),
            end_date=previous_end.isoformat(),
        )
        summary = self._summarize_expenses(current_expenses, user_id=user_id)
        previous_summary = self._summarize_expenses(previous_expenses, user_id=user_id)
        month_summary = self.get_monthly_overview(user_id, anchor.year, anchor.month)

        week_categories = {item["category"] for item in summary["categories"]}
        summary["period_start"] = start.isoformat()
        summary["period_end"] = (end - timedelta(days=1)).isoformat()
        summary["top_category"] = summary["categories"][0] if summary["categories"] else None
        summary["comparison"] = _build_comparison(
            summary["total_cents"],
            previous_summary["total_cents"],
            reference_label="semana passada",
        )
        summary["budget_alerts"] = self._build_budget_alerts(
            month_summary["budgets"],
            categories=week_categories,
        )
        summary["insights"] = self._build_insights(summary, period_phrase="nesta semana")
        return summary

    def _build_budget_alerts(self, budget_rows: list[dict], categories: set[str] | None = None) -> list[dict]:
        allowed_categories = categories or None
        alerts = []
        for item in budget_rows:
            if allowed_categories is not None and item["category"] not in allowed_categories:
                continue

            planned = item["planned_cents"]
            actual = item["actual_cents"]
            if planned <= 0 or actual <= 0:
                continue

            ratio = actual / planned
            if actual > planned:
                level = "over_budget"
                message = (
                    f"{item['category_label']} já passou do orçamento do mês em "
                    f"{cents_to_brl(actual - planned)}."
                )
            elif actual >= planned:
                level = "budget_reached"
                message = f"{item['category_label']} atingiu 100% do orçamento do mês."
            elif ratio >= 0.8:
                level = "budget_warning"
                message = f"{item['category_label']} já consumiu {int(round(ratio * 100))}% do orçamento do mês."
            else:
                continue

            alert = dict(item)
            alert["level"] = level
            alert["ratio"] = ratio
            alert["ratio_percent"] = int(round(ratio * 100))
            alert["message"] = message
            alerts.append(alert)

        alerts.sort(key=lambda item: (_ALERT_PRIORITY[item["level"]], item["ratio"]), reverse=True)
        return alerts

    def _build_insights(self, summary: dict, *, period_phrase: str) -> list[str]:
        insights: list[str] = []
        comparison = summary.get("comparison")
        if comparison and comparison.get("text"):
            insights.append(comparison["text"])

        budget_alerts = summary.get("budget_alerts", [])
        if budget_alerts and len(insights) < 2:
            insights.append(budget_alerts[0]["message"])

        recurring_total = summary.get("source_totals", {}).get("recurring", 0)
        recurring_count = summary.get("source_counts", {}).get("recurring", 0)
        if recurring_total and recurring_count and len(insights) < 2:
            label = "gasto recorrente" if recurring_count == 1 else "gastos recorrentes"
            insights.append(
                f"Seus {recurring_count} {label} já somaram {cents_to_brl(recurring_total)} {period_phrase}."
            )

        top_category = summary.get("top_category")
        if top_category and len(insights) < 2:
            insights.append(
                f"{top_category['category_label']} lidera seus gastos {period_phrase}, com {top_category['amount_brl']}."
            )

        return insights[:2]

    def _summarize_expenses(self, expenses: list[dict], *, user_id: int) -> dict:
        total_cents = sum(expense["amount_cents"] for expense in expenses)
        categories: dict[str, int] = {}
        source_totals = _source_totals(expenses)
        source_counts = _source_counts(expenses)
        for expense in expenses:
            categories[expense["category"]] = categories.get(expense["category"], 0) + expense["amount_cents"]

        ordered_categories = [
            {
                "category": category,
                "category_label": format_category(category, user_id),
                "amount_cents": amount_cents,
                "amount_brl": cents_to_brl(amount_cents),
            }
            for category, amount_cents in sorted(categories.items(), key=lambda item: item[1], reverse=True)
        ]

        recent_expenses = []
        for expense in expenses[:10]:
            item = dict(expense)
            item["category_label"] = format_category(expense["category"], user_id)
            recent_expenses.append(item)

        return {
            "total_cents": total_cents,
            "total_brl": cents_to_brl(total_cents),
            "expense_count": len(expenses),
            "categories": ordered_categories,
            "recent_expenses": recent_expenses,
            "source_totals": source_totals,
            "source_counts": source_counts,
        }


def _build_comparison(current_total: int, previous_total: int, *, reference_label: str) -> dict:
    delta = current_total - previous_total

    if current_total == 0 and previous_total == 0:
        text = "Sem movimentação relevante por enquanto."
        direction = "steady"
        delta_percent = 0
    elif previous_total == 0:
        text = f"{cents_to_brl(current_total)} a mais em relação a {reference_label}."
        direction = "up"
        delta_percent = None
    elif delta == 0:
        text = f"Mesmo total em relação a {reference_label}."
        direction = "steady"
        delta_percent = 0
    else:
        delta_percent = int(round(abs(delta) / previous_total * 100))
        if delta > 0:
            text = f"{cents_to_brl(delta)} a mais em relação a {reference_label} ({delta_percent}% acima)."
            direction = "up"
        else:
            text = f"{cents_to_brl(abs(delta))} a menos em relação a {reference_label} ({delta_percent}% abaixo)."
            direction = "down"

    return {
        "reference_label": reference_label,
        "direction": direction,
        "delta_cents": delta,
        "delta_brl": cents_to_brl(abs(delta)),
        "delta_percent": delta_percent,
        "text": text,
    }


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _source_totals(expenses: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for expense in expenses:
        source_type = expense.get("source_type") or "text"
        totals[source_type] = totals.get(source_type, 0) + expense["amount_cents"]
    return totals


def _source_counts(expenses: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for expense in expenses:
        source_type = expense.get("source_type") or "text"
        counts[source_type] = counts.get(source_type, 0) + 1
    return counts
