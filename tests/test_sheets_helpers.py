"""
Unit tests for the pure helpers in clients/sheets_writer.py.
Run from project root: python -m unittest discover tests
"""

import unittest
from unittest.mock import patch

from clients.sheets_writer import (
    _col_letter,
    _prev_month_name,
    _all_configured_prefixes,
)


class TestColLetter(unittest.TestCase):
    """0-indexed column number → A1 letter conversion."""

    def test_single_letter_columns(self):
        self.assertEqual(_col_letter(0), "A")
        self.assertEqual(_col_letter(1), "B")
        self.assertEqual(_col_letter(25), "Z")

    def test_double_letter_columns(self):
        self.assertEqual(_col_letter(26), "AA")
        self.assertEqual(_col_letter(27), "AB")
        self.assertEqual(_col_letter(51), "AZ")
        self.assertEqual(_col_letter(52), "BA")


class TestPrevMonthName(unittest.TestCase):
    """Prior-month name resolution including Jan→Dec wrap."""

    def setUp(self):
        # Pin naming to "spanish" regardless of user's actual config.yaml.
        self._patcher = patch.dict(
            "clients.sheets_writer.CONFIG",
            {"tab_strategy": {"naming": "spanish"}},
            clear=True,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_normal_month_in_year(self):
        self.assertEqual(_prev_month_name("2026-06-15"), "Mayo")
        self.assertEqual(_prev_month_name("2026-12-31"), "Noviembre")

    def test_january_wraps_to_december(self):
        self.assertEqual(_prev_month_name("2026-01-05"), "Diciembre")
        self.assertEqual(_prev_month_name("2027-01-01"), "Diciembre")

    def test_day_31_does_not_crash(self):
        # Without day=1 inside _prev_month_name, dt.replace(month=2) on
        # March 31 would raise (Feb 31 doesn't exist). This guards that fix.
        self.assertEqual(_prev_month_name("2026-03-31"), "Febrero")
        self.assertEqual(_prev_month_name("2026-05-31"), "Abril")


class TestAllConfiguredPrefixes(unittest.TestCase):
    """Prefix gathering ignores duplicates across income/outflow."""

    def test_returns_unique_prefixes_across_routing(self):
        fake_config = {
            "account_routing": {
                "acc_credit": {
                    "bank": "discover",
                    "income_table_prefix": "Discover_",
                    "outflow_table_prefix": "Discover_",  # same → 1 entry
                },
                "acc_checking": {
                    "bank": "sofi",
                    "income_table_prefix": "Sofi_Checkings_",
                    "outflow_table_prefix": "Gastos_Checkings_",
                },
            }
        }
        with patch.dict("clients.sheets_writer.CONFIG", fake_config, clear=True):
            prefixes = _all_configured_prefixes()
            self.assertEqual(
                prefixes,
                {"Discover_", "Sofi_Checkings_", "Gastos_Checkings_"},
            )

    def test_empty_routing_returns_empty_set(self):
        with patch.dict("clients.sheets_writer.CONFIG", {"account_routing": {}}, clear=True):
            self.assertEqual(_all_configured_prefixes(), set())


if __name__ == "__main__":
    unittest.main()
