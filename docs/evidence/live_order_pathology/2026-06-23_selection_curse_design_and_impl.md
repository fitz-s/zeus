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

## Artifact + validation — NOT ARM-SAFE (2026-06-24 no-leak re-validation)

The mechanism (estimator + loader + 4 seams) is built, reviewed (PR #419 frontier review), hardened
(mtime-aware loader, fail-soft seams, non-finite guards), and **tighten-only**. But the ARMING claim
did NOT survive a settlement-leak-free walk-forward and the bound ships **unarmed** (`armed_sides=[]`):

- The prior "armed buy_no, OOS over-claim **+0.002**" was a **settlement-availability LEAK artifact**:
  the committed walk-forward gated origins on `target_date < d`, training on markets whose outcomes had
  not settled at the simulated decision. Apples-to-apples, the leaked gate reproduces +0.002/ARM=True;
  the no-leak gate (`settle_avail < decision`) gives **+0.0324 / ARM=False** (identity; +0.0373
  fitted-k) — 3.2× over the +0.01 bar, driven by the real mid-price curse at the thick early-June
  origins (06-02 +0.057, 06-04 +0.096).
- **`settled_at` is corrupt** (bulk-backfilled: 71% of in-window rows = constant 2026-06-24, median lag
  ~21 days — physically impossible; `recorded_at` only spans 06-15..06-24). There is NO trustworthy
  settlement-availability timestamp in the DB, so the no-leak fit must rest on a deterministic proxy
  (`target_local_day_END + 24h`), and the arm verdict is **fragile to that proxy** (0h→arm, 24h→no-arm,
  48h→arm). An arm decision that flips on a ±1-day proxy is not arm-safe.

The full-set `realized_lcb` curve is unchanged (the no-leak fix touches only the arm gate, not the
fit) — the curse is a **real empirical signal** (monotone in price; favorites ≥0.95 calibrated) — it
is simply not walk-forward-arm-safe on this evidence. The fitter is self-protecting: it emits
`armed_sides=[]`, so placing the artifact is inert for buy_no (`SIDE_NOT_ARMED` → identity).

## DEFINITIVE re-validation with the REAL settlement time (2026-06-24)

The `settled_at` column is unusable (bulk-backfilled), but the REAL per-market settlement-availability
time is recoverable from the settling observation: `provenance_json.obs_id` → `observations.fetched_at`
(when the daily-high obs was published). It is trustworthy: 100% coverage, median lag **18h** after
target-day-end, 40 distinct fetch days, 0 negative lags — a real per-market time, not a backfill.

Re-running the no-leak walk-forward gated on this REAL availability time gives the DEFINITIVE verdict:
**buy_no does NOT arm** — OOS over-claim **+0.0198** (~2× the +0.01 bar), `armed_sides=[]`,
**seed-stable** (+0.0198/+0.0180/+0.0192) and **sigma-regime-stable** (fitted-k +0.0397), no longer
proxy-fragile. The entire over-claim is the two thick early-June origins (06-02 +0.057, 06-04 +0.107);
every other origin nets −0.009. Both the leaked +0.002/arm and the proxy arm verdicts are refuted.

## Root data fix (applied) + disposition

- **Root cause fixed:** `src/ingest/harvester_truth_writer.py` stamped `settled_at = datetime.now()`
  (the reconstruction batch time) — now derives `settled_at = obs_row["fetched_at"]` (the real
  availability time) with `recorded_at` the separate write time, and forces QUARANTINE when no fetch
  time exists. Mirrors the live M1 fix in `src/execution/harvester.py:1485-1495`. Antibody:
  `tests/test_harvester_truth_writer_m1_settled_at.py`. This unblocks no-leak validation for EVERY
  settlement-conditioned fitter, not just this one.
- The fitter (`scripts/fit_selection_curse_bound.py`) derives the real availability time directly from
  `provenance.obs_id → observations.fetched_at`, so it is trustworthy NOW (independent of a settled_at
  backfill) and self-protects (`armed_sides=[]`).
- **Ship UNARMED** (inert; gate is identity). The curse shape is real (curve unchanged; favorites
  calibrated) but **not walk-forward-arm-safe** on 5.4 weeks. Arm only when forward accrual clears
  +0.01 leak-free on the real availability time.
- **Historical `settled_at` backfill** (re-derive from `provenance.obs_id → observations.fetched_at`,
  100% recoverable) is a separate operator-gated canonical-table migration — fixes historical P&L
  grading + makes all historical settlement-conditioned fits trustworthy. Not applied here.

## Revert

The mechanism is inert when `armed_sides` is empty / artifact absent. Remove
`state/selection_curse_bound.json` → identity on the next decision (mtime-aware loader, no restart),
or re-checkout the touched files. Tighten-only → reverting can only re-admit, never block more.
