# Tribunal Verification: Ledger / Scorer / p_raw Layer
**Date:** 2026-05-29
**Authority basis:** Live code at `stat-whole-refactor` worktree, verified against claims file.
**Scope:** Claims 1-4. All verdicts cite file:line in WT.
**Method:** Read-only; no production DBs queried.

---

## Claim 1 — Ledger tags ALL rows source_kind='prior', including OpenData live rows

### C1-a: source_kind='prior' hardcoded for all rows
**CONFIRMED.**

`scripts/build_ens_residual_evidence.py:227`:
```python
"source_kind": "prior",
```
This string is unconditional — it is a hardcoded literal in the dict comprehension that emits every output row. The ledger has no branch on `data_version` or `source_id` to distinguish TIGGE (historical archive) from OpenData (live delivery channel). Every row, regardless of `e.data_version` (which is selected at line 125 but not filtered), receives `source_kind='prior'`. The SQL query at lines 122-135 does not filter on `data_version` or any source-identity column. If the source DB contains both `tigge_mx2t6_local_calendar_day_max_v1` and `ecmwf_opendata_high_mx2t3` rows for the same (city, date), both land as `source_kind='prior'`, collapsing product lineage.

Confirmed by the actual CSV (first data row): `data_version=tigge_mx2t6_local_calendar_day_max_v1` but `source_kind=prior` — product differentiation is lost.

### C1-b: Ledger LACKS the specified window-provenance fields
**CONFIRMED** for 5 of 6 fields; PARTIAL on `available_at`.

Actual CSV headers (verified against `ENS_RESIDUAL_EVIDENCE_12CITY_HIGH.csv`):
```
city, metric, season, month, target_date, source_kind, data_version, snapshot_id,
settlement_id, issue_time, cycle, lead_hours, contributes_to_target_extrema,
boundary_ambiguous, members_unit, ensemble_mean_c, settlement_value_c,
settlement_value_native, residual_c, selection_reason
```

Fields the report claims are absent:
- `forecast_window_start` — ABSENT (confirmed)
- `forecast_window_end` — ABSENT (confirmed)
- `startStep` / `endStep` — ABSENT (confirmed; raw ECMWF step range never emitted)
- `aggregation_window_hours` — ABSENT (confirmed)
- `source_run_id` — ABSENT (confirmed; `snapshot_id` is emitted, but the FK join chain to `source_run_id` is not traversed; `build_evidence()` fetches columns from `ensemble_snapshots e` only, not joining `source_run` or `source_run_coverage` tables)
- `available_at` — PRESENT in the internal `best` dict (line 155) but NOT in the final `out_rows` CSV dict (lines 225-239). The CSV omits `available_at` from output.

So all six named window-provenance fields are absent from the CSV output. The `available_at` field is used internally only for freshest-snapshot selection and is then discarded.

---

## Claim 2 — T4 scorer buckets by (city x metric x season) but NOT (product x cycle x lead_bucket)

### C2: Bucket key is (city, metric, season)
**CONFIRMED** with important context.

`scripts/score_error_model_candidates.py:38`:
```python
"""Outcome of the accept-gate for one (city, metric, season) bucket."""
```
The `CandidateDecision` dataclass docstring names exactly `(city, metric, season)` as the bucket dimensions. `choose_candidate()` (line 64) is a pure function that accepts pre-aggregated `candidate_metrics` and `raw_metrics` dicts — it has no `product`, `cycle`, or `lead_bucket` parameter. The scoring path description in the module docstring (lines 14-21) names the candidate set as `{raw, scale-only, prior-bias, live-bias, transported, hierarchical-fallback}` and mentions `blocked-by-target_date OOS folds`, but does NOT mention product, cycle, or lead_bucket stratification.

**Implication:** A `prior-bias` correction fit on TIGGE 00Z day-0-1 leads competes against raw on the same (city, metric, season) bucket that may also contain OpenData 12Z day-2-3 leads in a DB not filtered by product/cycle/lead. The scorer as shipped cannot distinguish whether the improvement came from a lead range where the correction actually helps. Cycle-stratified scoring is structurally absent.

### C2: Candidate set {raw, scale-only, prior-bias, live-bias, transported, hierarchical-fallback}
**CONFIRMED from docstring.** The module docstring at lines 16-17 explicitly names this set. There is no code yet that constructs these candidates (the comment at lines 18-20 says "plugs into this rule ... wired in a follow-up commit"). The current file is the rule-only stub; the scoring path that would construct candidates by product/cycle/lead is not yet implemented.

### C2: Accept rule
**CONFIRMED.** `choose_candidate()`:
- beats raw on `>= MIN_PROPER_SCORE_WINS` (2) of 3 proper scores (`logloss`, `rps`, `brier`) — lines 32, 97-98
- `improvement_lcb > 0` — line 100
- `not catastrophic.get(name, False)` — line 101

