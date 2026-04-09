from __future__ import annotations

from app.parsing import ParsedBudget, ParsedExpense
from app.services import cents_to_brl, format_category, pluralize


PERSONA_NAME = "EconomIA"
PERSONA_TAGLINE = "sua parceira leve para organizar a vida financeira"


def reply_empty() -> str:
    return (
        f"Oi, eu sou a {PERSONA_NAME} — {PERSONA_TAGLINE}.\n\n"
        "Pode me mandar um gasto do jeito que você falaria no WhatsApp e eu organizo para você.\n\n"
        "Exemplos rápidos:\n"
        "- gastei 42,50 no mercado hoje\n"
        "- paguei 18 no Uber ontem\n"
        "- quanto gastei esse mês?\n"
        "- como foi minha semana?\n"
        "- me mostra meus últimos gastos\n\n"
        "A ideia aqui é simples: menos planilha, menos atrito e mais clareza sobre para onde o dinheiro está indo."
    )


def reply_expenses_created(expenses: list[ParsedExpense], budget_alerts: list[dict] | None = None) -> str:
    total_cents = sum(e.amount_cents for e in expenses)
    count = len(expenses)

    if count == 1:
        expense = expenses[0]
        text = (
            f"Boa! Já deixei anotado {cents_to_brl(expense.amount_cents)} em {format_category(expense.category)} "
            f"({expense.description})."
        )
    else:
        lines = "\n".join(
            f"- {cents_to_brl(e.amount_cents)} em {format_category(e.category)} ({e.description})"
            for e in expenses
        )
        text = (
            f"Boa! Registrei {count} {pluralize(count, 'gasto', 'gastos')} de uma vez, somando {cents_to_brl(total_cents)}.\n\n"
            f"{lines}"
        )

    alert_block = _format_budget_alerts(budget_alerts)
    if alert_block:
        text = f"{text}\n\nOlho rápido no orçamento:\n{alert_block}"

    return (
        f"{text}\n\n"
        "Se quiser, agora eu posso te mostrar o impacto disso no mês, na semana ou separar tudo por categoria."
    )


def reply_budget_set(budget: ParsedBudget) -> str:
    return (
        f"Perfeito, combinamos assim: {format_category(budget.category)} com teto de {cents_to_brl(budget.amount_cents)}.\n\n"
        "Quando você quiser, eu comparo esse valor com o que já foi gasto e te mostro se está tudo no ritmo certo."
    )


def reply_budgets_set(budgets: list[ParsedBudget]) -> str:
    lines = "\n".join(
        f"- {format_category(b.category)}: {cents_to_brl(b.amount_cents)}" for b in budgets
    )
    return (
        f"Perfeito! Configurei {len(budgets)} orçamentos de uma vez:\n\n{lines}\n\n"
        "Quando quiser, comparo com seus gastos atuais para mostrar categorias em atenção."
    )


def reply_monthly_summary(summary: dict) -> str:
    expense_count = summary["expense_count"]
    if not expense_count:
        return (
            "Seu mês ainda está limpinho por aqui.\n\n"
            "Quando quiser começar, é só me mandar um gasto e eu organizo o resto."
        )

    top_category = summary.get("top_category") or summary["categories"][0]
    pieces = [
        (
            f"Até agora, você registrou {expense_count} {pluralize(expense_count, 'gasto', 'gastos')} neste mês, "
            f"somando {summary['total_brl']}."
        ),
        (
            f"No momento, a categoria que mais pesou foi {top_category['category_label']}, "
            f"com {top_category['amount_brl']}."
        ),
    ]

    notes = _format_note_block(summary)
    if notes:
        pieces.append(notes)

    pieces.append("Se quiser, eu também posso abrir isso por categoria ou te mostrar como a semana está andando.")
    return "\n\n".join(pieces)


def reply_weekly_summary(summary: dict) -> str:
    expense_count = summary["expense_count"]
    if not expense_count:
        return (
            "Sua semana está bem tranquila por enquanto.\n\n"
            "Se entrar algum gasto, me manda por aqui que eu atualizo tudo para você."
        )

    top_category = summary.get("top_category")
    pieces = [
        (
            f"Nesta semana, você registrou {expense_count} {pluralize(expense_count, 'gasto', 'gastos')} "
            f"e somou {summary['total_brl']}."
        )
    ]

    if top_category:
        pieces.append(
            f"A categoria que mais pesou até aqui foi {top_category['category_label']}, com {top_category['amount_brl']}."
        )

    comparison = summary.get("comparison")
    if comparison and comparison.get("text"):
        pieces.append(comparison["text"])

    notes = _format_note_block(summary)
    if notes:
        pieces.append(notes)

    pieces.append("Se quiser, eu também posso te mostrar os lançamentos desta semana ou comparar isso com o mês inteiro.")
    return "\n\n".join(pieces)


def reply_category_summary(summary: dict) -> str:
    if not summary["categories"]:
        return "Ainda não encontrei gastos neste mês para montar seu resumo por categoria."
    lines = "\n".join(
        f"- {item['category_label']}: {item['amount_brl']}" for item in summary["categories"]
    )
    return (
        "Aqui está o mapa das suas categorias neste mês:\n\n"
        f"{lines}\n\n"
        "Se quiser, no próximo passo eu também posso te mostrar o total do mês ou comparar com seus orçamentos."
    )


def reply_expense_deleted(expense: dict) -> str:
    return (
        f"Feito! Removi o gasto de {cents_to_brl(expense['amount_cents'])} "
        f"em {format_category(expense['category'])} ({expense['description']}) "
        f"do dia {expense['expense_date']}."
    )


