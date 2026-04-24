# DR-33-A Execution Log

**Packet**: DR-33-A (live-harvester enablement — code change + tests; feature flag OFF)
**Execution date**: 2026-04-23T20:30 UTC
**Executor**: team-lead
**Pre-review**: critic-opus (combined pre/post cycle; code-only packet)

---

## Section 1 — Changes

### 1.1 `src/execution/harvester.py` (3 functions replaced/added + 1 flag gate)

| Area | Before | After |
|---|---|---|
| `_find_winning_bin` | Reads `market["winningOutcome"]` — P-D proved absent in 412/412 Gamma events; unreachable | UMA-vote gate: `umaResolutionStatus='resolved'` + `outcomes=['Yes','No']` + `outcomePrices[0]='1'`; fail-closed on unexpected shapes; returns `(pm_bin_lo, pm_bin_hi)` |
| `_format_range` | Sentinel form `"-999-{hi}"` / `"{lo}-999"` | **Replaced by** `_canonical_bin_label(lo, hi, unit)`: text form `17°C` / `86-87°F` / `21°C or higher` / `15°C or below` (matches P-E convention; round-trips through `_parse_temp_range`) |
| `_write_settlement_truth` | Stamps `authority='VERIFIED'` without SettlementSemantics gate; inline `conn.commit()` at L569; no INV-14 fields or provenance_json | Full canonical write: SettlementSemantics gate → VERIFIED iff obs ∈ bin; else QUARANTINED with enumerable reason. INV-14 fields populated; provenance_json with writer/decision_time_snapshot_id/reconstruction_method. **No `conn.commit()`** — caller owns txn. Uses `INSERT OR REPLACE` (matches P-E canonical). |
| `_lookup_settlement_obs` (new) | — | Source-family-correct obs lookup: wu_icao→wu_icao_history, noaa→ogimet_metar_*, hko→hko_daily_api, cwa→None |
| `run_harvester` entry | Always runs full cycle | Feature-flag gate: `ZEUS_HARVESTER_LIVE_ENABLED != "1"` → early return with `status="disabled_by_feature_flag"`; no data-plane calls |
| Caller site (L312-320) | `_find_winning_bin` returns `(label, range_str)`; passed to old `_write_settlement_truth(conn, city, target_date, winning_label, event_slug=...)` | `_find_winning_bin` returns `(pm_bin_lo, pm_bin_hi)`; obs lookup via `_lookup_settlement_obs`; passes `pm_bin_lo/hi` + `obs_row` to new signature |

### 1.2 `src/data/market_scanner.py::_parse_temp_range` (surgical parser extension)

Added 2 branches:
1. `r"(-?\d+\.?\d*)\s*°[Ff]$"` — symmetric with existing `°[Cc]$` for P-E / DR-33 canonical F point labels (e.g., `17°F`).
2. `r"(-?\d+\.?\d*)\s*°[CcFf]\s+on\b"` — handles Gamma question point-bin form `"... be 17°C on April 15?"`.

Existing branches unchanged. `°C`/`°F` ranges, `or below`/`or higher` shoulders, `or lower`/`or above`/`or more` shoulder aliases all still parse identically.

### 1.3 `architecture/source_rationale.yaml`

Added `dr33_live_enablement` + `feature_flag` + `live_enabled_default: false` under `settlement_write` for traceability. Formal writer-identity registration aligned with DR-33-A plan.md.

### 1.4 `tests/test_harvester_dr33_live_enablement.py` (18 tests, all passing)

- T1-T3b: `_find_winning_bin` UMA-vote gate (4 cases: pending / YES-won / unexpected outcomes order / NO-won across all markets)
- T4-T4b: `_canonical_bin_label` all 4 shapes + round-trip through `_parse_temp_range`
- T5-T6b: `_write_settlement_truth` VERIFIED / QUARANTINED-outside-bin / QUARANTINED-no-obs paths
- T7: proxy connection proves `conn.commit()` is NOT called (caller owns txn)
- T8: feature flag OFF → `run_harvester` early-returns with `disabled_by_feature_flag` status; no data-plane mocks called
- T9: regression guard against re-introduction of unicode ≥/≤ shoulders (P-E C1 invariant)

---

## Section 2 — Test results

```
$ source .venv/bin/activate && pytest -q tests/test_harvester_dr33_live_enablement.py
..................                                                       [100%]
18 passed in 0.99s

$ pytest -q tests/test_schema_v2_gate_a.py tests/test_canonical_position_current_schema_alignment.py \
           tests/test_pe_reconstruction_relationships.py tests/test_harvester_dr33_live_enablement.py
41 passed, 7 subtests passed in 1.09s

$ pytest -q tests/ -k "parse_temp or market_scanner"
13 passed, 2389 deselected in 1.49s  (zero parser regressions)
```

