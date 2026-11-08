"""Plaid Link in a separate output directory; Sandbox is the default.

python -m onboarding.app --output-dir /private/tmp/finance-link-demo
Supply PLAID_CLIENT_ID and PLAID_SANDBOX_SECRET explicitly in the environment.
This module never loads .env or the root access_tokens.json.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, render_template, request, session


def save_link(directory, result):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Exclusive creation: a second link can never replace another bank token.
    path = directory / ('connection-' + secrets.token_hex(8) + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(result, handle, indent=2)
    return path


def create_app(client, output_dir, environment='sandbox', redirect_uri=None):
    if environment not in ('sandbox', 'production'):
        raise ValueError('Unsupported Plaid environment')
    if environment == 'production' and (not redirect_uri or urlparse(redirect_uri).scheme != 'https'):
        raise ValueError('Production onboarding requires an allowlisted HTTPS redirect URI')
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=environment == 'production')

    @app.before_request
    def csrf():
        if request.method == 'POST':
            token = session.get('csrf')
            if not token or not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), token):
                abort(403)

    @app.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/')
    def home():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(24)
        return render_template('link.html', csrf=session['csrf'], environment=environment)

    @app.post('/link-token')
    def link_token():
        from plaid.model.link_token_create_request import LinkTokenCreateRequest
        from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
        from plaid.model.products import Products
        from plaid.model.country_code import CountryCode
        kwargs = dict(products=[Products('transactions')], client_name='Finance Manager',
                      country_codes=[CountryCode('US')], language='en',
                      user=LinkTokenCreateRequestUser(client_user_id='finance-manager-owner'))
        if redirect_uri:
            kwargs['redirect_uri'] = redirect_uri
        try:
            result = client.link_token_create(LinkTokenCreateRequest(**kwargs))
            return jsonify(link_token=result['link_token'])
        except Exception:
            return jsonify(error='Unable to start Link. Check environment and OAuth access.'), 502

    @app.post('/exchange')
    def exchange():
        from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
        from plaid.model.accounts_get_request import AccountsGetRequest
        body = request.get_json(silent=True) or {}
        token = body.get('public_token')
        if not isinstance(token, str) or not token:
            return jsonify(error='Missing Link result'), 400
        try:
            result = client.item_public_token_exchange(ItemPublicTokenExchangeRequest(public_token=token))
            # Save the connection before /accounts/get so an API failure cannot
            # lose a successfully exchanged, one-use public token.
            save_link(output_dir, {'environment': environment, 'access_token': result['access_token'],
                                   'item_id': result['item_id']})
            session['linked'] = True
            accounts = client.accounts_get(AccountsGetRequest(access_token=result['access_token']))
            return jsonify(accounts=[{'account_id': a.account_id, 'name': a.name,
                                      'mask': a.mask, 'type': str(a.type)} for a in accounts.accounts])
        except Exception:
            return jsonify(error='Connection step failed. Check the isolated output before linking again.'), 502
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--environment', choices=('sandbox', 'production'), default='sandbox')
    parser.add_argument('--redirect-uri')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if args.environment == 'production' and (not args.redirect_uri or urlparse(args.redirect_uri).scheme != 'https'):
        parser.error('Production requires an allowlisted HTTPS redirect URI and HTTPS frontend')
    from plaid.api import plaid_api
    from plaid.api_client import ApiClient
    from plaid.configuration import Configuration
    client_id = os.environ.get('PLAID_CLIENT_ID')
    secret = os.environ.get('PLAID_' + args.environment.upper() + '_SECRET')
    if not client_id or not secret:
        parser.error('Supply explicit Plaid credentials in the environment; .env is not read')
    client = plaid_api.PlaidApi(ApiClient(Configuration(
        host='https://' + args.environment + '.plaid.com',
        api_key={'clientId': client_id, 'secret': secret})))
    create_app(client, args.output_dir, args.environment, args.redirect_uri).run(
        host='127.0.0.1', port=args.port, debug=False)


if __name__ == '__main__':
    main()
