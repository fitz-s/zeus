# Selection-curse authorization bound — design, evidence, implementation

- Created: 2026-06-23
- Authority basis: external lifecycle audit P0-1 (taker authorization must be execution-conditioned)
  → reframed by settlement evidence into a SELECTION (winner's-curse) correction; frontier consult
  REQ-20260623-104405 (SCMA design) cross-checked + corrected by local data; operator laws (no
  hardcode, settlement-evidenced, not-thin, no over-gating buy_yes, tighten-only).
- Branch: `claude/full-lifecycle-audit-impl` (worktree `full-lifecycle-impl`).

## One-line

The live book loses on buy_no because the admission gate ``q_lcb_side > price`` adversely-selects
mid-price NO whose realized settlement rate (~0.69) is far below the gate's claim (~0.83). The fix is
a settlement-evidenced **monotone realized-NO-rate bound conditioned on price**, deflating the served
``q_lcb_no`` so mid-price buy_no self-rejects while deep favorites and buy_yes pass unchanged. One
bound, applied at BOTH entry admission and the taker cross.

## Investigation chain (what the evidence forced)

1. **q_exec_lcb (thin per-cell pay-rate) — SCRAPPED.** First attempt keyed a Wilson LCB on settled
   FILLS (N=60). Thin + hardcoded n_min/z. Operator: "thin is itself a design problem." Removed.
2. **The population q is ~calibrated.** Re-materializing the served q over 4,312 settled markets
   (6.6 mo, identity sigma — byte-faithful, max diff 1.7e-20 vs persisted) gives modal gap +2.7pp,
   low-q ~0. The −20pp is NOT marginal forecast miscalibration. (`reliability_dataset_measurement.md`,
   `scma_dataset_build.md`.)
3. **It is SELECTION bias.** Reconstructing counterfactual admissions (re-materialized q_lcb ×
   historical executable prices × settlement) over the priced window: admitted buy_no claims ~0.83,
   realizes ~0.69 → **+14pp**, **−1.6% to −3.5% EV/share**; monotone in price (NO @0.50–0.70 gap
   +0.184 … ≥0.95 calibrated 0.000); buy_yes benign (EV +0.004); leave-one-city-out flat to ±0.003
   across 49 cities; **walk-forward correction collapses OOS over-claim from +0.16 to ±0.01**.
   (`counterfactual_selection_bias.md`.)

## Source-match (the costly-trap guard, satisfied)

The live q is built from ``raw_model_forecasts`` fusion (NOT ``ensemble_snapshots``). The calibration
is fit on the SAME object live serves, re-materialized byte-faithfully via
``compute_replacement_posterior_readonly`` (identity sigma = no leak from today's sigma_scale;
``target_date < decision`` history). Re-materialized q reproduced the persisted served q to machine
precision. (`source_match_rematerialization.md`, `scma_dataset_build.md`.)

## The design (no hardcode, settlement-evidenced, not-thin, tighten-only)

``corrected_q_lcb_no = min(served_q_lcb_no, realized_no_rate_lcb(no_price))``

* ``realized_no_rate_lcb(price)`` = monotone (isotonic PAVA — no hand buckets, no MIN_N/z) lower band
  of the realized NO settlement rate vs NO price, fit on the admitted slice, cluster-weighted 1/m_g
  per market-day, lower band via cluster bootstrap over market-days. Only ever TIGHTENS.
* buy_yes / deep favorites (≥~0.85) / price out of training support / absent / unarmed → identity.
* One bound consumed by ENTRY admission AND the taker cross → the taker can no longer cross on raw q.

## Implementation (all green; 208 tests in the affected suites)

- `src/decision/selection_curse_bound.py` — pure estimator (`SelectionCurseBound`,
  `corrected_side_q_lcb`). `tests/decision/test_selection_curse_bound.py` (8).
- `src/decision/selection_curse_bound_loader.py` — fail-soft loader, `config.state_path`
  (ZEUS_PRIMARY_ROOT-aware, avoids the zeus-live-main state footgun).
  `tests/decision/test_selection_curse_bound_loader.py` (5).
- ENTRY seam: `src/strategy/live_inference/live_admission.py:selection_calibrated_admission_q_lcb`
  composes the deflation (min). `tests/strategy/live_inference/test_selection_curse_entry_seam.py` (4).
- TAKER seams (one shared `_event_bound_q_exec_lcb`, now price-conditioned): proof-side mode decision
  (`_mode_consistent_ev_for_proof`), submit-time fresh re-eval (`_fresh_rest_then_cross_mode`), taker
  quality proof (`_build_event_bound_taker_quality_proof`) — `event_reactor_adapter.py`; bound +
  `select_rest_then_cross_mode`'s `q_exec_lcb` param in `mode_consistent_ev.py`; basis on the receipt.
  `tests/engine/test_q_exec_lcb_event_seam.py` (4), `tests/strategy/live_inference/test_q_exec_lcb_taker_gate.py` (5).
- Fitter: `scripts/fit_selection_curse_bound.py` (re-materialize ledger → PAVA + bootstrap LCB →
  walk-forward arm gate → `state/selection_curse_bound.json`). Allowlisted (read_only_ro_uri).

## Artifact + validation — ARMED buy_no (2026-06-24 no-leak, after-cost-EV criterion)

The mechanism (estimator + loader + 4 seams) is built, reviewed (PR #419 frontier review), hardened
(mtime-aware loader, fail-soft seams, non-finite guards), **tighten-only**, and now ships **ARMED**
(`armed_sides=["buy_no"]`, artifact `c22fb053…`, placed live at `state/selection_curse_bound.json`).

The arm criterion was corrected. The prior verdict gated on a fixed walk-forward over-claim residual
(`oos_resid_mean ≤ 0.01`) and concluded "+0.0198 → NOT arm". That is the **wrong question for a
tighten-only block** AND a fixed-% standard the operator banned ([success-criterion-no-fixed-number]).
A realized-rate lower bound does not need to perfectly calibrate the survivors; it needs to **not
sacrifice after-cost EV**. The real bar (operator law) is settlement-graded after-cost EV.

No-leak walk-forward, gated on the REAL per-market settlement-availability time (below), scored on
after-cost EV/share = `won − fee_adj(price)`:

| OOS buy_no | shares | after-cost EV sum |
|---|---|---|
| Raw gate admits (`q_lcb_no > cost`) | 1145 | **−45.74** |
| Bound-admitted subset | 92 | −3.87 |
| **Removed by the bound** | **1017** | **−41.87** (mean −0.041/share) |

The bound is **tighten-only** (bound-admitted ⊂ raw-admitted; it can only remove a trade, never add
one). It strips the toxic 89% of OOS buy_no — a set with aggregate after-cost EV **−41.87** — and
recovers **+41.87** of after-cost EV (raw −45.74 → bound −3.87). `ARM_ELIGIBLE = (n_removed>0 AND
ev_removed_sum ≤ 0)` → **True**. `leak_violations=0`. The +0.0198 residual is real (the 92 survivors
still slightly over-claim, concentrated at the two thick early-June origins) but is irrelevant to a
block decision: removing net-losing trades is loss-reduction regardless of survivor calibration.

Round-trip through the runtime: NO @0.55/0.65/0.70/0.75 deflates 0.83→0.43/0.62/0.65/0.69 →
self-rejects vs cost; favorite @0.97 identity (still admits); buy_yes identity. HASH_OK, PASS.

## The REAL settlement-availability time (no-leak gate key)

The `settled_at` column is unusable as an as-of gate (bulk-backfilled: 71% of in-window rows = constant
2026-06-24, median lag ~21 days — physically impossible; `recorded_at` only spans 06-15..06-24). The
REAL per-market settlement-availability time is recovered from the settling observation:
`provenance_json.obs_id` → `observations.fetched_at` (when the daily-high obs was published).
Trustworthy: ~100% coverage, median lag **18h** after target-day-end, 40 distinct fetch days, 0
negative lags — a real per-market time, not a backfill. The fitter gates the walk-forward on this time
(`build_admitted_buy_no(gate_key="obs_avail")`), so the arm verdict rests on no leak and no proxy.

## Root data fix (applied) + disposition

- **Root cause fixed:** `src/ingest/harvester_truth_writer.py` stamped `settled_at = datetime.now()`
  (the reconstruction batch time) — now derives `settled_at = obs_row["fetched_at"]` (the real
  availability time) with `recorded_at` the separate write time, and forces QUARANTINE when no fetch
  time exists. Mirrors the live M1 fix in `src/execution/harvester.py:1485-1495`. Antibody:
  `tests/test_harvester_truth_writer_m1_settled_at.py`. This unblocks no-leak validation for EVERY
  settlement-conditioned fitter, not just this one.
- **Historical `settled_at` backfill APPLIED** (`scripts/backfill_settled_at_from_obs_2026_06_24.py`):
  re-derived `settled_at` from `provenance.obs_id → observations.fetched_at` for 8,944 rows across
  `settlement_outcomes` + `settlements` (idempotent; `settled_at_prebackfill` provenance marker =
  reversible). Fixes historical P&L grading + makes all historical settlement-conditioned fits
  trustworthy.
- **Ship ARMED (`buy_no`).** The bound is live, tighten-only, after-cost-EV-positive (+41.87 OOS), and
  instantly revertible (remove `state/selection_curse_bound.json` → identity on next decision via the
  mtime-aware loader; no restart). Verified consumed by the live taker seam under the daemon's exact
  env (PYTHONSAFEPATH=1, PYTHONPATH=zeus-live-main): `_event_bound_q_exec_lcb(buy_no@0.68, raw=0.83) →
  0.637, basis SELECTION_CURSE:buy_no`.

## Revert

The mechanism is inert when `armed_sides` is empty / artifact absent. Remove
`state/selection_curse_bound.json` → identity on the next decision (mtime-aware loader, no restart),
or re-checkout the touched files. Tighten-only → reverting can only re-admit, never block more.
