"""Opt-in transaction identity for existing two-column card tables.

Row contents and identity are saved in ONE Sheets batchUpdate. A lost response
is recovered by searching metadata on the next run, not blindly appending.
Requires serialized writers. Do not edit/sort the Sheet during a sync.
"""
import hashlib
import json

from clients import sheets_writer as sheets
from clients.transaction_kind import transaction_kind


class ReconciliationRequired(RuntimeError):
    pass


def identity(item_id, transaction_id):
    return "finance_tx_" + hashlib.sha256(
        (item_id + ":" + transaction_id).encode()).hexdigest()


class TrackedWriter:
    def __init__(self, service, spreadsheet_id, item_id):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.item_id = item_id

    def lookup(self, transaction_id):
        result = sheets._execute_with_backoff(
            self.service.spreadsheets().developerMetadata().search(
                spreadsheetId=self.spreadsheet_id,
                body={"dataFilters": [{"developerMetadataLookup": {
                    "metadataKey": identity(self.item_id, transaction_id)}}]}))
        matches = result.get("matchedDeveloperMetadata", [])
        if len(matches) > 1:
            raise ReconciliationRequired("Duplicate transaction metadata")
        return matches[0]["developerMetadata"] if matches else None

    def _commit(self, requests):
        # NEVER use the generic HTTP retry helper here: a successful commit
        # whose response was lost must be looked up again on the next run.
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests}).execute()

    def _cells(self, sheet_id, row, col, description, amount):
        return {"updateCells": {
            "start": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col},
            "rows": [{"values": [
                {"userEnteredValue": {"stringValue": description}},
                {"userEnteredValue": {"numberValue": amount}}]}],
            "fields": "userEnteredValue"}}

    def _read(self, metadata, state):
        location = metadata["location"]["dimensionRange"]
        response = sheets._execute_with_backoff(
            self.service.spreadsheets().values().batchGetByDataFilter(
                spreadsheetId=self.spreadsheet_id, body={
                    "dataFilters": [{"gridRange": {
                        "sheetId": location["sheetId"],
                        "startRowIndex": location["startIndex"],
                        "endRowIndex": location["startIndex"] + 1,
                        "startColumnIndex": state["column"],
                        "endColumnIndex": state["column"] + 2}}],
                    "valueRenderOption": "UNFORMATTED_VALUE"}))
        rows = response["valueRanges"][0]["valueRange"].get("values", [])
        values = rows[0] if rows else []
        return (values + ["", ""])[:2]

    def _metadata_update(self, metadata, state):
        return {"updateDeveloperMetadata": {
            "dataFilters": [{"developerMetadataLookup": {
                "metadataId": metadata["metadataId"]}}],
            "developerMetadata": {"metadataValue": json.dumps(state)},
            "fields": "metadataValue"}}

    def upsert(self, tx, table, description, amount, prefix):
        metadata = self.lookup(tx.transaction_id)
        kind = transaction_kind(tx)
        if metadata:
            state = json.loads(metadata["metadataValue"])
            if state["date"] != str(tx.date) or state["prefix"] != prefix:
                raise ReconciliationRequired("Transaction changed date or table")
            if state.get("removed"):
                raise ReconciliationRequired("Removed transaction reappeared")
            # Replayed additions never overwrite the user's Sheet edits.
            if state["source_amount"] == tx.amount and state["source_name"] == tx.name:
                if state.get("kind", "unclassified") != kind:
                    state["kind"] = kind
                    self._commit([self._metadata_update(metadata, state)])
                return False
            current = self._read(metadata, state)
            if current[1] != state["written_amount"]:
                raise ReconciliationRequired("Bank update conflicts with edited amount")
            # Description edits survive subsequent bank updates.
            final_description = description if current[0] == state["written_description"] else current[0]
            loc = metadata["location"]["dimensionRange"]
            state.update(source_amount=tx.amount, source_name=tx.name,
                         written_amount=amount, written_description=final_description, kind=kind)
            self._commit([
                self._cells(loc["sheetId"], loc["startIndex"], state["column"], final_description, amount),
                self._metadata_update(metadata, state)])
            return True

        row = sheets._find_empty_data_row(self.service, self.spreadsheet_id, table)
        if row is None:
            # The legacy extender enlarges the range without moving the total
            # row. Refuse rather than turn a footer into a transaction or let
            # the viewer count it. Table expansion must preserve the footer.
            raise ReconciliationRequired("Table is full; expand it while preserving its total row")
        col = table["range"]["startColumnIndex"]
        location = {"dimensionRange": {
            "sheetId": table["range"]["sheetId"], "dimension": "ROWS",
            "startIndex": row - 1, "endIndex": row}}
        occupied = self._read({"location": location}, {"column": col})
        if occupied != ["", ""]:
            raise ReconciliationRequired("Candidate row contains user data")
        prior_records = sheets._execute_with_backoff(
            self.service.spreadsheets().developerMetadata().search(
                spreadsheetId=self.spreadsheet_id, body={"dataFilters": [{
                    "developerMetadataLookup": {"metadataLocation": location,
                                                "locationMatchingStrategy": "EXACT_LOCATION"}}]}))
        for match in prior_records.get("matchedDeveloperMetadata", []):
            record = match["developerMetadata"]
            if record.get("metadataKey", "").startswith("finance_tx_"):
                if json.loads(record["metadataValue"])["column"] == col:
                    raise ReconciliationRequired("Empty row still belongs to a tracked transaction")
        state = {"column": col, "date": str(tx.date), "prefix": prefix, "kind": kind,
                 "source_amount": tx.amount, "source_name": tx.name,
                 "written_amount": amount, "written_description": description}
        record = {"metadataKey": identity(self.item_id, tx.transaction_id),
                  "metadataValue": json.dumps(state), "visibility": "DOCUMENT",
                  "location": location}
        self._commit([self._cells(table["range"]["sheetId"], row - 1, col, description, amount),
                      {"createDeveloperMetadata": {"developerMetadata": record}}])
        return True

    def remove(self, transaction_id):
        metadata = self.lookup(transaction_id)
        if not metadata:
            return  # Historical untracked rows are never guessed or deleted.
        state = json.loads(metadata["metadataValue"])
        if state.get("removed"):
            return
        current = self._read(metadata, state)
        if current != [state["written_description"], state["written_amount"]]:
            raise ReconciliationRequired("Removed transaction has user edits")
        loc = metadata["location"]["dimensionRange"]
        state["removed"] = True
        # Keep a visible tombstone and ID, so retries cannot reuse this row.
        description = "[Removed by bank] " + current[0]
        self._commit([self._cells(loc["sheetId"], loc["startIndex"], state["column"], description, 0),
                      self._metadata_update(metadata, state)])
