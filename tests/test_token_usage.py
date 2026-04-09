from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from app import db
from app.auth import create_user
from app.token_usage import TokenLimitError, TokenUsageService


class TokenUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / f".tmpdb_{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        db.DATA_DIR = self.temp_dir
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.ensure_database(force=True)
        self.user_id = create_user(f"user_{uuid.uuid4().hex[:8]}", "senha123")
        self.svc = TokenUsageService()

    def tearDown(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_logs_usage_and_returns_summary(self) -> None:
        self.svc.log_usage(
            user_id=self.user_id,
            route="/api/chat",
            task_type="parse_message",
            model="gpt-4.1-mini",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            status="allowed",
        )
        summary = self.svc.get_month_summary()
        self.assertEqual(summary["total_tokens"], 150)
        self.assertEqual(summary["total_calls"], 1)

    def test_rate_limit_blocks_when_exceeded(self) -> None:
        self.svc.update_settings(
            monthly_token_limit=1_000_000,
            rate_window_minutes=10,
            rate_token_limit=100,
            enabled=True,
        )
        self.svc.log_usage(
            user_id=self.user_id,
            route="/api/chat",
            task_type="parse_message",
            model="gpt-4.1-mini",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=90,
            status="allowed",
        )
        with self.assertRaises(TokenLimitError):
            self.svc.check_limits(self.user_id, requested_tokens=20)
