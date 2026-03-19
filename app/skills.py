from __future__ import annotations

import json
from datetime import date
from textwrap import dedent


CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("alimentacao", "Alimentação", ["mercado", "supermercado", "almoco", "lanche", "padaria", "ifood", "restaurante", "comida", "cantina"]),
    ("transporte", "Transporte", ["uber", "99", "taxi", "gasolina", "onibus", "metro", "transporte", "estacionamento"]),
    ("moradia", "Moradia", ["aluguel", "condominio", "energia", "agua", "internet", "luz"]),
    ("saude", "Saúde", ["farmacia", "medico", "consulta", "academia", "remedio"]),
    ("lazer", "Lazer", ["cinema", "bar", "show", "netflix", "spotify"]),
    ("compras", "Compras", ["roupa", "amazon", "shopping", "loja"]),
    ("educacao", "Educação", ["curso", "livro", "faculdade"]),
    ("outros", "Outros", []),
]


def _categories_block(cats: list[tuple[str, str, list[str]]] | None = None) -> str:
    source = cats if cats is not None else CATEGORIES
    lines = []
    for key, _label, examples in source:
        if examples:
            lines.append(f"- {key}: ex. {', '.join(examples[:5])}")
        else:
            lines.append(f"- {key}: use quando nada fizer sentido nas categorias anteriores")
    return "\n".join(lines)


def _load_user_categories(user_id: int) -> list[tuple[str, str, list[str]]] | None:
    try:
        from app.services import CategoryService

        rows = CategoryService().list_for_user(user_id)
        if not rows:
            return None
        result = []
        for row in rows:
            try:
                examples = json.loads(row["examples"]) if row["examples"] else []
            except (json.JSONDecodeError, TypeError):
                examples = []
            result.append((row["key"], row["label"], examples))
        return result
    except Exception:
        return None


def finance_extraction_skill(user_id: int | None = None) -> str:
    today = date.today().isoformat()

    user_cats = _load_user_categories(user_id) if user_id is not None else None
    cats_block = _categories_block(user_cats)

    return dedent(
        f"""
        Voce e o skill de extracao estruturada do EconomIA.
        Data de hoje: {today}

        Sua tarefa e converter mensagens curtas em uma intencao financeira estruturada.
        Voce deve priorizar precisao, nao criatividade.

        Categorias permitidas:
        {cats_block}

        Regras obrigatorias:
        - Responda somente com JSON valido compativel com o schema solicitado.
        - Nao invente valores que nao estejam razoavelmente presentes na mensagem.
        - Se a mensagem for sobre resumo mensal geral, sem filtros, use a intencao `monthly_summary`.
        - Se a mensagem for sobre resumo geral da semana atual, sem filtros, use a intencao `weekly_summary`.
        - Se a mensagem for sobre categorias, sem filtros, use a intencao `category_summary`.
        - Se a mensagem pedir gastos filtrados por categoria, descricao, periodo ou lista recente (ex.: "quanto gastei no uber esse mes?", "o que gastei na semana passada?", "me mostra meus ultimos gastos"), use a intencao `filtered_search` e preencha `search_filter` com:
          - `category_hint`: chave da categoria, se mencionada (ex.: "transporte", "alimentacao")
          - `description_hint`: palavra-chave da descricao, se mencionada (ex.: "uber", "mercado")
          - `period`: um dos valores: "today", "yesterday", "this_month", "last_month", "this_week", "last_week", "last_N_days:N" ou "YYYY-MM"
          - `granularity`: "total" (padrao), "by_day" ou "by_category"
          - `limit`: use um numero pequeno, como 5, quando o usuario pedir os ultimos gastos ou lancamentos recentes
        - Se a mensagem for sobre definir orcamento, use a intencao `set_budget`.
        - Se a mensagem registrar um ou mais gastos, use a intencao `create_expenses` com todos os gastos na lista `expenses`.
        - Se a mensagem pedir para apagar, remover ou deletar um gasto, use a intencao `delete_expense` e preencha `expense_target` com as pistas disponiveis (description_hint, category_hint, amount_brl_hint, date_hint).
        - Se a mensagem pedir para editar, corrigir ou alterar um gasto existente, use a intencao `edit_expense` e preencha `expense_edit` com o campo `target` descrevendo o gasto a encontrar e os campos `new_*` com os novos valores.
        - Se a mensagem registrar um gasto recorrente, fixo, mensal ou semanal (ex.: "pago aluguel 1500 todo dia 5", "academia 120 toda semana", "internet 99 mensal dia 10"), use a intencao `create_recurring_expense` e preencha `recurring_expense` com:
          - `description`: descricao curta em portugues
          - `category`: chave da categoria
          - `amount_brl`: valor decimal com ponto
          - `frequency`: "monthly" se for mensal, "weekly" se for semanal
          - `day_of_month`: numero do dia (1-28) se for monthly
          - `day_of_week`: numero do dia da semana (0=seg, 1=ter, 2=qua, 3=qui, 4=sex, 5=sab, 6=dom) se for weekly
        - Se a mensagem perguntar sobre gastos fixos ou recorrentes (ex.: "quais sao meus gastos fixos?", "lista meus recorrentes"), use a intencao `list_recurring_expenses`.
        - Se a mensagem pedir para cancelar, remover ou deletar um gasto recorrente ou fixo (ex.: "cancela o recorrente de academia", "remove o gasto fixo de aluguel"), use a intencao `delete_recurring_expense` e preencha `recurring_target` com a descricao ou hint do recorrente a remover.
        - Se faltar dado essencial, use a intencao `help` e explique no campo `reply_hint`.
        - O campo `expense_date` deve ser uma data ISO (YYYY-MM-DD) calculada a partir da data de hoje informada acima. Use a data de hoje como padrao quando nenhuma data for mencionada.
        - O campo `amount_brl` deve estar no formato decimal com ponto, como `42.50`.
        - O campo `description` deve ser curta, objetiva e em portugues.
        - Para `date_hint` em `expense_target`, use 'today', 'yesterday', ou uma data ISO.
        """
    ).strip()
