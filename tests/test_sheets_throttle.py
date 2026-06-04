"""
Throttling / rate-limit tests for clients/sheets_writer.py.

We cannot make Google's API rate-limit us on demand, so these tests drive the
REAL writer code against a fake Sheets service that enforces a 60-read-per-window
quota and raises the same googleapiclient HttpError(429) Google does when you
cross it. The fix (metadata reuse + retry-with-backoff) is gated behind
sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED, so each test flips that toggle to
compare pre-fix vs post-fix behavior on identical load.

Run from project root: python -m unittest discover tests
"""

import unittest
from unittest.mock import patch

from googleapiclient.errors import HttpError

from clients import sheets_writer
from clients.sheets_writer import (
    get_or_create_month_tab,
    find_table_in_tab,
    insert_transaction_into_table,
)


# --------------------------------------------------------------------------
# Fake Sheets service: a deterministic stand-in for Google's rate limiter.
# --------------------------------------------------------------------------

class _FakeResp:
    # Minimal httplib2-style response so HttpError(e).resp.status works.
    def __init__(self, status):
        self.status = status
        self.reason = "Too Many Requests"


def _make_429():
    return HttpError(_FakeResp(429), b'{"error": {"code": 429, "message": "Rate Limit Exceeded"}}')


class _Request:
    # Mimics a googleapiclient request: build it, then call .execute().
    def __init__(self, service, kind, result=None):
        self._service = service
        self._kind = kind          # "read" counts against the quota; "write" does not
        self._result = result

    def execute(self):
        if self._kind == "read":
            self._service._count_read()
        else:
            self._service._count_write()
        return self._result


class _Values:
    def __init__(self, service):
        self._service = service

    def get(self, spreadsheetId=None, range=None, **kwargs):
        # Empty range -> writer's empty-row scan picks the first data row.
        return _Request(self._service, "read", {"values": []})

    def update(self, spreadsheetId=None, range=None, **kwargs):
        return _Request(self._service, "write", {})


class _Spreadsheets:
    def __init__(self, service):
        self._service = service

    def get(self, spreadsheetId=None, **kwargs):
        return _Request(self._service, "read", self._service.metadata)

    def batchUpdate(self, spreadsheetId=None, body=None, **kwargs):
        return _Request(self._service, "write", {"replies": [{}]})

    def values(self):
        return _Values(self._service)


class FakeSheetsService:
    """Enforces Google's 60-reads-per-minute-per-user limit: every read counts
    against a window, and crossing the cap raises HttpError(429) exactly like
    the real API. Writes are tracked but unmetered (the bug was a read-quota
    bug). reset_window() simulates the minute ticking over."""

    def __init__(self, metadata, read_cap=60):
        self.metadata = metadata
        self.read_cap = read_cap
        self.reads_in_window = 0
        self.total_reads = 0
        self.total_writes = 0
        self.window_resets = 0

    def spreadsheets(self):
        return _Spreadsheets(self)

    def _count_read(self):
        self.reads_in_window += 1
        self.total_reads += 1
        if self.reads_in_window > self.read_cap:
            raise _make_429()

    def _count_write(self):
        self.total_writes += 1

    def reset_window(self):
        self.reads_in_window = 0
        self.window_resets += 1


def _build_metadata():
    # One visible "Mayo" tab holding one named table, plus the hidden Template.
    # Matches what get_or_create_month_tab/find_table_in_tab expect, so the
    # existing-tab path runs (no creation needed for these tests).
    table_range = {
        "sheetId": 100,
        "startRowIndex": 5,
        "endRowIndex": 50,
        "startColumnIndex": 2,
        "endColumnIndex": 4,
    }
    return {
        "sheets": [
            {"properties": {"sheetId": 0, "title": "Template", "hidden": True}, "tables": []},
            {
                "properties": {"sheetId": 100, "title": "Mayo", "hidden": False},
                "tables": [
                    {"tableId": "t-disc", "name": "Discover_Mayo", "range": table_range},
                ],
            },
        ]
    }


def _process_one_transaction(service, sid="fake-sheet-id", date="2026-05-10", prefix="Discover_"):
    # Mirrors the per-transaction sheet work the pipeline does (pipeline.py
    # steps 3-5), reusing the returned metadata exactly as the hot loop does.
    tab, meta = get_or_create_month_tab(service, sid, date)
    table = find_table_in_tab(service, sid, tab["sheetId"], prefix, metadata=meta)
    insert_transaction_into_table(service, sid, table, "Test merchant", 12.34, metadata=meta)


