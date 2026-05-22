# Zeus Strategy V-Next — Mainline Completion Report

**Status:** COMPLETE (pending PR #284 merge). Written 2026-05-22 by orchestrator session `4beb2fa4`.
**Authority basis:** PHASE_0_V4_ULTRAPLAN.md §M ENUM (Phases 2–7) + this package's per-phase docs.
**Closing artifacts:** `SESSION_CLOSURE_VERDICT.md` (session-wide opus critic), `PROMO_VALIDATOR_CRITIC.md` (#77 opus critic).

---

## Headline

All 7 mainline phases (1–7) are on `origin/main`. **The single load-bearing invariant — no
new strategy reaches live capital — holds end-to-end**, verified by runtime enumeration of all
13 registry strategies. The cross-phase data seams compose correctly. PR #284 (this closure)
fixes the 3 residual closure findings and ships the #77 promotion-readiness validator.

---

## Phases landed (tags on origin/main)

| Phase | Content | Tag |
|---|---|---|
| 1 | decision_events instrumentation + Day0Nowcast | phase1_landed |
| 2 | book_hash_transitions, NoTradeReason/no_trade_events, FreshnessRegistry, MarketAnalysisVNext, Position.market_slug JSON | phase2_landed (5c471cd51f) |
| 3 | WeatherRegimeTag, ShoulderStrategyVNext (classify+stress+Kelly clamp), ShoulderExposureLedger + cluster cap | phase3_landed (7017670ca8) |
| 4 | FDR family-ID spread_bucket + 6 candidate stubs (all shadow/blocked) | phase4_landed (b6a7df9ff0) |
| 5 | WeatherRegimeTag consumers + Ledoit-Wolf correlation shrinkage + variance cluster-cap | phase5_landed (02491966dc) |
| 6 | EvidenceTier ladder + ShadowExperimentRegistry + RegretDecomposer + LiveReadinessTribunal | phase6_landed (98dafb944f) + phase6_wave_fix_landed (3aff16bfb9) |
| 7 | SettlementOutcome type-gate + Position.lifecycle_state + SettlementCaptureVerifier + backfill | phase7_landed (62ed96e133) |

Schema progression this session: SCHEMA_VERSION 15→26 (world), SCHEMA_FORECASTS_VERSION 5→6.
NOTE: 5 sibling live PRs (#279–283) merged concurrently after phase7_landed, advancing main to
22dba73349 and SCHEMA_VERSION to 26 (evidence-tier authority unification + contract gaps + order
truth). Closure (PR #284) is cut off 22dba73349.

---

## Money gate — THE invariant — VERIFIED INTACT

`StrategyProfile.is_runtime_live()` = `live_status=="live" AND evidence_tier>=LIVE_PILOT_TINY(5)`
(strategy_profile.py:135-138), wired live_allowed_keys → is_strategy_enabled → cycle_runtime.

Runtime enumeration of all 13 strategies (SESSION_CLOSURE_VERDICT SEAM 1):
- **runtime-live=True: 4 — ALL pre-existing** (center_buy, imminent_open_capture, opening_inertia,
  settlement_capture). All were already `live_status: live` at phase2_landed. NONE are new.
- **runtime-live=False: 9** — every new Phase 3/4 strategy (shoulder_buy/sell, 6 P4 candidates,
  center_sell). Gated OFF by tier (SHADOW_PASS=3, PAPER_COHORT=4 both < LIVE_PILOT_TINY=5) and/or
  live_status (shadow/blocked).

**No Phase 3–7 strategy can reach live capital without an explicit operator promotion.**

## Cross-phase seams — all PASS

- **Regime→Correlation→Candidate**: regime_tag_for → regime_correlation_cache read →
  RegimeCorrelationStore.get → off-diagonal gate. Genuinely wired (not stubbed); shadow-only sizing.
- **Cluster cap (P3 gross + P5 variance)**: `policy_heat = max(gross_heat, variance_heat)` — more
  restrictive wins; fires before the kelly multiplier; graceful degrade on UNKNOWN regime.
- **Tier→Settlement (P7 ⊃ P6)**: SettlementCaptureVerifier imports EvidenceTier, stores it;
  check_pre_promotion_gate is a COHERENT-count gate (≥ threshold). Coherent contract.
- **Schema chain 15→26 + forecasts 5→6**: required gate suite 39 passed/0 failed.
- **Antibodies**: `umaResolutionStatus ==` → 0 matches (P7 type-gate); BH counts-FDR partition;
  Kelly clamp [0.05,0.20]; regret-sign regression — all present + passing.

---

## #77 Promotion-Readiness Validator (the Phase-6-flagged gap, now shipped)

`src/analysis/promotion_readiness.py` — `PromotionReadinessValidator` composes 3 existing signals
per strategy into ONE operator-reviewable READY/NOT_READY recommendation:
(a) EvidenceReport credible-interval, (b) LiveReadinessTribunal promotion predicate,
(c) SettlementCaptureVerifier COHERENT-count gate.

**Hard contract (opus-critic verified):**
- Read-only — never auto-applies a tier, never calls adjudicate() (no DB write).
- READY requires all signals affirmative AND `tier_current < tier_required_for_live`.
- operator_ref fail-closed: a recommendation crossing into a live tier (≥ LIVE_PILOT_TINY) raises
  without operator_ref. Operator-applied ONLY.
- **Fitz #4 fix**: the promotion predicate is single-sourced as `promotion_predicate()` in
  live_readiness_tribunal.py; both adjudicate() and the validator call it, so the operator-facing
  recommendation and the tribunal verdict cannot silently diverge.

Not runtime-wired (advisory layer by design); the money gate above is the actual capital control.

---

## Open items / carry-forwards

- **NONE blocking.** Closure critic found no SEV-1. All 3 findings fixed in PR #284.
- **Advisory**: `evidence_tier_assignments` table has no runtime reader (money gate reads registry
  yaml). The operator-gate promise lives in the absence of an auto-apply reader — documented; if a
  future "latest tier = MAX(assigned_at)" auto-apply reader is built, it MUST preserve the
  operator_ref guard (Phase 6 wave-fix SEV2-2).
- **Tag-placement note**: phase3_landed points at sibling-PR #265's merge (interleaving artifact);
  phase-3 content is in ancestry and verified present + gated.

## What mainline does NOT do (by design)

- Auto-promote any strategy — promotion is operator-gated end to end.
- Live-trade any Phase 3–7 strategy — all gated OFF.
- Read evidence_tier_assignments at runtime — registry yaml is the gate source.
