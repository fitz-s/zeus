# Wave 5B — Live-Reactor Integration of the Rebuilt Q-Kernel Spine

Created: 2026-06-14
Authority basis: docs/rebuild/consult_build_spec.md (Wave 5 reactor wiring) +
docs/rebuild/impl_w4_family_decision_engine.md (the engine contract) +
docs/rebuild/arm_replay_report.md (the spine validated BEFORE integration — center proven,
q calibrated, σ honest std(z)=0.93 over 697 settled families) + the operator cutover law
(ONE flag; flag OFF = legacy byte-for-byte; legacy authorities INERT not deleted; Stage 11
removes them).

## What was built

The live reactor's per-family DECISION (q + candidate selection + sizing) is now routed
through the rebuilt q-kernel spine behind ONE boolean cutover flag. When the flag is OFF
(default), the legacy decision path is byte-for-byte unchanged. When ON, the per-family
decision is computed by `src/decision/family_decision_engine.FamilyDecisionEngine.decide()`
and the resulting `FamilyDecision.selected` is mapped back onto the SAME `_CandidateProof`
shape the reactor's submission pipeline already consumes — so RiskGuard, freshness/staleness
gates, MECE fail-closed, venue submission, receipt persistence, and the Stage-0
`decision_receipt_spine` emission all still run on the spine's selected candidate, unchanged.

This is a CUTOVER, not a permanent shadow: the flag exists so the orchestrator can flip it
ON at deploy and roll back if needed. The legacy authorities (EDLI bias correction, the
scalar `trade_score` selector, the binary Kelly, `1 - q_ucb_yes` NO LCB, `market_anchor`)
are left INERT for rollback; Stage 11 removes them later.

### Files written

- `config/settings.json` — added `feature_flags.qkernel_spine_enabled` (DEFAULT `false`) +
  a `_qkernel_spine_enabled_note` documenting the cutover/rollback contract.
- `src/engine/qkernel_spine_bridge.py` — NEW. The bridge: the flag accessor
  `qkernel_spine_enabled()`, the reactor→spine input mapping, the `decide()` driver, and the
  `FamilyDecision.selected` → `_CandidateProof` remap. Keeps the giant adapter file's diff to
  a single `if/else` branch (critical for the flag-OFF byte-for-byte guarantee) and avoids a
  circular import of the adapter (reactor-native helpers are passed in as callables).
- `src/engine/event_reactor_adapter.py` — the ONE flag branch at the per-family
  orchestration seam (≈14 inserted lines + the no-trade-reason threading; the legacy path is
  the unchanged `else`).
- `tests/integration/test_qkernel_spine_routing.py` — NEW. The Wave-5B smoke test.

## The exact insertion point (symbol + current line)

The per-family decision orchestration point is the function that, for ONE family, has the
executable proofs / snapshot rows / native costs / portfolio in scope and computes q (via
`_live_yes_probabilities`, called inside `_generate_candidate_proofs`) then selects+sizes
(via `_selected_candidate_proof` → `_select_proof_by_robust_marginal_utility`) and produces
the candidate that feeds the submission pipeline. That is the body of
`_attempt_event_submission` in `src/engine/event_reactor_adapter.py`, at the block:

```
proofs = _generate_candidate_proofs(...)        # the submission substrate (rows / prices /
                                                #  native costs) — generated on BOTH paths
proof  = _selected_candidate_proof(payload, proofs, ...)   # the legacy SELECTION authority
```

- The flag branch is inserted at **`event_reactor_adapter.py:2489` (`if qkernel_spine_enabled():`)**,
  immediately replacing the `proof = _selected_candidate_proof(...)` selection. The legacy
  `_selected_candidate_proof(...)` call is now the `else` branch at
  **`event_reactor_adapter.py:2508`** (byte-for-byte unchanged).
- The spine bridge entry point is **`qkernel_spine_bridge.py:638` (`decide_family_via_spine`)**.
- The flag accessor is **`qkernel_spine_bridge.py:135` (`qkernel_spine_enabled`)**.
- The spine's typed no-trade reason surfaces on the no-proof receipt at
  **`event_reactor_adapter.py:2561`** (`QKERNEL_SPINE_NO_TRADE:<reason>`).

