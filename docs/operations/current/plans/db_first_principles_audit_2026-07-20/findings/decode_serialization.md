# Lane W6 — Decode + Serialization Cost

Scope: `rg json.loads|json.dumps|pickle|msgpack|zlib|gzip` under `src/state`,
`src/engine`, `src/execution` (292 raw hits: 279 `json.loads`/`json.dumps`,
13 `zlib.*`, 0 `pickle`, 0 `msgpack`, 0 `gzip`); plus schema reads
(`src/state/schema/*.py`, `src/state/snapshot_repo.py`,
`src/decision_kernel/*.py`) and byte totals from
`findings/census_raw.jsonl`. All row samples pulled via the safe rowid-window
pattern (rules 2/3), `EXPLAIN QUERY PLAN` confirmed `SEARCH ... USING INTEGER
PRIMARY KEY` (never `SCAN`) before every row-data query. WAL sizes checked
before/after: `zeus_trades.db-wal` 241 MB, `zeus-forecasts.db-wal` 2.2 MB,
`zeus-world.db-wal` 8.5 MB — none near the 512 MiB stop threshold.

## (a) JSON/BLOB-carrying columns on the hot path

No `pickle`, `msgpack`, or `gzip` anywhere in the three trees — `zlib` is the
only compression primitive in use, confined to exactly two files
(`grep -rln base64 src/state src/engine src/execution` → only these two):
`src/engine/global_batch_runtime.py` (9 `zlib.compress` call sites) and
`src/engine/event_reactor_adapter.py` (1 `zlib.decompress` site,
`event_reactor_adapter.py:1354`), both implementing one house pattern,
literally tagged `"zlib+base64+canonical-json-v1"`
(`global_batch_runtime.py:813`, `:891`): `json.dumps(..., sort_keys=True,
separators=(",",":"))` → `zlib.compress(level=9)` → `base64.b64encode` →
embedded as a JSON string field inside a certificate payload. This pattern
exists **only** for global-auction receipt evidence (`book_native_side_receipt`,
`minimum_repair_zlib`, `evaluation_zlib`, `holding_coverage_zlib` —
`global_batch_runtime.py:816,894,928,983,1069,1582,1620,1634`). It is not
applied anywhere else in the codebase — notably not to any of the three
tables identified below as the actual size/decode-cost leaders.

Every `json.dumps` call site I sampled uses the same compact-encoding
convention: `sort_keys=True, separators=(",", ":")` (no pretty-printing, no
wasted whitespace) — e.g. `src/state/snapshot_repo.py:741`
(`_json()` helper). This rules out "sloppy indentation" as a size driver;
the size cost below is structural (repeated keys, embedded duplication), not
formatting.

Table.column inventory (cadence classified from the calling module):

| table.column | db | cadence | payload class |
|---|---|---|---|
| `executable_market_snapshots.orderbook_depth_json` | trade | **every ~20s reactor tick** (`src/main.py:119` `_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0`; `src/data/substrate_observer.py:213` `_priority_refresh_interval_seconds()` default 20.0 — two independent 20s jobs both call `refresh_executable_market_substrate_snapshots`, `market_scanner.py:4835`), ~30-90 outcomes/cycle per the function's own docstring (`market_scanner.py:4859-4863`) | 1.5-3 KB typical, up to 6.7 KB (measured) |
| `executable_market_snapshots.{token_map_json,fee_details_json,tradeability_status_json}` | trade | same cadence, same writer (`snapshot_repo.py:165-192`) | ~230-270 B each, essentially static per market |
| `ensemble_snapshots.members_json` | forecasts | per model ingest run (ECMWF/TIGGE fetch cycles: `src/data/ecmwf_open_data_ingest.py`, `src/data/tigge_db_fetcher.py`) + read every tick of `forecast_snapshot_ready` trigger evaluation (`src/events/triggers/forecast_snapshot_ready.py:1084,1222`) | ~300-1050 B (measured, 51-member float vector) |
| `ensemble_snapshots.provenance_json` | forecasts | same write cadence | ~1276-1362 B (measured) — **now the larger of the two**, see (b) |
| `ensemble_snapshots.forecast_window_block_reasons_json` | forecasts | same write cadence | 2-67 B (measured) |
| `decision_certificates.payload_json` | trade | **per decision-kernel evaluation** — one 24-certificate bundle per candidate/decision event (`src/decision_kernel/ledger.py:114` `insert_idempotent`), 24 distinct `certificate_type` values cycle every 24 rows in the live table | 93-260 B (`ClockModeCertificate`) up to 93,225-93,326 B (`ActionableTradeCertificate`, measured on 3 live rows) |

