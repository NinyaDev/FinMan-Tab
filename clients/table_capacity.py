"""Grow two-column transaction tables into empty space, preserving the total."""
import re


def expansion_requests(table, rows, conditional_formats, extra=10):
    """rows contains the current footer followed by the proposed empty space."""
    grid = table['range']
    start, end = grid['startRowIndex'], grid['endRowIndex']
    col, sid = grid['startColumnIndex'], grid['sheetId']
    if grid['endColumnIndex'] != col + 2 or not rows or len(rows[0]) != 2:
        raise ValueError('Expected a two-column table with a total row')
    label, formula = rows[0]
    if not isinstance(label, str) or not isinstance(formula, str) or not re.fullmatch(
            r'=SUM\([^()]+\)', formula, re.IGNORECASE):
        raise ValueError('Custom total requires manual table expansion')
    if any(any(value not in ('', None) for value in row) for row in rows[1:]):
        raise ValueError('Table expansion would overwrite existing cells')
    def letter(index):
        result = ''
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result
    amount_col = letter(col + 2)
    last = end + extra
    footer = dict(sheetId=sid, startRowIndex=last-1, endRowIndex=last,
                  startColumnIndex=col, endColumnIndex=col+2)
    requests = [{'updateTable': {'table': {'tableId': table['tableId'],
                 'range': {**grid, 'endRowIndex': last}}, 'fields': 'range'}},
        {'updateCells': {'range': {**grid, 'startRowIndex': end-1, 'endRowIndex': last-1},
                         'rows': [], 'fields': 'userEnteredValue'}},
        {'updateCells': {'start': {'sheetId':sid,'rowIndex':last-1,'columnIndex':col},
          'rows': [{'values': [{'userEnteredValue':{'stringValue':label}},
                              {'userEnteredValue':{'formulaValue':f'=SUM({amount_col}{start+2}:{amount_col}{last-1})'}}]}],
          'fields':'userEnteredValue'}}]
    # Google may extend conditional ranges when expanding a table. Reset the
    # dedicated total-color rule to exactly one row in the same atomic batch.
    old_footer = {**grid, 'startRowIndex':end-1}
    for index, rule in enumerate(conditional_formats):
        if rule.get('ranges') == [old_footer]:
            requests.append({'updateConditionalFormatRule': {
                'sheetId':sid,'index':index,'rule':{**rule,'ranges':[footer]}}})
    return requests


def expand(service, spreadsheet_id, table, execute, extra=10):
    grid = table['range']
    sid, end = grid['sheetId'], grid['endRowIndex']
    metadata = execute(service.spreadsheets().get(spreadsheetId=spreadsheet_id,
                       fields='sheets(properties,tables,merges,conditionalFormats)'))
    sheet = next(s for s in metadata['sheets'] if s['properties']['sheetId']==sid)
    if end+extra > sheet['properties']['gridProperties']['rowCount']:
        raise ValueError('Add worksheet rows before expanding this table')
    for other in [t['range'] for t in sheet.get('tables',[]) if t['tableId']!=table['tableId']] + sheet.get('merges',[]):
        if (other.get('startRowIndex',0)<end+extra and other.get('endRowIndex',0)>end
                and other.get('startColumnIndex',0)<grid['endColumnIndex']
                and other.get('endColumnIndex',0)>grid['startColumnIndex']):
            raise ValueError('Table expansion intersects another table or merge')
    result = execute(service.spreadsheets().values().batchGetByDataFilter(
        spreadsheetId=spreadsheet_id,body={'dataFilters':[{'gridRange':{
            **grid,'startRowIndex':end-1,'endRowIndex':end+extra}}], 'valueRenderOption':'FORMULA'}))
    rows = result['valueRanges'][0]['valueRange'].get('values',[])
    requests = expansion_requests(table,rows,sheet.get('conditionalFormats',[]),extra)
    # Do not retry an ambiguous commit; the next sync re-reads the table size.
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id,
                                      body={'requests':requests}).execute()
    return {**table,'range':{**grid,'endRowIndex':end+extra}}
