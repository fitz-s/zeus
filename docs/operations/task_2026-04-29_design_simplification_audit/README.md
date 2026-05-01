# Design Simplification Audit - 2026-04-29

Status: evidence packet plus scoped repair record.
Scope: Zeus live money path from weather source selection through forecast signal, fallback authorization, market identity, execution/fill/exposure truth, risk capability, mode/venue residue, telemetry/replay residue, and simplification candidates.
Non-goal: this packet does not authorize source-routing changes, DB mutation, live deploy, settlement-source edits, or config flips.

## Question Answered

Does the current design express the intended rule "Open-Meteo is only the final fallback"?

Initial audit answer: only for Day0 observation monitoring. The live ENS
probability path did not express that rule; entry and monitoring could fetch
Open-Meteo ECMWF through default `fetch_ensemble()` arguments.

Current Phase 1D/1E/1F status: the first source-policy safety slices now make
Open-Meteo live ENS an explicit degraded fallback. Omitted `fetch_ensemble()`
role/model arguments mean `entry_primary`; Open-Meteo source specs allow only
`monitor_fallback` and `diagnostic`; Open-Meteo live ENS is no longer
`tier="primary"`; evaluator entry fails closed before p_raw when entry-primary
authority is absent or a degraded fallback payload appears; monitor refresh
records fallback source/role/degradation in successful evidence. Evaluator and
monitor now read `settings["ensemble"]["primary"]`, evaluator crosscheck reads
`settings["ensemble"]["crosscheck"]`, and provider-specific `source_id` drives
model-bias lookup while `model_family` is preserved separately for context.
The ECMWF Open Data scheduled collector is now registered as a diagnostic,
non-executable source and writes legacy mirrored snapshots as `UNVERIFIED`.
TIGGE/direct ECMWF remains experimental/operator-gated and inactive, so this
packet still does not authorize live entries.

## Files

- `findings.md`: detailed findings, including prior thread-visible findings and new design-simplification findings.
- `evidence.md`: code, config, local DB, and official external evidence.
- `simplification_plan.md`: proposed first-principles repair sequence and coverage gates.
- `native_multibin_buy_no_implementation_spec.md`: implementation handoff for
  native executable multi-bin `buy_no`; evidence only, not live authorization.
- `probability_execution_split_spec.md`: critic-approved sequencing spec for
  separating market-prior probability, executable token cost, trade hypothesis
  economics, and reporting cohorts; evidence only, not live authorization.
- `missing_shoulder_support_layer_plan.md`: revised plan for separating
  contract support topology from executable weather-bin surfaces; evidence
  only, not live authorization.

## Audit Method

The audit used a layered money-path model:

1. Authority law and docs: current architecture, math spec, module AGENTS, operation current facts.
2. Runtime reachability: `src/main.py`, cycle runner/runtime, evaluator, monitor, execution, harvester.
3. Source selection: forecast registry, ENS client, TIGGE stub, Open Data collector, historical forecast appender.
4. Fallback semantics: Day0 observation chain, tier resolver, replay diagnostic fallback, feature flags.
5. Market/execution truth: Gamma event identity, CLOB snapshot authority, venue command grammar, fill facts, exposure lots, and chain reconciliation.
6. Risk/capability gates: cutover guard, risk levels, portfolio governor, heartbeat, WS gap, collateral, and source degradation.
7. Mode and venue residue: live-only boot gate, paper-mode branches, benchmark fake venue, shadow telemetry.
8. Evidence checks: local DB read-only counts and official provider documentation.
9. False-positive control: each finding labels whether it is a live blocker, semantic risk, complexity debt, or an explicit non-issue boundary.

## Current Top-Level Verdict

Fixing the earlier 13 thread-visible findings was not enough to say Zeus can
run live on Polymarket with no system-design obstacles. Phase 1D/1E/1F closes
the Open-Meteo omitted-default entry path, settings-driven primary/crosscheck
selection, provider-specific bias identity, and the unowned ECMWF Open Data
scheduled collector surface, but the broader forecast-source authority program
remains open until a primary direct TIGGE/ECMWF source, source timing/payload
facts, and downstream source-evidence propagation are complete.

Current 2026-04-30 non-Paris repair overlay: the local code path now closes or
fails closed the previously active non-Paris design blockers for Day0
observation authority, local-day ENS/vector validity, entry and exit executable
snapshot production, V2 bound/final submission-envelope provenance, RED/ORANGE
risk behavior, entry/exit partial-fill exposure truth, harvester HIGH/LOW
settlement and learning lineage, calibration maturity, collateral freshness,
fee-rate units, and day0-capture discovery windows. Paris source mismatch is
still excluded/open, and live deployment remains blocked by external
`current_state.md` evidence requirements plus explicit operator authorization.
Whale-toxicity now has a narrow monitor-side orderbook-adjacent pressure
detector with tests; Zeus does not claim true market-wide trade-print sweep
detection until a separate public market-trade event feed is wired and tested.

The broader first-principles closure set now spans eleven repair axes: source policy, canonical data writes, runtime/evidence naming, feature-flag convergence, fallback degradation, market identity, exposure ledger, submit capability, evidence-grade grammar, time causality, and ex-ante economic alpha proof. Several of these are architecture simplifications rather than direct live blockers, but all are necessary before making the stronger claim that only math/physics uncertainty remains.

## Repair Readiness Verdict

The findings are detailed enough to begin repair. The repair path is now phaseable:

1. Freeze evidence vocabulary and live-mode authority.
2. Close source and time causality.
3. Close market identity and execution capability.
4. Close exposure ledger and fill finality.
5. Close strategy reachability and selection/sizing parity.
6. Implement promotion-grade economics and staged live.

This packet still does not authorize live deploy or production DB mutation by itself. It is sufficient to start scoped implementation packets, one phase at a time, with topology navigation, scoped AGENTS reads, regression tests, and closeout evidence per phase.
