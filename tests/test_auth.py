from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from app import db
from app.auth import create_user, get_user_by_username, hash_password, verify_password


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / f".tmpdb_{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        db.DATA_DIR = self.temp_dir
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.ensure_database()

    def tearDown(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hash_and_verify_roundtrip(self) -> None:
        salt_hex, hash_hex = hash_password("minha_senha_segura")
        self.assertTrue(verify_password("minha_senha_segura", salt_hex, hash_hex))

    def test_wrong_password_returns_false(self) -> None:
        salt_hex, hash_hex = hash_password("correta")
        self.assertFalse(verify_password("errada", salt_hex, hash_hex))

    def test_duplicate_username_raises_value_error(self) -> None:
        create_user("joao", "abc123")
        with self.assertRaises(ValueError):
            create_user("joao", "outra_senha")

    def test_get_user_by_username(self) -> None:
        user_id = create_user("maria", "senha123")
        user = get_user_by_username("maria")
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], user_id)
        self.assertEqual(user["username"], "maria")
        self.assertEqual(user["onboarded"], 0)

    def test_get_nonexistent_user_returns_none(self) -> None:
        result = get_user_by_username("naoexiste")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
