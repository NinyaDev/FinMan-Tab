"""Local-only demo by default: python -m viewer.app. No production imports."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, render_template, request
from viewer.data import normalize, summary, read_sheet

DEMO = Path(__file__).parent / 'demo.json'


def create_app(provider=None):
    app = Flask(__name__)
    provider = provider or (lambda: json.loads(DEMO.read_text()))

    @app.template_filter('money')
    def money(cents):
        return ('−' if cents < 0 else '') + f'${abs(cents) / 100:,.2f}'

    @app.template_filter('month_label')
    def month_label(value):
        try:
            return datetime.strptime(value, '%Y-%m').strftime('%B %Y')
        except ValueError:
            return value

    @app.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get('/')
    def journal():
        try:
            data = normalize(provider())
        except Exception:
            return render_template('unavailable.html'), 503
        months = sorted({t['month'] for t in data['transactions']}, reverse=True)
        month = request.args.get('month', months[0] if months else '')
        all_accounts = sorted({t['account'] for t in data['transactions']})
        account = request.args.get('account', '')
        query = request.args.get('q', '').strip()
        monthly = [t for t in data['transactions'] if t['month'] == month]
        selected = [t for t in monthly if not account or t['account'] == account]
        transactions = [t for t in selected if query.casefold() in t['description'].casefold()]
        transactions.sort(key=lambda t: t['date'], reverse=True)
        cards = [{'name': name, **summary([t for t in monthly if t['account'] == name])}
                 for name in all_accounts]
        url = urlparse(data['sheet_url'])
        sheet_url = data['sheet_url'] if url.scheme == 'https' and url.hostname == 'docs.google.com' else ''
        return render_template('journal.html', data=data, months=months, month=month,
                               account=account, query=query, cards=cards, totals=summary(selected),
                               transactions=transactions, sheet_url=sheet_url)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--sheet-config', type=Path)
    parser.add_argument('--readonly-credentials', type=Path)
    args = parser.parse_args()
    if bool(args.sheet_config) != bool(args.readonly_credentials):
        parser.error('Live read-only mode requires BOTH explicit config and credentials paths')
    provider = None
    if args.sheet_config:
        # Service account must be shared as Viewer, never Editor. This does not
        # use or modify the production Google OAuth token.json.
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        config = json.loads(args.sheet_config.read_text())
        credentials = Credentials.from_service_account_file(
            args.readonly_credentials,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        service = build('sheets', 'v4', credentials=credentials, cache_discovery=False)
        provider = lambda: read_sheet(service, config)
    create_app(provider).run(host='127.0.0.1', port=args.port, debug=False)


if __name__ == '__main__':
    main()
