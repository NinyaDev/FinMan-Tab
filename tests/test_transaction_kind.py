import unittest
from types import SimpleNamespace
from clients.transaction_kind import transaction_kind
from tests.test_tracked_sync import FakeSheet
from clients.tracked_writer import TrackedWriter
from unittest.mock import patch
import json


class KindTests(unittest.TestCase):
    def tx(self, primary, amount=10, confidence='HIGH'):
        return SimpleNamespace(amount=amount, personal_finance_category={
            'primary': primary, 'confidence_level': confidence})

    def test_confident_purchase(self):
        self.assertEqual(transaction_kind(self.tx('FOOD_AND_DRINK')), 'purchase')

    def test_low_confidence_and_negative_amount_require_review(self):
        self.assertEqual(transaction_kind(self.tx('FOOD_AND_DRINK', confidence='LOW')), 'unclassified')
        self.assertEqual(transaction_kind(self.tx('FOOD_AND_DRINK', amount=-10)), 'unclassified')
        self.assertEqual(transaction_kind(self.tx('INCOME', amount=-10)), 'unclassified')

    def test_payment_and_transfer_not_purchases(self):
        self.assertEqual(transaction_kind(self.tx('LOAN_PAYMENTS')), 'payment')
        self.assertEqual(transaction_kind(self.tx('TRANSFER_IN', amount=-10)), 'transfer')

    def test_category_only_update_changes_metadata_not_cells(self):
        sheet = FakeSheet()
        writer = TrackedWriter(sheet, 'fake', 'item')
        tx = self.tx('FOOD_AND_DRINK')
        tx.transaction_id, tx.name, tx.date = 'tx', 'Bank description', '2026-11-07'
        table = {'range': {'sheetId': 7, 'startColumnIndex': 2}}
        with patch('clients.tracked_writer.sheets._find_empty_data_row', return_value=3):
            writer.upsert(tx, table, 'Cleaned description', 10, 'Card_')
        self.assertEqual(json.loads(sheet.records[0]['metadataValue'])['kind'], 'purchase')
        sheet.cells[(7, 2, 2)] = ['My edit', 8]
        tx.personal_finance_category['primary'] = 'TRANSFER_OUT'
        writer.upsert(tx, table, 'Do not overwrite', 10, 'Card_')
        self.assertEqual(sheet.cells[(7, 2, 2)], ['My edit', 8])
        self.assertEqual(json.loads(sheet.records[0]['metadataValue'])['kind'], 'transfer')
