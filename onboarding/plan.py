"""Produce an account-routing plan offline. Never writes secrets or cursors."""
import argparse
from datetime import date
import json
import re


def make_plan(account_id, start_date, nickname='amex', prefix='Amex_'):
    date.fromisoformat(start_date)
    if not account_id or not re.fullmatch(r'[a-z][a-z0-9_]*', nickname):
        raise ValueError('Account ID and a simple bank nickname are required')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', prefix):
        raise ValueError('Use a simple unique table prefix')
    return {'account_routing': {account_id: {'bank': nickname, 'income_table_prefix': prefix,
                                           'outflow_table_prefix': prefix}},
            'onboarding': {'start_date': start_date, 'status': 'not_activated',
                           'requires': ['current-month and Template tables',
                                        'reviewed first import and initialized cursor',
                                        'private credential/state merge preserving existing banks']}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--start-date', required=True)
    args = parser.parse_args()
    print(json.dumps(make_plan(args.account_id, args.start_date), indent=2))
