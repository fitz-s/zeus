# Stage 0 LOOP-BACK — Producer Stash Implementation Report

- Created: 2026-06-14
- Authority basis: `docs/rebuild/consult_build_spec.md` Stage 0 (lines 994-1033, one-invariant 5-12);
  `docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md` (module note: stash already-computed
  values only; partial-write tolerated)
- Module: `stage0_producer`

## What the loop-back had to fix

Prior-critic finding (verified true): NOTHING in `src/` wrote
`_edli_spine_mu_native` / `_edli_spine_sigma_native` / `_edli_spine_raw_members_native` /
`_edli_spine_debiased_members_native` / `_edli_spine_q_vector`. Only `_edli_q_source` was written.
`grep -rn '_edli_spine_'` across `src/` returned READS only (the consumer
`_build_decision_receipt_spine` at `:3518-3614` and the lift seam at `:7511-7536`), zero writes.
Consequence: the consumer's early guard (`"_edli_spine_q_vector" not in spine_inputs and
"_edli_spine_mu_native" not in spine_inputs → return None`, `:3546-3550`) fired for EVERY live
candidate, so the spine was `None` for every receipt — violating spec:998/1033. The 10 prior tests
did not catch it because the only live-path test (`test_live_emission_helper_...`) HAND-BUILDS the
`spine_inputs` dict, masking the missing producer.

## What I built (the genuine producer half)

All edits in **`src/engine/event_reactor_adapter.py`**, inside the live q-build
`_market_analysis_from_event_snapshot` (the function the reactor calls once per candidate via
`_canonical_probability_and_fdr_proof` → `_live_yes_probabilities`, passing the SAME threaded
`payload` the lift seam later reads). No new source files.

1. **`:11060-11069`** — initialize `_spine_mu_native` / `_spine_sigma_native` (None) right after the
   raw member array is read, so they are in scope across all three q-build branches.
2. **`:11162-11164`** — EMOS branch: capture the predictive `_emos_mu_native` / `_emos_sigma_native`
   (EMOS IS the predictive N(mu, sigma)).
3. **`:11210-11212`** — honest-raw branch: capture `_hr_mu` / `_hr_sigma` (honest-raw IS
   N(xbar, floored-sigma)).
4. **`:11352-11425`** — the producer stash, placed at the SINGLE point where the predictive
   center/dispersion, the raw member array, the debiased member array, and the final calibrated q
   vector are all in scope (immediately before `return MarketAnalysis(...)`). It writes onto the
   threaded `payload`:
   - `_edli_spine_q_vector` = `p_cal` (the final calibrated point distribution, after the day0 mask)
   - `_edli_spine_raw_members_native` = `raw_members` (the genuine uncorrected snapshot members)
   - `_edli_spine_debiased_members_native` = `members` (the array actually integrated /
     `member_maxes`: debiased on the Platt/bias path, raw on EMOS/honest-raw/day0)
   - `_edli_spine_mu_native` / `_edli_spine_sigma_native` (see drift resolution below)
   - (`_edli_q_source` continues to be written by the existing one-calibrator seam)

The stash is wrapped in `try/except: pass` (observability must never alter or fail a decision) and
writes only the keys it has values for (the consumer guard tolerates partial — drift-ledger note).

## Spec lines implemented

- spec:1006-1027 (the receipt field list): `mu_native`, `sigma_native`, `member_*_native`,
  `debiased_member_*_native`, `applied_debias_native`, `q_source`, `q_sum` are now reconstructable
  from the producer-written inputs through `DecisionReceipt.from_q_build` (the consumer derives
  `q_sum`/envelope from the stashed arrays).
- spec:1032-1033 (live verification signal "No candidate receipt lacks μ/σ/member envelope/q_source/
  route"): a real Tokyo-like high candidate now yields a non-None spine with mu/sigma/member-envelope/
  q_sum (proven by the end-to-end test).

## Drift resolved (toward the live type)

- **mu/sigma on branches with no explicit predictive center.** Spec assumes a single predictive
  N(mu, sigma) on every path, but the live Platt/bias and day0 branches compute NO explicit
  predictive center/dispersion — they integrate a member array directly. Per the drift-ledger module
  note ("stash already-computed values only"), the stash records the EMOS / honest-raw predictive
  mu/sigma WHEN that branch computed it, and otherwise derives the empirical mean / sample-std of the
  SAME integrated member array (`members`) — which is exactly the center the live
  `_direction_law_family_center` and the `_make_*_bootstrap_sampler` already imply from that array.
  This is an already-implied value, not an invented one; no new statistical object is created.
- **`p_cal` vs `p_raw` as the q vector.** Resolved to `p_cal` (the calibrated point distribution the
  decision actually uses, after the day0 mask), so the receipt's q vector is byte-identical to the
  distribution the decision consumed — asserted in the test (`payload["_edli_spine_q_vector"] ==
  analysis.p_cal`).
- **Branch routing under test.** The conftest autouse fixture
  (`tests/conftest.py:194`) pins `edli_emos_sole_calibrator_enabled = False`, so under pytest the
  default live path is the Platt/bias branch. The end-to-end tests force the flag OFF explicitly
  (self-contained) so they deterministically exercise the branch that wrote NOTHING pre-fix and must
  now derive mu/sigma from the member array — the strongest proof of the producer.

## READ-ONLY / corrected-transformation compliance

- The stash copies already-computed values onto `payload` only; the returned `MarketAnalysis` is
  unchanged byte-for-byte (the `hypotheses` scan reads only `MarketAnalysis`, never `payload`). ZERO
  change to any decision, sizing, or submit behavior. Proven: the q-build regression suite
  (`test_bias_grid_mutual_exclusion`, `test_bootstrap_bias_correction_lockstep`, EMOS seam tests,
  unit-divergence, etc.) shows the SAME 2 pre-existing failures with my change stashed vs applied
  (the warning line merely shifts 11861↔11948 by the +87 added lines), and money_path + live_inference
  are fully green.
- No gate/cap/clamp/haircut/detector. The corrected transformation is that the consumer DERIVES the
  coherence fields (envelope min/max, q_sum) from the same arrays the producer stashed, so a receipt
  that misrepresents its own forecast/q is unconstructable — not flagged after the fact.

## RED-on-revert tests added

`tests/decision/test_live_receipt_contract.py`:

- **`test_live_qbuild_writes_spine_keys_onto_payload_end_to_end`** (the spec-named producer-stash
  RED-on-revert): drives the REAL `_market_analysis_from_event_snapshot` on a Tokyo-like high
  candidate (members ~21..23), asserts the threaded payload actually carries the `_edli_spine_*`
  keys, that the stashed q vector == the decision's `p_cal` (read-only proof), and that the REAL
  consumer (`_build_decision_receipt_spine`) over the producer-written inputs reconstructs a non-None
  DecisionReceipt with non-None mu/sigma/member-envelope/q_sum. It does NOT hand-feed the keys — it
  reads them back from the payload the real producer mutated.
- **`test_live_qbuild_records_debiased_envelope_distinct_from_raw_when_bias_applied`**: drives the
  q-build with the live bias hook mocked to apply a +2.0°C shift, asserts the producer stashes BOTH
  the raw and the (distinct) debiased envelope, and that `applied_debias_native` reconstructs as
  (debiased_mean − raw_mean).

RED-on-revert proven empirically: with the producer stash removed, the first assertion
(`"_edli_spine_q_vector" in payload`) is False and the consumer returns None (the exact prior-critic
finding) — see the report's verification probe.

## Test results

- `pytest -q tests/decision/test_live_receipt_contract.py` → **12 passed** (10 prior + 2 new).
- `pytest -q tests/money_path tests/strategy/live_inference` → **331 passed**.
- Q-build regression suites: 102 passed, 2 pre-existing failures in
  `test_bootstrap_bias_correction_lockstep.py` (`test_ii_...`, `test_iii_...`) — confirmed PRE-EXISTING
  (identical failure on baseline with my source change stashed), unrelated to this read-only stash.

## Files written

- `src/engine/event_reactor_adapter.py` (modified — producer stash, read-only)
- `tests/decision/test_live_receipt_contract.py` (modified — 2 end-to-end producer-stash tests)
- `docs/rebuild/impl_stage0_producer.md` (this report)
