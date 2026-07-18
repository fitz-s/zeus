# P2-F ENS faster-mirror transport-equivalence licence — empirical proof (read-only)

Date: 2026-07-18. DB: `state/zeus-forecasts.db` (opened `?mode=ro`). No source edited, no worktree created.

## 1. Plan gate, verbatim (docs/operations/current/plans/upstream_data_physical_2026-07-17.md, "P2-F ENS FASTER MIRROR" line)

> **P2-F ENS FASTER MIRROR [BLOCKER on identity]**: never alias open-meteo ensemble to ecmwf_open_data product id (interpolated hourly + elevation downscaling + land-cell defaults = different product). Prefer their 3-hourly min/max extrema variables if conformance probe passes; disable elevation adjustment; match extraction operator. Two-stage licence: (i) transport equivalence 60 paired cycles/30 days, cluster by cycle, 100% member mapping, median |ext diff|<=0.05C p99<=0.25C, bias CI +-0.05C, spread ratio [0.95,1.05], recentered bin-prob MAD<=0.01; (ii) settlement equivalence: recentered CRPS UCB<=0.03C, coverage degradation<=2pp, no shoulder-bin degradation. Pass -> transport-equivalent carrier inherits calibration; fail -> new product (90 settled dates pooled, 60 city-specific). Whole-cycle fallback, never mix members across transports.

Identity rule applied here: the incumbent baseline is `tigge_mx2t6_local_calendar_day_max` / `tigge_mn2t6_local_calendar_day_min`, **model_version = `ecmwf_ens`** (the canonical 51-member ECMWF ENS, 6-hourly extraction operator). Any dataset row under a different `model_version` is a different product for identity purposes even if `dataset_id` matches, and any 3-hourly extraction is a different operator from the incumbent's 6-hourly operator per "match extraction operator."

## 2. Data reality (verified)

`ensemble_snapshots` unique key is `(city, target_date, temperature_metric, issue_time, dataset_id)`; `members_json` holds the 51-member ENS array per row. Distinct `(dataset_id, model_version)` combinations relevant to P2-F:

| dataset_id | model_version | rows | issue_time span |
|---|---|---|---|
| tigge_mx2t6_local_calendar_day_max | **ecmwf_ens** (incumbent HIGH) | 384,970 | 2024-01-01 → **2026-05-04** |
| tigge_mx2t6_local_calendar_day_max | ecmwf_ifs025 (NOT incumbent) | 69 | 2026-05-19 → 2026-05-28 |
| tigge_mn2t6_local_calendar_day_min | **ecmwf_ens** (incumbent LOW) | 384,202 | 2024-01-01 → **2026-05-02T12** |
| ecmwf_opendata_mx2t6_local_calendar_day_max (6h, operator-matched candidate) | ecmwf_ens | 1,342 | 2026-05-03 → 2026-05-07 |
| ecmwf_opendata_mn2t6_local_calendar_day_min (6h, operator-matched candidate) | ecmwf_ens | 508 | 2026-05-03 → 2026-05-03T12 |
| ecmwf_opendata_mx2t3_local_calendar_day_max (3h, different-operator candidate) | ecmwf_ens | 44,616 | 2026-05-06 → 2026-07-18 (live) |
| ecmwf_opendata_mn2t3_local_calendar_day_min (3h, different-operator candidate) | ecmwf_ens | 43,042 | 2026-05-15 → 2026-07-18 (live) |

**Critical fact**: the incumbent ENS ingestion (`model_version='ecmwf_ens'`) for both HIGH and LOW stopped at issue_time 2026-05-04 / 2026-05-02T12 respectively (last `recorded_at` 2026-05-16, a single backfill batch — no writes since). Today is 2026-07-18, 75 days later. The 69 `tigge_mx2t6` rows dated 2026-05-19→05-28 are tagged `model_version='ecmwf_ifs025'`, a **different product**, not the canonical incumbent.

## 3. Pairing (join on city, target_date, temperature_metric, issue_time), incumbent restricted to `model_version='ecmwf_ens'`

| Path | Metric | Paired rows | Distinct cycles | Distinct dates | Cities | Span |
|---|---|---|---|---|---|---|
| 6h operator-matched (opendata mx2t6 vs incumbent ENS) | HIGH | 168 | **2** | 4 | 34 | 2026-05-03→05-04 (1 day) |
| 6h operator-matched (opendata mn2t6 vs incumbent ENS) | LOW | 0 | 0 | 0 | 0 | none — no overlap window ever existed |
| 3h different-operator (opendata mx2t3 vs incumbent ENS) | HIGH | **0** | 0 | 0 | 0 | none |
| 3h different-operator (opendata mn2t3 vs incumbent ENS) | LOW | 0 | 0 | 0 | 0 | none |

