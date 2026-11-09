"""Fetch a complete Plaid update before changing either Sheets or the cursor."""
from plaid.model.transactions_sync_request import TransactionsSyncRequest


def fetch_changes(client, access_token, cursor=""):
    added, modified, removed = [], [], []
    seen = set()
    while True:
        response = client.transactions_sync(TransactionsSyncRequest(
            access_token=access_token, cursor=cursor))
        added.extend(response.added)
        modified.extend(response.modified)
        removed.extend(response.removed)
        next_cursor = response.next_cursor
        if not response.has_more:
            return added, modified, removed, next_cursor
        if next_cursor == cursor or next_cursor in seen:
            raise RuntimeError("Sync pagination did not advance")
        seen.add(next_cursor)
        cursor = next_cursor


def apply_changes(added, modified, removed, upsert, remove):
    """Posted-only import. A failure leaves the caller's cursor unchanged.

    Upsert/remove must be idempotent. Fetch pagination completes before this
    function, so a pagination mutation cannot leave partially applied pages.
    """
    latest = {t.transaction_id: t for t in [*added, *modified]}
    removed_ids = {t.transaction_id for t in removed}
    # Plaid may return newest first; match the Sheet's oldest-first layout.
    for tx in sorted(latest.values(), key=lambda t: str(getattr(t, 'date', ''))):
        if tx.transaction_id in removed_ids:
            continue
        pending_id = getattr(tx, "pending_transaction_id", None)
        if pending_id:
            remove(pending_id)
        if getattr(tx, "pending", False):
            remove(tx.transaction_id)
            continue
        upsert(tx)
    for transaction_id in removed_ids:
        remove(transaction_id)
