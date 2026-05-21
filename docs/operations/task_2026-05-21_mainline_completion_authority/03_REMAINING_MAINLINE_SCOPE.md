# Remaining Mainline Scope

Authority chain row 1 (v4 §M ENUM) gives the slot names. Authority chain row 2 (GPT Round 1 dossier) gives substantive intent. Authority chain row 5 (`origin/main` code state) gives what's already implemented.

Each surface below: (a) v4 §M scope label verbatim, (b) dossier intent §/object summary, (c) current `origin/main` state, (d) target outcome, (e) likely file impact, (f) downstream consumers.

---

## Surface 1: Shoulder Strategy Refinement (→ Phase 3)

**v4 §M**: line 1100 "Phases 2-7 (... Shoulder ...)"

**Dossier intent §7**: shoulder is open-ended tail exposure, not bin alpha. Sell-shoulder is short-vol/short-tail; buy-shoulder is regime-dependent long-tail; center-vs-shoulder is family-relative pair trade; tail-hedged basket is research-only; shoulder no-trade gate is mandatory. Requires `ShoulderStrategyVNext` 21-field object (§7.3; verifier recount 2026-05-21: 21 enumerated rows — row count is authoritative over original "20" header), 5 strategy variants (§7.4), separate `hypothesis_family_id` + Kelly haircut 0.05-0.20 + cluster cap (§7.5), Day0-bound interaction logic (§7.6).

**Current `origin/main` state** (re-grep before locking):
- `architecture/strategy_profile_registry.yaml` declares `shoulder_sell` strategy (live_status to verify).
- `src/strategy/strategy_profile.py` (461 LOC) registry + live/shadow/blocked profile gate.
- Per Phase 3 v2 planner finding: hardcoded shoulder branches at `src/engine/evaluator.py:1461/1477/1493` + `src/engine/cycle_runner.py:455` — three triplicate sites + one mirror.
- `src/strategy/selection_family.py:36-50` `make_hypothesis_family_id()` lacks `source` + `regime` fields.
- 5 of 6 `StrategyProfile` declarative gate methods are dead code (only `is_runtime_live` wired).
- `tail_correlation_cluster` does not exist (verify).
- `ShoulderExposureLedger` does not exist (verify).
- No `TailStressScenario` table.

**Target outcome**: Phase 3 ships per planner's v2 plan at `docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md`. Three tracks T1 (`WeatherRegimeTag` + correlation_cluster + family-ID extension), T2 (`ShoulderStrategyVNext` + `TailStressScenario` + registry classifier + Kelly haircut), T3 (`ShoulderExposureLedger` + cluster_cap + shadow_readiness_report). `shoulder_sell` stays `shadow` at Phase 3 end. Sequential T1→T2→T3.

**Likely file impact**: `src/contracts/weather_regime_tag.py` (NEW), `src/contracts/shoulder_strategy_vnext.py` (NEW or extension), `src/strategy/selection_family.py` (extend `make_hypothesis_family_id`), `src/strategy/kelly.py` (shoulder-specific clamp), `src/strategy/strategy_profile.py` (registry-driven classifier), `src/engine/evaluator.py` (replace hardcoded triplicate), `src/engine/cycle_runner.py` (mirror update), `src/state/db.py` (2 new tables: `tail_stress_scenarios`, `shoulder_exposure_ledger`), `src/contracts/no_trade_reason.py` (6 new SHOULDER_* members). Schema bumps: world 15→16 (T2), 16→17 (T3) — both additive.

**Downstream consumers** (must work after Phase 3): live evaluator, riskguard exposure caps, family-FDR (Phase 4), settlement attribution (Phase 6+).

---

## Surface 2: FDR Family-ID `spread_bucket` Extension + Candidate Stubs Production (→ Phase 4)

**v4 §M**: line 1100 "candidate stubs" + line 1101 "FDR family-ID `spread_bucket` extension — Phase 4 `selection_family.py` work (Critic 2 P8)"

