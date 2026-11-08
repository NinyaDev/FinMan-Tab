import copy
import json
import unittest
from types import SimpleNamespace as Tx
from unittest.mock import patch

from clients.sync_changes import apply_changes, fetch_changes
from clients.tracked_writer import TrackedWriter, ReconciliationRequired


class Request:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSheet:
    def __init__(self):
        self.records = []
        self.cells = {}
        self.lose_response = False
        self.fail_before = False
        self.writes = 0

    def spreadsheets(self):
        return self

    def developerMetadata(self):
        return self

    def values(self):
        return self

    def search(self, **kwargs):
        lookup = kwargs['body']['dataFilters'][0]['developerMetadataLookup']
        return Request(lambda: {'matchedDeveloperMetadata': [
            {'developerMetadata': copy.deepcopy(m)} for m in self.records
            if (m['metadataKey'] == lookup['metadataKey'] if 'metadataKey' in lookup
                else m['location'] == lookup['metadataLocation'])]})

    def batchGetByDataFilter(self, **kwargs):
        grid = kwargs['body']['dataFilters'][0]['gridRange']
        return Request(lambda: {'valueRanges': [{'valueRange': {'values': [self.cells.get(
            (grid['sheetId'], grid['startRowIndex'], grid['startColumnIndex']), [])]}}]})

    def batchUpdate(self, **kwargs):
        def commit():
            if self.fail_before:
                raise TimeoutError('request not delivered')
            for request in kwargs['body']['requests']:
                if 'updateCells' in request:
                    update = request['updateCells']
                    start = update['start']
                    self.cells[(start['sheetId'], start['rowIndex'], start['columnIndex'])] = [
                        next(iter(cell['userEnteredValue'].values())) for cell in update['rows'][0]['values']]
                elif 'createDeveloperMetadata' in request:
                    metadata = copy.deepcopy(request['createDeveloperMetadata']['developerMetadata'])
                    metadata['metadataId'] = len(self.records) + 1
                    self.records.append(metadata)
                else:
                    update = request['updateDeveloperMetadata']
                    record_id = update['dataFilters'][0]['developerMetadataLookup']['metadataId']
                    self.records[record_id - 1]['metadataValue'] = update['developerMetadata']['metadataValue']
            self.writes += 1
            if self.lose_response:
                self.lose_response = False
                raise TimeoutError('response lost after commit')
            return {}
        return Request(commit)


class TrackedTests(unittest.TestCase):
    def test_full_table_stops_without_moving_or_overwriting_footer(self):
        with patch('clients.tracked_writer.sheets._find_empty_data_row', return_value=None):
            with self.assertRaises(ReconciliationRequired):
                self.writer.upsert(self.tx, self.table, 'Coffee', 12.5, 'Card_')
        self.assertEqual(self.sheet.writes, 0)

    def setUp(self):
        self.sheet = FakeSheet()
        self.writer = TrackedWriter(self.sheet, 'fake-sheet', 'fake-item')
        self.table = {'range': {'sheetId': 7, 'startColumnIndex': 2}}
        self.tx = Tx(transaction_id='fake-tx', date='2026-11-07', amount=12.5,
                     name='Coffee', pending=False, pending_transaction_id=None)
        self.empty = patch('clients.tracked_writer.sheets._find_empty_data_row', return_value=3)
        self.empty.start()
        self.addCleanup(self.empty.stop)

    def write(self, description='Coffee', amount=None):
        return self.writer.upsert(self.tx, self.table, description,
                                  self.tx.amount if amount is None else amount, 'Card_')

    def test_lost_response_retry_does_not_duplicate_or_overwrite_edit(self):
        self.sheet.lose_response = True
        with self.assertRaises(TimeoutError):
            self.write()
        self.sheet.cells[(7, 2, 2)][0] = 'My custom description'
        self.assertFalse(self.write())
        self.assertEqual(len(self.sheet.records), 1)
        self.assertEqual(self.sheet.writes, 1)
        self.assertEqual(self.sheet.cells[(7, 2, 2)][0], 'My custom description')

    def test_failure_before_write_is_retryable(self):
        self.sheet.fail_before = True
        with self.assertRaises(TimeoutError):
            self.write()
        self.assertEqual(self.sheet.records, [])
        self.sheet.fail_before = False
        self.assertTrue(self.write())

    def test_credit_keeps_negative_sign(self):
        self.tx.amount = -10
        self.write('Reward credit')
        self.assertEqual(self.sheet.cells[(7, 2, 2)], ['Reward credit', -10])

    def test_modified_amount_preserves_edited_description(self):
        self.write()
        self.sheet.cells[(7, 2, 2)][0] = 'School supplies'
        self.tx.amount = 15
        self.write('Bank changed description')
        self.assertEqual(self.sheet.cells[(7, 2, 2)], ['School supplies', 15])

    def test_modified_amount_conflict_stops_without_overwriting(self):
        self.write()
        self.sheet.cells[(7, 2, 2)][1] = 7
        self.tx.amount = 15
        with self.assertRaises(ReconciliationRequired):
            self.write()
        self.assertEqual(self.sheet.cells[(7, 2, 2)][1], 7)

    def test_remove_is_idempotent_and_retains_identity(self):
        self.write()
        self.writer.remove('fake-tx')
        self.writer.remove('fake-tx')
        self.assertEqual(self.sheet.cells[(7, 2, 2)][1], 0)
        self.assertEqual(len(self.sheet.records), 1)
        self.assertEqual(self.sheet.writes, 2)

    def test_unknown_removal_never_touches_legacy_rows(self):
        self.writer.remove('untracked')
        self.assertEqual(self.sheet.writes, 0)

    def test_date_change_requires_reconciliation(self):
        self.write()
        self.tx.date = '2026-12-01'
        with self.assertRaises(ReconciliationRequired):
            self.write()

    def test_row_metadata_follows_moved_row(self):
        self.write()
        self.sheet.cells[(7, 8, 2)] = self.sheet.cells.pop((7, 2, 2))
        self.sheet.records[0]['location']['dimensionRange'].update(startIndex=8, endIndex=9)
        self.tx.amount = 20
        self.write()
        self.assertEqual(self.sheet.cells[(7, 8, 2)][1], 20)

    def test_same_amount_different_id_is_not_deduplicated(self):
        self.write()
        self.tx.transaction_id = 'another-purchase'
        with patch('clients.tracked_writer.sheets._find_empty_data_row', return_value=4):
            self.write()
        self.assertEqual(len(self.sheet.records), 2)

    def test_empty_description_does_not_allow_overwriting_amount(self):
        self.sheet.cells[(7, 2, 2)] = ['', 42]
        with self.assertRaises(ReconciliationRequired):
            self.write()
        self.assertEqual(self.sheet.writes, 0)

    def test_cleared_row_with_metadata_cannot_be_reused(self):
        self.write()
        self.sheet.cells[(7, 2, 2)] = ['', '']
        self.tx.transaction_id = 'new-id'
        with self.assertRaises(ReconciliationRequired):
            self.write()
        self.assertEqual(len(self.sheet.records), 1)


