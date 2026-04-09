from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import require_login
from app.import_sessions import ImportSessionService
from app.import_parsers import ImportParseError, detect_duplicates, parse_csv, parse_pdf
from app.parsing import ParsedExpense
from app.services import CategoryService, ExpenseService, cents_to_brl, parse_money_to_cents

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(prefix="/import")
logger = logging.getLogger(__name__)
import_session_service = ImportSessionService()
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def import_page(request: Request, user: dict = Depends(require_login)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "import.html",
        {"request": request, "user": user, "error": None},
    )


@router.post("/upload", response_model=None)
async def import_upload(
    request: Request,
    user: dict = Depends(require_login),
    file: UploadFile = File(...),
    reference_month: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    user_id = user["user_id"]

    ref_month: date | None = None
    if reference_month:
        try:
            ref_month = date.fromisoformat(reference_month + "-01")
        except ValueError:
            return templates.TemplateResponse(
                request,
                "import.html",
                {"request": request, "user": user, "error": "Mês de referência inválido. Use o formato AAAA-MM."},
                status_code=422,
            )

    try:
        file_bytes = await file.read()
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise ImportParseError("Arquivo muito grande. O limite atual é de 5 MB.")
        filename = file.filename or "arquivo"
        content_type = file.content_type or ""

        if filename.lower().endswith(".csv") or content_type.startswith("text"):
            expenses = parse_csv(file_bytes, reference_month=ref_month)
        elif filename.lower().endswith(".pdf") or content_type == "application/pdf":
            expenses = parse_pdf(file_bytes, reference_month=ref_month)
        else:
            try:
                expenses = parse_csv(file_bytes, reference_month=ref_month)
            except ImportParseError:
                expenses = parse_pdf(file_bytes, reference_month=ref_month)

        # Optional: LLM batch categorization for items left as "outros"
        if settings.llm_enabled:
            try:
                from app.llm import OpenAIInterpreter
                others = [(i, e.description) for i, e in enumerate(expenses) if e.category == "outros"]
                if others:
                    suggestions = OpenAIInterpreter().categorize_batch(others, user_id=user_id)
                    expenses = [
                        ParsedExpense(
                            description=e.description,
                            category=suggestions.get(i, e.category),
                            amount_cents=e.amount_cents,
                            expense_date=e.expense_date,
                            source_type=e.source_type,
                        )
                        for i, e in enumerate(expenses)
                    ]
            except Exception as exc:
                logger.warning("Falha na categorizacao em lote via LLM durante importacao: %s", exc)

        duplicates = detect_duplicates(user_id, expenses)
        session_id = import_session_service.save(user_id, expenses, duplicates, filename)

    except ImportParseError as exc:
        logger.info("Falha de parse na importacao para user_id=%s: %s", user_id, exc)
        return templates.TemplateResponse(
            request,
            "import.html",
            {"request": request, "user": user, "error": str(exc)},
            status_code=422,
        )

    return RedirectResponse(f"/import/preview?session={session_id}", status_code=303)


@router.get("/preview", response_class=HTMLResponse, response_model=None)
async def import_preview(
    request: Request,
    session: str,
    user: dict = Depends(require_login),
) -> HTMLResponse | RedirectResponse:
    data = import_session_service.load(session, user["user_id"])
    if not data:
        return RedirectResponse("/import", status_code=303)

    user_categories = CategoryService().list_for_user(user["user_id"])

    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "request": request,
            "user": user,
            "session_id": session,
            "expenses": data["expenses"],
            "duplicates": data["duplicates"],
            "filename": data.get("filename", "arquivo"),
            "user_categories": user_categories,
            "cents_to_brl": cents_to_brl,
        },
    )


@router.post("/confirm")
async def import_confirm(
    request: Request,
    user: dict = Depends(require_login),
) -> RedirectResponse:
    form = await request.form()
    user_id = user["user_id"]
    session_id = str(form.get("session_id", ""))

    data = _load_session(session_id, user_id)
    if not data:
        return RedirectResponse("/import", status_code=303)

    expenses_data = data["expenses"]
    to_create: list[ParsedExpense] = []

    for i, row in enumerate(expenses_data):
        if not form.get(f"include_{i}"):
            continue

        description = str(form.get(f"description_{i}", row["description"])).strip()[:120]
        category = str(form.get(f"category_{i}", row["category"])).strip().lower()
        date_str = str(form.get(f"expense_date_{i}", row["expense_date"])).strip()
        amount_brl = str(form.get(f"amount_brl_{i}", "")).strip()

        try:
            expense_date = date.fromisoformat(date_str)
        except ValueError:
            expense_date = date.today()

        if amount_brl:
            try:
                amount_cents = parse_money_to_cents(amount_brl)
            except ValueError:
                amount_cents = row["amount_cents"]
        else:
            amount_cents = row["amount_cents"]

        if amount_cents <= 0:
            continue

        to_create.append(ParsedExpense(
            description=description,
            category=category,
            amount_cents=amount_cents,
            expense_date=expense_date,
            source_type="import",
        ))

    if to_create:
        ExpenseService().create_expenses(to_create, user_id=user_id)

    import_session_service.delete(session_id, user_id)
    request.session["import_success"] = f"{len(to_create)} gasto(s) importado(s) com sucesso."
    return RedirectResponse("/dashboard", status_code=303)
