from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.db import DATA_DIR, get_connection


class TokenLimitError(Exception):
    pass


_MODEL_COST_PER_1K = {
    "gpt-4.1-mini": 0.0006,
    "gpt-4.1": 0.01,
}


class TokenUsageService:
    def _ensure_settings_row(self) -> None:
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM llm_settings").fetchone()[0]
            if row == 0:
                conn.execute(
                    """
                    INSERT INTO llm_settings (
                        id, monthly_token_limit, rate_window_minutes, rate_token_limit, enabled
                    ) VALUES (1, ?, ?, ?, 1)
                    """,
                    (
                        settings.economia_monthly_token_limit,
                        settings.economia_rate_window_minutes,
                        settings.economia_rate_token_limit,
                    ),
                )
                conn.commit()

    def get_settings(self) -> dict:
        self._ensure_settings_row()
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, monthly_token_limit, rate_window_minutes, rate_token_limit, enabled
                FROM llm_settings
                WHERE id = 1
                """
            ).fetchone()
        return dict(row)

    def update_settings(
        self,
        *,
        monthly_token_limit: int,
        rate_window_minutes: int,
        rate_token_limit: int,
        enabled: bool,
    ) -> None:
        self._ensure_settings_row()
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE llm_settings
                SET monthly_token_limit = ?, rate_window_minutes = ?, rate_token_limit = ?, enabled = ?
                WHERE id = 1
                """,
                (monthly_token_limit, rate_window_minutes, rate_token_limit, 1 if enabled else 0),
            )
            conn.commit()

    def _sum_tokens_since(self, user_id: int | None, since_iso: str) -> int:
        with get_connection() as conn:
            if user_id is None:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(total_tokens), 0)
                    FROM llm_usage_logs
                    WHERE datetime(created_at) >= datetime(?) AND status = 'allowed'
                    """,
                    (since_iso,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(total_tokens), 0)
                    FROM llm_usage_logs
                    WHERE user_id = ? AND datetime(created_at) >= datetime(?) AND status = 'allowed'
                    """,
                    (user_id, since_iso),
                ).fetchone()
        return int(row[0] or 0)

    def check_limits(self, user_id: int | None, requested_tokens: int = 0) -> None:
        policy = self.get_settings()
        if not policy["enabled"]:
            return

        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        used_month = self._sum_tokens_since(user_id, month_start)
        if used_month + requested_tokens > int(policy["monthly_token_limit"]):
            raise TokenLimitError("Limite mensal de tokens atingido.")

        window_start = (now - timedelta(minutes=int(policy["rate_window_minutes"]))).isoformat()
        used_window = self._sum_tokens_since(user_id, window_start)
        if used_window + requested_tokens > int(policy["rate_token_limit"]):
            raise TokenLimitError("Limite de taxa de tokens atingido. Tente novamente em instantes.")

    def _estimate_cost_brl(self, model: str, total_tokens: int) -> int:
        usd_per_1k = _MODEL_COST_PER_1K.get(model, _MODEL_COST_PER_1K["gpt-4.1-mini"])
        usd = (total_tokens / 1000.0) * usd_per_1k
        brl = usd * 5.0
        return int(round(brl * 100))

    def log_usage(
        self,
        *,
        user_id: int | None,
        route: str,
        task_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        estimated_cost_cents = self._estimate_cost_brl(model, total_tokens)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO llm_usage_logs (
                    user_id, route, task_type, model, prompt_tokens, completion_tokens,
                    total_tokens, estimated_cost_cents, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    route,
                    task_type,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_cents,
                    status,
                    error_message,
                ),
            )
            conn.commit()

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "user_id": user_id,
            "route": route,
            "task_type": task_type,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_cents": estimated_cost_cents,
            "status": status,
            "error_message": error_message,
        }
        with (Path(DATA_DIR) / "token_usage.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def get_recent_logs(self, limit: int = 100) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, route, task_type, model, prompt_tokens, completion_tokens,
                       total_tokens, estimated_cost_cents, status, error_message, created_at
                FROM llm_usage_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_month_summary(self) -> dict:
        start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(estimated_cost_cents), 0) AS total_cost_cents,
                       COUNT(*) AS total_calls
                FROM llm_usage_logs
                WHERE datetime(created_at) >= datetime(?)
                """,
                (start,),
            ).fetchone()
        return dict(row)
