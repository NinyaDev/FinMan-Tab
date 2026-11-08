# Sync tracking, Amex, and the Sheets viewer

The optional tracked writer and read-only viewer have synthetic live Google
Sheets validation. See [validation evidence](live-sheet-validation.md) for
results, the metadata bug fix, and transport limits. Personal deployment state
and onboarding records belong in the private runner, not this public guide.

## Offline verification

From the repository root:

```sh
.venv/bin/python scripts/test_offline.py
```

This runner copies only public code and synthetic fixtures to a temporary
folder, supplies the example configuration and a dummy Gemini key, strips
credentials from the subprocess environment, and blocks socket connections.
It never copies `.env`, private configuration, tokens, or migration state.
Do not run the existing CI command `cp config.example.yaml config.yaml` locally;
it would replace personal configuration. No production pipeline is executed by
the tests; its orchestration is exercised with injected fakes.

## Retry-safe writes in existing card tables

`pipeline.track_transaction_ids: true` enables the new path. The default is
false; enabling it is a later deployment step, not part of testing.
Alternatively, `pipeline.tracked_banks: [amex]` opts in only the new bank.
`pipeline.bank_start_dates: {amex: "2026-11-01"}` excludes older transactions
and later modifications to transactions outside that import window.

- Plaid Item ID + transaction ID become a stable hashed Sheets metadata key.
- The row values and its developer metadata are committed in one Sheets
  `batchUpdate`. New writes use literal string cells, not executable formulas.
- The existing bank cursor advances only after all changes succeed. Cursor
  files are replaced atomically. Their existing private encrypted storage stays
  authoritative; there is no second competing local cursor database.
- After a partial failure or lost API response, the retry searches for the ID
  before writing. Already-written transactions are not inserted again.
- Added, modified, and removed events are fetched through every page before
  applying any. Pagination errors leave the cursor unchanged for a new run.
- Only posted transactions are imported. Tracked pending entries are removed
  when a posted replacement arrives. Existing untracked historical rows are
  never guessed or automatically deleted.
- Description edits survive retries and amount updates. An amount conflict,
  changed date/table, edited removal, or reused tracked row stops that bank for
  reconciliation. Removed records get a zero-amount tombstone, preserving ID.
- A modified historical transaction without row identity stops for reconciliation
  instead of appending a second copy. Untracked removals leave historical rows
  untouched; they still need manual reconciliation.
- Negative rewards/refunds are preserved if supplied by Plaid. This does not
  establish whether OnePay supplies any particular reward transaction.
- A full tracked table stops safely instead of using the legacy extender,
  which does not move the total row. Expand the table with its total preserved
  before resuming; the failed run leaves that bank's cursor unchanged.

Constraints: serialize runners, as the private workflow already does. Do not
sort/edit tables during sync. Metadata is attached to whole rows plus a stored
column offset: whole-row insertions are supported, but sorting just one card's
cells, moving columns, or moving tables needs reconciliation. Do not remove
metadata. Sheets metadata has finite quotas; this design needs a capacity review
before years of history are imported. It intentionally fails instead of
continuing without identity protection if metadata cannot be saved.

There is no automatic historical deduplication. Before enabling tracking,
reconcile partial writes since the latest saved production cursor in a disposable
copy. Do not reset the cursor to backfill old rows; those rows have no IDs yet.
Manual adjustments remain ordinary Sheet rows and are never imported into Plaid.

## Amex onboarding

A new isolated helper lives in `onboarding/`. The old `setup/link_banks.py` is not
used: it writes the root token file and does not implement the new OAuth resume
flow. The new helper loads no `.env`, defaults to Sandbox, and writes unique
0600 connection files in the explicitly selected output folder. It never merges
with existing banks or calls Transactions Sync.

For a future Sandbox exercise, supply explicit Sandbox credentials in the shell
and run:

```sh
.venv/bin/python -m onboarding.app --output-dir /private/tmp/finance-link-sandbox
```

Open localhost:8766. OAuth redirects resume Link with the original link token.
POST requests require the per-session CSRF token. Token exchange saves the
connection before account discovery, so an accounts API error does not lose the
one-use token exchange. Keep the helper private; it is a local setup tool.

For real Amex: verify Production/OAuth access in the Plaid dashboard, configure
an allowlisted HTTPS redirect and HTTPS frontend to this loopback service, then
explicitly select `--environment production --redirect-uri https://YOUR-HOST/`.
You must complete Amex login and consent yourself. This was not run here.

After identifying the card, preview routing without changing any files:

```sh
.venv/bin/python -m onboarding.plan --account-id EXAMPLE_ACCOUNT --start-date 2026-11-01
```

