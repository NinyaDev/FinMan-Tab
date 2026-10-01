import unittest
from unittest.mock import patch

from clients import gemini_client, sheets_writer
from tests.test_sheets_throttle import FakeSheetsService, _build_metadata


class TestLogPrivacy(unittest.TestCase):
    def test_sheet_write_does_not_log_transaction_or_spreadsheet_id(self):
        metadata = _build_metadata()
        service = FakeSheetsService(metadata)
        table = metadata['sheets'][1]['tables'][0]
        with self.assertLogs(sheets_writer.log, level='INFO') as captured:
            sheets_writer.insert_transaction_into_table(
                service, 'private-sheet-id', table, 'Private merchant sentinel',
                9876.54, metadata=metadata,
            )
        output = '\n'.join(captured.output)
        for sensitive in ('Private merchant sentinel', '9876.54', 'private-sheet-id'):
            self.assertNotIn(sensitive, output)
        self.assertEqual(service.total_writes, 1)

    def test_cleanup_failure_does_not_log_merchant_or_exception_payload(self):
        with patch.object(gemini_client._client.models, 'generate_content',
                          side_effect=RuntimeError('private-api-payload')):
            with self.assertLogs(gemini_client.log, level='ERROR') as captured:
                result = gemini_client.clean_description(
                    'private-merchant', 9876.54, 'private-account', '2026-09-30')
        self.assertEqual(result, 'private-merchant')
        output = '\n'.join(captured.output)
        for sensitive in ('private-api-payload', 'private-merchant', '9876.54', 'private-account'):
            self.assertNotIn(sensitive, output)
