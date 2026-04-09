from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "economia.db"
_INITIALIZED_DB_PATH: Path | None = None


def _current_db_path() -> Path:
    return DB_PATH.resolve()


def _get_schema_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def ensure_database(force: bool = False) -> None:
    global _INITIALIZED_DB_PATH

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current_db_path = _current_db_path()
    if not force and _INITIALIZED_DB_PATH == current_db_path and current_db_path.exists():
        return

    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                onboarded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                key TEXT NOT NULL,
                label TEXT NOT NULL,
                examples TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                expense_date TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'text',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL UNIQUE,
                amount_cents INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()

        version = _get_schema_version(connection)

        if version < 1:
            try:
                connection.execute("ALTER TABLE expenses ADD COLUMN user_id INTEGER")
            except sqlite3.OperationalError:
                pass
            try:
                connection.execute("ALTER TABLE budgets ADD COLUMN user_id INTEGER")
            except sqlite3.OperationalError:
                pass
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS budgets_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        category TEXT NOT NULL,
                        amount_cents INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, category)
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO budgets_new (id, user_id, category, amount_cents, created_at) "
                    "SELECT id, user_id, category, amount_cents, created_at FROM budgets"
                )
                connection.execute("DROP TABLE budgets")
                connection.execute("ALTER TABLE budgets_new RENAME TO budgets")
            except sqlite3.OperationalError:
                pass
            existing = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if existing == 0:
                connection.execute("INSERT INTO schema_version (version) VALUES (1)")
            else:
                connection.execute("UPDATE schema_version SET version = 1")
            connection.commit()
            version = 1

        if version < 2:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_user_id ON chat_messages(user_id, id DESC)"
            )
            existing = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if existing == 0:
                connection.execute("INSERT INTO schema_version (version) VALUES (2)")
            else:
                connection.execute("UPDATE schema_version SET version = 2")
            connection.commit()
            version = 2

        if version < 3:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recurring_expenses (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id          INTEGER NOT NULL REFERENCES users(id),
                    description      TEXT NOT NULL,
                    category         TEXT NOT NULL,
                    amount_cents     INTEGER NOT NULL,
                    frequency        TEXT NOT NULL,
                    day_of_month     INTEGER,
                    day_of_week      INTEGER,
                    active           INTEGER NOT NULL DEFAULT 1,
                    last_generated   TEXT,
                    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            existing = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if existing == 0:
                connection.execute("INSERT INTO schema_version (version) VALUES (3)")
            else:
                connection.execute("UPDATE schema_version SET version = 3")
            connection.commit()
            version = 3

        if version < 4:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_sessions (
                    id         TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    payload    TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute("UPDATE schema_version SET version = 4")
            connection.commit()
            version = 4

        if version < 5:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_usage_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id),
                    route TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_cents INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    monthly_token_limit INTEGER NOT NULL DEFAULT 200000,
                    rate_window_minutes INTEGER NOT NULL DEFAULT 10,
                    rate_token_limit INTEGER NOT NULL DEFAULT 20000,
                    enabled INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            existing = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if existing == 0:
                connection.execute("INSERT INTO schema_version (version) VALUES (5)")
            else:
                connection.execute("UPDATE schema_version SET version = 5")
            connection.commit()

        _INITIALIZED_DB_PATH = current_db_path
    finally:
        connection.close()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    ensure_database()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
    finally:
        connection.close()