The 3h path's apparent "69 paired rows / 12 cycles / 6 dates / 30 cities" (issue_time 2026-05-19→05-27) is a join against `model_version='ecmwf_ifs025'`, **not** the incumbent ENS. Per the plan's own identity rule that row set must be excluded — it is a different product/model, and using it to license a "faster mirror of the incumbent ENS" would be exactly the aliasing the gate forbids. Re-run restricted to `model_version='ecmwf_ens'` confirms **zero** valid paired cycles for the 3h path, either metric.

## 4. Stage-(i) statistics, restricted to valid ENS-vs-ENS pairs (member values unit-converted to °C via `members_unit`; extrema = max/min of non-null members per row; bias CI = 95% normal CI on cycle-clustered means)

### 6h operator-matched, HIGH (n=168 rows, 2 cycles, 4 target dates, 34 cities)
| Gate | Threshold | Measured | Result |
|---|---|---|---|
| paired cycles / days | ≥60 / ≥30d | 2 / 1 day | **FAIL** (30x short on cycles, 30x short on days) |
| median \|ext diff\| | ≤0.05°C | 0.84°C | **FAIL** (17x over) |
| p99 \|ext diff\| | ≤0.25°C | 8.73°C | **FAIL** (35x over) |
| bias CI | ±0.05°C | mean 0.696°C, CI (0.651, 0.742) | **FAIL** (CI entirely outside band, ~13x over) |
| spread ratio | [0.95, 1.05] | median 1.044 (row range 0.58–3.52) | borderline pass on median only; per-row dispersion is enormous |
| member mapping (slot completeness) | 100% | 7446/7446 = 100% | pass (all 51 slots present both sides) |
| recentered bin-prob MAD | ≤0.01 | not computed | moot — 3 gates above already fail by an order of magnitude |

### 3h different-operator path
No valid ENS-vs-ENS paired row exists (see §3). **Every stage-(i) statistic is undefined — there is no evidence to run the conformance probe on.**

### LOW metric, both paths
No valid paired row exists (see §3). **Undefined for the same reason.**

## 5. Stage (ii) settlement equivalence

Not attempted. Per the plan's own gating ("Pass -> ... inherits calibration; fail -> new product"), stage (ii) is only reached after stage (i) passes. Stage (i) does not pass on any path — the 6h path fails decisively on available (if underpowered) evidence, and the 3h/LOW paths have zero valid evidence at all.

## 6. Verdict

**3h conformance path: NOT LICENCE-ISSUABLE — zero valid paired cycles.** The only apparent pairing (69 rows) matches the incumbent's `dataset_id` against a different `model_version` (`ecmwf_ifs025`), which is a different product under the plan's identity rule; excluding it (correctly) leaves zero ENS-vs-ENS 3-hourly-vs-6-hourly pairs in history. There is no data to run the conformance probe on, so "if conformance probe passes" cannot be answered yes — it can't be evaluated at all.

**6h operator-matched path: genuinely accumulation-gated, and the accumulation window is currently closed.** Only 2 paired cycles / 1 day / 168 rows exist (HIGH only; LOW has 0). Need 58 more paired cycles across ≥29 more days for HIGH, and the full 60/30d for LOW from scratch (LOW has never had an overlapping issue-time window between incumbent and candidate). Beyond being short, on the sample that does exist the transport-equivalence hypothesis **fails**, not just "insufficient n": median diff 0.84°C vs 0.05°C budget, p99 8.73°C vs 0.25°C budget, bias CI (0.65,0.74)°C vs ±0.05°C budget. And critically, no further pairs can accumulate at all: incumbent ENS ingestion into `ensemble_snapshots` (`tigge_mx2t6`/`tigge_mn2t6`, `model_version='ecmwf_ens'`) stopped on 2026-05-04/05-02 (single backfill batch, `recorded_at` 2026-05-16) and has not resumed in the 75 days since, while the candidate (`ecmwf_opendata_mx2t3`) keeps ingesting live through today. Waiting does not accumulate paired cycles; only resuming incumbent ENS ingestion in parallel with the candidate would.

**Conclusion: no P2-F licence can be issued now via either path. This is not a "wait longer" gap for the 3h path — it is a zero-evidence gap requiring incumbent ENS ingestion to be resumed in parallel with the candidate before any pairing, let alone a 60-cycle/30-day licence, becomes possible.**
