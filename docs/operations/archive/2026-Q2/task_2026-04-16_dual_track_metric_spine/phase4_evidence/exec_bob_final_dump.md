# exec-bob Final Dump — Phase 4B Knowledge Transfer

Date: 2026-04-16 | Author: exec-bob (retiring after 3 compacts)

---

## 1. Non-obvious learnings from touched modules

**`observation_client.py`**: The `WU_API_KEY` import-time `SystemExit` blocks the entire test
collection for any file that imports this module transitively. Use `WU_API_KEY=dummy` as env
prefix when running regression suites that touch `monitor_refresh.py` or `cycle_runner.py`.
This is a pre-existing hard exit, not a conditional guard.

**`evaluator.py:794` rejection branch**: The `OBSERVATION_UNAVAILABLE_LOW` branch fires when
`low_so_far` is None in the `Day0ObservationContext`. Phase 3 made `low_so_far` a required
non-None output — providers that cannot produce it must raise a typed exception rather than
returning None. This means R-H (evaluator low unblock) is gated entirely on Phase 3
observation_client correctness, not on evaluator logic changes.

**`monitor_refresh.py` seams**: Imports `observation_client` at module level, so any test that
imports `monitor_refresh` triggers the `WU_API_KEY` crash. The seam is the import chain, not
the function call. To test monitoring logic in isolation you need either env var injection or
`importlib` tricks.

**Calibration store API design decisions**:
- `add_calibration_pair_v2` takes `metric_identity: MetricIdentity` as a required keyword arg
  with no default. This is intentional — the TypeError is the antibody. A caller that doesn't
  pass it fails loudly at import/call time rather than writing a row with NULL
  `temperature_metric`.
- `_resolve_training_allowed` is a pure function that must be called before every write. It
  normalizes case and strips whitespace before whitelist checks — this was added in M1 after
  critic found `"TIGGE_"` (trailing underscore) and `" tigge_..."` (leading space) would have
  bypassed the check without normalization.
- `save_platt_model_v2` uses plain `INSERT` (not `INSERT OR REPLACE`) deliberately — the
  UNIQUE constraint on `(temperature_metric, cluster, season, data_version, input_space)`
  enforces no silent overwrites. Callers must explicitly deactivate old models first.

---

## 2. Phase 4.5 hazard map for exec-dan — GRIB extractor output contract

`ingest_json_file` expects the JSON produced by `tigge_local_calendar_day_extract.py`. Every
field below is required; missing fields cause silent wrong-default behavior.

**Top-level required fields** (exact names, exact types):

| Field | Type | Notes |
|---|---|---|
| `data_version` | str | Must be `"tigge_mx2t6_local_calendar_day_max_v1"` — any other value fires `assert_data_version_allowed` guard |
| `physical_quantity` | str | `"mx2t6_local_calendar_day_max"` |
| `city` | str | Exact city name matching Zeus `cities_by_name` |
| `unit` | str | `"C"` or `"F"` — NOT `"degC"` or `"K"`. Ingest maps `"C"→"degC"`, `"F"→"degF"` and rejects anything else |
| `issue_time_utc` | str | ISO 8601 UTC e.g. `"2024-01-01T00:00:00+00:00"` |
| `target_date_local` | str | `"YYYY-MM-DD"` in city's local timezone |
| `lead_day` | int | `(target_local_date - issue_utc.date()).days` — NOT lead_hours |
| `manifest_sha256` | str | SHA-256 of the TIGGE coordinate manifest file used during extraction |
| `training_allowed` | bool | True only if all 51 members have non-None values |
| `members` | list[dict] | 51 elements, each `{"member": int, "value_native_unit": float|null}` |
| `nearest_grid_lat` | float | Grid point actually used |
| `nearest_grid_lon` | float | Grid point actually used |
| `nearest_grid_distance_km` | float | Distance from city to nearest grid point |

**Optional fields** (for low track / Phase 5):

- `causality` — dict with `{"status": str, "pure_forecast_valid": bool}`. High track omits
  this; ingest defaults to `causality_status="OK"`.
- `boundary_policy` — dict with `{"boundary_ambiguous": bool, "ambiguous_member_count": int}`.
  High track omits this; ingest defaults to `boundary_ambiguous=0, ambiguous_member_count=0`.

**Type coercion pitfalls**:
- `value_native_unit` can be `null` (Python `None`) for missing members. `ingest_json_file`
  stores `null` in the JSON array. Downstream `rebuild_calibration_pairs_v2.py` must filter
  None values per-member before computing statistics.
- `lead_day` is an integer but ingest converts to `lead_hours = lead_day * 24.0` (float) for
  the DB column `lead_hours REAL`.
- `unit` from the extractor is single-char `"C"` or `"F"`, not `"degC"`. The mapping lives
  in `_normalize_unit()` inside the ingest script.

---

## 3. Structural patterns to replicate

