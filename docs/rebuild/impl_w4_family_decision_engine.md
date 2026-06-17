# Stage 8b — family_decision_engine implementation report

Created: 2026-06-14
Authority basis: docs/rebuild/consult_build_spec.md (Create src/decision/family_decision_engine.py
block lines 854-904: FamilyDecision 858-871, decide() algorithm 876-901; Stage 8 block 1166-1184) +
docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD — no live edits; reactor
wiring is Wave 5).

## What was built

The terminal decision orchestrator: ONE `decide()` over the whole q-kernel spine. It ASSEMBLES the
already-built modules (forecast → q → band → family book → coherence → routes → payoff economics);
it re-implements none of them. It is the only decision authority.

### Files written (new files only — no live file touched)

- `src/decision/family_decision_engine.py` — the engine + the FamilyDecision contract.
- `tests/decision/test_family_decision_engine.py` — the three spec-named RED-on-revert tests plus
  supporting/primitive checks.
- `src/decision/__init__.py` — MODIFIED only to re-export the new public symbols (package init for
  the new module; not a live trading file, changes no behavior).

### Symbols (`src/decision/family_decision_engine.py`)

- `FamilyDecision` (frozen dataclass, spec lines 858-871) — EXACT spec field names: `decision_id`,
  `case`, `predictive`, `omega`, `joint_q`, `band`, `family_book`, `market_coherence`, `candidates`
  (`tuple[CandidateEconomics, ...]`), `selected` (`CandidateEconomics | None`), `no_trade_reason`
  (`str | None`), `receipt_hash`. Plus two non-spec provenance fields excluded from the spec
  contract: `candidate_decisions` (per-candidate route + flags + scalar telemetry) and
  `market_implied_q` (the de-frictioned market q for the receipt).
- `CandidateDecision` (frozen dataclass) — internal carrier: `route` (CandidateRoute), `economics`
  (CandidateEconomics), `direction_law_ok`, `coherence_allows`, `robust_trade_score` (the demoted
  SCALAR telemetry; never selects).
- `FamilyDecisionEngine` — holds the injected readers/builders; `decide(...)` runs the pipeline.
  Private steps: `_enumerate_candidates`, `_score_route`, `_zero_economics`, `_select`,
  `_no_trade_before_q`, `_receipt_hash`, `_decision_id`.
- `forecast_bin_id(joint_q)` — the modal (argmax-mass) bin: the direction-law reference (spec 947-951).
- `direction_law_ok(route, *, forecast_bin)` — YES legal iff bin IS the forecast bin; NO legal iff bin
  is NOT the forecast bin (spec 947-951).
- `coherence_allows(route, report)` — allows unless the report is `INCOHERENT_BLOCK_LIVE` and the
  candidate's bin is an offending bin (spec 891, 953).
- Reader protocols (injected; the reactor owns the real ones at Wave 5): `FreshModelReader`,
  `Day0Reader`, `PredictiveBuilder`, `FamilyBookBuilder`.
- No-trade reason vocabulary: `PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE`, `NO_EXECUTABLE_ROUTE_CANDIDATE`,
  `NO_DIRECTION_LAW_CANDIDATE`, `MARKET_INCOHERENT_BLOCK_LIVE`, `NO_POSITIVE_EDGE_CANDIDATE`.

## Spec lines implemented (decide() algorithm 876-901)

The pipeline runs in the spec ORDER (the order is the contract):

1. `resolution = event_resolution_for_city(case)` — threaded via `case.resolution` (validated to
   match `omega.resolution`).
2. `omega = outcome_space_from_family(family, resolution)` — the engine takes the already-built
   `OutcomeSpace` (the reactor resolves it; identity when the caller holds the Omega).
3. `models = fresh_model_reader.read(case)`; `obs = day0_reader.read(case)`.
4. `predictive = predictive_builder.build(case, models, obs)`.
5. **FIRST GATE** (884-885): `if not predictive.live_eligible: return no_trade(
   "PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE")` — emitted BEFORE q is integrated
   (`joint_q`/`band`/`family_book`/`market_coherence` all None; empty candidates; receipt present).
