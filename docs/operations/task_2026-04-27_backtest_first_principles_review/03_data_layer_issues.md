# 03 — Data Layer Issues (Disk-Verified)

Created: 2026-04-27
Last reused/audited: 2026-04-27
Authority basis: live SQL probes against `state/zeus-world.db` and `state/zeus_trades.db` 2026-04-27, `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/12_major_findings.md`, `docs/operations/current_data_state.md`
Status: planning evidence; not authority. All row counts probed live within the 30-min audit window of this writing.

---

## 1. Why this doc exists separately

Backtest depends on data substrates that are not yet ready. The forensic audit (2026-04-23) enumerated the issues; this doc projects them onto the **backtest axis** with disk-verified evidence and ranked priority by which backtest purpose each unblocks.

Every claim in §2 was probed live on 2026-04-27. Memory L20 grep-gate applied throughout.

---

## 2. Issue register (disk-verified, ranked by backtest impact)

### 2.1 — `market_events*` empty (CRITICAL — economics blocker)

**Probe:**
```sql
SELECT COUNT(*) FROM market_events;          -- 0
SELECT COUNT(*) FROM market_events_v2;       -- 0
SELECT COUNT(*) FROM market_price_history;   -- 0
```

**Forensic ref:** F13 ("Market event tables are empty — Polymarket REPLAY IMPOSSIBLE")

**Backtest impact:** the entire `BacktestPurpose.ECONOMICS` lane is structurally impossible. The current replay's `_assert_market_events_ready_for_replay()` ([replay.py:1622-1718](../../../src/engine/replay.py:1622)) correctly raises `ReplayPreflightError` when these tables are empty (unless `allow_snapshot_only_reference=True`, which is the diagnostic-only escape hatch).

**External reality calibration (verified 2026-04-27 against polymarket.com + docs.polymarket.com):**

