from __future__ import annotations

import hashlib
import os

from app.db import get_connection


def hash_password(plain: str) -> tuple[str, str]:
    """Returns (salt_hex, hash_hex)."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations=260_000)
    return salt.hex(), key.hex()


def verify_password(plain: str, salt_hex: str, stored_hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    key = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations=260_000)
    return key.hex() == stored_hash_hex


def create_user(username: str, plain_password: str) -> int:
    """Creates a new user. Raises ValueError if username is already taken."""
    salt_hex, hash_hex = hash_password(plain_password)
    with get_connection() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, password_salt) VALUES (?, ?, ?)",
                (username, hash_hex, salt_hex),
            )
            connection.commit()
            return int(cursor.lastrowid)
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValueError(f"Usuário '{username}' já existe.") from exc
            raise


def get_user_by_username(username: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, username, password_hash, password_salt, onboarded, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, username, onboarded FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_onboarded(user_id: int) -> None:
    with get_connection() as connection:
        connection.execute("UPDATE users SET onboarded = 1 WHERE id = ?", (user_id,))
        connection.commit()


def is_admin_username(username: str) -> bool:
    return username.strip().lower() == "admin"