The Amex current-month and Template tables must exist, plus earlier month tables
if importing older transactions. Confirm the first import date.

**Production activation is still required:** the private state store rejects a
new bank without a nonempty cursor. Do not simply add Amex to Secrets. A reviewed
one-time first import must obtain and persist Amex's cursor with its own tracked
writes, then merge that connection/cursor into the latest encrypted private state
without changing existing banks or summary history. Never seed production with a
Sandbox cursor or a cursor advanced against a disposable Sheet. Pause scheduling
while performing this coordinated activation. No state migration is included in
the default linking command.

## Margin viewer

```sh
.venv/bin/python -m viewer.app
```

Open http://127.0.0.1:8765. Default data is entirely synthetic and visibly marked
as a sample. Card and month filters, search, mobile layout, and net purchase totals
work without bank or Google credentials. There are no transaction-edit routes.

The live adapter reads existing named tables directly from Sheets on each page
request. It reads the description and amount columns, skipping header/footer
rows. An explicit tab-to-year/month map prevents guessing a year from `Noviembre`.
Visible tables stay **Description / Amount only**. The reader obtains dates and
classifications from invisible row metadata written by the opt-in tracked sync.
The writer records confident purchase, transfer, and loan-payment classifications
from structured Plaid categories. It never guesses a credit's meaning from its
merchant text or sign. Existing untracked rows and uncertain credits are marked
for review; no history or classification migration runs automatically.

Optional overrides belong in a separate tab, such as `Viewer classifications`,
which can be hidden. Its columns are `Transaction key`, `Type`, `Date`. Configure
`classifications_range: "'Viewer classifications'!A2:C"` only after that tab exists.
Leave it null to use tracking metadata alone. Tracked keys are the row's
`finance_tx_…` metadata keys. Untracked/manual row keys use
`row:SHEET_ID:ONE_BASED_ROW:ONE_BASED_COLUMN`; these must be reviewed if rows move.
Do not expose these technical keys in the main card tables. This initial version
reads overrides but does not create/populate that supporting tab automatically.

Types: `purchase`, `refund`, `reward`, `payment`, `transfer`, `income`, `adjustment`,
`ignore`. Dates are ISO `YYYY-MM-DD` or blank. Overrides take precedence over
bank classifications. Mark carryovers `ignore`; they then disappear from the
viewer but remain in the Sheet. Alternatively use `exclude_rows` (absolute,
1-based Sheet row numbers, updated after moving rows). Invalid/duplicate override
keys fail the snapshot rather than silently picking a classification.

Purchases minus refunds form spending. Rewards are separate; payments and
transfers are excluded. Unclassified/adjustment entries are explicitly excluded
from partial totals. Description/amount edits always come from current Sheet
cells, not stale metadata. A sign-changing edit that contradicts the category
requires review. An unavailable snapshot shows an error rather than sample data.

The compact layout opens on spending, card filters, and transactions. The web
manifest, icon, and mobile tags prepare it for Home Screen/Dock access once
privately hosted over HTTPS. There is no offline financial-data cache and no
native widget. A local address is accessible only on the computer running it.

For a later read-only connection, make a private copy of
`viewer/sheet-config.example.json`. Use a dedicated Google service account shared
as **Viewer** on the chosen Sheet. Do not reuse or refresh production `token.json`.

```sh
.venv/bin/python -m viewer.app \
  --sheet-config /PRIVATE/PATH/sheet-config.json \
  --readonly-credentials /PRIVATE/PATH/service-account.json
```

This explicitly requests the Sheets readonly scope. These commands are setup
instructions; no real connection was made. The app binds to loopback and sets
no-store responses. Phone access needs private HTTPS hosting/access control or a
private network with authenticated transport; this local prototype is not a
publicly deployable finance service. Do not expose Flask's development server.
No native iOS widget is included; the responsive viewer is the first interface.

## Next production review

1. Reconcile a representative duplicate and the OnePay credit, if source records
   are available. No need to block Amex on a provider omission.
2. Review the completed disposable-Sheet validation and remaining transport
   limits. Full private-runner authentication and quota behavior still need checks.
3. Review opt-in deployment and the pinned commit in the private runner.
4. Complete Amex consent, table creation, and coordinated initial state migration.
5. Connect the read-only viewer and decide private phone/laptop hosting.

Sources: Google [developer metadata](https://developers.google.com/workspace/sheets/api/guides/metadata),
[batch updates](https://developers.google.com/workspace/sheets/api/guides/batchupdate),
Plaid [transaction states](https://plaid.com/docs/transactions/transactions-data/),
[OAuth](https://plaid.com/docs/link/oauth/).
