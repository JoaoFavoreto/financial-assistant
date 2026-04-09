from __future__ import annotations

import json
import uuid

from app.db import get_connection
from app.parsing import ParsedExpense


class ImportSessionService:
    def save(self, user_id: int, expenses: list[ParsedExpense], duplicates: list[bool], filename: str) -> str:
        session_id = str(uuid.uuid4())
        payload = json.dumps(
            {
                "expenses": [
                    {
                        "description": e.description,
                        "category": e.category,
                        "amount_cents": e.amount_cents,
                        "expense_date": e.expense_date.isoformat(),
                        "source_type": e.source_type,
                    }
                    for e in expenses
                ],
                "duplicates": duplicates,
                "filename": filename,
            }
        )
        with get_connection() as conn:
            conn.execute("DELETE FROM import_sessions WHERE created_at < datetime('now', '-2 hours')")
            conn.execute(
                "INSERT INTO import_sessions (id, user_id, payload) VALUES (?, ?, ?)",
                (session_id, user_id, payload),
            )
            conn.commit()
        return session_id

    def load(self, session_id: str, user_id: int) -> dict | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT payload FROM import_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["payload"])

    def delete(self, session_id: str, user_id: int) -> None:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM import_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
            conn.commit()
