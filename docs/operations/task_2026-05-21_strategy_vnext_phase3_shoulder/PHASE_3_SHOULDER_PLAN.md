# Phase 3 — ShoulderStrategyVNext + Tail-Risk Ledger (Plan v2)

**Authority**: docs/operations/task_2026-05-21_mainline_completion_authority/04_PHASE_3_SHOULDER.md

**Created**: 2026-05-21 | **Supersedes**: v1 (2026-05-21, pre-dossier — registry-gate-wireup framing was insufficient) | **Status**: SCAFFOLD-NEEDS-CRITIC

## §0 Authority

**Verbatim from `PHASE_0_V4_ULTRAPLAN.md` §M L1100**: `"Phases 2–7 (Day0Nowcast, MarketAnalysisVNext, Shoulder, candidate stubs, EvidenceLadder promotion)."`

**Verbatim from `AUTHORITY_GPT_ROUND_1_DOSSIER.md` §7**:
- §7.1: `"Open shoulder: unbounded state region, tail model dominates, rare event sample scarcity, correlated weather regime crash, source anomaly exposure, retail lottery demand possible, market-maker inventory skew possible."`
- §7.3: `"Fields: is_open_shoulder, shoulder_side (upper/lower), metric (high/low), tail_direction, finite_adjacent_bin, tail_probability_raw, tail_probability_calibrated, tail_probability_stressed, tail_regime_tag, retail_lottery_bias_score, extreme_weather_underpricing_score, source_anomaly_score, native_yes_quote, native_no_quote, liquidity_gate, shoulder_family_id, tail_correlation_cluster, max_loss_scenario, kelly_haircut, max_exposure_cap, no_trade_reason."`
- §7.5: `"Separate hypothesis_family_id = shoulder:{city}:{metric}:{target_date}:{source}:{regime}. Kelly multiplier max e.g. 0.05–0.20 of normal until forward evidence. Hard max notional per shoulder side. Cluster cap across same weather system. No same-direction shoulder sell across multiple cities under one heat dome/cold front. Stress test every candidate under: +2σ forecast error, station anomaly, late-day advection, source revision, model tail underdispersion, correlated city crash."`
- §7.6: `"Shoulder strategy becomes safer only when Day0 bound has eliminated tail."`
- §12 Phase 3: `"isolate shoulder economics and prevent short-tail contamination. New artifacts: ShoulderStrategyVNext, ShoulderExposureLedger, TailStressScenario, WeatherRegimeTag. Tests: open_shoulder identification, cap enforcement, Kelly haircut, cluster cap, stress failure. Blast radius: risk sizing. Rollback: block all shoulder live profiles. Promotion gate: shoulder-specific shadow report and stress pass. Unresolved: best regime taxonomy."`
- §7.7: `"Shoulder is not banned. But shoulder live promotion without VNext gates is a hidden portfolio bomb."`

**Current `origin/main` code state** (all via `git show origin/main:<path>`, re-grepped 2026-05-21 in this session):
- `architecture/strategy_profile_registry.yaml` L171-196: shoulder_sell `live_status: shadow`, `kelly_default_multiplier: 0.0`, `min_shadow_decisions: 100`. L226-244: shoulder_buy `live_status: blocked`.
- `src/engine/evaluator.py` L1461/L1477/L1493: shoulder classification hardcoded. L4068: `cluster_exposure_for_bankroll` enforces K3 `city.cluster` cap but NOT "weather system" cross-city correlation per §7.5.
- `src/strategy/risk_limits.py` L7-13: `RiskLimits` has `max_correlated_pct` but no shoulder-side hard cap.
- `src/strategy/kelly.py` L60-78: `strategy_kelly_multiplier` reads registry; L198: `phase_aware_kelly_multiplier` is canonical resolver. No `kelly_haircut` field.
- `src/strategy/selection_family.py` L36-50: `make_hypothesis_family_id` keys = `(cycle_mode, city, target_date, temperature_metric, discovery_mode, decision_snapshot_id)`. **Missing `source` and `regime` per §7.5**.
- `src/strategy/candidates/` 6 stubs exist; NOT Phase 3 surface (Phase 4/6).
- **NEW**: `ShoulderExposureLedger`, `TailStressScenario`, `WeatherRegimeTag` all absent — `git grep` returns 0 hits in `src/`.
- **Phase 2 consumable**: `src/contracts/no_trade_reason.py`, `src/contracts/freshness_registry.py`, `src/analysis/market_analysis_vnext.py`, `src/state/decision_events.py`, `src/state/no_trade_events.py`.
- **Phase 2 NOT yet landed** (per dossier §12): `Day0BoundState`. **Implication**: §7.6 wire is record-only (relationship test xfail until Day0Bound lands).