All three conditions must hold; selecting by `max(improvement_lcb)` among passing candidates at line 114. Matches report verbatim.

---

## Claim 3 — p_raw generation: Monte Carlo or analytic? Gaussian noise?

### C3-a: Current method is Monte Carlo, 10,000 draws
**CONFIRMED.**

`src/signal/ensemble_signal.py:254-258`:
```python
for _ in range(n_mc):
    noised = member_maxes + rng.normal(0, effective_sigma, n_members)
    measured = settlement_semantics.round_values(noised)
    p += bin_counts_from_array(measured, bins)
p = p / (float(n_members) * n_mc)
```
`n_mc` defaults to `ensemble_n_mc()` = `settings["ensemble"]["n_mc"]` = **10,000** (confirmed from `config/settings.json`). The loop explicitly iterates `n_mc` times; there is no analytic formula.

### C3-b: Noise model is Gaussian additive per member
**CONFIRMED.**

At line 252-255: `effective_sigma = float(np.hypot(sig.value, extra_member_sigma))` where `sig = sigma_instrument_for_city(city)`. Then `rng.normal(0, effective_sigma, n_members)` adds IID Gaussian noise to each of the `n_members` member maxes. This is a mixture-of-Gaussians structure: each member contributes a shifted Gaussian centered at `member_max_i + bias_shift`, with variance `effective_sigma^2`. The settlement `round_values` step (WU integer rounding) introduces discrete quantization, but the pre-rounding noise is exactly Gaussian.

### C3-c: Report claim that analytic Gaussian-mixture CDF could replace MC
**PARTIALLY SUPPORTABLE.** Without the rounding step, `p_raw[bin]` for a bin `[lo, hi)` is exactly `(1/n_members) * Σ_i [Φ((hi - μ_i)/σ) - Φ((lo - μ_i)/σ)]` — a mixture of normal CDFs, computable analytically. However, `settlement_semantics.round_values()` quantizes (e.g., rounds to nearest integer for WU °F display). The rounding step transforms each `N(μ_i, σ²)` into a discrete distribution whose CDF is a staircase, not a smooth Gaussian CDF. An analytic mixture-of-normals CDF would only be exact if rounding is skipped or approximated. Whether that approximation error is acceptable at the bin widths used is a calibration design question, not a code-read question.

### C3-d: PredictiveErrorModel residual_sd_c as Gaussian extra_member_sigma
**CONFIRMED.**

`src/calibration/ens_error_model.py:143,172`:
```python
total_residual_sd_c: float    # sqrt(residual_sd^2 + heterogeneity_var) for the MC draw
```
`p_raw_vector_with_error_model()` at lines 232-236:
```python
resid_sd_native = error_model.total_residual_sd_c * scale
...
return p_raw_vector_from_maxes(..., extra_member_sigma=resid_sd_native)
```
This adds in quadrature to the instrument sigma at `ensemble_signal.py:252`. Both are treated as Gaussian standard deviations. The error model noise is Gaussian additive per member — the assumption enabling an analytic mixture approximation would be valid before rounding.

---

## Claim 4 — Inference reader serves single freshest snapshot, discards cross-run spread

### C4-a: Single snapshot elected
**CONFIRMED.**

`src/data/executable_forecast_reader.py:1231`:
```python
best = min(candidates, key=_bundle_rank)
```
`_candidate_forecast_bundles()` (line 1042-1096) enumerates ALL `source_run_coverage` rows for `(city, date, metric, source_id, source_transport, data_version)` and gates each independently. `min(candidates, key=_bundle_rank)` selects exactly one winner. No averaging or stacking of multiple cycle snapshots occurs.

Ranking key `_bundle_rank` (lines 1031-1034):
```
(FULL_CONTRIBUTOR=0 else 1, -source_cycle_time_epoch, -available_at_epoch, -snapshot_id)
```
A FULL_CONTRIBUTOR 00Z run always beats a NON_CONTRIBUTOR 12Z run regardless of recency. Within the same contributor class, the freshest (most recent cycle time) wins.

### C4-b: Cross-run spread is NOT recorded anywhere
**CONFIRMED.**

No field, table, or DB write path in `executable_forecast_reader.py` captures:
- how many candidate cycles were enumerated
- their individual p_raw vectors or member means
- the inter-cycle disagreement (spread between 00Z and 12Z elected vectors)

The `ensemble_spread` fields computed in `evaluator.py` and `monitor_refresh.py` are intra-cycle ensemble spreads (σ across member maxes within one snapshot), not cross-run inter-cycle spread. The number of cycles that competed and their scores are computed and discarded within `_candidate_forecast_bundles()` — nothing is persisted.

The `audit_inference_selection.md` reference doc (Q2, Q3) confirms this: "A SINGLE snapshot is elected — no averaging, no stacking of multiple snapshots."

---