class ChangeTests(unittest.TestCase):
    def test_pending_replaced_by_posted_and_removed_wins(self):
        pending = Tx(transaction_id='pending', pending=True)
        posted = Tx(transaction_id='posted', pending=False, pending_transaction_id='pending')
        writes, removals = [], []
        apply_changes([pending, posted], [], [Tx(transaction_id='pending')], writes.append, removals.append)
        self.assertEqual(writes, [posted])
        self.assertIn('pending', removals)

    def test_modified_wins_over_added(self):
        old = Tx(transaction_id='t', pending=False, amount=1)
        new = Tx(transaction_id='t', pending=False, amount=2)
        writes = []
        apply_changes([old], [new], [], writes.append, lambda _: None)
        self.assertEqual(writes, [new])

    def test_pagination_collects_all_change_types(self):
        responses = iter([Tx(added=[1], modified=[], removed=[], next_cursor='b', has_more=True),
                          Tx(added=[], modified=[2], removed=[3], next_cursor='c', has_more=False)])
        client = Tx(transactions_sync=lambda _: next(responses))
        self.assertEqual(fetch_changes(client, 'fake-token', 'a'), ([1], [2], [3], 'c'))

    def test_fetch_error_prevents_any_apply(self):
        def fail(_):
            raise RuntimeError('pagination mutation')
        with self.assertRaises(RuntimeError):
            fetch_changes(Tx(transactions_sync=fail), 'fake-token', 'a')