def reply_expense_updated(old: dict, new: dict) -> str:
    return (
        f"Atualizado! O gasto '{old['description']}' era {cents_to_brl(old['amount_cents'])} "
        f"e agora é {cents_to_brl(new['amount_cents'])} "
        f"em {format_category(new['category'])} ({new['description']})."
    )


def reply_expense_not_found(hint: str) -> str:
    return (
        f"Não encontrei nenhum gasto correspondente a '{hint}' nos últimos 90 dias. "
        "Tente ser mais específico com o valor ou a descrição."
    )


def reply_ambiguous_match(candidates: list[dict]) -> str:
    lines = "\n".join(
        f"- {cents_to_brl(c['amount_cents'])} em {format_category(c['category'])} "
        f"({c['description']}) — {c['expense_date']}"
        for c in candidates[:5]
    )
    return (
        "Encontrei mais de um gasto parecido. Qual você quer remover?\n\n"
        f"{lines}\n\n"
        "Tente ser mais específico com valor, data ou descrição."
    )


def reply_confirm_delete(expense: dict) -> str:
    return (
        f"Você quer apagar o gasto de {cents_to_brl(expense['amount_cents'])} "
        f"em {format_category(expense['category'])} ({expense['description']}) "
        f"do dia {expense['expense_date']}?\n\n"
        "Responda 'sim' para confirmar ou qualquer outra coisa para cancelar."
    )


def reply_filtered_search(expenses: list[dict], total_cents: int, filter_desc: str) -> str:
    if not expenses:
        return f"Não encontrei nenhum gasto para {filter_desc}."

    count = len(expenses)
    lines = "\n".join(
        f"- {cents_to_brl(e['amount_cents'])} em {format_category(e['category'])} "
        f"({e['description']}) — {e['expense_date']}"
        for e in expenses[:20]
    )
    suffix = f"\n\n_(mostrando {min(count, 20)} de {count})_" if count > 20 else ""
    return (
        f"Encontrei {count} {pluralize(count, 'gasto', 'gastos')} para {filter_desc}, "
        f"totalizando {cents_to_brl(total_cents)}.\n\n"
        f"{lines}{suffix}\n\n"
        "Se quiser, eu também posso transformar isso em um resumo por categoria."
    )


def reply_manage_categories() -> str:
    return (
        "Para gerenciar suas categorias — adicionar, renomear ou excluir — "
        "acesse a página de configurações:\n\n"
        "→ /settings/categories\n\n"
        "Lá você pode personalizar as categorias do jeito que preferir."
    )


_WEEKDAY_NAMES = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _format_recurring(rec: dict) -> str:
    amount = cents_to_brl(rec["amount_cents"])
    if rec["frequency"] == "monthly":
        return f"{rec['description']} — {amount}/mês (dia {rec['day_of_month']})"
    day_name = _WEEKDAY_NAMES[rec["day_of_week"]] if rec["day_of_week"] is not None else "?"
    return f"{rec['description']} — {amount}/semana ({day_name})"


def reply_recurring_created(rec: dict) -> str:
    return (
        f"Recorrente criado! {_format_recurring(rec)}\n\n"
        "Vou registrar automaticamente esse gasto nos próximos ciclos."
    )


def reply_recurring_created_many(recurrings: list[dict]) -> str:
    lines = "\n".join(f"- {_format_recurring(r)}" for r in recurrings)
    return (
        f"Recorrentes criados! Registrei {len(recurrings)} itens:\n\n{lines}\n\n"
        "Vou lançar automaticamente cada um deles nos próximos ciclos."
    )


def reply_recurring_list(recurrings: list[dict]) -> str:
    if not recurrings:
        return (
            "Você ainda não tem gastos recorrentes configurados.\n\n"
            "Para adicionar, diga algo como: \"pago aluguel 1500 todo dia 5\" ou \"academia 120 toda semana\"."
        )
    lines = "\n".join(f"- {_format_recurring(r)}" for r in recurrings)
    count = len(recurrings)
    return (
        f"Seus {count} {pluralize(count, 'gasto recorrente', 'gastos recorrentes')} ativos:\n\n"
        f"{lines}"
    )


def reply_recurring_deleted(rec: dict) -> str:
    return f"Removido! O recorrente \"{rec['description']}\" não será mais gerado automaticamente."


def reply_help(hint: str | None = None) -> str:
    base = hint or "Hmmm, essa eu não consegui pegar direito. Mas posso te ajudar com gastos, categorias, orçamento e resumo do mês."
    return (
        f"{base}\n\n"
        "Tenta algo como:\n"
        "- gastei 35 no almoço hoje\n"
        "- como foi minha semana?\n"
        "- me mostra meus últimos gastos\n"
        "- orçamento transporte 400\n"
        "- categorias\n"
        "- apaga o uber de ontem"
    )


def _format_budget_alerts(budget_alerts: list[dict] | None) -> str:
    if not budget_alerts:
        return ""
    return "\n".join(f"- {item['message']}" for item in budget_alerts[:2])


def _format_note_block(summary: dict) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for item in summary.get("insights", []):
        if item and item not in seen:
            lines.append(item)
            seen.add(item)
    for alert in summary.get("budget_alerts", [])[:2]:
        message = alert["message"]
        if message not in seen:
            lines.append(message)
            seen.add(message)
    if not lines:
        return ""
    return "Leitura rápida:\n" + "\n".join(f"- {line}" for line in lines[:2])