**`commit_then_export` wiring**: Every canonical DB write must go through this helper from
`src/state/canonical_write.py`. The pattern is:
```python
def _db_op() -> None:
    conn.execute("INSERT ...", row)
commit_then_export(conn, db_op=_db_op)
```
Do NOT call `conn.commit()` directly in scripts. The helper enforces DT#1: DB commit before
any JSON export. Any bypass creates a split-brain window.

**`validate_members_unit` guard placement**: Call it immediately after `_normalize_unit()`,
before any data extraction. The guard must fire before the INSERT, not after. Same pattern as
`assert_data_version_allowed` — both are write-time refusal guards, not read-time validators.

**Kelvin/degC mapping**: ECMWF GRIB delivers values in Kelvin. The extraction scripts convert
to native unit using `kelvin_to_native(value_k, city["unit"])` before writing JSON. By the
time `ingest_json_file` sees the data, values are already in `"C"` or `"F"`. The ingest
layer's job is to map `"C"→"degC"` for the DB column and validate — not to convert from K.

**`manifest_hash` content-addressing**: Hash the provenance fields (data_version,
physical_quantity, manifest_sha256, issue_time_utc, city, target_date_local) as a sorted JSON
string, SHA-256. This creates a content-addressed row identity that survives re-ingest. Using
only the manifest_sha256 field from the extractor would be wrong — two cities in the same
GRIB batch share manifest_sha256 but are distinct rows.

**Dynamic step_204 requirement**: The TIGGE plans forbid `range(6, 181, 6)` hardcoded. For
west-coast Americas cities (LA, Seattle) with day7 target, the local calendar day ends ~200
hours after the 00Z issue. `ceil_to_next_6h(200)` = 204. The extractor must request steps up
to 204 or those cities will have missing members for day7 and `training_allowed=False`.

---

## 4. Anti-patterns narrowly avoided

**Dict-access on typed `Day0ObservationContext`**: The old observation layer returned plain
dicts. Phase 3 moved to a typed dataclass. A test that did `obs["low_so_far"]` instead of
`obs.low_so_far` would fail with `TypeError` not `KeyError` — confusing to diagnose. Always
use attribute access on typed dataclasses.

**IEM silent fallback**: `observation_client.py` has an Open-Meteo fallback path. INV-15
exists because that path previously could write `training_allowed=True` calibration pairs with
fallback data. The fix is structural: `_resolve_training_allowed` checks the data_version
prefix, not just the caller's intent. A fallback row with `data_version="openmeteo_hourly_v1"`
is forced to `training_allowed=False` regardless of what the caller passes.

**`INSERT OR REPLACE` defeating `--overwrite` intent**: My first implementation used
`INSERT OR IGNORE` unconditionally and added a pre-flight skip check for the non-overwrite
case. This meant `--overwrite` bypassed the skip check but still hit `INSERT OR IGNORE` — so
duplicates were silently dropped instead of replaced. Fix: branch `insert_verb` on the
`overwrite` flag before constructing the SQL string.

**`QUARANTINED_DATA_VERSIONS` set-only check**: The quarantine set contained
`"tigge_mx2t6_local_peak_window_max_v1"` (exact string). A version bump to `_max_v2` would
escape the set check silently. M3 added the prefix `"tigge_mx2t6_local_peak_window"` to
`QUARANTINED_DATA_VERSION_PREFIXES` so any version bump is caught by the prefix loop.

---

## 5. Known harder-than-they-looked problem spots

**`_resolve_training_allowed` two-signal design**: First attempt checked only data_version
prefix. Critic found a test passing `source="openmeteo_hourly"` with a canonical data_version
— that row should be rejected (fallback source) but passed the single-signal check. The fix
adds a source check, but the source check must skip (return True) when source is empty string
— because many callers don't pass source. The invariant is: non-empty non-whitelisted source
is a hard reject; empty source is "not asserted, skip check." Get this wrong and you either
reject all rows without an explicit source, or let fallback sources through.

**Case normalization on a frozenset**: `_TRAINING_ALLOWED_SOURCES = frozenset({"tigge",
"ecmwf_ens"})` stores lowercase entries. Without normalization, `"TIGGE_"` (uppercase,
trailing underscore) doesn't match `"tigge"` in the set — it starts with "tigge" in lowercase
but the `in` check was against original case. The fix normalizes both the input and the
comparison. A teaching lesson: frozensets for string membership must always pair with
case-normalization at the lookup site.

**Phantom-work discipline**: Twice during Phase 4A, teammates (and I) reported work complete
based on memory rather than current disk state. The mandatory protocol: after every edit, run
`git status --short <file>` and `grep <changed_symbol> <file>` before reporting status. Memory
of "I wrote that" is unreliable across compacts and across context boundaries.

**Test isolation for `ingest_json_file`**: The R-L test class used direct SQL INSERT to prove
the schema accepted 7 fields. Critic correctly identified this proved schema correctness, not
code correctness. The integration test must: (1) write a temp JSON file on disk matching the
extractor output format exactly, (2) call `ingest_json_file(conn, path, ...)` directly, (3)
read back from the DB and assert each field. Skipping step (2) means the test cannot catch a
future refactor that silently drops a mandatory INSERT parameter.
