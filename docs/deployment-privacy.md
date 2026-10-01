# Deployment privacy

Keep production execution in a private repository. Public Actions runs must use synthetic data and no production secrets. The private workflow should check out a reviewed commit of this repository with persisted checkout credentials disabled for the public source.

Store Google, Plaid, and Gemini credentials in repository secrets. Never put tokens, account configuration, or summary history in Actions caches or public artifacts. Preserve bank cursors and summary history in durable encrypted private storage, with the encryption key stored separately as a secret. Serialize production runs and save state even after partial pipeline failures. Missing or invalid state must fail closed rather than start a new bank sync.

Logs should contain counts and operation status, not transaction descriptions, amounts, account identifiers, raw API exception bodies, or OAuth tokens. Production logs are private even with this reduced logging.

When migrating, disable public scheduling, preserve the latest state, remove old sensitive logs and caches, and rotate potentially exposed credentials. Keep Plaid cursors associated with the same bank Item when rotating its token. Do not reset cursors during credential rotation.

The current pipeline writes transactions before saving a bank cursor. A partial failure can replay already-written rows. Reconcile those entries before retrying; cursor preservation is not transaction-level deduplication.