---

## Section 3 — INV-FP-# coverage (same pattern as P-E)

| Invariant | Status | Evidence |
|---|---|---|
| INV-03 (append-first) | HONORED | Single `INSERT OR REPLACE` per settlement; canonical write path |
| INV-06 (point-in-time truth) | HONORED | `decision_time_snapshot_id = obs.fetched_at` |
| INV-08 (one transaction boundary) | HONORED | `_write_settlement_truth` doesn't commit; caller owns boundary |
| INV-14 (metric identity 4 fields) | HONORED | Every INSERT populates `temperature_metric='high'`, `physical_quantity='daily_maximum_air_temperature'`, `observation_field='high_temp'`, `data_version=per-source` |
| INV-FP-1 (provenance unbroken) | HONORED | 12-key provenance_json: writer, source_family, obs_source/id/fetched_at, rounding_rule, reconstruction_method, event_slug, reconstructed_at, audit_ref, + optional quarantine_reason |
| INV-FP-3 (temporal causality) | HONORED | decision_time_snapshot_id references obs.fetched_at |
| INV-FP-4 (SettlementSemantics gate MANDATORY) | HONORED | `SettlementSemantics.for_city(city).assert_settlement_value(...)` is the AUTHORITY GATE for 'VERIFIED'. Unlike P-E's pre-computed-at-dry-run pattern, DR-33 calls the canonical gate at every INSERT (it's a LIVE write path, not one-off reconstruction). |
| INV-FP-5 (authority earned, monotonic) | HONORED | authority='VERIFIED' only if SettlementSemantics rounds AND containment passes; else QUARANTINED with enumerable reason. `settlements_authority_monotonic` trigger (P-B) is NOT triggered by INSERT (BEFORE UPDATE OF authority syntax); CHECK constraint on authority enum still applies. |
| INV-FP-6 (write routes registered) | HONORED | source_rationale.yaml updated with DR-33 reference + feature_flag metadata |
| INV-FP-7 (source role boundaries) | HONORED | `_lookup_settlement_obs` strict per-source-type routing; no cross-family fallback |
| INV-FP-9 (NULL first-class) | HONORED | QUARANTINED rows → winning_bin=NULL; no_obs rows → settlement_value=NULL + obs_id=NULL + decision_time_snapshot_id=NULL |
| INV-FP-10 (re-derivation suspect) | HONORED | provenance_json.reconstruction_method='harvester_live_uma_vote' explicit |

Fatal misreads checked:
- `wu_website_daily_summary_not_wu_api_hourly_max`: preserved via data_version='wu_icao_history_v1' (not claiming website equivalence)
- `hong_kong_hko_explicit_caution_path`: preserved — SettlementSemantics.for_city routes HKO to oracle_truncate automatically
- `daily_day0_hourly_forecast_sources_are_not_interchangeable`: preserved — harvester consumes settlement_daily_source obs only; `_lookup_settlement_obs` enforces via source_type routing
- `code_review_graph_answers_where_not_what_settles`: preserved — harvester reads UMA vote, not graph output

R3 / AP regression scan: no regressions introduced; AP-11 non-reversal attestation preserved (docstring of `_find_winning_bin` explicitly notes P-D §5.3 distinction from removed `outcomePrices >= 0.95` pre-resolution fallback).

---

## Section 4 — What DR-33-A did NOT do (per plan §2 out-of-scope)

- No flag flip (ON is DR-33-C territory; requires operator approval + monitoring plan)
- No atomicity refactor of `append_many_and_project` callers (P-H / DR-33-B territory)
- No `_parse_temp_range` full rewrite with `re.fullmatch` (NH-E1 future hygiene; surgical extension applied for Gamma point-bin case only)
- No midstream T6.1/T6.2 P&L regression (still blocked on DR-33-C + ≥30 live settlements)

---

## Section 5 — Safety properties verified

- Default-OFF feature flag: T8 + manual env check confirm no runtime behavior change on deploy
- Rollback: `git revert` reverses; no DB state changed (code-only)
- Idempotency: `INSERT OR REPLACE` + UNIQUE(city, target_date) means re-runs are safe
- Fail-closed: `_find_winning_bin` has no silent pass-through; every branch is `continue` or explicit YES-won return
- Transaction boundary: `_write_settlement_truth` does NOT commit (T7 proves via proxy connection)

---

**DR-33-A execution complete. Code-only; flag OFF; no DB state changed. All 18 tests + 41 regression tests passing.**
