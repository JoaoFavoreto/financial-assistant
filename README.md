# EconomIA MVP

MVP local do EconomIA com:

- chat web local para registrar gastos em portugues
- persistencia em SQLite
- dashboard com resumo mensal, categorias e orcamentos
- servicos deterministicos para calculos financeiros
- integracao opcional com OpenAI via `.env`
- arquitetura de skill prompts para extracao financeira
- testes basicos de parsing e agregacao

## Requisitos

- Python 3.13+

## Como rodar

```bash
python main.py
```

Depois abra [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Configuracao OpenAI

1. Copie `.env.example` para `.env`
2. Preencha `OPENAI_API_KEY`
3. Opcionalmente ajuste `OPENAI_MODEL`

Exemplo:

```env
OPENAI_API_KEY=<SECRET>
OPENAI_MODEL=gpt-4.1-mini
ECONOMIA_USE_LLM=true
ECONOMIA_LLM_TIMEOUT=30
```

Quando a chave estiver configurada, o chat passa a usar OpenAI para interpretar mensagens.
Se a API falhar, o app volta automaticamente para o parser por regras.

## Comandos uteis

Executar testes:

```bash
python -m unittest discover -s tests -v
```

## Fluxos do MVP

- Registrar gasto por texto:
  - `gastei 42,50 no mercado hoje`
  - `paguei 89 em uber ontem`
- Consultar resumo:
  - `quanto gastei esse mes?`
  - `resumo`
- Definir orcamento:
  - `orcamento alimentacao 1200`
  - `definir orcamento transporte 400`
- Consultar categorias:
  - `categorias`

## Estrutura

- `main.py`: ponto de entrada do servidor
- `app/db.py`: conexao e schema SQLite
- `app/parsing.py`: interpretacao de mensagens e fallback local
- `app/llm.py`: integracao com OpenAI e structured outputs
- `app/skills.py`: prompts modulares do agente
- `app/config.py`: configuracao por `.env`
- `app/services.py`: regras de negocio de gastos e orcamentos
- `app/server.py`: servidor HTTP local
- `tests/`: testes unitarios