`proofs` is generated identically on BOTH paths because it IS the submission substrate (the
per-candidate executable rows / `execution_price` / native costs the pipeline and the
`opportunity_book` consume). Only the SELECTION authority differs — the spine replaces the
DECISION computation, not the submission machinery.

## Input mapping (reactor field → spine type)

The bridge builds every spine `decide()` input from the reactor-native data in scope at the
seam:

| spine input (`decide()`) | reactor source | how it is built |
|---|---|---|
| `case: ForecastCase` | `family.{city,target_date,metric}` + `decision_time` | `build_forecast_case` → resolves the versioned `EventResolution` via the LIVE `event_resolution_for_city` (the per-city settlement identity; HK `oracle_truncate`, else `wmo_half_up`). City resolved from `runtime_cities_by_name()` (keyed by `city.name` == `family.city`). |
| `omega: OutcomeSpace` | `family.candidates[].bin` | `build_outcome_space` → one `OutcomeBin` per candidate's already-MECE-validated `Bin` (`low`→`lower_native`, `high`→`upper_native`, `label`), carrying the family resolution's `rounding_rule`. `OutcomeSpace.validate()` is the fail-closed gate. The `OutcomeBin.bin_id` is the SAME `stable_hash(condition_id + bin geometry)` the reactor's `_candidate_bin_id(proof)` uses, so the Omega bins, the sizing-candidate keys, the family-book market keys, and the route keys are all the same id. |
| predictive distribution | the threaded `_edli_spine_*` payload values (Stage-0 producer) | injected `PredictiveBuilder` constructs a `PredictiveDistribution` DIRECTLY from the reactor's served `mu*`/`sigma`/debiased members (see "Reconstruction / drift" below). `live_eligible=True` iff the reactor served a positive finite σ. |
| `FreshModelSet` (reader) | `_edli_spine_raw_members_native` (falls back to debiased) | `build_fresh_model_set` → `RawModelMember` per served member value, carrying the case provenance. Served via an injected `FreshModelReader` (no second DB read). |
| day0 obs (reader) | (forecast lane has none at this seam) | injected `Day0Reader` returns `None` → the predictive builder applies the inactive `NO_DAY0` identity transform. Day0-scope wiring is a follow-up. |
| `family_book` | the proofs' rows `orderbook_depth_json`/`orderbook_depth_jsonb` | injected `family_book_builder` (`_family_book_builder_from_proofs`) builds the `FamilyBook` DIRECTLY from each sibling's four native ladders — the SAME books the reactor priced each proof against — bypassing `ExecutableMarketSnapshot` reconstruction. |
| `sizing_candidates: {(bin_id, side): NativeSideCandidate}` | the proofs | reuses the reactor's ONE materialization path `_native_side_candidate_from_proof` (passed in), keyed by `(bin_id, side)` where side YES/NO ← direction buy_yes/buy_no. |
| `matrix: FamilyPayoffMatrix` | the omega bins | `utility_ranker.FamilyPayoffMatrix.over_bins(bin_ids)` (passed in) — the SAME geometry the legacy ranker uses. |
| `portfolio: PortfolioExposureVector` | `_robust_marginal_utility_exposure` + `_robust_marginal_utility_baseline_usd` (passed in) | flat baseline for SELECTION (`extra_exposure_by_bin_id=None`) — the legacy `_select_proof_by_robust_marginal_utility` also selects on the flat baseline; existing exposure only re-sizes the chosen leg afterward. |
| `shares_for_routing` | the proofs' `min_order_size` | `_family_min_order_shares` → the family's venue min order size (probability-unit shares), so routes price at a FEASIBLE size (the engine default of 1 share would mark routes non-executable on a book whose min order is larger). |

## Output mapping (FamilyDecision → candidate)

- `FamilyDecisionEngine.decide()` returns a `FamilyDecision`. Its `selected:
  CandidateEconomics` (or `None`) is the spine's `argmax optimal_delta_u` over the survivors
  of the filter chain `direction_law_ok → coherence_allows → (edge_lcb>0 AND optimal_delta_u>0)`.