**Dossier intent §2.5 + §12 Phase 6**: family scope must widen from `candidate/snapshot` to `hypothesis_family_id × source_truth × weather_system × strategy_family × time_window`. Phase 6 in dossier = candidate stubs implementation (`stale_quote_detector`, `liquidity_provision_with_heartbeat`, `resolution_window_maker`, `neg_risk_basket`, `cross_market_correlation_hedge`, `weather_event_arbitrage`) as shadow-first cohorts with `strategy_profile` blocked-from-live gate.

**Current `origin/main` state**:
- `src/strategy/candidates/` contains 6 stub files (verify each is stub vs implementation).
- `src/strategy/selection_family.py` (148 LOC) — small file, narrow scope. Needs spread_bucket extension.
- `architecture/strategy_profile_registry.yaml` likely lists candidate strategies as `live_status: blocked` or `shadow` — verify.
- `MarketAnalysisVNext` (Phase 2 T4) already has spread fields available (`wide_spread_displayed`, `spread_observed_window_ms`, `depth_at_best_ask` per Phase 0 PR 2+7).

**Target outcome**: Phase 4 ships:
- T1: `spread_bucket: Literal["tight", "medium", "wide"]` (≤$0.05, ≤$0.10, >$0.10) added to `hypothesis_family_id` grammar; FDR families subdivide by spread bucket so wide-spread candidates don't share BH discoveries with tight-spread ones.
- T2-T7: each of 6 candidate stub files gets a production implementation behind `strategy_profile.live_status: shadow` flag. Each consumes `MarketAnalysisVNext` + `EffectiveKellyContext` + `NoTradeReason` taxonomy.
- T8 (cross-cutting): `ShadowDecisionLogVNext` rows from candidate strategies + dossier §9 evidence-tier recording.

**Likely file impact**: `src/strategy/selection_family.py` (extension), `src/strategy/candidates/*.py` (6 production files), `src/strategy/strategy_profile.py` (registry rows for each candidate), `architecture/strategy_profile_registry.yaml` (lifecycle + evidence-tier per candidate), tests `tests/test_candidate_*.py` (one per candidate). No schema bumps; data lives in `no_trade_events` + `decision_events` (already exist).

**Downstream consumers**: Phase 5 (regime-tagged FDR), Phase 6 (evidence ladder), Phase 7 (settlement social→type-gate).

---

## Surface 3: `WeatherRegimeTag` + Math Spec §15.4 Correlation Matrix via Shrinkage (→ Phase 5)

**v4 §M**: line 1105 "Math spec §15.4 correlation-matrix-via-shrinkage — Phase 5 `WeatherRegimeTag` dependency"

**Dossier intent §10 + §13.5**: false diversification is the largest portfolio risk. Cities under same heat dome / cold front correlate; market-maker quotes correlate across families; same source-station carries shared anomaly risk. Required: `WeatherSystemClusterId` + correlation matrix shrinkage to deal with sample-size limits.

**Math basis**: Ledoit-Wolf shrinkage estimator (or analogous) — when sample covariance has `n < p` (few realizations, many cities), shrink toward a structured target (identity, constant-correlation, or single-factor). Math spec §15.4 prescribes shrinkage parameter selection; specific intensity formula must be read verbatim from spec at plan time.

**Current `origin/main` state**:
- `WeatherRegimeTag` Phase 3 T1 will likely land first (per Phase 3 v2 plan) — 5-member enum (verify enum members at Phase 3 T1 merge).
- `config/city_correlation_matrix.json` exists per project memory `keyFiles`. Likely a static historical correlation matrix.
- No shrinkage layer.
- `docs/reference/zeus_math_spec.md` §15.4 — verify section anchor + read prescription.

**Target outcome**: Phase 5 ships:
- T1: math spec §15.4 implementation — `src/strategy/correlation_shrinkage.py` (NEW) with Ledoit-Wolf-style intensity selection + shrinkage target.
- T2: integration with `cluster_exposure_for_bankroll` at `src/engine/evaluator.py:~4068` so the cluster cap uses shrunk correlation, not raw historical matrix.
- T3: `WeatherRegimeTag` × `cluster_id` cross product for regime-conditional correlation (heat dome → high inter-city correlation; normal regime → moderate).
- Tests: synthetic 51-member ensemble residuals → shrinkage target matches spec; cluster cap under heat-dome regime tightens vs normal.

