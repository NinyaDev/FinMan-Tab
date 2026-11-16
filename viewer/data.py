"""Sheet descriptions/amounts plus invisible tracking data and optional overrides."""
import json
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation

KINDS = {'purchase', 'refund', 'reward', 'payment', 'transfer', 'income', 'adjustment', 'ignore'}


def normalize(snapshot):
    transactions = []
    overrides = snapshot.get('overrides', {})
    for table in snapshot['tables']:
        month = table['month']
        date.fromisoformat(month + '-01')
        details = table.get('details', {})
        for index, row in enumerate(table['rows']):
            if not row or not str(row[0]).strip():
                continue
            description = str(row[0])
            info = details.get(str(index), {})
            if info.get('removed') or description.startswith('[Removed by bank]'):
                continue
            row = list(row) + ['', '']
            try:
                value = Decimal(str(row[1]))
                if not value.is_finite():
                    raise ValueError('Non-finite Sheet amount')
                cents = int((value * 100).quantize(Decimal('1')))
            except (InvalidOperation, ValueError):
                raise ValueError('A transaction amount is not numeric') from None
            key = info.get('id', f"{table['account']}:{month}:{table.get('prefix', '')}:{index}")
            override = overrides.get(key, {})
            kind = override.get('kind') or info.get('kind', 'unclassified')
            kind = kind if kind in KINDS else 'unclassified'
            if (kind == 'purchase' and cents < 0) or (kind in ('refund', 'reward', 'income') and cents > 0):
                kind = 'unclassified'  # A sign-changing edit needs an explicit review.
            day = override.get('date') or info.get('date', '')
            if day:
                date.fromisoformat(day)
                if not day.startswith(month + '-'):
                    raise ValueError('Transaction date does not match its month tab')
            if kind == 'ignore':
                continue
            transactions.append({'id': key, 'account': table['account'], 'month': month,
                                 'date': day, 'description': description,
                                 'cents': cents, 'kind': kind})
    return {'transactions': transactions, 'updated_at': snapshot['updated_at'],
            'demo': snapshot.get('demo', False), 'sheet_url': snapshot.get('sheet_url', '')}


def summary(transactions):
    result = dict(spending=0, rewards=0, income=0, review=0)
    for tx in transactions:
        kind, amount = tx['kind'], tx['cents']
        if kind == 'purchase':
            result['spending'] += abs(amount)
        elif kind == 'refund':
            result['spending'] -= abs(amount)
        elif kind == 'reward':
            result['rewards'] += abs(amount)
        elif kind == 'income':
            result['income'] += abs(amount)
        elif kind in ('unclassified', 'adjustment'):
            result['review'] += 1
    return result


def metadata_index(metadata):
    """Match row tags by sheet AND column, since card tables sit side by side."""
    records = list(metadata.get('developerMetadata', []))
    for sheet in metadata.get('sheets', []):
        records.extend(sheet.get('developerMetadata', []))
        for block in sheet.get('data', []):
            for row in block.get('rowMetadata', []):
                records.extend(row.get('developerMetadata', []))
    index = {}
    for record in records:
        if not record.get('metadataKey', '').startswith('finance_tx_'):
            continue
        state = json.loads(record['metadataValue'])
        location = record['location']['dimensionRange']
        if location['dimension'] != 'ROWS' or location['endIndex'] != location['startIndex'] + 1:
            raise ValueError('Invalid transaction row metadata')
        key = (location['sheetId'], location['startIndex'], state['column'])
        item = {**state, 'id': record['metadataKey']}
        if key in index and index[key] != item:
            raise ValueError('Multiple tracked transactions occupy one cell')
        index[key] = item
    return index


def read_sheet(service, config):
    """Only GETs; visible tables remain Description / Amount.

    Types/dates come from row metadata. Optional classification overrides live
    in a separate (hideable) Sheet tab, never in the transaction table columns.
    """
    spreadsheet_id = config['spreadsheet_id']
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields='sheets(properties,tables)').execute()
    tables, ranges = [], []
    for tab in metadata.get('sheets', []):
        title = tab['properties']['title']
        if title not in config['months']:
            continue
        for table in tab.get('tables', []):
            matches = [a for a in config['accounts'] if table['name'].startswith(a['prefix'])]
            if not matches:
                continue
            if len(matches) != 1:
                raise ValueError('Overlapping viewer table prefixes')
            account = matches[0]
            grid = table['range']
            if grid['endColumnIndex'] - grid['startColumnIndex'] < 2:
                raise ValueError('Viewer needs description and amount columns')
            def column(number):
                result = ''
                while number:
                    number, rem = divmod(number - 1, 26)
                    result = chr(65 + rem) + result
                return result
            first = grid['startRowIndex'] + 2
            last = grid['endRowIndex'] - 1
            if last < first:
                continue
            quoted = title.replace("'", "''")
            ranges.append(f"'{quoted}'!{column(grid['startColumnIndex'] + 1)}{first}:"
                          f"{column(grid['startColumnIndex'] + 2)}{last}")
            tables.append({'account': account['name'], 'prefix': account['prefix'],
                           'month': config['months'][title], 'exclude_rows': account.get('exclude_rows', []),
                           'first_row': first, 'sheet_id': tab['properties']['sheetId'],
                           'column': grid['startColumnIndex']})
    overrides_range = config.get('classifications_range')
    if overrides_range:
        ranges.append(overrides_range)
    overrides = {}
    if ranges:
        # Google returns row metadata in GridData.rowMetadata, not directly
        # on Sheet. Bound this second read to the actual viewer ranges.
        tracking_response = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id, ranges=ranges, includeGridData=True,
            fields='sheets(properties(sheetId),data(startRow,rowMetadata(developerMetadata)))').execute()
        tracking = metadata_index(tracking_response)
        result = service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id, ranges=ranges,
            valueRenderOption='UNFORMATTED_VALUE', dateTimeRenderOption='FORMATTED_STRING').execute()
        values = result.get('valueRanges', [])
        if len(values) != len(ranges):
            raise ValueError('Incomplete Sheet snapshot')
        for table, value in zip(tables, values):
            table['rows'], table['details'] = [], {}
            for i, row in enumerate(value.get('values', [])):
                row_number = i + table['first_row']
                if row_number in table['exclude_rows']:
                    continue
                details = tracking.get((table['sheet_id'], row_number - 1, table['column']), {})
                if details and details['prefix'] != table['prefix']:
                    raise ValueError('Transaction metadata no longer matches the table')
                details = dict(details)
                details.setdefault('id', f"row:{table['sheet_id']}:{row_number}:{table['column'] + 1}")
                table['details'][str(len(table['rows']))] = details
                table['rows'].append(row)
        if overrides_range:
            for row in values[-1].get('values', []):
                if not row or not str(row[0]).strip():
                    continue
                key, kind, day = (list(row) + ['', '', ''])[:3]
                key, kind, day = str(key).strip(), str(kind).strip().lower(), str(day).strip()
                if key in overrides or (kind and kind not in KINDS):
                    raise ValueError('Duplicate or invalid classification override')
                overrides[key] = {'kind': kind, 'date': day}
    return {'demo': False, 'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'sheet_url': f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit',
            'tables': tables, 'overrides': overrides}