- 361 live temperature markets exist right now → historical backlog is large
- CLOB API has `getTrades` / `getTradesPaginated` for trade history
- ⚠ **CORRECTED (see [04 §C4](04_corrections_2026-04-27.md#c4-polymarket-no-public-historical-archive-api))**: Polymarket has 4 data layers (Gamma API, public Subgraph with `orderbook-subgraph`, Data API REST `/trades`, WebSocket Market Channel). Trade history IS retrievable; orderbook-snapshot retention via subgraph still unverified (04 §3 U4).
- WebSocket "Market Channel" is the canonical real-time source; capture must be done forward-only
- ⚠ **CORRECTED (see [04 §C3](04_corrections_2026-04-27.md#c3-polymarket-us-weather-market-resolution-source))**: US weather markets verified verbatim use **Wunderground** (KLGA/KORD/KMIA/KLAX) — Zeus's settlement_source matches reality. The earlier "NOAA stations" framing here was a WebSearch hallucination.

**Implication.** Even ingesting Polymarket data won't automatically align with Zeus's existing `settlements` table because:

- `settlements.settlement_source` is dominated by `wunderground.com` URLs (1400/1469 rows verified 2026-04-27)
- Polymarket's resolution oracle for US markets is NOAA
- F9 ("Settlement source cannot join to observation source") is a **real semantic gap**, not a join-syntax issue

**Required work (separate packet):**

1. Polymarket WebSocket capture (Q3 from plan.md §5).
2. Per-market resolution-source check during ingestion: classify market as `RESOLUTION_SOURCE_MATCHES_ZEUS_OBS` vs `RESOLUTION_SOURCE_MISMATCH`.
3. Mismatch markets enter a quarantine bucket — NOT used for training/economics until Zeus also ingests the matching observation source (NOAA / WU/VHHH for HK / etc.).

**Backtest design accommodation:** the `economics.py` tombstone in `01_backtest_upgrade_design.md` §3.C remains in place until `market_events_v2 > 0` AND the resolution-source-match cohort is ≥ MIN_DECISION_GROUPS.

---

### 2.2 — Empty WU observation provenance (CRITICAL — training/SKILL blocker)

**Probe:**
```
source=wu_icao_history: total=39,437, empty_provenance=39,431 (99%)
source=ogimet_metar_*: total=2,491, empty_provenance=0 (0%)
source=hko_daily_api: total=821, empty_provenance=0 (0%)
```

**Forensic ref:** F5 (CRITICAL) + H4

**Backtest impact:**

- **SKILL** lane: training-eligibility filter rejects empty-provenance rows; if all 39k rows are filtered, only 6 WU rows + 3,312 non-WU rows = 3,318 of 42,749 = **7.8%** of observations remain. Significant scale loss.
- **DIAGNOSTIC** lane: same impact, since calibration depends on the same observation substrate.
- **ECONOMICS** lane: secondary impact — economics is already gated by 2.1.

**Reconstruction feasibility check (verified 2026-04-27):**

| Dimension | Settlements | Oracle Shadow | Overlap |
|---|---|---|---|
| Date range | 2025-12-30 → 2026-04-16 | 2026-04-15 → 2026-04-26 | **2 dates** (2026-04-15, 2026-04-16) |
| Cities | 50 | 48 | ~48 (mostly overlapping) |
| Files | — | 480 (48 × 10) | ~96 (48 × 2 dates) |

**Conclusion:** oracle_shadow_snapshots covers ~96 of 39,431 empty-provenance rows = **0.24%**. It cannot meaningfully backfill historical empty provenance. It IS authoritative going-forward, however — the format already includes `wu_raw_payload`, `station_id`, `captured_at_utc`, `source`, `data_version`. So the going-forward writer should be hardened against ever producing empty provenance again, while history is handled by quarantine.

**Required work (separate packet):**

1. Quarantine: `UPDATE observations SET authority='QUARANTINED', quarantine_reason='empty_provenance_wu_daily_pre_2026-04-15'` for the ~39,335 rows pre-2026-04-15.
2. Backfill from oracle_shadow_snapshots: for the ~96 overlap rows (2026-04-15, 2026-04-16) populate `provenance_metadata` from the corresponding shadow file. Hash matches; explicit provenance_method = `oracle_shadow_backfill_v1`.
3. Forward-going writer hardening: `wu_history_writer.py` (or wherever the WU daily writer lives) MUST require `provenance_metadata` payload-hash + source URL + parser version (per forensic Fix 4.3.B).
4. Antibody test: relationship test that asserts NO `wu_icao_history` row can be inserted with empty provenance going forward.

**Operator decision needed:** Q5 from plan.md.

---

### 2.3 — `forecasts.forecast_issue_time` NULL on every row (HIGH — F11 hindsight risk)

**Probe:**
```
forecasts: 23,466 rows
sample row keys: [..., 'forecast_basis_date', 'forecast_issue_time', 'lead_days', ..., 'raw_payload_hash', 'captured_at', 'authority_tier']
sample values: forecast_issue_time = None, raw_payload_hash = None, captured_at = None, authority_tier = None
```

**Forensic ref:** F11 ("Forecast `available_at` may be reconstructed → hindsight leakage risk")

**Backtest impact:** the entire `decision_time_truth.AvailabilityProvenance` axis is sitting at NULL or `RECONSTRUCTED`. Every backtest using `forecasts` rows currently violates INV-06 (point-in-time truth) silently. The current `_forecast_reference_for()` ([replay.py:326-362](../../../src/engine/replay.py:326)) emits `decision_reference_source: "forecasts_table_synthetic"` and `decision_time_status: "SYNTHETIC_MIDDAY"` — i.e., it knows the source is non-canonical. But downstream consumers don't enforce.

**External calibration (verified 2026-04-27 at ecmwf.int):**

- ⚠ **CORRECTED (see [04 §C1](04_corrections_2026-04-27.md#c1-ecmwf-ens-dissemination-lag))**: ENS Day 0 = `base + 6h40m`; Day 15 = `base + 7h40m` (Confluence wiki). Earlier "40 minutes" claim was a misread of "40 min earlier" delta.
- Other model runs (GFS, ICON, UKMO, OpenMeteo) have their own published schedules — must be cited similarly
- The `forecasts.source` column distribution shows: `openmeteo_previous_runs`, `gfs_previous_runs`, `ecmwf_previous_runs`, `icon_previous_runs`, `ukmo_previous_runs` — five distinct dissemination calendars

**Required work (parallel-safe code slice):**

1. Extend `forecasts` schema or use `provenance_json` to carry `availability_provenance` ∈ {`FETCH_TIME`, `RECORDED`, `DERIVED_FROM_DISSEMINATION`, `RECONSTRUCTED`}.
2. Implement `decision_time_truth.derive_availability(source, base_time)` for the 5 model sources, each with cited dissemination schedule. Tests cite the canonical URL.
3. Backfill the 23,466 existing rows: `availability_provenance = "DERIVED_FROM_DISSEMINATION"` since `forecast_issue_time` is NULL but `forecast_basis_date` exists. Going-forward writer must require `RECORDED` when source headers permit.
4. Training-eligibility view rejects `availability_provenance IN ('reconstructed', NULL)`.

**Antibody test:** `tests/test_forecasts_availability_provenance_required.py`.

---

### 2.4 — `observation_instants_v2` INV-14 columns 100% NULL on existing rows (HIGH)

**Probe:**
```sql
SELECT temperature_metric, COUNT(*) FROM observation_instants_v2 GROUP BY temperature_metric;
-- (None, 1813662)

SELECT source_role, COUNT(*) FROM observation_instants_v2 GROUP BY source_role;
-- (None, 1813662)
```

**Forensic ref:** C7 + C8 + F7 + F8

**Backtest impact:**

- 1.81 million rows have NULL on every INV-14 column added by C7's ALTER (`temperature_metric`, `physical_quantity`, `observation_field`, `data_version`, `training_allowed`, `causality_status`, `source_role`)
- `training_allowed DEFAULT 1` means **all 1.81M rows are silently training-eligible** despite the writer never populating these fields
- A reader filtering on `WHERE temperature_metric='high'` gets ZERO rows; a reader NOT filtering silently mixes everything
- Backtest using these rows produces metric-confused calibration data

**Required work (separate packet, P3 territory):**

1. Decide design Q (forensic §14): is metric tagged at instant-level (1.81M rows × per-row metric) or at daily-aggregate layer? This is operator/architecture decision **blocked**.
2. If instant-level: writer extension to populate INV-14 fields; backfill script for 1.81M rows.
3. If daily-aggregate: revert C7 ALTER; create `daily_observations_v2` instead.
4. Reader gate (F7 antibody): every consumer of `observation_instants_v2` rejects NULL-metric rows.

**Backtest design accommodation:** the new `decision_time_truth` loader does NOT read from `observation_instants_v2` (it reads from `ensemble_snapshots` for forecasts and `settlements` for ground truth). So this issue does not directly block backtest, but does block training and Day0 signal computation.

---

### 2.5 — `settlements` is 100% HIGH-track; LOW-track structurally absent

**Probe:**
```sql
SELECT temperature_metric, COUNT(*) FROM settlements GROUP BY temperature_metric;
-- ('high', 1561)
-- (no 'low' rows)
```

**Forensic ref:** C4 + F3

**Backtest impact:** any backtest scoped to LOW track has zero ground truth. SKILL/DIAGNOSTIC/ECONOMICS for LOW are 0% feasible until LOW writer + reconstruction lands.

**External reality:** Polymarket has both HIGH and LOW temperature markets (visible at polymarket.com/weather/temperature). If Zeus intends LOW-track production trading, the writer gap is a market-coverage gap, not just a backtest gap.

**Required work:** separate packet. Operator answers Q4. Out of this packet's scope.

**Backtest design accommodation:** the `BacktestPurpose` runtime contract requires explicit `metric` parameter; LOW-track raises `LowTrackUnavailable` until writer + data land.

---

### 2.6 — `settlements` settlement_source ≠ Polymarket resolution source (HIGH — semantic gap)

**Probe:**
```sql
-- 1400/1469 (95%) settlement_source rows are wunderground.com URLs
-- vs Polymarket US weather markets typically resolved against NOAA
```

**Forensic ref:** F9

**Backtest impact:**

- Even with `market_events_v2` populated (issue 2.1), the join `settlements ⨝ market_events` may resolve **wrong** for the US market cohort (NYC, Chicago, Atlanta, Dallas, Miami, Seattle, Houston, San Francisco, Los Angeles, Austin, Denver — major US cities).
- Zeus's `settlement_value` from WU may not match Polymarket's actual resolved value from NOAA.
- Backtest using these mismatched settlements produces silently-wrong PnL (economics) and silently-wrong outcomes (skill).

**Required work:**

1. Per-market resolution-source classifier: `RESOLUTION_SOURCE_AGREED` vs `RESOLUTION_SOURCE_DIVERGED`.
2. For DIVERGED markets: either ingest the matching observation source (NOAA for US), OR exclude from training.
3. The HK 2026-03-13/14 case (per `known_gaps.md`) is the canonical instance: Polymarket used WU/VHHH airport, Zeus has HKO observatory.

**Required research (operator):** per-city, per-period source authority audit. Forensic Q4 ("What Polymarket high/low market universe is in scope?") is a precondition.

---

### 2.7 — `ensemble_snapshots*` empty (HIGH — blocks SKILL with full ENS, blocks ECONOMICS)

**Probe:**
```sql
SELECT COUNT(*) FROM ensemble_snapshots;       -- 0
SELECT COUNT(*) FROM ensemble_snapshots_v2;    -- 0
```

**Forensic ref:** F1 + C1 + H6

**Backtest impact:**

- Without `ensemble_snapshots`, the SKILL lane falls back to `forecasts_table_synthetic` ([replay.py:326](../../../src/engine/replay.py:326)) with `decision_time_status: "SYNTHETIC_MIDDAY"`. This is a **real degradation** — synthetic decision-time, not an actual Zeus snapshot.
- The full probability chain (51 ENS members → MC noise → P_raw) cannot be replayed; only `forecasts.forecast_high` per-source aggregates are available.
- TIGGE data is downloaded on cloud but not yet ingested locally per current_data_state.md §9 + handoff §C1/C2.

**Required work:**

1. Operator: TIGGE rsync cloud → local (3.B in handoff Fix 4.7).
2. Data-engineering: `scripts/ingest_grib_to_snapshots.py` runs against local TIGGE; populates `ensemble_snapshots_v2`.
3. Source-time verification: `available_at` from TIGGE source headers, never reconstructed (F11 antibody applies here too).

**Backtest design accommodation:** SKILL lane works today against `forecasts_table_synthetic` with explicit downgrade label; full-fidelity SKILL via `ensemble_snapshots_v2` becomes available after TIGGE ingest.

---

### 2.8 — `zeus_trades.db` is 100% empty (HIGH — DIAGNOSTIC corpus blocker)

**Probe:**
```
position_events: 0
trade_decisions: 0
chronicle: 0
shadow_signals: 0
venue_commands: 0
```

**Backtest impact:** the `DIAGNOSTIC` purpose, scoped against historical decisions, has zero corpus. The legacy `state/zeus.db` may have older records but is not canonical per `current_data_state.md`.

**Required work:**

1. Probe `state/zeus.db` (legacy) for residual `trade_decisions` history. If usable as DIAGNOSTIC-only (with explicit legacy provenance flag), it can seed a corpus.
2. Live restart (separate packet, gated on auto_pause tombstone decision).
3. Forward-only DIAGNOSTIC: instrument the live engine to capture decisions; build corpus over weeks.

---

### 2.9 — `data_coverage` 350,088 rows; lacks v2 forecast/settlement family tracking

**Probe:** 350,088 rows in `data_coverage` (verified 2026-04-27).

**Forensic ref:** F14

**Backtest impact:** the data-immune-system memory exists but doesn't track `ensemble_snapshots_v2`, `settlements_v2`, `calibration_pairs_v2`, `market_events_v2`. Any "is the substrate ready?" question against v2 silently returns "no row in coverage = no expectation = OK", which is wrong.

**Required work (separate packet):** extend `data_coverage` schema or add a v2 sibling that tracks the v2 family with its own LEGITIMATE_GAP / WRITTEN / MISSING / FAILED grammar. Out of this packet's scope.

---

## 3. Cross-cutting issues

### 3.A — Provenance is mono-source-failed, not systemic

99% empty provenance is a single-writer defect. Fixing the WU writer + going-forward enforcement is a small code change. Backfill is the expensive part.

### 3.B — F11 hindsight is real on disk, not theoretical

23,466 NULL `forecast_issue_time` rows make F11 a present-tense risk, not a future-tense risk. The new `availability_provenance` typed enum is the antibody.

### 3.C — Polymarket is opaque to Zeus historically

⚠ **CORRECTED (see [04 §C4](04_corrections_2026-04-27.md#c4-polymarket-no-public-historical-archive-api))**: Polymarket DOES expose historical infrastructure (Subgraph + Data API + Gamma API). The previous framing ("no public archive → physics constraint") was overstated. The real constraint is narrower: **historical orderbook snapshots at arbitrary timestamps** (vs trade events) may not be retained in the subgraph (U4 unverified). Trade event history is fully retrievable. ECONOMICS purpose is gated by ingestion code work, not absence of upstream data.

### 3.D — Going-forward truth is reachable, history is not

For all five issues 2.1–2.5 the going-forward fix is small (writer hardening + capture). The backfill is large and in some cases impossible. Backtest design must accept this asymmetry.

---

## 4. Minimum data state for each backtest purpose

This is the structural truth the upgrade design encodes. Each row says: "to run purpose X you must have data state Y."

| Purpose | Minimum data state | Today (2026-04-27)? |
|---|---|---|
| **SKILL** (with `forecasts_table_synthetic` fallback) | `settlements > 0` AND any decision-time vector source available | ✓ runnable |
| **SKILL** (full-fidelity via ensemble_snapshots_v2) | + `ensemble_snapshots_v2 > 0` (after TIGGE ingest) | ✗ blocked by 2.7 |
| **SKILL** (training-eligible filter) | + provenance gate: `availability_provenance ∈ {FETCH_TIME, RECORDED, DERIVED_FROM_DISSEMINATION}` AND `authority='VERIFIED'` AND non-empty observation provenance | ✗ blocked by 2.2 + 2.3 (until backfill + writer hardening) |
| **DIAGNOSTIC** | `decision_log.artifact_json` OR `trade_decisions > MIN` rows in canonical scope | ✗ blocked by 2.8 (probe legacy `zeus.db` first) |
| **ECONOMICS** | + `market_events_v2 > 0` (with resolution-source-match cohort) AND `market_price_history > 0` AND parity at full | ✗ structurally blocked by 2.1 + 2.6 |

---

## 5. Priority ranking (by backtest unblock impact)

| # | Issue | Unblocks | Effort | Priority |
|---|---|---|---|---|
| 1 | 2.3 — `forecasts.forecast_issue_time` typed | SKILL training-eligibility | Code (small) + backfill (medium) | **P0 — code-only, ship now** |
| 2 | 2.2 — empty WU provenance triage | SKILL training-eligibility | Operator decision + backfill | **P0 — Q5 needed** |
| 3 | 2.5 — LOW-track decision (Q4) | LOW-track scope | Operator decision | **P0 — Q4 needed** |
| 4 | 2.7 — TIGGE rsync + ingest | full-fidelity SKILL | Operator + data-eng | **P1** |
| 5 | 2.4 — `observation_instants_v2` INV-14 backfill | training/Day0, NOT direct backtest | Architecture decision (forensic §14 Q7) | **P1 — separate packet** |
| 6 | 2.8 — `zeus_trades.db` corpus | DIAGNOSTIC | Live restart (separate) | **P2 — gated on operator** |
| 7 | 2.1 — `market_events_v2` populated | ECONOMICS | Operator + data-eng + external archive | **P2 — long lead** |
| 8 | 2.6 — resolution-source classifier | ECONOMICS quality | Per-market audit | **P3 — after 2.1** |
| 9 | 2.9 — `data_coverage` v2 extension | hygiene | Data-eng | **P3 — separate packet** |

---

## 6. Memory-rule applications

- **L20 grep-gate**: every row count in this doc was probed live within 30 minutes of writing.
- **L22 commit boundary**: implementation packets MUST NOT autocommit before critic review.
- **L28 critic-baseline**: critic re-runs all SQL probes in this doc; team-lead row counts may drift if ingest tick happens between writing and review.
- **Fitz Constraint #4 (data provenance)**: this entire doc IS the application of the principle. Every row count carries a `source` (live SQL probe) and `authority` (verified at 2026-04-27).
