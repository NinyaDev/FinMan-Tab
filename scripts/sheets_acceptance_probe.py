"""Replay real Sheets readbacks through the production writer to emit its requests.

Transport-only harness: reads are adapted from a bounded Google snapshot; writes
are emitted for execution through the connected Google Drive tools, then read
back before the next stage. Does not load credentials or production config.
The native developerMetadata.search HTTP endpoint is not exercised here.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import socket
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
config = ModuleType('config')
config.CONFIG = {'sheet': {'spreadsheet_id': 'TEST_ONLY'}, 'tab_strategy': {'naming': 'spanish'}}
sys.modules['config'] = config

def blocked(*args, **kwargs):
    raise AssertionError('Use connector transport; direct network is disabled')
socket.socket.connect = blocked
socket.create_connection = blocked

from clients.tracked_writer import TrackedWriter, ReconciliationRequired
from viewer.data import read_sheet, normalize, summary


class Request:
    def __init__(self, fn):
        self.fn = fn
    def execute(self):
        return self.fn()


class SnapshotTransport:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.spreadsheet_id = snapshot['spreadsheetId']
        self.requests = []
        self.lose_response = False
        self.cells, self.records = {}, list(snapshot.get('developerMetadata', []))
        for sheet in snapshot['sheets']:
            sid = sheet['properties']['sheetId']
            self.records.extend(sheet.get('developerMetadata', []))
            for data in sheet.get('data', []):
                for i, row in enumerate(data.get('rowData', [])):
                    for j, cell in enumerate(row.get('values', [])):
                        value = cell.get('effectiveValue', cell.get('userEnteredValue', {}))
                        self.cells[(sid, data.get('startRow', 0) + i, data.get('startColumn', 0) + j)] = next(iter(value.values()), '')
                for row in data.get('rowMetadata', []):
                    self.records.extend(row.get('developerMetadata', []))
        self.records = list({r['metadataId']: r for r in self.records}.values())

    def spreadsheets(self): return self
    def developerMetadata(self): return self
    def values(self): return self

    def guard(self, kwargs):
        assert kwargs.get('spreadsheetId') == self.spreadsheet_id, 'Unexpected target'

    def get(self, **kwargs):
        self.guard(kwargs)
        if 'range' not in kwargs:
            return Request(lambda: copy.deepcopy(self.snapshot))
        return Request(lambda: {'values': self.a1(kwargs['range'])})

    def grid(self, sid, r1, r2, c1, c2):
        rows = []
        for r in range(r1, r2):
            row = [self.cells.get((sid, r, c), '') for c in range(c1, c2)]
            while row and row[-1] == '': row.pop()
            rows.append(row)
        while rows and not rows[-1]: rows.pop()
        return rows

    def a1(self, value):
        match = re.fullmatch(r"'?([^']+)'?!([A-Z]+)([0-9]+):([A-Z]+)([0-9]+)", value)
        assert match, value
        name, c1, r1, c2, r2 = match.groups()
        def col(s):
            n = 0
            for ch in s: n = n * 26 + ord(ch) - 64
            return n - 1
        sid = next(s['properties']['sheetId'] for s in self.snapshot['sheets'] if s['properties']['title'] == name)
        return self.grid(sid, int(r1)-1, int(r2), col(c1), col(c2)+1)

    def search(self, **kwargs):
        self.guard(kwargs)
        lookup = kwargs['body']['dataFilters'][0]['developerMetadataLookup']
        def match(record):
            if 'metadataKey' in lookup:
                return record['metadataKey'] == lookup['metadataKey']
            return record['location'] == lookup['metadataLocation']
        return Request(lambda: {'matchedDeveloperMetadata': [{'developerMetadata': r} for r in self.records if match(r)]})

    def batchGetByDataFilter(self, **kwargs):
        self.guard(kwargs)
        g = kwargs['body']['dataFilters'][0]['gridRange']
        return Request(lambda: {'valueRanges': [{'valueRange': {'values': self.grid(
            g['sheetId'], g['startRowIndex'], g['endRowIndex'], g['startColumnIndex'], g['endColumnIndex'])}}]})

    def batchGet(self, **kwargs):
        self.guard(kwargs)
        return Request(lambda: {'valueRanges': [{'values': self.a1(r)} for r in kwargs['ranges']]})

    def batchUpdate(self, **kwargs):
        self.guard(kwargs)
        def write():
            self.requests.extend(kwargs['body']['requests'])
            if self.lose_response:
                raise TimeoutError('Simulated lost response; emitted request must be committed and read back')
            return {}
        return Request(write)


def run(stage, snapshot):
    transport = SnapshotTransport(snapshot)
    sheet = next(s for s in snapshot['sheets'] if s['properties']['title'] == 'Noviembre')
    tables = {t['name']: t for t in sheet['tables']}
    sid = sheet['properties']['sheetId']
    w = TrackedWriter(transport, snapshot['spreadsheetId'], 'SYNTHETIC_ONEPAY_ITEM')
    s = TrackedWriter(transport, snapshot['spreadsheetId'], 'SYNTHETIC_SOFI_ITEM')
    def tx(key, name, amount):
        return SimpleNamespace(transaction_id=key, name=name, amount=amount, date='2026-11-07',
                               personal_finance_category={'primary':'GENERAL_MERCHANDISE','confidence_level':'HIGH'})
    purchase = tx('synthetic-purchase', 'Synthetic groceries', 25.5)
    credit = tx('synthetic-reward', 'Synthetic reward credit', -10)
    evidence = []
    onepay = lambda t, name=None: w.upsert(t, tables['OnePay_Noviembre'], name or t.name, t.amount, 'OnePay_')
    if stage == 'initial':
        assert len(transport.records) == 0
        transport.lose_response = True
        try:
            onepay(purchase)
            raise AssertionError('Expected timeout')
        except TimeoutError:
            evidence.append('Lost response injected after request generation; inspect committed readback before retry')
    elif stage == 'retry_credit':
        assert transport.grid(sid, 1, 2, 0, 2) == [['Synthetic groceries', 25.5]]
        assert len(transport.records) == 1
        assert onepay(purchase) is False
        assert not transport.requests, 'Retry generated a duplicate write'
        evidence.append('Retry found real persisted identity and emitted zero duplicate writes')
        onepay(credit)
        other = tx('synthetic-sofi', 'Synthetic SoFi purchase', 17.25)
        s.upsert(other, tables['Sofi_Noviembre'], other.name, other.amount, 'Sofi_')
    elif stage == 'user_edit':
        assert transport.grid(sid, 1, 3, 0, 2) == [['Synthetic groceries',25.5],['Synthetic reward credit',-10]]
        assert transport.grid(sid, 1, 2, 3, 5) == [['Synthetic SoFi purchase',17.25]]
        assert len(transport.records) == 3
        evidence.append('Negative credit and adjacent account both persisted correctly; visible tables remain two columns')
        transport.requests.append(w._cells(sid, 1, 0, 'My edited groceries', 25.5))
    elif stage == 'bank_update':
        purchase.amount = 27.5
        onepay(purchase)
        evidence.append('Generated bank amount update while preserving edited description')
    elif stage == 'conflict_setup':
        assert transport.grid(sid,1,2,0,2) == [['My edited groceries',27.5]]
        purchase.amount = 27.5
        assert onepay(purchase) is False
        assert not transport.requests
        evidence.append('Modified bank amount persisted, Sheet description preserved, replay emits no writes')
        transport.requests.append(w._cells(sid, 1, 0, 'My edited groceries', 28.5))
    elif stage == 'conflict_check':
        purchase.amount = 30.5
        try:
            onepay(purchase)
            raise AssertionError('Expected conflict')
        except ReconciliationRequired:
            pass
        assert not transport.requests
        assert transport.grid(sid,1,2,0,2) == [['My edited groceries',28.5]]
        evidence.append('Conflicting manual amount preserved; no write generated')
        # Keep the manual edit, remove only the separate unedited credit.
        w.remove(credit.transaction_id)
    elif stage == 'remove_check':
        assert transport.grid(sid,2,3,0,2) == [['[Removed by bank] Synthetic reward credit',0]]
        w.remove(credit.transaction_id)
        assert not transport.requests
        assert len(transport.records) == 3
        evidence.append('Removed credit neutralized with identity retained; repeated removal emits no writes')
        transport.requests.append({'insertDimension': {'range': {'sheetId':sid,'dimension':'ROWS','startIndex':1,'endIndex':2},'inheritFromBefore':False}})
    elif stage == 'row_move':
        purchase.amount = 27.5
        assert onepay(purchase) is False
        assert transport.grid(sid,2,3,0,2) == [['My edited groceries',28.5]]
        assert w.lookup(purchase.transaction_id)['location']['dimensionRange']['startIndex'] == 2
        evidence.append('Google moved metadata with inserted row; retry retained manual edits without duplication')
        viewer_config = {'spreadsheet_id':snapshot['spreadsheetId'],'months':{'Noviembre':'2026-11'},
                         'accounts':[{'name':'OnePay','prefix':'OnePay_'},{'name':'SoFi','prefix':'Sofi_'}]}
        result = normalize(read_sheet(transport, viewer_config))
        assert len(result['transactions']) == 2, result
        assert summary(result['transactions'])['spending'] == 4575, result
        assert any(t['description']=='My edited groceries' and t['cents']==2850 for t in result['transactions'])
        evidence.append('Viewer reads current Sheet values + hidden metadata; $45.75 combined, removed row excluded')
    else:
        raise ValueError('Unknown stage')
    return {'stage':stage,'spreadsheet_id':snapshot['spreadsheetId'], 'requests':transport.requests,'evidence':evidence}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--stage',required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.stage,json.loads(args.snapshot.read_text()))))
