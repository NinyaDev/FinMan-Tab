import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from viewer.app import create_app, DEMO
from viewer.data import normalize, summary, read_sheet, metadata_index
from onboarding.app import create_app as link_app, save_link
from onboarding.plan import make_plan


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads(DEMO.read_text())

    def test_rewards_transfers_and_payments_not_spending(self):
        data = normalize(self.snapshot)
        onepay = [t for t in data['transactions'] if t['account'] == 'OnePay' and t['month'] == '2026-11']
        self.assertEqual(summary(onepay), dict(spending=8689, rewards=1000, income=0, review=0))

    def test_sheet_edits_appear_on_next_request_and_html_is_escaped(self):
        client = create_app(lambda: self.snapshot).test_client()
        self.snapshot['tables'][0]['rows'][0][0] = '<script>private</script>'
        response = client.get('/?account=Amex&month=2026-11')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'&lt;script&gt;private&lt;/script&gt;', response.data)
        self.assertNotIn(b'Groceries \xe2\x80\xa2 weekly shop', response.data)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(client.post('/').status_code, 405)

    def test_search_and_empty_month(self):
        client = create_app(lambda: self.snapshot).test_client()
        self.assertIn(b'Coffee before class', client.get('/?q=Coffee').data)
        self.assertIn(b'No transactions here', client.get('/?month=2025-01').data)

    def test_unknown_credits_require_review_not_guessed_income(self):
        self.snapshot['tables'] = [{'account': 'OnePay', 'month': '2026-11', 'rows': [['Unknown', -10]]}]
        totals = summary(normalize(self.snapshot)['transactions'])
        self.assertEqual(totals, dict(spending=0, rewards=0, income=0, review=1))

    def test_error_does_not_leak_source_or_fall_back_to_demo(self):
        def failed():
            raise ValueError('secret-details')
        response = create_app(failed).test_client().get('/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'secret-details', response.data)
        self.assertNotIn(b'SAMPLE DATA', response.data)

    def test_read_only_adapter_uses_existing_table_boundaries(self):
        calls = []
        class Request:
            def __init__(self, value):
                self.value = value
            def execute(self):
                return self.value
        class Service:
            def spreadsheets(self):
                return self
            def values(self):
                return self
            def get(self, **kwargs):
                calls.append(('get', kwargs))
                return Request({'sheets': [{'properties': {'title': 'Noviembre', 'sheetId': 7}, 'tables': [
                    {'name': 'Amex_Noviembre', 'range': {'startRowIndex': 2, 'endRowIndex': 10,
                                                       'startColumnIndex': 5, 'endColumnIndex': 7}}],
                    'data': [{'startRow': 3, 'rowMetadata': [{'developerMetadata': [{'metadataKey': 'finance_tx_test',
                        'metadataValue': json.dumps({'column': 5, 'prefix': 'Amex_',
                                                     'kind': 'purchase', 'date': '2026-11-01'}),
                        'location': {'dimensionRange': {'sheetId': 7, 'dimension': 'ROWS',
                                                       'startIndex': 3, 'endIndex': 4}}}]}]}]}]})
            def batchGet(self, **kwargs):
                calls.append(('batchGet', kwargs))
                return Request({'valueRanges': [{'values': [['Purchase edited in Sheets', 12]]}]})
        config = {'spreadsheet_id': 'fake', 'months': {'Noviembre': '2026-11'},
                  'accounts': [{'name': 'Amex', 'prefix': 'Amex_'}]}
        snapshot = read_sheet(Service(), config)
        self.assertEqual(calls[1][1]['ranges'], ["'Noviembre'!F4:G9"])
        self.assertTrue(calls[1][1]['includeGridData'])
        self.assertEqual(calls[2][1]['ranges'], ["'Noviembre'!F4:G9"])
        self.assertEqual(summary(normalize(snapshot)['transactions'])['spending'], 1200)

    def test_hidden_override_reclassifies_credit_without_extra_columns(self):
        self.snapshot['tables'] = [{'account': 'OnePay', 'month': '2026-11',
                                   'rows': [['Points redeemed', -10]],
                                   'details': {'0': {'id': 'finance_tx_reward', 'kind': 'unclassified'}}}]
        self.snapshot['overrides'] = {'finance_tx_reward': {'kind': 'reward', 'date': '2026-11-05'}}
        data = normalize(self.snapshot)
        self.assertEqual(summary(data['transactions'])['rewards'], 1000)
        self.assertEqual(data['transactions'][0]['description'], 'Points redeemed')
        self.assertEqual(data['transactions'][0]['date'], '2026-11-05')
        self.assertEqual(len(self.snapshot['tables'][0]['rows'][0]), 2)

    def test_metadata_matches_column_not_only_row(self):
        def record(column, name):
            return {'metadataKey': name, 'metadataValue': json.dumps({'column': column}),
                    'location': {'dimensionRange': {'sheetId': 7, 'dimension': 'ROWS',
                                                   'startIndex': 3, 'endIndex': 4}}}
        index = metadata_index({'developerMetadata': [record(2, 'finance_tx_one'), record(5, 'finance_tx_two')]})
        self.assertEqual(index[(7, 3, 2)]['id'], 'finance_tx_one')
        self.assertEqual(index[(7, 3, 5)]['id'], 'finance_tx_two')

    def test_sign_changing_sheet_edit_is_not_misrepresented_as_purchase(self):
        self.snapshot['tables'][0]['rows'][0][1] = -64.8
        tx = normalize(self.snapshot)['transactions'][0]
        self.assertEqual(tx['kind'], 'unclassified')
        self.assertEqual(tx['cents'], -6480)

    def test_manual_carryover_can_be_hidden_from_viewer(self):
        self.snapshot['tables'][0]['details']['0']['kind'] = 'ignore'
        self.assertNotIn('Dinner with friends', [t['description'] for t in normalize(self.snapshot)['transactions']])

    def test_compact_page_and_home_screen_manifest(self):
        client = create_app(lambda: self.snapshot).test_client()
        page = client.get('/').data
        self.assertNotIn(b'A little clarity', page)
        self.assertIn(b'Monthly spending', page)
        self.assertLess(page.index(b'aria-label="Monthly spending"'), page.index(b'aria-label="Filter by account"'))
        manifest = json.loads(client.get('/static/manifest.webmanifest').data)
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(client.get('/static/icon.svg').status_code, 200)


class OnboardingTests(unittest.TestCase):
    def test_connection_is_separate_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'access_tokens.json'
            original.write_text('existing-state')
            first = save_link(directory, {'access_token': 'fake'})
            second = save_link(directory, {'access_token': 'fake-other'})
            self.assertNotEqual(first, second)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            self.assertEqual(original.read_text(), 'existing-state')

    def test_production_requires_https(self):
        with self.assertRaises(ValueError):
            link_app(None, '/unused', 'production', 'http://localhost:8766/')

    def test_csrf_denies_unrequested_linking(self):
        with tempfile.TemporaryDirectory() as directory:
            client = link_app(None, directory).test_client()
            self.assertEqual(client.post('/exchange', json={'public_token': 'fake'}).status_code, 403)

    def test_plan_contains_no_cursor_or_credentials(self):
        plan = make_plan('fake-amex-account', '2026-11-01')
        self.assertEqual(plan['account_routing']['fake-amex-account']['outflow_table_prefix'], 'Amex_')
        self.assertEqual(plan['onboarding']['status'], 'not_activated')
        self.assertNotIn('access_token', json.dumps(plan))
        self.assertNotIn('cursor', plan)
