import json
import unittest

from financial_validation import issue, money


class FinancialValidationTests(unittest.TestCase):
    def test_details_are_curated_and_do_not_expose_raw_payload_or_secrets(self):
        data = issue("parcel-test", "professional", {"description": "Consulta teste", "api_key": "private", "raw_bill": {"token": "private"}},
                     bill={"uuid": "title-test", "person": {"name": "Contato teste", "cpf": "private"}},
                     source="parcel", gross=10000, net=9700)
        self.assertEqual(data["title_id"], "title-test")
        self.assertEqual((data["gross"], data["net"]), (100, 97))
        self.assertEqual(data["contact"], "Contato teste")
        self.assertNotIn("private", json.dumps(data))

    def test_zero_is_preserved_and_invalid_money_remains_unknown(self):
        self.assertEqual(money(0), 0)
        for value in (None, True, -1, "NaN", "Infinity", "1e999", {}, "invalid"):
            self.assertIsNone(money(value))

    def test_detail_text_is_bounded_and_missing_data_stays_empty(self):
        data = issue("record", "date", {"description": "x" * 1000, "person": "malformed"})
        self.assertEqual(len(data["description"]), 500)
        self.assertEqual(data["contact"], "")
        self.assertIsNone(data["date"])


if __name__ == "__main__":
    unittest.main()