6. `q = build_joint_q(predictive, omega)`; `band = build_joint_q_band(predictive, omega)`.
7. `family_book = family_book_builder(omega, snapshots)`; `market_q = build_market_implied_q(family_book)`.
8. `coherence = assess_market_coherence(joint_q, family_book, candidate_bins, ...)` (the live API
   subsumes the spec's separate `market_implied_q_builder.build` + `market_coherence.evaluate`).
9. `routes = build_negrisk_route_set(omega→family_book)`; candidates enumerated one per (bin, side)
   executable route, each scored by `compute_candidate_economics` (the VECTOR edge + vector-argmax size).
10. The filter chain (896-898): `direction_law_ok` → `coherence_allows` → `edge_lcb > 0 AND
    optimal_delta_u > 0` (with the payoff layer's `live_candidate_passes` as a final structural re-proof).
11. `selected = argmax(survivors, key=optimal_delta_u)` (900). The scalar `robust_trade_score`
    is recorded per candidate as telemetry and NEVER reaches the selection.
12. Every exit returns a `FamilyDecision` with a `receipt_hash` (871); a no-trade carries
    `selected=None` and the `no_trade_reason` naming the first gate that emptied the survivor set.

## The three corrected transformations preserved (operator law — no gate/cap over a broken transform)

1. **Selection is `argmax optimal_delta_u`, never a scalar trade score** (900-903, 1184). The scalar
   `q - price` (`scalar_trade_score` from payoff_vector) is computed for the receipt but is not a
   filter condition and not the argmax key. A scalar-positive / vector-negative or scalar-higher /
   ΔU-lower candidate cannot be promoted — the only selection inputs are the vector quantities and the
   structural proofs.
2. **Coherence blocks BEFORE scoring** (891, 953; Stage 9). `coherence_allows` reads the typed
   `MarketCoherenceReport`; an `INCOHERENT_BLOCK_LIVE` offending bin is DROPPED before the edge/ΔU
   gate (the Tokyo q=0.47 vs deep ask=0.001 dies here). The q is never mutated.
3. **Live eligibility is the first gate** (884-885). No width-less q, no degenerate band, no candidate
   when the predictive distribution is ineligible.

## RED-on-revert tests (each fails if the corrected transform is reverted)

All in `tests/decision/test_family_decision_engine.py`:

- `test_decide_filters_direction_then_coherence_then_edge_then_argmax_delta_u` — end-to-end `decide()`
  selects the direction-law-legal, coherent, +edge, +ΔU candidate by argmax ΔU (a cheap-but-coherent
  underpriced family; YES on the forecast bin b25 is the survivor; the non-forecast-bin YES candidates
  are direction-law-filtered).
- `test_no_trade_reason_present_when_no_candidate_passes` — every route priced above fair value →
  empty survivor set → a no-trade `FamilyDecision` with `selected=None`,
  `no_trade_reason == NO_POSITIVE_EDGE_CANDIDATE`, and a 64-char `receipt_hash` (not `None`).
- `test_tokyo_impossible_bin_blocked_by_coherence_before_scoring` — model q ~0.47+ on the forecast bin
  b25 vs a DEEP market q ~0.001 → `INCOHERENT_BLOCK_LIVE` with b25 offending; YES_25 has a positive raw
  edge but `coherence_allows=False`, so it is NOT selected (dropped before scoring).

Supporting tests: `test_select_uses_argmax_delta_u_not_scalar_trade_score` (the isolated, fast
RED-on-revert for the selection KEY — hand-built decisions where the scalar-max ≠ the ΔU-max; the
engine picks the ΔU-max); `test_no_trade_predictive_not_live_eligible_is_first_gate` (the σ-authority-
missing first gate); `test_forecast_bin_is_the_modal_bin_and_direction_law_reads_it`.

### RED-on-revert proven empirically

- Reverting `_select` to `argmax robust_trade_score` → `test_select_uses_argmax_delta_u_not_scalar_trade_score`
  FAILS.
- Reverting `coherence_allows` to always-True → `test_tokyo_impossible_bin_blocked_by_coherence_before_scoring`
  FAILS ("YES_25 must NOT be selected").

## Drift resolved (recorded per operator law)

1. **`decide(case, family, snapshots, portfolio)` opaque inputs → live types.** `family` is the
   already-built `OutcomeSpace` (the reactor resolves `outcome_space_from_family`; the engine threads
   it and validates `omega.resolution` against `case.resolution`). `snapshots` is the
   `Mapping[str, ExecutableMarketSnapshot]` keyed by bin_id the `FamilyBook` builder consumes.
   `portfolio` is split into the `PortfolioExposureVector` (A_y) and the `FamilyPayoffMatrix` (the
   outcome geometry) — both live `utility_ranker` types the payoff layer already uses.
2. **`market_coherence.evaluate(q, market_q)` → the live `assess_market_coherence` API.** The live
   function builds the de-frictioned market-implied q FROM the family book internally and compares per
   candidate bin, returning the same typed `MarketCoherenceReport`. The market-implied q is also
   surfaced on the decision (`build_market_implied_q`) for the receipt — the spec's separate
   `market_implied_q_builder.build` step is subsumed.
3. **`payoff_decision_builder.score(...)` → per-candidate fold.** The live
   `compute_candidate_economics` is per (CandidateRoute, NativeSideCandidate); the engine enumerates one
   candidate per (bin, side) executable route (direct YES + the dominant NO via `best_no_route`), pairs
   each with its sizing candidate, and folds the family `score`.
4. **Readers/builders are injected Protocols.** The spec's `fresh_model_reader` / `day0_reader` /
   `predictive_builder` / `family_book_builder` are small injected interfaces (defaults reuse the live
   builder functions), so the engine is testable now and the reactor injects real readers at Wave 5
   without editing this file.
5. **Direction-law reference = the modal joint-q bin.** The spec direction law (947-951: "YES legal
   only when buying the forecast bin; NO legal only when the payoff vector is 'not forecast bin'") is
   anchored on `forecast_bin_id = argmax q` — the bin the predictive distribution most favors.

## Test results

Module tests: `python -m pytest -q tests/decision/test_family_decision_engine.py`
→ **6 passed in 72.02s** (2 benign numpy quantile RuntimeWarnings on a degenerate band-draw subtract;
no failures).

Money-path regression: `python -m pytest -q tests/money_path tests/strategy/live_inference`
→ **331 passed in 4.25s** — no regression; the money path is unaffected (greenfield module, not wired).

## Verdict

CURRENT_REUSABLE — new files only; assembles the audited Stage 1-9 spine modules; all three spec-named
RED-on-revert tests pass and are proven RED when the corrected transform is reverted; money path
unaffected. Reactor wiring is Wave 5 (this module is not yet wired into the live decision path).