**Likely file impact**: `src/strategy/correlation_shrinkage.py` (NEW), `src/strategy/cluster_exposure.py` (NEW or existing — verify), `src/engine/evaluator.py` (cluster cap consumer), `config/city_correlation_matrix.json` (may extend to multi-regime), `docs/reference/zeus_math_spec.md` §15.4 (verify spec is complete; may need amendment). Schema: TBD — likely no bump if correlation lives in config.

**Downstream consumers**: riskguard, Phase 6 evidence ladder (cluster-cap-respecting position aggregation).

---

## Surface 4: `EvidenceLadder` + Promotion Gates + Shadow Experiment Registry (→ Phase 6)

**v4 §M**: line 1100 "EvidenceLadder promotion"

**Dossier intent §9 + §13.5**: 15-layer evidence ladder (historical weather replay → forecast hindcast → market snapshot reconstruction → synthetic order-book stress → shadow decision logging → forward paper trading → no-trade tracking → negative controls → strategy ablation → Bayesian confidence tiers → small-N cohort gate → regret decomposition → execution feasibility scoring → alpha decay measurement → settlement capture verification). Promotion rule: Tier 0 (idea) → Tier 1 (deterministic semantics) → Tier 2 (replay) → Tier 3 (shadow + no-trade) → Tier 4 (paper) → Tier 5 (tiny live pilot) → Tier 6 (limited live w/ Kelly haircut) → Tier 7 (normal live). No strategy uses normal Kelly before Tier 6.

**Current `origin/main` state**:
- `architecture/maturity_model.yaml` exists per `ls architecture/`. May overlap with promotion tier logic.
- `src/strategy/strategy_profile.py` has `live_status: live | shadow | blocked` — coarser than 7-tier system.
- `decision_events` (Phase 1 T1) + `no_trade_events` (Phase 2 T2) already provide raw substrate for shadow logs.
- `ShadowExperimentRegistry` does not exist.

**Target outcome**: Phase 6 ships:
- T1: `src/contracts/evidence_tier.py` (NEW) — `EvidenceTier` IntEnum 0-7; per-tier promotion rule.
- T2: `src/state/shadow_experiment_registry.py` (NEW) — immutable experiment ID + cohort assignment.
- T3: `src/analysis/regret_decomposer.py` (NEW) — per-trade regret components (forecast/observation/quote/non-fill/fee/timing/source-ambiguity).
- T4: `src/analysis/evidence_report.py` (NEW) — per-strategy evidence report aggregating across shadow + paper + live cohorts.
- T5: `StrategyEvidenceReport` + `PromotionStatus` + `LiveReadinessTribunal` — promotion gate that reads ladder tier and refuses live unless tier ≥ 6.
- T6 (architecture): `architecture/strategy_profile_registry.yaml` extended with per-strategy `evidence_tier` field; `is_runtime_live` consults tier ≥ 6 invariant.

**Likely file impact**: 5+ new files in `src/contracts/` + `src/state/` + `src/analysis/`; `strategy_profile.py` extension; registry yaml extension; `docs/operations/current_strategy_evidence.md` (NEW report). Schema: `shadow_experiments` table (world) + `regret_decompositions` table (forecasts or trades). ~2 schema bumps.

**Downstream consumers**: ALL live promotion gates. Phase 7 may consume `EvidenceTier` for settlement type-gate decisions.

---

## Surface 5: Settlement Social→Type-Gate Migration (→ Phase 7)

**v4 §M**: line 1104 "Settlement social→type-gate migration (SYNTHESIS §2.2) — stays social-gated until Phase 6+"

**Synthesis §2.2 intent** (re-grep): currently settlement attribution / promotion uses "social" signals (e.g., `umaResolutionStatus` text matching). Migration to "type-gate" means typed enum branching (`ResolutionEra` already provides era split; further typing for settlement outcomes). Dossier §1.2 (settlement source truth) + §6.7 (`SettlementCaptureVerifier`) provide the substantive content.