## (b) Top-3 heaviest deserialization paths per cycle

**1. `executable_market_snapshots.orderbook_depth_json` — the dominant byte
and decode-count leader.** Census: `bytes=46,285,611,008` (46.3 GB),
`cells=17,989,157` rows, `payload_bytes=40,492,360,874`,
`mx_payload=6,662` (`census_raw.jsonl`, trades db). That is **55% of the
entire `zeus_trades.db`** on-disk footprint (46.3 GB of the DB's total)
concentrated in one table, and within that table the four JSON columns'
measured lengths on the last 50 rows (rowid 10322359-10322408) show
`orderbook_depth_json` at 1574-3009 B vs. 230-270 B for the other three JSON
columns combined — i.e. **orderbook_depth_json alone is ~85-95% of every
row's payload**. It is `_canonical_json(raw_orderbook)`
(`src/data/market_scanner.py:3292`) — the **entire raw CLOB orderbook**
(every bid/ask price level), stored verbatim, once per capture, forever
(append-only, `snapshot_repo.py:97-101` triggers `RAISE(ABORT)` on
UPDATE/DELETE — "NC-NEW-B"). At ~30-90 captures/cycle × one cycle every 20s
× two independent 20s jobs (EDLI warm + priority refresh both call the same
writer), this table has grown to 17.99M rows with **no retention/pruning
mechanism found** (`grep -rn "DELETE FROM executable_market_snapshots"
src/` → no hits outside the append-only trigger definitions themselves).

Decode side: **at least 10 independent call sites re-run
`json.loads(snapshot.orderbook_depth_jsonb)` on the same immutable string**,
each doing its own parse of the identical bytes rather than sharing one
parsed structure on the snapshot object: `src/analysis/market_analysis_vnext.py:204`,
`src/contracts/execution_intent.py:648`, `src/execution/executor.py:5324`,
`src/engine/event_reactor_adapter.py:3091`,
`src/engine/cycle_runtime.py:1136`,
`src/strategy/live_inference/executable_cost.py:139`,
`src/risk_allocator/governor.py:1603` (via `getattr` + downstream parse),
plus `src/engine/qkernel_spine_bridge.py:2157` and
`src/engine/event_reactor_adapter.py:20622,20667,36388` reading the raw
column string directly for hashing/depth extraction. On the money path
(`cycle_runtime.py`, `event_reactor_adapter.py`, `executor.py`,
`qkernel_spine_bridge.py` all execute every decision cycle), a single
snapshot's orderbook JSON is very plausibly parsed **4-8 times** across one
decision evaluation. At the measured ~2.24 KB average raw size and ~30-90
snapshots refreshed per 20s tick, that is roughly **270 KB-1.6 MB of pure
re-parse work per 20s tick that could be a single decode reused**
(HYPOTHESIS on the exact multiplier — I did not trace one live cycle's call
graph to count actual invocations, only enumerated static call sites; the
byte-per-decode number itself is measured).

**2. `ensemble_snapshots` (`members_json` + `provenance_json`) — census:
`bytes=3,592,073,216` (3.6 GB), `cells=2,085,231`, `payload_bytes=2,870,410,406`,
`mx_payload=9,554` (forecasts db). Sampled last 50 rows (rowid
1,218,064-1,218,113): `members_json` 306-1017 B, `provenance_json`
1276-1362 B — **provenance_json is now the larger column**, the inverse of
what the table's name/purpose ("ensemble members") suggests. `p_raw_json`
was `NULL` on all 50 sampled rows (unused on the current write path).
Consumers: `src/calibration/ens_bias_repo.py:406,825` (bulk calibration
scans), `src/engine/replay.py:862` (bulk replay), and
`src/events/triggers/forecast_snapshot_ready.py:1084,1222,1853` which reads
`members_json` on **every trigger-evaluation tick** for each
city/target/metric being watched — this is the per-cycle hot path for this
table, not the calibration/replay bulk scans.

**3. `decision_certificates.payload_json`, specifically the
`ActionableTradeCertificate` sub-type — the concentration-of-bytes leader
*within* a single row.** Census: `bytes=238,821,376`, `cells=58,021`,
`payload_bytes=216,157,094`, `mx_payload=99,830` (trades db). Sampled 3 live
`ActionableTradeCertificate` rows: 93,225 / 93,326 / (third row, older
bundle) B. Summed across one full 24-certificate decision bundle (rowid
36,479-36,502, one of each `certificate_type`): **total bundle payload ≈
127,754 B, of which `ActionableTradeCertificate` alone is 93,225 B — 73% of
one decision's total certificate evidence.** Its `opportunity_book` field
is 92,800 of those 93,225 bytes (99.5% of the certificate). See (d) for why.

## (c) Compression status

**Nothing on the three tables above is compressed.** All are stored as
plain `TEXT` JSON, `sort_keys=True, separators=(",",":")` compact-encoded
but never `zlib`'d, despite the codebase having a working
`"zlib+base64+canonical-json-v1"` pattern already in production use for a
*different, much smaller* blob class (global-auction receipt evidence,
(a) above).

Measured raw-vs-zlib-6 ratios, last-50-row samples, local Python
(`zlib.compress(payload.encode("utf-8"), 6)`), scratch files under
`/private/tmp/claude-501/-Users-leofitz-zeus/0336a436-51ee-413b-9391-74d0393cdc1d/scratchpad/`:

| column | n rows | raw bytes | zlib-6 bytes | ratio |
|---|---|---|---|---|
| `executable_market_snapshots.orderbook_depth_json` | 50 | 112,128 | 25,293 | **4.43x** |
| `ensemble_snapshots.members_json` | 50 (non-null) | 43,564 | 18,749 | 2.32x |
| `ensemble_snapshots.provenance_json` | 50 | 64,567 | 30,470 | 2.12x |
| `decision_certificates.payload_json` (`ActionableTradeCertificate`) | 3 | 279,822 | 39,295 | **7.12x** |

At the `orderbook_depth_json` ratio applied to the table's full
`payload_bytes` (40.49 GB), zlib-6 alone would shrink that one column's
payload to a HYPOTHESIS-flagged ballpark of ~9.1 GB — a rough
extrapolation from a 50-row sample to 17.99M rows, not a measured total;
flagged as a size estimate to validate with a larger sample before acting,
not a firm number.

## (d) Double-encode smells / repeated-key structure

1. **`orderbook_depth_json` — array-of-objects instead of parallel arrays.**
   Each price level is `{"price": "0.003", "size": "100"}`; a book with N
   bid + M ask levels repeats the literal strings `"price"` and `"size"` N+M
   times. Measured sample: `{"asks": [...25 levels...], "bids": [...]}` — the
   4.43x zlib ratio above is driven largely by this key repetition (zlib's
   dictionary window trivially eats repeated literal keys); switching to
   `[[price, size], ...]` tuples would cut raw bytes before compression even
   enters the picture, independent of whether compression is adopted.

2. **`ensemble_snapshots.provenance_json` — string-serialized JSON nested
   inside JSON, duplicating a sibling column.** Sampled row (rowid
   1,218,074+10): `provenance_json` contains
   `"contract_outcome_evidence": {..., "forecast_window_block_reasons_json":
   "[]"}` — a **JSON string** (not a native array) nested inside the
   `provenance_json` object, serializing the exact same logical value the
   table already carries in its own top-level
   `forecast_window_block_reasons_json` column
   (`src/state/schema/v2_schema.py:266`). The value is written twice per
   row: once as a native column, once double-encoded (string-of-JSON) inside
   a different JSON column.

3. **`decision_certificates.ActionableTradeCertificate.opportunity_book` —
   the same sub-object embedded up to 25x in one row.** The sampled
   certificate's `opportunity_book.candidates` array has 22 entries, each
   carrying its own `qkernel_execution_economics` dict (~35 keys: hashes,
   IDs, floats). The string `"qkernel_execution_economics"` occurs 25 times
   in the serialized payload, and the *selected* candidate's specific
   `receipt_hash` value occurs **25 times** in one row — the winning
   candidate's full economics dict is present independently in (a) the
   certificate's own top-level `qkernel_execution_economics` field, (b)
   `opportunity_book.cache_summary.selected_qkernel_execution_economics`,
   and (c) as one element of `opportunity_book.candidates[]` — the same
   ~1.5-2 KB dict serialized three separate times for the winner alone, on
   top of 21 more full dicts for every non-selected candidate that was
   merely evaluated and rejected.

4. **Payloads exceeding the 3,900 B overflow-risk line (page_size 4096,
   local-payload max 4,061 B per §C of the consult doc).** I cannot compute
   the aggregate overflow-bytes ratio per table — that requires
   `dbstat`/`pagetype='overflow'` aggregation, which is BANNED under Safety
   Law rule 1. What `mx_payload` from the existing census supports (row-max
   only, not a ratio):
   - `decision_certificates`: `mx_payload=99,830` — **every**
     `ActionableTradeCertificate` row (measured 93-93.3 KB, consistently,
     across 3 samples) chains into roughly 23-24 overflow pages
     (99,830 / ~4,092 B usable per overflow page). This is not a rare
     outlier row; it is a fixed cost paid on **every single decision
     evaluated**, 1-in-24 rows structurally.
   - `ensemble_snapshots`: `mx_payload=9,554` — some rows overflow (1-2
     pages), the 50-row sample's largest (`provenance_json` ~1362 B +
     `members_json` ~1017 B + row overhead) stays comfortably under 4,061 B,
     so overflow is a minority-row phenomenon here, not systemic.
   - `executable_market_snapshots`: `mx_payload=6,662` — just over the
     local-max line; the 50-row sample topped out at 3,009 B
     (`orderbook_depth_json`) + ~730 B other columns ≈ 3,750 B, under
     threshold — overflow is a tail case (deep order books), not the
     common row.
   - No overflow risk found in `token_price_log` (mx_payload 283),
     `trade_decisions` (mx_payload 3,801 — close to but under the line),
     `selection_hypothesis_fact` (601), `observations` (2,004),
     `settlements`/forecasts (1,227), `market_events` (443),
     `decision_certificate_edges` (212).

## Coverage note

`census_raw.jsonl` had 284 lines at read time and appeared to still be
growing per the task brief; I used what was present (all rows needed for
this lane — the three candidate tables, plus the comparison set in (d) —
were already in the file). No `dbstat`, `VACUUM`, `ANALYZE`, or
`wal_checkpoint` was run; all size numbers are either from the existing
census file or `length()`/row-sample reads through the mandated safe
pattern. Row-level bytes for `zlib` ratio testing came from 50-row (or 3-row
for the rare `ActionableTradeCertificate` type) rowid-window samples per
table — small-N estimates, not full-table measurements; extrapolations to
full-table byte savings are explicitly flagged as HYPOTHESIS above, not
measured claims.