- The bridge parses `FamilyDecision.selected.candidate_id` (`"SIDE:bin_id:route_id"`) into
  `(bin_id, side)` and looks up the matching reactor `_CandidateProof` via
  `_proof_by_bin_side` (keyed by the same `_candidate_bin_id` hash). The selected proof's
  executable identity (row / token / `execution_price` / `native_quote_available`) is LEFT
  UNCHANGED — the spine selected this exact executable leg, and the submit pipeline
  re-authorizes it at submit time. The receipt-facing q fields are restamped from the spine's
  economics (`q_posterior` ← `q_dot_payoff`, `trade_score` ← `point_ev`, `q_source` ←
  `"qkernel_spine"`) so the receipt reflects the spine's decision.
- A no-trade (`selected is None`) returns the spine's own typed `no_trade_reason`
  (`PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE` / `MARKET_INCOHERENT_BLOCK_LIVE` /
  `NO_DIRECTION_LAW_CANDIDATE` / `NO_POSITIVE_EDGE_CANDIDATE` / `NO_EXECUTABLE_ROUTE_CANDIDATE`),
  surfaced on the receipt as `QKERNEL_SPINE_NO_TRADE:<reason>`. A genuine reconstruction gap
  returns a typed bridge reason (`SPINE_INPUTS_UNAVAILABLE` / `SPINE_WIRING_FAULT`).
- The submission pipeline (RiskGuard, freshness, MECE fail-closed, venue submission, receipts,
  decision_receipt_spine) runs on the spine's selected proof UNCHANGED. The honest pre-existing
  gates STAY: the spine's own filter chain (direction law + coherence + `edge_lcb>0` &
  `optimal_delta_u>0`) IS the capital-efficiency q_lcb>price law, and the selected proof still
  flows through the reactor's downstream submit-time re-proofs.

## Reconstruction / drift resolved (recorded per operator law)