**Current `origin/main` state**:
- `src/contracts/resolution_era.py` (Phase 0 PR 1) provides `ResolutionEra` 2-member enum.
- `src/contracts/settlement_semantics.py` provides WMO rounding + Weather Underground default + HKO oracle_truncate + settlement value gate.
- `src/contracts/uma_resolution.py` likely exists (verify) — currently social-gated.
- `architecture/settlement_dual_source_truth_2026_05_07.yaml` is the authority for era boundaries + `EraAuthorityBasis`.

**Target outcome**: Phase 7 ships:
- T1: full typing of `settlement_provenance` — every row in `settlements_v2` carries `era: ResolutionEra` + `EraAuthorityBasis` (already partially done in Phase 0 PR 1; verify completeness).
- T2: settlement outcome enum (`PHYSICALLY_CONFIRMED`, `SOURCE_PUBLISHED_VENUE_UNRESOLVED`, `VENUE_RESOLVED`, `REDEEMED`, `DISPUTED`, `UMA_UNKNOWN_50_50` — verify against dossier §2.7 + §6.4) replacing social string matching.
- T3: `SettlementCaptureVerifier` (NEW) — per dossier §13.1 #10. Verifies `fact-known/source-published/venue-resolved/redeemable` timestamps cohere.
- T4: position lifecycle states (`fact_known`, `source_published`, `venue_resolved`, `redeemable`) added to `Position` (Phase 2 T5 added `market_slug`; this adds lifecycle field).

**Likely file impact**: `src/contracts/settlement_outcome.py` (NEW), `src/contracts/settlement_semantics.py` (extend), `src/execution/harvester.py` + `src/ingest/harvester_truth_writer.py` (typed outcomes), `src/state/portfolio.py` (lifecycle field), `architecture/settlement_dual_source_truth_2026_05_07.yaml` (lifecycle states), `tests/test_settlement_*.py` (typed gates). Schema: world maybe 17→18 (settlement_outcomes column on settlements_v2 or new table).

**Downstream consumers**: settlement-capture strategy (Phase 4 candidate `resolution_window_maker`), position lifecycle reporting, redemption automation.

---

## Cross-surface dependencies

```
Phase 3 Shoulder (Surfaces 1)
   ├── needs Phase 1 T1 (decision_events) ✓ landed
   ├── needs Phase 2 T2 (NoTradeReason) ✓ landed
   ├── needs Phase 2 T3 (FreshnessRegistry) ✓ landed
   ├── extends `selection_family.py` (T1)
   └── delivers WeatherRegimeTag (consumed by Surface 3)

Phase 4 Candidates + spread_bucket (Surface 2)
   ├── needs Phase 2 T4 (MarketAnalysisVNext fields) ✓ landed
   ├── needs Phase 0 PR 2+7 (EffectiveKellyContext) ✓ landed
   ├── needs Phase 3 T1 WeatherRegimeTag (via Surface 1)
   └── delivers candidate strategies (consumed by Surface 4)

Phase 5 Correlation Shrinkage (Surface 3)
   ├── needs Phase 3 T1 WeatherRegimeTag
   ├── needs cluster_exposure infrastructure (verify on main)
   └── delivers shrunk correlation matrix (consumed by Surface 4 candidates + riskguard)

Phase 6 EvidenceLadder (Surface 4)
   ├── needs Phase 1 T1 (decision_events) ✓ landed
   ├── needs Phase 2 T2 (no_trade_events) ✓ landed
   ├── needs Surface 2 candidates (shadow log substrate)
   └── delivers PromotionStatus (consumed by ALL future strategy work)

Phase 7 Settlement Type-Gate (Surface 5)
   ├── needs Phase 0 PR 1 (ResolutionEra) ✓ landed
   ├── needs Surface 4 EvidenceTier (for type-gated promotion)
   └── delivers typed settlement outcomes
```

Recommend execution order: Phase 3 → Phase 5 (regime + correlation) → Phase 4 (candidates use both) → Phase 6 (evidence ladder over candidates) → Phase 7 (settlement type-gate consumes ladder).

Alternative order if operator wants the shoulder-related shadow runs first: Phase 3 → Phase 4 (candidates only, no spread_bucket yet) → Phase 5 → Phase 6 → Phase 7. Trade-off: Phase 4 candidates ship without regime-tagged FDR until Phase 5 lands; some false-diversification risk during the gap.
