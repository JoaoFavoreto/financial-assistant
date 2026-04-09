from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH, override=False)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    openai_model_simple: str = os.getenv("OPENAI_MODEL_SIMPLE", "gpt-4.1-mini")
    openai_model_complex: str = os.getenv("OPENAI_MODEL_COMPLEX", os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    economia_use_llm: bool = _as_bool(os.getenv("ECONOMIA_USE_LLM"), True)
    economia_llm_timeout: float = float(os.getenv("ECONOMIA_LLM_TIMEOUT", "30"))
    economia_monthly_token_limit: int = int(os.getenv("ECONOMIA_MONTHLY_TOKEN_LIMIT", "200000"))
    economia_rate_window_minutes: int = int(os.getenv("ECONOMIA_RATE_WINDOW_MINUTES", "10"))
    economia_rate_token_limit: int = int(os.getenv("ECONOMIA_RATE_TOKEN_LIMIT", "20000"))
    secret_key: str = os.getenv("ECONOMIA_SECRET_KEY", "change-me-in-prod")

    @property
    def llm_enabled(self) -> bool:
        return self.economia_use_llm and bool(self.openai_api_key.strip())


settings = Settings()
