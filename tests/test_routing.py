"""
Unit tests for pipeline.route_transaction - the sign-handling logic for
single-table (Discover/OnePay) vs two-table (SoFi) accounts.

Run from project root: python -m unittest discover tests
"""

import unittest

from pipeline import route_transaction


class FakeTx:
    """Minimal stand-in for a Plaid Transaction object."""
    def __init__(self, account_id: str, amount: float):
        self.account_id = account_id
        self.amount = amount


class TestRouteTransaction(unittest.TestCase):
    def setUp(self):
        self.routing = {
            "acc_credit": {
                "bank": "discover",
                # Single-table: same prefix for both directions.
                "income_table_prefix": "Discover_",
                "outflow_table_prefix": "Discover_",
            },
            "acc_checking": {
                "bank": "sofi",
                # Two-table: separate income / outflow tables.
                "income_table_prefix": "Sofi_Checkings_",
                "outflow_table_prefix": "Gastos_Checkings_",
            },
        }

    def test_unmapped_account_returns_none(self):
        tx = FakeTx(account_id="not_in_routing", amount=10.0)
        self.assertIsNone(route_transaction(tx, self.routing))

    def test_single_table_outflow_preserves_sign(self):
        # Discover charge (positive in Plaid = outflow).
        tx = FakeTx(account_id="acc_credit", amount=15.0)
        bank, prefix, direction, amount = route_transaction(tx, self.routing)
        self.assertEqual(bank, "discover")
        self.assertEqual(prefix, "Discover_")
        self.assertEqual(direction, "outflow")
        self.assertEqual(amount, 15.0)

    def test_single_table_income_preserves_negative_sign(self):
        # Cashback / payment posted negative - must stay negative so the
        # single Discover_ table reflects credit-card sign convention.
        tx = FakeTx(account_id="acc_credit", amount=-9.7)
        bank, prefix, direction, amount = route_transaction(tx, self.routing)
        self.assertEqual(bank, "discover")
        self.assertEqual(direction, "income")
        self.assertEqual(amount, -9.7)
        self.assertEqual(prefix, "Discover_")

    def test_two_table_outflow_uses_abs_and_outflow_prefix(self):
        # SoFi spend posted positive - goes to Gastos_Checkings_ as positive.
        tx = FakeTx(account_id="acc_checking", amount=25.0)
        bank, prefix, direction, amount = route_transaction(tx, self.routing)
        self.assertEqual(bank, "sofi")
        self.assertEqual(direction, "outflow")
        self.assertEqual(prefix, "Gastos_Checkings_")
        self.assertEqual(amount, 25.0)

    def test_two_table_income_uses_abs_and_income_prefix(self):
        # SoFi deposit posted negative - flipped to positive in income table.
        tx = FakeTx(account_id="acc_checking", amount=-100.0)
        bank, prefix, direction, amount = route_transaction(tx, self.routing)
        self.assertEqual(bank, "sofi")
        self.assertEqual(direction, "income")
        self.assertEqual(prefix, "Sofi_Checkings_")
        self.assertEqual(amount, 100.0)


if __name__ == "__main__":
    unittest.main()
