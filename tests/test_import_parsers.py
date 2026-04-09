from __future__ import annotations

import unittest
from datetime import date

from app.import_parsers import ImportParseError, parse_csv


NUBANK_CSV = (
    "date,category,title,amount\n"
    "2025-02-15,Transporte,UBER *VIAGEM,45.90\n"
    "2025-02-16,Alimentação,IFOOD,32.50\n"
    "2025-02-18,Outros,PAGAMENTO EM DEBITO,-100.00\n"
    "2025-02-20,Compras,AMAZON,89.90\n"
)

SEMICOLON_CSV = (
    "Data;Descrição;Valor\n"
    "15/02/2025;Mercado Extra;120,50\n"
    "16/02/2025;Farmácia;45,00\n"
    "17/02/2025;Pagamento fatura;-500,00\n"
)

NO_YEAR_CSV = (
    "data;descricao;valor\n"
    "15/02;Uber;45,90\n"
    "16/02;iFood;32,50\n"
)

BAD_CSV = "nome;endereco\nJoao;Rua X\n"

PAYMENT_ONLY_CSV = "data,descricao,valor\n15/02/2025,PAGAMENTO RECEBIDO,500.00\n"


class ParseCSVTests(unittest.TestCase):

    def test_nubank_format_skips_negative(self) -> None:
        expenses = parse_csv(NUBANK_CSV.encode())
        # Should skip the negative payment
        self.assertEqual(len(expenses), 3)
        descs = [e.description for e in expenses]
        self.assertIn("UBER *VIAGEM", descs)
        self.assertIn("IFOOD", descs)
        self.assertIn("AMAZON", descs)

    def test_semicolon_delimiter(self) -> None:
        expenses = parse_csv(SEMICOLON_CSV.encode())
        self.assertEqual(len(expenses), 2)
        self.assertEqual(expenses[0].amount_cents, 12050)
        self.assertEqual(expenses[1].amount_cents, 4500)

    def test_no_year_uses_reference_month(self) -> None:
        ref = date(2025, 2, 1)
        expenses = parse_csv(NO_YEAR_CSV.encode("latin-1"), reference_month=ref)
        self.assertEqual(len(expenses), 2)
        self.assertEqual(expenses[0].expense_date, date(2025, 2, 15))
        self.assertEqual(expenses[1].expense_date, date(2025, 2, 16))

    def test_source_type_is_import(self) -> None:
        expenses = parse_csv(NUBANK_CSV.encode())
        for e in expenses:
            self.assertEqual(e.source_type, "import")

    def test_category_auto_detection_uber(self) -> None:
        expenses = parse_csv(NUBANK_CSV.encode())
        uber = next(e for e in expenses if "UBER" in e.description)
        self.assertEqual(uber.category, "transporte")

    def test_category_auto_detection_mercado(self) -> None:
        expenses = parse_csv(SEMICOLON_CSV.encode())
        mercado = next(e for e in expenses if "Mercado" in e.description)
        self.assertEqual(mercado.category, "alimentacao")

    def test_missing_amount_column_raises(self) -> None:
        with self.assertRaises(ImportParseError):
            parse_csv(BAD_CSV.encode())

    def test_empty_bytes_raises(self) -> None:
        with self.assertRaises(ImportParseError):
            parse_csv(b"")

    def test_all_rows_ignored_raises(self) -> None:
        with self.assertRaises(ImportParseError):
            parse_csv(PAYMENT_ONLY_CSV.encode())

    def test_amounts_stored_as_cents(self) -> None:
        expenses = parse_csv(NUBANK_CSV.encode())
        amazon = next(e for e in expenses if "AMAZON" in e.description)
        self.assertEqual(amazon.amount_cents, 8990)

    def test_latin1_encoding(self) -> None:
        # Farmácia contains non-ASCII — encoded as latin-1 bytes
        csv_latin1 = "data;descricao;valor\n15/02/2025;Farmacia;45,00\n".encode("latin-1")
        expenses = parse_csv(csv_latin1)
        self.assertEqual(len(expenses), 1)
        self.assertEqual(expenses[0].amount_cents, 4500)

    def test_iso_date_format(self) -> None:
        expenses = parse_csv(NUBANK_CSV.encode())
        uber = next(e for e in expenses if "UBER" in e.description)
        self.assertEqual(uber.expense_date, date(2025, 2, 15))

    def test_full_date_dd_mm_yyyy(self) -> None:
        expenses = parse_csv(SEMICOLON_CSV.encode())
        self.assertEqual(expenses[0].expense_date, date(2025, 2, 15))


if __name__ == "__main__":
    unittest.main()
