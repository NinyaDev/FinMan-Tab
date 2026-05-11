"""
Unit tests for clients/insights.py - the deterministic helpers behind the
monthly summary email.

Network-bound helpers (Gemini call, Gmail send, Sheets reads) are not
exercised here - they are wired in the orchestrator and tested live.

Run from project root: python -m unittest discover tests
"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from clients.insights import (
    _prior_month_key,
    _should_send_summary,
    _classify_transactions,
    _build_chart_url,
    _build_summary_email_body,
)


class TestPriorMonthKey(unittest.TestCase):
    """'YYYY-MM' -> 'YYYY-MM' for the month immediately before."""

    def test_normal_month(self):
        self.assertEqual(_prior_month_key("2026-05"), "2026-04")
        self.assertEqual(_prior_month_key("2026-12"), "2026-11")

    def test_january_wraps_to_december_of_prior_year(self):
        self.assertEqual(_prior_month_key("2027-01"), "2026-12")
        self.assertEqual(_prior_month_key("2026-01"), "2025-12")


class TestShouldSendSummary(unittest.TestCase):
    """Decision: 'YYYY-MM' to summarize, or None."""

    def test_empty_state_returns_prior_month(self):
        # June 1 -> target is May
        result = _should_send_summary(date(2026, 6, 1), {})
        self.assertEqual(result, "2026-05")

    def test_already_summarized_returns_none(self):
        # June 1 with last_summarized = May -> already done, skip
        state = {"last_summarized": "2026-05"}
        self.assertIsNone(_should_send_summary(date(2026, 6, 1), state))

    def test_january_wraps_to_december_of_prior_year(self):
        # Jan 5 2027 with empty state -> target Dec 2026
        result = _should_send_summary(date(2027, 1, 5), {})
        self.assertEqual(result, "2026-12")

    def test_mid_month_with_stale_state_still_sends(self):
        # June 15 with last_summarized = Apr -> May summary still due
        state = {"last_summarized": "2026-04"}
        self.assertEqual(_should_send_summary(date(2026, 6, 15), state), "2026-05")


class TestClassifyTransactions(unittest.TestCase):
    """Direction tagging based on table_prefix + sign."""

    SINGLE_TABLE_CONFIG = {
        "account_routing": {
            "acc_credit": {
                "bank": "discover",
                "income_table_prefix": "Discover_",
                "outflow_table_prefix": "Discover_",  # same prefix = single-table mode
            },
            "acc_checking": {
                "bank": "sofi",
                "income_table_prefix": "Sofi_Checkings_",
                "outflow_table_prefix": "Gastos_Checkings_",  # different = two-table mode
            },
        }
    }

    def test_single_table_positive_amount_is_expense(self):
        with patch.dict("clients.insights.CONFIG", self.SINGLE_TABLE_CONFIG, clear=True):
            result = _classify_transactions([
                {"description": "Taco Bell", "amount": 9.70, "table_prefix": "Discover_"},
            ])
            self.assertEqual(result[0]["direction"], "expense")
            self.assertEqual(result[0]["amount"], 9.70)

    def test_single_table_negative_amount_is_income(self):
        # Cashback / credit card payment received -> negative sign -> income
        with patch.dict("clients.insights.CONFIG", self.SINGLE_TABLE_CONFIG, clear=True):
            result = _classify_transactions([
                {"description": "Cashback Credit", "amount": -9.70, "table_prefix": "Discover_"},
            ])
            self.assertEqual(result[0]["direction"], "income")
            self.assertEqual(result[0]["amount"], 9.70)  # abs() applied

    def test_two_table_gastos_prefix_is_expense(self):
        with patch.dict("clients.insights.CONFIG", self.SINGLE_TABLE_CONFIG, clear=True):
            result = _classify_transactions([
                {"description": "Venmo", "amount": 25.00, "table_prefix": "Gastos_Checkings_"},
            ])
            self.assertEqual(result[0]["direction"], "expense")

    def test_two_table_sofi_prefix_is_income(self):
        with patch.dict("clients.insights.CONFIG", self.SINGLE_TABLE_CONFIG, clear=True):
            result = _classify_transactions([
                {"description": "Tuition", "amount": 397.50, "table_prefix": "Sofi_Checkings_"},
            ])
            self.assertEqual(result[0]["direction"], "income")

    def test_unmapped_prefix_is_skipped(self):
        with patch.dict("clients.insights.CONFIG", self.SINGLE_TABLE_CONFIG, clear=True):
            result = _classify_transactions([
                {"description": "?", "amount": 10, "table_prefix": "Unknown_"},
            ])
            self.assertEqual(result, [])


class TestBuildChartUrl(unittest.TestCase):
    """Pie chart URL composition."""

    def test_url_points_to_quickchart(self):
        url = _build_chart_url([
            {"category": "Groceries", "amount": 100},
            {"category": "Dining", "amount": 50},
        ])
        self.assertTrue(url.startswith("https://quickchart.io/chart?c="))

    def test_url_contains_percentage_labels(self):
        # Two categories with 100 + 50 = 150 total -> 67% / 33%
        url = _build_chart_url([
            {"category": "Groceries", "amount": 100},
            {"category": "Dining", "amount": 50},
        ])
        # Labels include percent computed by Python
        self.assertIn("67%25", url)  # %25 is url-encoded '%'
        self.assertIn("33%25", url)

    def test_handles_empty_input_without_crashing(self):
        url = _build_chart_url([])
        self.assertTrue(url.startswith("https://quickchart.io/chart?c="))


class TestBuildSummaryEmailBody(unittest.TestCase):
    """HTML email body composition."""

    BASE_SUMMARY = {
        "total_income": 1000.00,
        "total_expenses": 700.00,
        "net": 300.00,
        "top_merchants": [{"name": "Walmart", "total_spend": 100.00, "count": 1}],
        "spend_by_category": [{"category": "Groceries", "amount": 100.00}],
        "observations": ["You spent less on dining."],
        "commentary": "A solid month overall.",
    }

    def test_includes_month_name_and_net(self):
        html = _build_summary_email_body(
            self.BASE_SUMMARY, "Mayo", chart_url="http://example.com/chart.png"
        )
        self.assertIn("Mayo Summary", html)
        self.assertIn("$300.00", html)

    def test_includes_chart_image_tag(self):
        html = _build_summary_email_body(
            self.BASE_SUMMARY, "Mayo", chart_url="http://example.com/chart.png"
        )
        self.assertIn('<img src="http://example.com/chart.png"', html)

    def test_includes_top_merchants_and_commentary(self):
        html = _build_summary_email_body(
            self.BASE_SUMMARY, "Mayo", chart_url=""
        )
        self.assertIn("Walmart", html)
        self.assertIn("A solid month overall.", html)
        self.assertIn("You spent less on dining.", html)

    def test_negative_net_uses_red_color(self):
        neg = dict(self.BASE_SUMMARY, net=-200.00)
        html = _build_summary_email_body(neg, "Mayo", chart_url="")
        self.assertIn("#c62828", html)  # red color hex

    def test_prior_summary_renders_delta_line(self):
        prior = {"net": 100.00}
        html = _build_summary_email_body(self.BASE_SUMMARY, "Mayo", chart_url="", prior_summary=prior)
        self.assertIn("vs prior month net", html)
        self.assertIn("+$200.00", html)  # 300 - 100 = +200

    def test_no_prior_summary_omits_delta_line(self):
        html = _build_summary_email_body(self.BASE_SUMMARY, "Mayo", chart_url="")
        self.assertNotIn("vs prior month net", html)


class TestSummaryStateRoundTrip(unittest.TestCase):
    """File I/O: empty default + round-trip preservation."""

    def test_empty_file_returns_default_shape(self):
        # Point STATE_FILE at a temp path that does NOT exist
        tmpdir = tempfile.mkdtemp()
        missing_path = Path(tmpdir) / "does_not_exist.json"
        with patch("clients.insights.SUMMARY_STATE_FILE", missing_path):
            from clients.insights import _load_summary_state
            state = _load_summary_state()
            self.assertEqual(state, {"last_summarized": None, "history": {}})

    def test_save_then_load_preserves_state(self):
        tmpdir = tempfile.mkdtemp()
        tmp_path = Path(tmpdir) / "state.json"
        with patch("clients.insights.SUMMARY_STATE_FILE", tmp_path):
            from clients.insights import _load_summary_state, _save_summary_state
            original = {
                "last_summarized": "2026-05",
                "history": {"2026-05": {"net": 42.0, "total_income": 100, "total_expenses": 58, "spend_by_category": []}},
            }
            _save_summary_state(original)
            self.assertEqual(_load_summary_state(), original)


if __name__ == "__main__":
    unittest.main()