class PipelineRecoveryTests(unittest.TestCase):
    def test_amex_only_tracking_preserves_legacy_path_and_ignores_pre_start_updates(self):
        import pipeline
        from contextlib import ExitStack
        from unittest.mock import Mock
        old = Tx(transaction_id='old', account_id='amex-account', date='2026-10-30',
                 amount=10, name='Before import window', pending=False, pending_transaction_id=None)
        writer = Mock()
        legacy = Mock(return_value=([], 'legacy-next'))
        tracked = Mock(return_value=([], [old], [], 'amex-next'))
        saved = []
        config = {'sheet': {'spreadsheet_id': 'fake'}, 'account_routing': {},
                  'pipeline': {'tracked_banks': ['amex'], 'bank_start_dates': {'amex': '2026-11-01'}}}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(pipeline.CONFIG, config, clear=True))
            for name, value in {
                'get_client': lambda **_: None, 'get_credentials': lambda: None,
                'build': lambda *a, **k: None,
                'load_tokens': lambda: {'existing': {'access_token':'legacy','cursor':'legacy-before'},
                                       'amex': {'access_token':'amex','item_id':'new-item','cursor':'amex-before'}},
                'fetch_recent_transactions': legacy, 'fetch_changes': tracked,
                'TrackedWriter': lambda *args: writer,
                'save_tokens': lambda x: saved.append(copy.deepcopy(x)),
                'maybe_send_monthly_summary': lambda *args: None,
            }.items():
                stack.enter_context(patch.object(pipeline, name, value))
            pipeline.main()
        legacy.assert_called_once_with(None, 'legacy', 'legacy-before')
        tracked.assert_called_once_with(None, 'amex', 'amex-before')
        writer.upsert.assert_not_called()
        writer.lookup.assert_not_called()
        self.assertEqual(saved[-1]['existing']['cursor'], 'legacy-next')
        self.assertEqual(saved[-1]['amex']['cursor'], 'amex-next')

    def test_partial_success_keeps_cursor_and_retry_does_not_duplicate(self):
        import pipeline
        sheet = FakeSheet()
        txs = [Tx(transaction_id=str(i), account_id='account', date='2026-11-07', amount=10.0,
                  name='Same merchant', pending=False, pending_transaction_id=None) for i in (1, 2)]
        tokens = {'bank': {'access_token': 'fake', 'item_id': 'item', 'cursor': 'before'}}
        saved = []
        table = {'range': {'sheetId': 7, 'startColumnIndex': 2}}
        class FailOnceWriter(TrackedWriter):
            def upsert(self, tx, *args):
                if tx.transaction_id == '2' and not self.lookup('2'):
                    sheet.lose_response = True
                return super().upsert(tx, *args)
        config = {'sheet': {'spreadsheet_id': 'fake'},
                  'pipeline': {'track_transaction_ids': True},
                  'account_routing': {'account': {'bank': 'bank', 'income_table_prefix': 'Card_',
                                                  'outflow_table_prefix': 'Card_'}}}
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.dict(pipeline.CONFIG, config, clear=True))
            for name, value in {
                'get_client': lambda **_: None, 'get_credentials': lambda: None,
                'build': lambda *a, **k: sheet, 'load_tokens': lambda: copy.deepcopy(tokens),
                'save_tokens': lambda result: saved.append(copy.deepcopy(result)),
                'fetch_changes': lambda *args: (txs, [], [], 'after'),
                'clean_description': lambda *args: 'Same merchant',
                'get_or_create_month_tab': lambda *args: ({'sheetId': 7}, {}),
                'find_table_in_tab': lambda *args: table,
                'maybe_send_monthly_summary': lambda *args: None,
                'TrackedWriter': FailOnceWriter, 'GEMINI_PACING': 0,
            }.items():
                stack.enter_context(patch.object(pipeline, name, value))
            stack.enter_context(patch('clients.tracked_writer.sheets._find_empty_data_row',
                                      side_effect=lambda *a: len(sheet.records) + 3))
            with self.assertRaises(SystemExit):
                pipeline.main()
            self.assertEqual(saved, [])
            self.assertEqual(tokens['bank']['cursor'], 'before')
            self.assertEqual(len(sheet.records), 2)
            pipeline.main()
            self.assertEqual(len(sheet.records), 2)
            self.assertEqual(sheet.writes, 2)
            self.assertEqual(saved[0]['bank']['cursor'], 'after')

    def test_atomic_cursor_replace_failure_keeps_original(self):
        import tempfile
        from pathlib import Path
        from clients import plaid_client
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'access_tokens.json'
            target.write_text('{"cursor": "before"}')
            with patch.object(plaid_client, 'TOKENS_FILE', target):
                with patch('clients.plaid_client.os.replace', side_effect=OSError('interrupted')):
                    with self.assertRaises(OSError):
                        plaid_client.save_tokens({'cursor': 'after'})
            self.assertEqual(json.loads(target.read_text()), {'cursor': 'before'})
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_untracked_modified_transaction_does_not_append_or_advance_cursor(self):
        import pipeline
        from contextlib import ExitStack
        from unittest.mock import Mock
        tx = Tx(transaction_id='legacy-id', account_id='account', date='2026-11-07',
                amount=10.0, name='Historical row', pending=False, pending_transaction_id=None)
        writer = Mock()
        writer.lookup.return_value = None
        save = Mock()
        config = {'sheet': {'spreadsheet_id': 'fake'}, 'pipeline': {'track_transaction_ids': True},
                  'account_routing': {'account': {'bank': 'bank', 'income_table_prefix': 'Card_',
                                                  'outflow_table_prefix': 'Card_'}}}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(pipeline.CONFIG, config, clear=True))
            for name, value in {
                'get_client': lambda **_: None, 'get_credentials': lambda: None,
                'build': lambda *a, **k: None,
                'load_tokens': lambda: {'bank': {'access_token': 'fake', 'item_id': 'item', 'cursor': 'old'}},
                'save_tokens': save, 'fetch_changes': lambda *args: ([], [tx], [], 'next'),
                'TrackedWriter': lambda *args: writer,
                'maybe_send_monthly_summary': lambda *args: None,
            }.items():
                stack.enter_context(patch.object(pipeline, name, value))
            with self.assertRaises(SystemExit):
                pipeline.main()
        writer.upsert.assert_not_called()
        save.assert_not_called()
