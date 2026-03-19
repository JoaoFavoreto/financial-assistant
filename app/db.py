from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "economia.db"


def _get_schema_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def ensure_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        # Core tables (idempotent)
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
            # Add user_id to expenses
            try:
                connection.execute("ALTER TABLE expenses ADD COLUMN user_id INTEGER")
            except sqlite3.OperationalError:
                pass
            # Add user_id to budgets
            try:
                connection.execute("ALTER TABLE budgets ADD COLUMN user_id INTEGER")
            except sqlite3.OperationalError:
                pass
            # Recreate budgets with UNIQUE(user_id, category) instead of UNIQUE(category)
            # Only needed if the old unique constraint exists (no user_id yet)
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
            # Set schema version
            existing = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if existing == 0:
                connection.execute("INSERT INTO schema_version (version) VALUES (1)")
            else:
                connection.execute("UPDATE schema_version SET version = 1")
            connection.commit()

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
            connection.execute("UPDATE schema_version SET version = 2")
            connection.commit()


def get_connection() -> sqlite3.Connection:
    ensure_database()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection
