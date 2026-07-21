# Regime Unification (大一统) — 2026-06-12

Authority: operator directive 2026-06-12 (verbatim): "把这些乱套的规则全部都整理一下，
又有fallback又有shadow又有active又有off又有bias。包括别的方面也是，进行一次大一统."
Prior law absorbed: no-caps law, no-unsupported-hardcoded-values law, single-authority
registry law (architecture census 2026-06-11), provenance envelope law.

The disease being killed: ERA LAYERING. Each generation of the system (legacy baseline →
EMOS → full_transport → edli_per_city → replacement fusion) left behind its own probability
treatment, its own shadow/active/off switch vocabulary, and its own fallback into the
PREVIOUS era. The result is a system where the answer to "which number decided this?"
depends on which era's fallback happened to fire. 48h evidence: the exit monitor ran 82%
of its refreshes on a different era's probability than entry used.

## U1 — ONE probability authority per domain (era layering dies)

| Domain | THE authority | Everything else |
|---|---|---|
| Forecast markets (entry + exit + redecision + FDR + Kelly + evidence fields) | replacement chain posterior (`forecast_posteriors` q_json). On the source-clock live route: current provider center + latest causal target-specific ECMWF ENS within-spread + absolute ENS/provider-center disagreement + simultaneous provider between-spread, then decision-time q_lcb machinery. | RETIRED on that route: historical residual σ, constant/fitted σ floors, fitted uniform mixture, city shape mixture, fitted affine center shift, edli_per_city_v1 shift, full_transport_v1 path, legacy ENS p_raw+Platt monitor chain, baseline LCB cap |
| Same-day (day0) extremes | observation authority (METAR/WU running extremes → EMOS/honest-raw on the snapshot) — honestly a DIFFERENT domain (nowcast, not forecast); documented, not a fallback | RETIRED: per-city bias shift on day0 lane (already unreachable under EMOS-sole-calibrator) |

A consumer that cannot use THE authority is either (a) in the other domain (day0), or
(b) evidence the authority's coverage must be COMPLETED (positive direction), never a
license to resurrect a retired era.

2026-07-11 clarification: a missing current target-specific ENS shape is degraded
evidence, not permission to substitute the older residual/floor regime. The source-clock
posterior is non-live until that current carrier exists and passes causality/provenance
checks.

The source-clock carrier also names its probability-semantics revision. A
shaped certificate from another revision cannot be mixed into the current
family auction or held-position monitor; it is re-materialized through the same
seed queue, without a legacy fallback or a parallel regime.

## U2 — Degraded-mode law (the only legal "fallback")

A degraded mode is the SAME authority with explicit staleness/absence branding
(fresh=False, typed reason, watchdog). A fallback into a DIFFERENT era's model is
forbidden — it converts data-staleness into silent model-disagreement.

Concrete: the exit monitor's legacy ENS fallback RETIRES. Replacement-posterior staleness
is fixed at the source instead:
- targeted re-materialization for HELD-position families when their posterior exceeds
  budget (sister mechanism to decision-triggered snapshot refresh);
- the freshness budget is DERIVED from each family's observed materialization cadence
  (artifact, fitted-quantile pattern — never an operator-picked hour count; the current
  9h-vs-25h mismatch was exactly such a guess);
- if still stale: position belief stays fresh=False, BELIEF_AUTHORITY_FAULT escalates —
  blind-and-branded beats wrong-and-confident.

## U3 — ONE regime vocabulary (shadow/active/off dies)

Runtime regimes collapse to TWO orthogonal axes, each typed once:
- submit_lane (per receipt, built 2026-06-12): LIVE / SUBMIT_DISABLED / NO_SUBMIT_ADAPTER / SHADOW.
- EventProcessingDisposition (per event, Wave-2.5): PENDING / PROCESSING / TRANSIENT_WAIT /
  TERMINAL_REJECT / SIDE_EFFECT_UNKNOWN / PROCESSED — persisted, with reason + horizon.
Everything else that says shadow/active/off/canary/veto is either deleted (Wave 1B
precedent) or mapped onto these two axes. Specifically scheduled for retirement:
edli_live_scope strings (admission = event type + source truth), NATIVE_MULTIBIN_*_SHADOW/LIVE,
openmeteo *_shadow/_veto/_trade_authority flag family, replacement-hook SHADOW_ONLY/
SHADOW_VETO statuses (data-class markers stay; gate semantics die).

## U4 — Flags collapse to three legitimate kinds

1. Operator arm (edli_live_operator_authorized + real submit) — policy.
2. Daemon role (launchd identity / live_execution_mode two-state) — wiring.
3. Fitted artifacts (sigma_scale_fit.json pattern: provenance + CI + refit cadence) — math.
Anything else is either always-on (correct), deleted (wrong), or a bug being hidden.
Remaining numeric knobs (market-anchor alpha=0.4, redecision tick-multiples, freshness
budgets) migrate to fitted artifacts on touch.

## U5 — Execution order

_Status re-verified 2026-07-21: item 1 DONE; items 2–5 NOT shipped. Item 3 (retire edli_live_scope as a live gate) unexecuted — `src/engine/event_reactor_adapter.py:6745,6953` still hard-gate admission on `edli_live_scope != "forecast_plus_day0"`; item 5 (edli_per_city_v1 retirement) likewise open — `src/forecast/debias_authority.py` still documents live read+subtract. This checklist has drifted; migrate it to an operations packet on next touch._

1. ✅ DONE 2026-06-12 (commit 479cb34446): Wave-2 batch — baseline-cap exit (single q
   authority), mode-string two-state (edli_live), σ-floor per-cell (no flags),
   refuted-branch deletion (code + keys), taker fold. Deployed all daemons 18:09Z.
2. Exit-staleness root fix: held-family targeted re-materialization + derived freshness
   budget; THEN retire the legacy monitor fallback + unify flag + full_transport monitor
   path in one cut.
3. Regime-vocabulary retirement wave (U3 list) per the full inventory
   (docs/evidence/regime_unification/2026-06-12_inventory.md).
4. Receipt second-brain merge (Wave-2 #4) — final intent unconstructable unless
   invariants hold.
5. edli_per_city_v1 family retirement once steps 2-3 zero its consumers.

Inventory + per-site verdicts: docs/evidence/regime_unification/ (census agents).