# Pin tab naming so date 2026-05-10 -> "Mayo" regardless of the real config.yaml.
_CONFIG_PATCH = {"tab_strategy": {"naming": "spanish"}, "sheet": {"template_tab": "Template"}}


class _FlagRestoringTestCase(unittest.TestCase):
    """Saves/restores the module toggle so tests can't leak state into each
    other (or into the rest of the suite)."""

    def setUp(self):
        self._cfg = patch.dict("clients.sheets_writer.CONFIG", _CONFIG_PATCH, clear=True)
        self._cfg.start()
        self._orig_flag = sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED

    def tearDown(self):
        self._cfg.stop()
        sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED = self._orig_flag


class TestReadAmplificationThrottles(_FlagRestoringTestCase):
    """The headline A/B: identical load (20 transactions, 60-read cap).
    Fix off -> throttles partway. Fix on -> sails through."""

    def _run_n(self, n):
        service = FakeSheetsService(_build_metadata(), read_cap=60)
        processed = 0
        error = None
        for _ in range(n):
            try:
                _process_one_transaction(service)
                processed += 1
            except HttpError as e:
                error = e
                break
        return service, processed, error

    def test_without_fix_throttles(self):
        # ~5 reads/transaction against a 60-read cap -> 429 around the 12th.
        sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED = False
        service, processed, error = self._run_n(20)
        self.assertIsNotNone(error, "expected a 429 with the fix disabled")
        self.assertEqual(error.resp.status, 429)
        self.assertLess(processed, 20)
        self.assertLessEqual(processed, 13)

    def test_with_fix_same_load_completes(self):
        # ~2 reads/transaction -> ~40 reads, comfortably under the cap.
        sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED = True
        service, processed, error = self._run_n(20)
        self.assertIsNone(error, "the fix should keep 20 tx under the read cap")
        self.assertEqual(processed, 20)
        self.assertEqual(service.window_resets, 0, "no retry should have been needed")


class TestBackoffRetry(_FlagRestoringTestCase):
    """The seatbelt in isolation: a transient 429 retries and recovers when the
    fix is on, and raises immediately when it's off."""

    def test_with_fix_retries_then_succeeds(self):
        sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED = True
        calls = {"n": 0}

        class Req:
            def execute(_self):
                calls["n"] += 1
                if calls["n"] <= 2:      # fail twice...
                    raise _make_429()
                return "ok"              # ...succeed on the third attempt

        with patch("clients.sheets_writer.time.sleep") as mock_sleep:
            result = sheets_writer._execute_with_backoff(Req())

        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_without_fix_raises_immediately(self):
        sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED = False
        calls = {"n": 0}

        class Req:
            def execute(_self):
                calls["n"] += 1
                raise _make_429()

        with patch("clients.sheets_writer.time.sleep") as mock_sleep:
            with self.assertRaises(HttpError):
                sheets_writer._execute_with_backoff(Req())

        self.assertEqual(calls["n"], 1, "no retry should happen with the fix off")
        self.assertEqual(mock_sleep.call_count, 0)


class TestStressBurstRecovers(_FlagRestoringTestCase):
    """End-to-end stress: drive enough transactions that even the optimized
    path crosses the 60-read cap, and prove the whole run still finishes by
    waiting out the window instead of crashing."""

    def test_heavy_load_recovers_via_backoff(self):
        sheets_writer.SHEETS_OPTIMIZATIONS_ENABLED = True
        service = FakeSheetsService(_build_metadata(), read_cap=60)

        # Patch sleep so "waiting out the minute" refills the read bucket;
        # keeps the test instant instead of sleeping real seconds.
        with patch("clients.sheets_writer.time.sleep", side_effect=lambda s: service.reset_window()):
            for _ in range(50):
                _process_one_transaction(service)   # any escaping 429 fails the test

        # 50 tx * ~2 reads = ~100 reads > 60 cap, so it MUST have throttled and
        # recovered at least once rather than crashing the run.
        self.assertGreater(service.total_reads, 60)
        self.assertGreaterEqual(service.window_resets, 1)


if __name__ == "__main__":
    unittest.main()
