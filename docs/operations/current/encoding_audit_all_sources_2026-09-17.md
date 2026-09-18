# Encoding audit — is MN2T3 special, or is every path mis-encoded? (2026-09-17)

Operator question: of the ~13 weather/market sources Zeus ingests, only MN2T3/MX2T3
keeps raw files on disk. Is that one source's problem, or does the whole data pipeline
fall short of optimal execution?

**Answer: the raw-file retention is genuinely ECMWF-only and mostly correct. The
mis-encoding is systemic and affects every large table in the system.** Two separate
findings; do not conflate them.

---

## 1. Raw-file retention IS ECMWF-only — and it is working as designed

`rg` for binary writes (`open(...,"wb")`, `write_bytes`) across `src/data/` +
`src/ingest/` returns exactly three modules:

| module | retains? |
|---|---|
| `src/data/ecmwf_open_data.py` | **yes** — 46 GB of GRIB2, 2-day window |
| `src/data/source_clock_update_probe.py` | no — writes a temp, `os.unlink`s it (`:150,162`) |
| `src/data/openmeteo_ecmwf_ifs9_bucket_transport.py` | no retained-write hits found |

All 13 registered sources (`architecture/data_sources_registry_2026_05_08.yaml`):
ecmwf_open_data, tigge_mars, openmeteo_archive, openmeteo_previous_runs, wu_icao_history,
meteostat_bulk, ogimet_metar, hko_daily_api, polymarket_uma_oo_v2, polymarket_gamma,
polymarket_clob, polymarket_data_api, source_health_probe. **Every source except ECMWF
parses its HTTP response in-process and persists only rows.**

So the operator's observation is exactly right, but the diagnosis inverts: ECMWF is the
only source whose payload is a *binary scientific archive format* rather than a JSON/CSV
API response. Keeping it briefly is defensible (GRIB holds the full grid; the DB holds
only the decoded per-city extrema, so the raw file is not reconstructible from the DB).
Its retention is automatic, 2 calendar days, and proof-gated on canonical DB truth before
deleting (`src/data/ecmwf_open_data.py:289-442`). **Verdict: KEEP.** Not a defect.

### The genuine ECMWF defects (small bytes, unbounded count)

- `.partial` (4.1 GB) and `.ranges.json` (11 MB) are **permanently invisible** to
  retention: `_raw_file_identity` (`:279-286`) returns `None` for any name not matching
  the two `.grib2` patterns, and the retention plan `continue`s those. The 2-day cutoff
  never reaches them at any age.
- The range-resume manifest is never unlinked on the **success** path (`_fetch_step`
  `:2214-2273` unlinks the `.partial` pair but not `_range_resume_manifest_path`); only a
  checksum/ETag mismatch cleans it.
- `open_ens_mn2t6_localday_min` (847 MB) + `open_ens_mx2t6_localday_max` (545 MB) are
  **dead**: the extractor's `--output-root` moved to `coordinate_manifests` on
  2026-09-09, no consumer, content already in `ensemble_snapshots.members_json`.
- The live successor `coordinate_manifests/` (224 MB, growing daily, JSON, gzip 3.1x)
  inherited **no** retention.

### Pipeline shape worth questioning (open, sent to consult)

Each cycle runs: bytes → disk GRIB → **subprocess** (`subprocess.run` of a conda python
per track, `:2879`) → JSON written into `coordinate_manifests` → re-read → rows in
`ensemble_snapshots`. Download concurrency is `ThreadPoolExecutor(max_workers=
_DOWNLOAD_MAX_WORKERS)` where the default is **2** (`ZEUS_ECMWF_MAX_WORKERS`, `:536`)
while a docstring in the same file (`:19`) claims `max_workers=5` — **the comment
contradicts the code.** Since ECMWF publishes incrementally from cycle+6h40m to +8h01m,
download concurrency and earliest-usable-step processing directly set possession time.

---

## 2. The systemic finding: low-cardinality TEXT repeated per row

This is not about MN2T3. Every large table stores wide identifier strings on every row
where a small integer key would carry the same information. Measured, not inferred.

### `zeus-forecasts.db calibration_pairs` — 81,314,490 rows, ~241 B/row, ~19.6 GB

Distinct-value counts measured over a 200k-row window:

