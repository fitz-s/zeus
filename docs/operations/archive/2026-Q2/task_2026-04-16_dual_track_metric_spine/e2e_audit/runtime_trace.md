# Dual-Track Metric Spine — End-to-End Runtime Trace

**HEAD**: `0a760bb` (P9C close) · **Branch**: `data-improve` · **Audit date**: 2026-04-18
**Question**: If Golden Window lifts tomorrow and real LOW TIGGE data arrives, what actually happens?
**Stance**: Read-only forensic. Phase contracts are not evidence of runtime correctness.

---

## OBSERVE → FRAME

Observed artifact: the code claims LOW is wired end-to-end after P9C. The claim rests on
three P9C seam fixes (A1 conditional v2 read, A3 LOW `p_vector`, A4 DT#7 gate, L3 metric-
aware `get_calibrator`, B1 CLI flag). What is **not** directly observed is a single LOW
GRIB passing through all four scenarios — Golden Window blocks live LOW data, so every
"works" claim is architectural, not empirical.

---

## SCENARIO A — LOW historical rebuild (script batch)

### Hypotheses

| Rank | Hypothesis                          | Confidence | Evidence strength | Why plausible                                      |
|------|-------------------------------------|------------|-------------------|----------------------------------------------------|
| 1    | **A2′ Clean landing w/ LOW-only iteration** | High       | Strong            | Scripts iterate `METRIC_SPECS`; column dispatch metric-aware |
| 2    | Data-present code-partial (`extract` writes ok, `rebuild` can't read) | Medium     | Moderate          | Depends on v2 snapshot schema carrying `low_temp` column |
| 3    | Loud LOW failure (data_version quarantine) | Medium     | Moderate          | `assert_data_version_allowed` gate at L227 can refuse |
| 4    | Silent HIGH substitution            | Very Low   | Weak              | refit_platt_v2 key carries `temperature_metric` (store.py:375) |
| 5    | Crosstalk (HIGH v2 polluted by LOW refit) | Low        | Weak              | `save_platt_model_v2` UNIQUE(model_key) includes metric — rows separate |

### Evidence For / Against

- **H1**: `scripts/rebuild_calibration_pairs_v2.py:174` dispatches column by
  `spec.identity.temperature_metric == "high"` else `low_temp`. `refit_platt_v2.py:299`
  loops `for spec in METRIC_SPECS:` and always writes via `save_platt_model_v2(metric_identity=...)`.
  `store.py:375` composes `model_key = f"{metric}:{cluster}:{season}:{data_version}:{input_space}"`
  — **metric is in the primary key**, HIGH and LOW rows never collide.
- **H2**: `rebuild_calibration_pairs_v2.py:86` filters source snapshots by
  `WHERE temperature_metric = ?` — assumes `ensemble_snapshots_v2` rows were written
  with correct `temperature_metric`. The **ingest seam** (`extract_tigge_mn2t6_localday_min.py`)
  is authoritative for that column; was not re-audited in this pass.
- **H3**: `assert_data_version_allowed` runs at line 227 inside the rebuild loop. An
  unapproved `data_version` tag (e.g. operator types `tigge_mn2t6_v2` instead of the
  allow-listed tag) kills the rebuild **loudly** with a RuntimeError — fail-closed is
  correct, but operator friction is real.
- **H4/H5 against**: `_fit_from_pairs` (manager.py:263) early-returns when
  `temperature_metric != "high"` with a dedicated "on-the-fly refit is HIGH-only per
  P9C.1 two-seam law" log — **this is explicit anti-crosstalk antibody** on the write side.

### Verdict
**Most likely: clean landing, contingent on `extract_tigge_mn2t6_localday_min.py` correctly
tagging ingest rows with `temperature_metric='low'`.** That ingest correctness is the
hinge I did not directly audit and is the single most important probe before lifting Golden
Window.

---

## SCENARIO B — LOW evaluator replay (shadow)

### Hypotheses

| Rank | Hypothesis                          | Confidence | Evidence strength | Why plausible                                    |
|------|-------------------------------------|------------|-------------------|--------------------------------------------------|
| 1    | Clean landing — LOW Platt read + LOW columns | Medium-high | Moderate        | v2-first + metric kwarg threaded end-to-end      |
| 2    | **Data-present code-partial: legacy fallback at `forecasts` table drops LOW** | Medium | Moderate | replay.py:309 `WHERE forecast_high IS NOT NULL` — HIGH-only filter |
| 3    | Silent HIGH substitution via `forecast_col` default | Low        | Weak          | default kwarg = "high" at multiple signatures    |
| 4    | Loud failure (empty `historical_forecasts_v2`) | Medium     | Moderate      | Returns 0 rows cleanly; no coverage — not silent |
| 5    | Crosstalk via FDR family collision  | Low        | Weak-moderate     | family_id lacks `temperature_metric` field       |

### Evidence For / Against

- **H1 for**: `run_replay(temperature_metric='low')` (replay.py:1995) → threads to
  `_replay_one_settlement` (L1167) → `get_decision_reference_for(... metric)` (L1189) →
  `_forecast_rows_for` v2-first branch at L266-296 uses `AND temperature_metric = ?` and
  translates per-row metric to legacy dual-column shape. `get_calibrator(..., metric)`
  at L1252 is now metric-aware (manager.py:124). Column dispatch at replay.py:335 picks
  `forecast_low` vs `forecast_high` correctly.
- **H1 against / H2 for (CRITICAL latent failure)**: `_forecast_rows_for` legacy fallback
  branch at **replay.py:309** still has
  `WHERE city = ? AND target_date = ? AND forecast_high IS NOT NULL`
  — hardcoded HIGH NULL-guard. When v2 has zero rows (Golden Window OR very old date
  never backfilled) AND legacy `forecasts` has the row but only `forecast_low`
  populated (not a current code path, but schema allows), LOW replay returns empty.
  For the common case (Golden Window intact, no v2 LOW, legacy HIGH-only), the fallback
  returns HIGH rows that then get read through the `forecast_low` column at L336 →
  **`float(row['forecast_low'])` on a None field → TypeError → caught upstream → no
  coverage, not silent HIGH substitution**. H3 therefore down-ranks.
- **H5 for**: `make_hypothesis_family_id` (selection_family.py:28) composes key as
  `hyp|cycle_mode|city|target_date|discovery_mode|snapshot_id` — **no temperature_metric**.
  Two concurrent candidates (HIGH and LOW) for the same (city, target_date) with the
  same snapshot_id would share a family_id and **pool into a single BH denominator**.
  **However**, the BH is applied within a single `evaluate_candidate` invocation's rows
  (evaluator.py:531 `apply_familywise_fdr(rows)` on rows produced from one candidate),
  so cross-metric pooling across different MarketCandidate objects does not currently
  happen in one call. The collision risk is **latent** for any future aggregation lane
  that concatenates rows across candidates before calling `apply_familywise_fdr`.
- **H5 against**: Each candidate carries its own `decision_snapshot_id` and each
  candidate is evaluated independently — practical cross-metric FDR dilution is zero
  in the current hot path.

### Verdict
**Most likely: LOW replay runs end-to-end and produces real coverage once v2 is populated,
but coverage drops to 0% at any city-date where only legacy `forecasts` rows exist** —
because the legacy fallback branch is HIGH-shaped. That is fail-closed (no wrong number),
but operators must not conclude "LOW replay broken" when the real cause is empty v2
table.

---

## SCENARIO C — LOW Day0 candidate in live cycle (entry)

### Hypotheses

| Rank | Hypothesis                          | Confidence | Evidence strength | Why plausible                                  |
|------|-------------------------------------|------------|-------------------|------------------------------------------------|
| 1    | Clean landing — typed dispatch all the way | High       | Strong            | MetricIdentity + Day0Router + for_metric dataclass |
| 2    | Loud failure at DT#7 gate (fail-closed) | Medium     | Moderate          | boundary_ambiguous=1 refuses; correct behavior |
| 3    | Data-present code-partial (observation.low_so_far=None rejection) | Medium | Moderate  | evaluator.py:876 gates explicitly              |
| 4    | Silent HIGH substitution            | Very Low   | Weak              | Router dispatches on `temperature_metric.is_low()` first |
| 5    | Crosstalk (oracle_penalty per-city only) | Low-med    | Moderate          | Oracle blacklist fires for ALL metrics of city |

### Evidence For / Against

- **H1**: `_normalize_temperature_metric` at evaluator.py:707 wraps the raw candidate
  string into typed `MetricIdentity`. `Day0Router.route` at day0_router.py:54 branches
  on `inputs.temperature_metric.is_low()` and returns `Day0LowNowcastSignal` with LOW
  ceiling semantics. `Day0LowNowcastSignal.p_vector(bins)` (P9C A3) is LOW-specific with
  no HIGH delegation. `remaining_member_extrema_for_day0` at day0_window.py:67 branches
  `if temperature_metric.is_low(): arr = slice_data.min(axis=1)` — metric-aware member
  reduction. `get_calibrator(..., metric)` at evaluator.py:964 reads LOW Platt.
- **H2**: DT#7 gate at evaluator.py:728-751 reads v2 metadata for
  `(city, target_date, metric)` and refuses if `boundary_ambiguous=1`. Pre-Golden-Window
  the helper returns `{}` → gate is a no-op. Post-lift with LOW ingest, the gate fires
  correctly — this is an **antibody, not a bug**.
- **H3**: `evaluator.py:876` `if temperature_metric.is_low() and candidate.observation.low_so_far is None`
  → fail-closed rejection with `OBSERVATION_UNAVAILABLE_LOW` stage. Day0LowNowcastSignal
  constructor at day0_low_nowcast_signal.py:28 **also** raises `ValueError` if
  `observed_low_so_far is None` — double-guard. Correct.
- **H5**: `oracle_penalty.py:23-25` loads a single JSON with one entry per **city name**;
  no metric partition. A city oracle-blacklisted for HIGH errors would block LOW entries
  too, **even if the LOW track is clean**. This is crosstalk by shared-table design and
  is a real concern when LOW goes live — the oracle_error_rates.json historically only
  tracks HIGH errors (pre-dual-track), so LOW positions inherit a penalty they didn't
  earn.

### Verdict
**Most likely: LOW Day0 entry completes cleanly, with one structural concern:**
oracle_penalty shared-table sizing penalty applies LOW positions penalties accrued from
HIGH history. Blast radius = Kelly size reduction, not wrong direction. Not fail-closed
but quantitatively biased.

---

## SCENARIO D — LOW position monitoring + exit

### Hypotheses

| Rank | Hypothesis                          | Confidence | Evidence strength | Why plausible                                    |
|------|-------------------------------------|------------|-------------------|--------------------------------------------------|
| 1    | **Silent LOW Day0 monitor failure (NameError swallowed)** | **High**   | **Strong**        | `remaining_member_maxes` undefined at L355, L405 |
| 2    | Clean landing — metric-aware calibrator + Day0Router | Medium-low | Moderate     | get_calibrator + Day0Router correctly threaded   |
| 3    | Crosstalk — non-Day0 lane lacks metric awareness | Medium | Moderate              | L135-140 passes metric kwarg; ok                 |
| 4    | Loud failure (calibrator raises on LOW) | Very Low   | Weak              | load_platt_model_v2 returns None cleanly         |
| 5    | Data-present code-partial (exit_lifecycle drops metric) | Low        | Weak          | DT#2 sweep iterates positions without metric check (OK) |

### Evidence For / Against

- **H1 (CRITICAL — latent bomb)**: `src/engine/monitor_refresh.py:355`
  `ensemble_spread = TemperatureDelta(float(np.std(remaining_member_maxes)), city.settlement_unit)`
  and `src/engine/monitor_refresh.py:405` `"member_maxes": remaining_member_maxes,` —
  **`remaining_member_maxes` is never defined anywhere in that function**. The refactor
  to `extrema = remaining_member_extrema_for_day0(...)` (L300) replaced the old variable
  name but these two call sites were not updated.

  Impact: any Day0 position (HIGH or LOW) hitting `_refresh_day0_observation` raises
  `NameError: name 'remaining_member_maxes' is not defined` at L355. The NameError
  propagates up to `refresh_position` (L591) which wraps the whole recompute in
  `try: ... except Exception as e: logger.debug(...)` at L614. **Exception is swallowed
  at DEBUG level; position `last_monitor_prob` stays stale; `last_monitor_prob_is_fresh`
  stays at whatever it was (potentially still True from a prior cycle).** No operator
  alert, no RED signal. Exit decisions downstream then run on stale probability.

  This is a **pre-existing HIGH bug** that was not introduced by the dual-track refactor
  — it affects both tracks equally — but the P9C work **did not surface it** because
  Day0 monitor path is lightly exercised in production (most positions are ENS-member-
  counting entries, not Day0). For LOW this means the monitor lane **has never been
  clean in any production run, past or future**, until this is fixed.

- **H2 for partial**: For ENS-member-counting positions (non-Day0 state),
  `_refresh_ens_member_counting` path at L135-140 threads `temperature_metric` to
  `get_calibrator` correctly. That lane **does** work for LOW as of P9C L3.
- **H3/H4/H5**: Calibrator fallback at manager.py:165 skips legacy table for non-HIGH
  metrics, so LOW miss returns `None` cleanly (level=4, uncalibrated → p_raw passes
  through). Exit decision runs on p_raw, not HIGH Platt. DT#2 sweep at cycle_runner.py:84
  iterates positions metric-agnostically; LOW positions get swept correctly into
  `exit_reason="red_force_exit"`.

### Verdict
**Most likely outcome for LOW Day0 positions: monitor silently fails, position exits
happen with stale probability.** For LOW ENS-member positions: clean.

---

## TOP 3 SILENT-FAILURE RISKS (ranked by blast radius)

### #1 — Day0 monitor NameError swallowed (BOTH tracks)
**File:line**: `src/engine/monitor_refresh.py:355` and `:405`
**Quote**: `np.std(remaining_member_maxes)` and `"member_maxes": remaining_member_maxes`
**Blast radius**: Every Day0 LOW **and** HIGH position. Exit decisions run on stale
`p_posterior` from up to 24 h earlier; `last_monitor_prob_is_fresh=True` can remain
True from a prior cycle that succeeded before the refactor introduced the undefined
reference.
**Severity**: Critical. This is a pre-existing bug the P9C dual-track refactor did not
touch and the contract tests did not catch, because contract tests exercise the entry
path, not `_refresh_day0_observation`.
**Smoke test**: invoke `_refresh_day0_observation` with a synthetic `Position(state='day0_window',
temperature_metric='low', ...)` + mock ENS result; expect `NameError` to propagate out
of the function (but be swallowed by caller).

### #2 — Replay legacy fallback HIGH-only filter
**File:line**: `src/engine/replay.py:309`
**Quote**: `WHERE city = ? AND target_date = ? AND forecast_high IS NOT NULL`
**Blast radius**: Any LOW replay over a date range where `historical_forecasts_v2`
is not yet backfilled → 0% coverage reported. Operator may mis-read "LOW replay broken"
when the true signal is "v2 empty". Not wrong-number-silent; coverage-silent.
**Smoke test**: `python scripts/run_replay.py --start 2026-03-01 --end 2026-03-07
--temperature-metric low` on a DB with `historical_forecasts_v2` empty but legacy
`forecasts` populated → confirm `n_replayed=0`, then populate one LOW row in v2 for
one city-date and re-run → coverage should appear.

### #3 — Oracle penalty per-city shared table
**File:line**: `src/strategy/oracle_penalty.py:59` (`for city, data in raw.items()`)
**Quote**: `_cache: dict[str, OracleInfo]` keyed by city name, **not** (city, metric)
**Blast radius**: LOW positions inherit HIGH-track penalties for the same city. Kelly
size is scaled down by a multiplier that LOW has not earned. Quantitative bias, not
directional — but structurally wrong because the penalty mechanism conflates two
independent error streams.
**Smoke test**: place a LOW candidate in a city where `oracle_error_rates.json` shows
`oracle_error_rate > 0.03` (accumulated from HIGH errors). Inspect the sizing output:
Kelly should be penalized. Correct behavior post-fix: LOW penalty should derive from
LOW-specific error rate only.

---

## SMOKE TEST RECIPE (minimal data lift to expose top-3)

1. **Pick one city + one LOW-eligible date** (e.g. `Miami` + `2025-01-15`, LOW season).
2. **Insert one v2 snapshot row** into `ensemble_snapshots_v2` with
   `temperature_metric='low'`, valid `members_json`, `data_version='tigge_mn2t6_localday_min.v1'`,
   `boundary_ambiguous=0`, `training_allowed=1`.
3. **Run rebuild**: `python scripts/rebuild_calibration_pairs_v2.py` — confirm one LOW
   row lands in `calibration_pairs_v2` with `temperature_metric='low'`.
4. **Run refit**: `python scripts/refit_platt_v2.py` — confirm one row in
   `platt_models_v2` with `temperature_metric='low'` (even if n_samples is below level3
   and the fit refuses, the refusal log should be LOW-specific, not HIGH-substituted).
5. **Run LOW replay**: `python scripts/run_replay.py --mode audit --start 2025-01-15
   --end 2025-01-15 --temperature-metric low --allow-snapshot-only-reference`
   — Exposes risk #2: expect nonzero coverage now.
6. **Simulate a LOW Day0 position**: create a synthetic `Position(state='day0_window',
   temperature_metric='low', entered_at=recent, ...)` and call `refresh_position(pos,
   portfolio, conn)`. Watch for DEBUG-level "ENS refresh failed" log with
   `NameError: remaining_member_maxes` — exposes risk #1.
7. **Inject a LOW candidate** for a city already in `oracle_error_rates.json` with
   `error_rate=0.05`. Call `evaluate_candidate`; inspect the sizing output. Expected
   post-fix: LOW candidate unaffected by HIGH error history — exposes risk #3.

---

## CRITICAL UNKNOWN

The one load-bearing fact I did not directly verify: **does
`scripts/extract_tigge_mn2t6_localday_min.py` stamp every row it writes to
`ensemble_snapshots_v2` with `temperature_metric='low'` (and only 'low')?**

If that tag is wrong or missing, every downstream "metric-aware" read (rebuild, refit,
get_calibrator v2 branch, DT#7 gate, replay v2-first) reads correctly-structured rows
with a wrong label — a **provenance failure that all the type wiring cannot catch**.
Fitz's Constraint #4 applies directly: correct code + wrong tag = silent disaster.

## DISCRIMINATING PROBE

**One command**: inspect the production `extract_tigge_mn2t6_localday_min.py` `INSERT`
into `ensemble_snapshots_v2` and grep for `temperature_metric=` in the parameter tuple.
Either it is a hardcoded literal `'low'`, a derived value from a `MetricIdentity`
instance, or — the latent-bomb case — an inherited default from a `HIGH_LOCALDAY_MAX`
constant elsewhere in the module. This single line determines whether all P9C downstream
wiring is load-bearing or decorative.

## UNCERTAINTY NOTES

- I did not audit `extract_tigge_mn2t6_localday_min.py` ingest logic directly.
- I did not execute any of the scripts; all claims are static-read.
- The NameError in `_refresh_day0_observation` is verified by grep + dataflow but not
  by live repro. Risk: there is some import-level shim or monkey-patch that rebinds
  `remaining_member_maxes` that I missed. Confidence of bug existence: **high**; should
  be confirmed by a 5-line unit test before declaring a fix path.
- FDR cross-metric pooling (Scenario B H5) is **latent** — not live in current call
  sites. Listed for completeness because any future aggregated-BH refactor would
  activate it silently.