**Structural readout** (Fitz #1 — K decisions, not N artifacts): 4 dossier §12 artifacts collapse to **3 structural decisions**:
- **D1** = `WeatherRegimeTag` + `tail_correlation_cluster` + family-ID `:source:regime` extension. Without this, downstream artifacts hardcode regime as a literal.
- **D2** = `ShoulderStrategyVNext` (§7.3 fields) + `TailStressScenario` stress kernel (§7.5 +2σ). Wires registry-driven classification, killing hardcoded shoulder triplicate per §7.7 "VNext gates".
- **D3** = `ShoulderExposureLedger` + weather-system cluster cap + shoulder-specific shadow-readiness report (§12 promotion gate). Operator-gated; no auto-promote.

shoulder_sell stays `live_status: shadow`, shoulder_buy stays `blocked` at Phase 3 end. Rollback per §12 = block all shoulder live profiles remains safe state.

## §1 Scope + Non-Goals

**In scope**: 4 dossier §12 artifacts; registry-driven shoulder classifier (kills hardcoded triplicate); hypothesis_family_id `:source:regime` extension; shoulder Kelly haircut [0.05, 0.20]; weather-system cluster cap; shoulder shadow-readiness report.

**Non-goals (explicit defer per v4 §M phase ENUM)**:
- **No live promotion** of shoulder_sell / shoulder_buy at Phase 3 end.
- **Candidate stubs** (6 files in `src/strategy/candidates/`) → **Phase 4**.
- **EvidenceLadder / shadow_experiment_id / promotion_status / regret decomposer** → **Phase 5+**.
- **Day0BoundState wire** (§7.6) → Phase 2 backfill owns; Phase 3 ships relationship-test xfail only.
- **No regime taxonomy lock-in** — §12: `"Unresolved: best regime taxonomy."` T1 ships MINIMAL `HEAT_DOME / COLD_SNAP / NOMINAL / SOURCE_ANOMALY / UNKNOWN`; operator-tunable post-T1.
- **No NoTradeReason schema bump** (additive members only per Phase 2 T2 contract).

## §2 Tracks

### T1 — `WeatherRegimeTag` + Family-ID Extension (~250-350 LOC, D1)
**Purpose**: build "regime + correlation cluster" surface §7.5 depends on.

**Files**: `src/contracts/weather_regime_tag.py` (new — `WeatherRegimeTag(StrEnum)` 5 members; `regime_tag_for(city, target_date, decision_time, conn) -> WeatherRegimeTag` rule-based classifier reading observation history + forecast median; fail-open to `UNKNOWN`); `src/strategy/correlation_cluster.py` (new — `tail_correlation_cluster_for(city, regime, target_date) -> str` maps to cluster ID e.g. `"heat_dome_east_2026_07_15"`); `src/strategy/selection_family.py` L36-50 (extend signature `*, source: str = "", regime: str = ""`; new wrapper `make_shoulder_hypothesis_family_id` enforces both non-empty for shoulder strategies per §7.5); `tests/test_weather_regime_tag.py` + `tests/test_selection_family_shoulder_scope.py` (new).

**Schema**: no DB change. **Relationship test**: `test_inv_shoulder_family_id_requires_source_and_regime`. **NoTradeReason**: none added. **FreshnessLevel**: `FreshnessRegistry.evaluate("observation_history", age)` for classifier inputs. **MarketAnalysisVNext**: none. **Depends on**: nothing landed-after.

### T2 — `ShoulderStrategyVNext` + `TailStressScenario` (~400-550 LOC, D2)
**Purpose**: build §7.3 object model + §7.5 stress kernel; wire registry-driven classification per §7.7.

**Files**: `src/strategy/shoulder_vnext.py` (new — `ShoulderStrategyVNext` dataclass §7.3 fields; `classify_shoulder_candidate(edge, candidate, market_phase, conn) -> ShoulderStrategyVNext | None`); `src/strategy/tail_stress.py` (new — `TailStressScenario(StrEnum)` 6 §7.5 scenarios: `FORECAST_PLUS_2SIGMA, STATION_ANOMALY, LATE_DAY_ADVECTION, SOURCE_REVISION, MODEL_TAIL_UNDERDISPERSION, CORRELATED_CITY_CRASH`; `stress_test(candidate, scenarios) -> dict[TailStressScenario, float]` returns max_loss_pct; `tail_probability_stressed = max(scenarios)`); `src/engine/evaluator.py` L1455-1500 (replace hardcoded shoulder branches in `_edge_source_for` / `_strategy_key_for` / `_strategy_key_for_hypothesis` with single `_classify_via_registry` helper consulting `strategy_profile.is_direction_allowed / is_bin_topology_allowed / is_phase_allowed`; cycle-axis short-circuits STAY AS-IS); `src/engine/cycle_runner.py` L455 (mirror); `src/strategy/kelly.py` (extend `strategy_kelly_multiplier` to clamp shoulder paths to [0.05, 0.20] per §7.5 when `live_status=shadow` AND `kelly_default_multiplier > 0.0`); `src/contracts/no_trade_reason.py` (additive: `SHOULDER_STRESS_FAIL, SHOULDER_REGIME_MISMATCH, SHOULDER_NATIVE_NO_DEPTH_INSUFFICIENT, SHOULDER_DAY0_BOUND_NOT_ELIMINATED, SHOULDER_NO_TRADE_GATE` per §7.4 variant 5); `tests/test_shoulder_vnext_classify.py` + `tests/test_tail_stress.py` + `tests/test_inv_shoulder_kelly_haircut_clamp.py` (new).

**Schema**: world DB new table `tail_stress_scenarios` (PK matches DecisionNaturalKey; columns: scenarios JSON, max_loss_pct, tail_probability_stressed, schema_version CHECK (15, 16)); SCHEMA_WORLD_VERSION 15→16. **Relationship test**: `test_inv_classifier_equals_registry_for_all_boot_safe_strategies` + `test_inv_shoulder_kelly_multiplier_within_5_to_20_pct`. **NoTradeReason additions**: 5. **FreshnessLevel**: `FreshnessRegistry.evaluate("ens_snapshot", age)` for stress kernel input. **MarketAnalysisVNext**: consumes `native_yes_quote / native_no_quote / liquidity_gate` from `ExecutableMarketSnapshotV2`. **Depends on**: T1 merged; P2 T2 ✓; P2 T4 ✓.

### T3 — `ShoulderExposureLedger` + Shadow-Readiness Report (~300-400 LOC, D3)
**Purpose**: persistent shoulder-side exposure + §7.5 cluster cap + §12 promotion-gate report. Operator-gated.

**Files**: `src/state/shoulder_exposure_ledger.py` (new — writer/reader; world DB table `shoulder_exposure_ledger` columns: shoulder_side, weather_system_cluster, city, target_date, source, regime, notional_usd, decision_event_id FK, observed_at, schema_version CHECK (16, 17); INV-37 ATTACH+SAVEPOINT); `src/strategy/shoulder_cluster_cap.py` (new — `check_shoulder_cluster_cap(cluster, side, proposed_notional) -> tuple[bool, str]` per §7.5 "no same-direction shoulder sell across multiple cities under one heat dome"); `src/engine/evaluator.py` ~L4080 (single new site after `cluster_exposure_for_bankroll`, shoulder-only; emits `NoTradeReason.SHOULDER_CLUSTER_CAP_EXCEEDED`); `scripts/shoulder_shadow_readiness_report.py` (new CLI — aggregates `decision_events` shoulder shadow + `no_trade_events` shoulder rejections + `tail_stress_scenarios` + `shoulder_exposure_ledger`; emits markdown + JSON; readiness_status enum `{INSUFFICIENT_SHADOW, INSUFFICIENT_STRESS_COVERAGE, INSUFFICIENT_REGIME_COVERAGE, READY_FOR_OPERATOR_REVIEW}`; **NEVER mutates live_status**); `architecture/script_manifest.yaml` + `architecture/source_rationale.yaml` (register); `tests/test_shoulder_exposure_ledger.py` + `tests/test_shoulder_cluster_cap.py` + `tests/test_shoulder_shadow_readiness_report.py` (new); `docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PROMOTION_PLAYBOOK.md` (new — operator runbook tying §7.4 SHADOW_FIRST / UNKNOWN_BUT_INTERESTING / RESEARCH_ONLY / IMPLEMENTATION_READY labels to live_status flips).

**Schema**: 1 new world table; SCHEMA_WORLD_VERSION 16→17 (after T2). **Relationship test**: `test_inv_shoulder_cluster_cap_prevents_correlated_overconcentration` + `test_inv_readiness_status_pure_function_of_inputs`. **NoTradeReason additions**: 1 (`SHOULDER_CLUSTER_CAP_EXCEEDED`). **FreshnessLevel**: `FreshnessRegistry.evaluate("decision_events", age)` on most-recent shadow shoulder decision. **MarketAnalysisVNext**: none. **Depends on**: T2 merged; P1 T1 ✓; P2 T2 ✓; P2 T3 ✓.

## §3 Cross-Track Invariants

1. **shoulder_sell stays `live_status: shadow`, shoulder_buy stays `blocked`** at Phase 3 end. No code mutates `live_status`. Rollback (§12) = block all shoulder live profiles is safe state.
2. **Registry-driven classification.** No hardcoded shoulder string literals in `src/engine/` or `src/strategy/` outside `strategy_profile.py` + registry YAML + tests/fixtures.
3. **Kelly haircut [0.05, 0.20]** per §7.5 (only when `live_status=shadow` AND `kelly_default_multiplier > 0.0`; current 0.0 unchanged). Test `test_inv_shoulder_kelly_haircut_clamp`.
4. **Cluster cap fires BEFORE `phase_aware_kelly_multiplier`** — wasted compute on capped-out entries is the failure mode.
5. **INV-37** for all cross-DB reads (tail_stress_scenarios, shoulder_exposure_ledger, decision_events, no_trade_events, settlements_v2).
6. **Relationship tests land BEFORE implementation** (Fitz). §7.6 Day0Bound interaction is RECORDED but UNGATED — `SHOULDER_DAY0_BOUND_NOT_ELIMINATED` member exists in T2; `test_inv_shoulder_safer_after_day0_bound` is `xfail(strict=False)` until P2 ships `Day0BoundState`.
7. **No regime taxonomy lock-in.** T1's 5-member enum is MINIMAL; operator extends via PROMOTION_PLAYBOOK config without code change.

## §4 3-Class Grep Checklist (all via `git show origin/main:<path>`, executor re-runs within 10 min of dispatch per memory:`grep_gate_before_contract_lock`)

**Type-1 field/symbol**: `architecture/strategy_profile_registry.yaml` for `live_status:`/`min_shadow_decisions:`/`kelly_default_multiplier:` (expect 7 strategies); `src/strategy/selection_family.py` for `def make_hypothesis_family_id`/`def make_edge_family_id` (L36/L74 lack source+regime); `src/contracts/no_trade_reason.py` enum baseline (additive-extension contract); `src/strategy/kelly.py` for `def strategy_kelly_multiplier`/`def phase_aware_kelly_multiplier`.

**Type-2 storage**: `src/state/decision_events.py` CREATE TABLE + strategy_key (P1 T1); `src/state/no_trade_events.py` CREATE TABLE + reason CHECK (P2 T2); `src/state/db.py` for `SCHEMA_WORLD_VERSION` + `settlements_v2` (T2 bumps to 16, T3 to 17).

**Type-3 line-anchor**: `src/engine/evaluator.py` L1455-1500 (hardcoded shoulder branches at L1461/L1477/L1493 — T2 replace) and L4060-4090 (`cluster_exposure_for_bankroll` K3 — T3 add-after); `src/engine/cycle_runner.py` L450-460 (`_classify_edge_source` L455 — T2 mirror); `src/strategy/selection_family.py` L30-100 (family-ID — T1 extend); `src/strategy/kelly.py` L55-90 (`strategy_kelly_multiplier` — T2 clamp).

## §5 Risk + Escape Hatches

**Risks**:
- **R-1 Regime taxonomy unresolved** (§12). Naive tag misclassifies → false cluster collapse / cap-breach. **Mit**: T1 5-member enum + fail-open to `UNKNOWN`; `UNKNOWN` treated as no-cluster. Antibody `test_inv_unknown_regime_does_not_aggregate_cluster`.
- **R-2 Registry classifier mis-orders cycle-axis short-circuit** (settlement_capture / opening_inertia / imminent_open_capture early-return BEFORE per-edge topology). **Mit**: keep cycle-axis branches AS-IS; only trailing shoulder/center routes through new helper. `test_evaluator_strategy_key_failclosed.py` extension pins ordering.
- **R-3 Kelly haircut clamp could silently zero shoulder positions** when `kelly_default_multiplier=0.0`. **Mit**: clamp applies only when `live_status=shadow` AND mult > 0.0; current 0.0 unchanged.
- **R-4 +2σ stress kernel needs ensemble sigma** maybe unavailable. **Mit**: `stress_test` fails closed (`tail_probability_stressed=NaN → SHOULDER_STRESS_FAIL`) when ENS members < min_members.
- **R-5 Double schema bump (T2 15→16, T3 16→17)**. **Mit**: both additive; revert order = reverse dispatch order.

**Escape hatches**:
- **EH-1 `ZEUS_SHOULDER_VNEXT_ENABLED`** (default OFF one canary cycle). ON activates registry classifier + cluster cap + stress kernel; OFF restores hardcoded branches + skips ledger writes.
- **EH-2 Rollback (§12)**: `ZEUS_SHOULDER_VNEXT_ENABLED=0` + force `live_status: blocked` for shoulder_sell + shoulder_buy. Shadow logging stops; existing decisions retained.
- **EH-3 Per-track revert** `gh pr revert <num>` standalone. T1 kwargs default ""; revert of T1 leaves T2/T3 functional. Revert of T2 leaves T3 cluster cap functional.
- **EH-4 Schema rollback**: `scripts/rollback_phase3_t{2,3}.py` DROP TABLE for additive tables ship in respective PRs.

## §6 Dispatch Order

```
T1 (~300 LOC, branch feat/phase3-t1-weather-regime-tag-20260521) — blocks on nothing landed-after; ships WeatherRegimeTag + correlation_cluster + selection_family extension
   ▼
T2 (~500 LOC, branch feat/phase3-t2-shoulder-vnext-stress-20260521) — blocks on T1 merged; ships ShoulderStrategyVNext + TailStressScenario + registry classifier + Kelly haircut + 5 NoTradeReason members + tail_stress_scenarios table (SCHEMA_WORLD_VERSION 15→16)
   ▼
T3 (~350 LOC, branch feat/phase3-t3-shoulder-ledger-readiness-20260521) — blocks on T2 merged; ships ShoulderExposureLedger + cluster_cap + shadow_readiness_report + PROMOTION_PLAYBOOK + 1 NoTradeReason member (SHOULDER_CLUSTER_CAP_EXCEEDED) (SCHEMA_WORLD_VERSION 16→17)
```

**Parallelism**: T1 → T2 → T3 strictly sequential — D1→D2→D3 dependency cascade (T1's regime+family_id feed T2; T2's `tail_correlation_cluster`+stress table feed T3). **Critics**: per-track opus SCAFFOLD critic on each (each ships an architectural object model — memory:`opus_critic_on_architectural_scaffold_4_for_4_roi`) + ONE opus wave-critic across all 3 PRs BEFORE main merge (memory:`wave_level_critic_not_per_slice`). **PR-open discipline**: all 3 tracks clear 300-LOC threshold (memory:`pr_300_loc_threshold_with_education`); no `ZEUS_PR_ALLOW_TINY` invocation.

---

*Plan v2 authored 2026-05-21 against `origin/main` HEAD at session start + GPT Round 1 dossier §7 + §12 verbatim. Every cited file:line verified via `git show origin/main:<path>` in this session. Operator approval + per-track opus SCAFFOLD critics + opus wave-critic before main merge. v1 (registry-gate-wireup + promotion-aggregator framing) is superseded — value folded into v2 T2 + T3; v1 entirely missed Tail/Stress/Regime/Cluster surface that dossier §7 demands.*