| column | bytes/row | distinct values |
|---|---|---|
| `dataset_id` | 43.0 | **2** |
| `decision_group_id` | 40.0 | 2009 |
| `forecast_available_at` | 25.0 | 336 |
| `recorded_at` | 19.0 | 150 |
| `source_id` | 15.0 | **2** |
| `bin_source` | 12.0 | **1** |
| `target_date` | 10.0 | 242 |
| `observation_field` | 8.0 | **1** |
| `authority` | 8.0 | **1** |
| `temperature_metric` / `season` / `causality_status` / `horizon_profile` | 3.0 / 3.0 / 2.0 / 4.0 | **1 each** |

`dataset_id` spends 43 bytes per row across 81.3M rows to express **one of two values**.
Replacing each with a 1-2 byte key sized to its cardinality saves **192 B of 241 B/row
= 80%**: **19.6 GB → 4.0 GB, ~15.6 GB reclaimed, fully lossless** (the strings live once
in a dimension table). The same TEXT keys are re-carried inside this table's 7 indexes,
so index savings stack on top.

### `research/family_books.db book_top` — 16.4M rows

`token_id` (77 B) + `condition_id` (66 B) + `event_slug` (55 B) re-stored on every row
for only **13,860 distinct tokens**, while `token_meta` (PK `token_id`, 13,860 rows)
**already holds those exact fields**. 64% of the row is the repetition; the actual
bid/ask/size values are 3%. ~4.1 GB, lossless via an integer FK.

### `zeus-forecasts.db forecast_posteriors.q_json` — the probability vectors

Raw sample:

```
{"Will the lowest temperature in Paris be 10°C on September 18?":0.005980000000000031, ...}
```

The dict **keys are full English market questions**, repeated on every row, and the
values are float64 as decimal text. For 11 probabilities: **88 bytes of float64 data
occupying 900 bytes of JSON text = 10.2x, lossless.** Verified `float(repr(v)) == v` for
every value, so the text round-trips exactly today — precision is *not* currently lost,
but it is paid for at 10x. (float32 would introduce ~1.4e-08 error, so keep float64.)
The bin labels belong in the bin-topology dimension already keyed by
`bin_topology_hash`, not in every posterior row.

### `decision_log.*_zlib_b64` — already reported

`zlib.compress(level=9)` then base64 into JSON TEXT: base64 wastes a measured 25.0%, and
zstd-3 raw BLOB is 46% smaller **and 11.4x faster** (65.0 ms → 5.7 ms). See
`storage_and_latency_verdict_2026-09-17.md` §1.

### Where a naive fix would have been wrong

`provenance_json` (94.6% of a 78 KB row, 46.8 GB) looks like the same problem, but is
not. Measured across 200 rows: **167 distinct key-path sets, 4,578 distinct key paths,
and 75.5% of the bytes are genuine leaf values** — the skeleton is heterogeneous, so
"normalize it into columns" would not work cleanly. Dictionary compression is the right
tool here: zstd-19 4.38x, **zstd-19 + trained dictionary 5.02x** (the dictionary is what
captures the shared skeleton). ~46.8 GB → ~9 GB.

---

## 3. Verdict on the operator's question

> is it this one source's problem, or has no data processing reached optimal execution?

- **Raw-file retention**: ECMWF-only, and correct in design. Real leaks are the
  retention-invisible `.partial`/`.ranges.json` (4.1 GB, unbounded count), the two dead
  MN2T6/MX2T6 dirs (1.4 GB), and `coordinate_manifests` having no retention at all.
- **Encoding**: **systemic, not one source.** Every large table pays to re-store
  identifier strings that a dimension table already holds, or stores numbers as text.
  Combined lossless reclaim from encoding alone: ~15.6 GB (calibration_pairs) +
  ~37 GB (provenance dict-compression) + ~4.1 GB (book_top) + decision_log's 46%, with
  no information discarded anywhere.
- **The root cause is one missing convention**, not 13 separate bugs: there is no rule
  that a repeated identifier becomes a key and a number is stored as a number. That is
  why it recurs in every table written by a different author.

## 4. UNVERIFIED / open

- Whether `calibration_pairs`'s low-cardinality columns are low-cardinality *globally* or
  only in the 200k-row window sampled (a long-lived `dataset_id` vocabulary could be
  larger historically). Re-measure over the full table before migrating.
- Whether the ECMWF subprocess boundary is load-bearing (eccodes/conda isolation, crash
  containment) or an accident — sent to consult.
- `_DOWNLOAD_MAX_WORKERS=2` vs the docstring's 5: which is intentional, and what raising
  it costs against ECMWF rate limits.
- Whether any consumer depends on reading these columns as TEXT (a migration needs a
  compatibility view).