## Audit-Missed Omissions

### O1: Ledger mixes TIGGE and OpenData rows without stratification
The ledger query at `build_ens_residual_evidence.py:128` selects from `ensemble_snapshots` without a `data_version IN (?)` filter. TIGGE (`tigge_mx2t6_local_calendar_day_max_v1`) uses the daily-max NWP field (`mx2t6`); OpenData uses `mx2t3`. These have different temporal aggregation windows and potentially different bias characteristics (3h vs 6h max, different ingest pipelines). Mixing them under the same `source_kind='prior'` label creates a dataset with invisible product heterogeneity. The bias model fit from this mixed ledger will be biased toward whichever product has more coverage in each season bucket. The `data_version` column is emitted in the CSV but is not used as a stratification key by the T4 scorer (`score_error_model_candidates.py:38` names `(city, metric, season)` only). This gap between the ledger's data_version field and the scorer's bucket key means product-specific bias cannot be detected or corrected.

### O2: Ledger selection picks FRESHEST snapshot per (city, date), not cycle-matched to inference
`build_evidence()` keeps `"freshest by available_at"` (line 151-155) within the accepted cycle. The inference reader also picks the freshest FULL_CONTRIBUTOR by `source_cycle_time` and then `available_at`. These two "freshest" rules are not guaranteed to select identical snapshots: the ledger's `available_at` comparison is a string comparison (`str(av) > str(prev["available_at"])`) on raw DB timestamp strings, which gives correct ordering only if timestamps are ISO-8601 UTC strings (timezone suffix matters). If any `available_at` values are naive (no +00:00 suffix), string comparison may order them incorrectly, causing the ledger to retain a different snapshot than the inference reader would elect. No validation of the string format is performed.

### O3: T4 scorer stub: scoring path is unimplemented (follow-up commit not yet landed)
`score_error_model_candidates.py` lines 18-20 explicitly state the scoring path (per-bucket candidate construction, OOS folds, bootstrap LCB) "plugs into this rule and is wired in a follow-up commit." The current commit contains only the `choose_candidate()` rule stub and the `CandidateDecision` dataclass. The `audit_refit_proper_scores.py` (the scoring engine cited in the claim) groups distributions only by `city` (via `cluster`) and `lead_bucket`/`cycle` cohorts — but it produces a report for human review, not a machine-readable dict keyed by `(city, metric, season)` that could be fed into `choose_candidate()`. There is no wiring layer that transforms `audit_refit_proper_scores.py` output into `choose_candidate()` inputs. The T4 pipeline is structurally incomplete at HEAD.

---

## Summary (12 lines)

C1-a CONFIRMED: `source_kind='prior'` is hardcoded at `build_ens_residual_evidence.py:227` for ALL rows — no branch on data_version or source_id; TIGGE and OpenData collapse identically.  
C1-b CONFIRMED: CSV has 20 columns; `forecast_window_start/end`, `startStep/endStep`, `aggregation_window_hours`, `source_run_id` are ALL absent; `available_at` used internally but dropped from output.  
C2 CONFIRMED: `CandidateDecision` docstring names `(city, metric, season)` bucket only (`score_error_model_candidates.py:38`); no product/cycle/lead_bucket stratification present. Candidate set `{raw, scale-only, prior-bias, live-bias, transported, hierarchical-fallback}` confirmed. Accept rule (2/3 proper scores + LCB>0 + no catastrophe) confirmed.  
C3 CONFIRMED: MC with 10,000 draws (`ensemble_signal.py:254`); noise is Gaussian per member (`rng.normal`); `PredictiveErrorModel.total_residual_sd_c` enters as `extra_member_sigma` (Gaussian, quadrature) confirming analytic mixture-of-normals approximation is structurally valid pre-rounding; post-rounding WU quantization prevents exact analytic equivalence.  
C4 CONFIRMED: `min(candidates, key=_bundle_rank)` at `executable_forecast_reader.py:1231` elects ONE snapshot; cross-run inter-cycle spread is computed nowhere; `ensemble_spread` in evaluator is intra-cycle only.  
O1 AUDIT MISS: Ledger mixes TIGGE (mx2t6) and OpenData (mx2t3) rows without product-stratification; T4 bucket `(city, metric, season)` is coarser than ledger's `data_version` — product-specific bias invisible.  
O2 AUDIT MISS: Ledger `available_at` freshness comparison is a raw string comparison (`str(av) > str(prev[...])`); naive timestamp strings (no UTC suffix) may mis-order, selecting a different snapshot than inference would elect.  
O3 AUDIT MISS: T4 scoring path is a stub — `score_error_model_candidates.py` lines 18-20 state the candidate construction + OOS scoring wiring "is wired in a follow-up commit"; no wiring from `audit_refit_proper_scores.py` output to `choose_candidate()` inputs exists at HEAD; pipeline is structurally incomplete.
