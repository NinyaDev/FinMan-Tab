"""Read-only row matching for migrating an existing bank to tracked imports.

No amount-only match is activated automatically: descriptions may be manual edits.
The caller must review candidates and preserve the existing production cursor.
"""
from collections import defaultdict
from decimal import Decimal
import re


def cents(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal('1')))


def label(value):
    return re.sub(r'\W+', '', str(value).casefold())


def match_rows(rows, transactions):
    """Group within bank/account table and month; never match across directions."""
    row_groups, tx_groups = defaultdict(list), defaultdict(list)
    for row in rows:
        row_groups[(row['prefix'], row['month'], cents(row['amount']))].append(row)
    for tx in transactions:
        tx_groups[(tx['prefix'], tx['month'], cents(tx['amount']))].append(tx)
    matches, review = [], []
    for key in sorted(set(row_groups) | set(tx_groups)):
        remaining_rows = list(row_groups[key])
        remaining_txs = list(tx_groups[key])
        for row in list(remaining_rows):
            candidates = [t for t in remaining_txs if label(row['description']) in
                          {label(t['name']), label(t.get('merchant_name') or t['name'])}]
            same_rows = [r for r in remaining_rows if label(r['description']) == label(row['description'])]
            if len(candidates) == len(same_rows) == 1:
                tx = candidates[0]
                matches.append({'row': row, 'transaction': tx, 'basis': 'unique_name_and_amount'})
                remaining_rows.remove(row)
                remaining_txs.remove(tx)
        if remaining_rows or remaining_txs:
            review.append({'prefix': key[0], 'month': key[1], 'amount': key[2] / 100,
                           'rows': remaining_rows, 'transactions': remaining_txs,
                           'reason': 'edited_description' if len(remaining_rows) == len(remaining_txs) == 1
                           else 'ambiguous_or_unmatched'})
    return {'matches': matches, 'review': review}