1. **Predictive distribution: served-truth, not a second authority read.** The spine's
   `PredictiveBuilder` protocol expects to build the predictive distribution from a
   `FreshModelSet` via `DebiasAuthority` + `build_center` + `build_sigma` (calibration-artifact
   reads). At the LIVE reactor seam the reactor has ALREADY produced and ARM-validated the
   served center (mu*), dispersion (σ), and debiased member envelope — threaded on the payload
   under `_edli_spine_*` by the Stage-0 producer. Re-running the σ/center authorities here would
   be a SECOND authority read that could DIVERGE from the q the reactor's validated build
   produced. **Resolution (toward the reactor's served truth, zero drift):** the injected
   `PredictiveBuilder` constructs the `PredictiveDistribution` DIRECTLY from the reactor's served
   mu*/σ/members (the "stash already-computed values" principle the Stage-0 receipt spine
   established, applied forward). The spine then performs its OWN joint_q integration, band,
   family book, coherence, routes, and payoff/ΔU selection — the parts that ARE the rebuilt-spine
   decision logic — over the SAME N(mu*, σ) the reactor already validated. If the reactor served
   no predictive center/σ (the threaded inputs are absent — a genuine gap), the bridge returns a
   TYPED `SPINE_INPUTS_UNAVAILABLE` no-trade rather than fabricating a center.

2. **Family book: built from the proofs' native ladders, not reconstructed snapshots.** The
   engine's `family_book` step consumes `ExecutableMarketSnapshot` per sibling. The reactor seam
   holds executable snapshot ROWS (DB row dicts) on the proofs, not reconstructed snapshot
   objects, and `ExecutableMarketSnapshot` reconstruction from a raw row is schema-coupled and
   fragile. **Resolution:** an injected `family_book_builder` reads each sibling's four native
   ladders DIRECTLY off the proof's `orderbook_depth_json` into a `MarketBook` — the SAME native
   ladders the reactor priced each proof's `execution_price` against — and assembles the
   `FamilyBook`. No second capture, no snapshot reconstruction; the route set / candidate
   economics the spine computes walk the SAME books the reactor's q-build saw.

3. **Selection exposure baseline.** The legacy `_select_proof_by_robust_marginal_utility`
   selects on the FLAT exposure baseline (existing exposure re-sizes the winner afterward). The
   bridge passes `extra_exposure_by_bin_id=None` (flat baseline) into `decide()` for the same
   selection behavior. Existing-exposure-aware re-sizing on the winning leg is a downstream
   concern preserved on the legacy submit path; the spine's `decide()` already produces the
   selection + size in one pass against the flat baseline.

No required spine input was found to be genuinely unreconstructable at the seam: when the served
predictive inputs are present (the live forecast lane always threads them), the full
predictive→q→band→book→coherence→routes→payoff→argmax-ΔU pipeline runs. The day0 observed-extreme
lane is served as inactive at this seam (a follow-up scope), which is the conservative
NO_DAY0 identity — never a fabricated observation.

## Flag wiring

- `config/settings.json`: `feature_flags.qkernel_spine_enabled` = `false` (default).
- Read via `qkernel_spine_bridge.qkernel_spine_enabled()` →
  `settings["feature_flags"].get("qkernel_spine_enabled", False)` — the SAME accessor the other
  reactor feature flags use (no new config mechanism). A config read fault fails CLOSED to the
  legacy path.
- The reactor reads it through a local import at the seam (`from src.engine.qkernel_spine_bridge
  import decide_family_via_spine, qkernel_spine_enabled`) so the flag check is self-contained and
  there is no module-load circular import.
- The flag was NOT flipped to true. No deploy, no daemon restart, no main-tree edits.

## Test output

### Money-path (flag default OFF) — legacy unchanged

```
$ .venv/bin/python -m pytest -q tests/money_path tests/strategy/live_inference
331 passed in 4.17s
```

### Smoke test (flag forced ON) — `tests/integration/test_qkernel_spine_routing.py`

The smoke drives `decide_family_via_spine` (the seam orchestration the reactor calls) on a
realistic Paris 3-bin C family with priced executable proofs in the SAME `_CandidateProof` shape
the reactor materializes, with the Stage-0 `_edli_spine_*` predictive inputs threaded. It asserts:

- (a) the decision is produced by `family_decision_engine` (the spine) — the result carries
  `decided_by_spine` and a `FamilyDecision` whose `receipt_hash` is the spine receipt anchor and
  whose `joint_q` is present (the predictive distribution was live-eligible and the full pipeline
  ran); the selection is the spine's argmax-ΔU, not the legacy scalar selector.
- (b) a no-trade returns a TYPED `no_trade_reason` (spine vocabulary, and
  `SPINE_INPUTS_UNAVAILABLE` when the predictive inputs are genuinely absent).
- (c) the submission-pipeline-facing candidate is well-formed (token_id, direction,
  execution_price, q_posterior, q_lcb_5pct, candidate.condition_id present; the spine overlaid
  `q_source="qkernel_spine"`; the spine's selected candidate_id maps to the proof's (bin, side)).

```
$ .venv/bin/python -m pytest -q tests/integration/test_qkernel_spine_routing.py -rs
....                                                                     [100%]
=============================== warnings summary ===============================
  numpy/lib/_function_base_impl.py:4596: RuntimeWarning: invalid value encountered
  in scalar subtract  (benign degenerate band-draw subtract; same warning the
  engine's own Stage-8b test suite emits)
4 passed, 3 warnings in 56.70s
```

The four cases: (a) `test_decision_is_produced_by_the_spine_not_the_legacy_selector`
(the spine ran the full pipeline — `decided_by_spine`, a 64-hex `receipt_hash`, `joint_q`
present); (b) `test_no_trade_returns_typed_reason_when_every_candidate_is_overpriced` +
`test_no_trade_typed_reason_when_spine_inputs_unavailable` (typed `no_trade_reason` /
`SPINE_INPUTS_UNAVAILABLE`); (c) `test_selected_proof_shape_is_submission_pipeline_ready`
(the spine SELECTED a trade on the underpriced family and the returned `_CandidateProof`
carries token_id / direction / execution_price / q_posterior / q_lcb_5pct /
candidate.condition_id, `q_source="qkernel_spine"`, and the spine's selected candidate_id
maps to the proof's (bin, side)).

## Verdict

CURRENT_REUSABLE — the cutover is behind ONE default-OFF flag; flag OFF leaves the legacy
decision + money path byte-for-byte unchanged (331 money-path + live_inference tests green); flag
ON routes the per-family decision through the rebuilt spine and maps the selection back onto the
submission-pipeline proof shape unchanged. Legacy authorities are INERT (not deleted) for
rollback; Stage 11 removes them. No deploy, no daemon touch, no commit.
