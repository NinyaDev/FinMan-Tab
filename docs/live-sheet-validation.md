# Live Google Sheets validation

Validated in a separate workbook containing synthetic data only. Its address
is retained in private deployment records.

The workbook lives in the ChatGPT folder. `Noviembre` contains two native,
two-column tables; `Results` records the checks and their limits. No existing
finance workbook, credential file, production cursor, or private runner was
read or modified by this validation. The Google Drive connector authenticated
the requests; local production OAuth tokens were not loaded.

## Method

`scripts/sheets_acceptance_probe.py` runs the actual `TrackedWriter` and viewer
normalization code against bounded real Google readbacks. It emits the writer's
batch requests, which are executed through the connected Google Drive tool.
Each operation is read back from Google before the next probe stage. Direct
network access is blocked inside the Python probe, and config is injected with
synthetic values before importing the writer.

The transport adapter performs read filtering/metadata lookup locally over the
Google snapshot. Native metadata key and exact-row filters were separately
validated using Google's updateDeveloperMetadata request with unchanged values.
This does NOT exercise the native developerMetadata.search HTTP endpoint or the
production OAuth/service-account authentication path end to end.

The simulated lost response is injected after request generation. The emitted
request is then committed to Google, its readback supplied to a fresh retry,
and the retry is verified to emit zero duplicate writes. This models successful
remote commit followed by interruption; it is not an induced Google outage.

## Observed results

- Google accepted row values plus hidden transaction identity in one batch.
- Replaying the same purchase after simulated response loss generated no write.
- The synthetic reward appeared as **-10.00**, not a positive charge.
- A separate SoFi purchase appeared as **17.25** in its own two-column table.
- A bank amount update to **27.50** preserved `My edited groceries` in the Sheet.
- Replaying that update generated no write.
- A manual amount of **28.50** blocked a conflicting bank update to **30.50**.
- Removing the separate credit set its amount to **0.00** and retained its ID;
  repeated removal generated no write. The visible tombstone is current behavior.
- Inserting a whole row moved Google's row metadata correctly. Replay still
  preserved the user's description and amount, without adding another row.
- The viewer used current Sheet values plus hidden metadata to produce **45.75**
  (28.50 + 17.25), with the removed credit excluded.
- A deliberately invalid two-row metadata location produced Google HTTP 400.
  The companion sentinel cell was absent afterward: the invalid batch did not
  partially write cell data.
- Native metadata key filtering matched one transaction; exact-row filtering
  matched both adjacent transactions sharing that row, as expected.
- The revised offline suite passed **79 tests**, with sockets disabled.

## Bug discovered and fixed

Google returns row-attached developer metadata inside
`sheets[].data[].rowMetadata[].developerMetadata`. The viewer had looked only
at spreadsheet/sheet-level metadata. It now performs a second bounded metadata
read for the actual table ranges, requesting GridData row metadata, and indexes
records by sheet, row, and column. The adapter regression fixture now uses the
real Google response shape instead of the incorrect mock shape.

## Limits and remaining checks

This validates real Sheets mutations/readbacks and local application behavior,
not real Plaid data availability. The missing OnePay reward and Amex consent
remain separate checks. There was no production activation, historical cleanup,
large-volume/quota exercise, sorting of individual card cells, full scheduled
runner execution, or authenticated phone deployment.

Native browser rendering was unavailable. Values, headers, number formats,
wrapping, and column dimensions were inspected through Google API readback.
The workbook is retained for review; it has not been deleted or shared publicly.
