# Third-Party Verifier Probe Contract

This file gives a fresh-context opus verifier the per-phase probe list. The verifier reads this file, runs the probes, returns CONFIRM / DISPUTE per probe.

**Verifier dispatch model**: opus, fresh context, NO prior-phase memory. Brief includes:
- Path to the phase package file under audit
- Probe list from below (copy verbatim into brief)
- Authority chain access (verifier can read `git show origin/main:<path>`)
- Output contract: CONFIRM / DISPUTE per probe with evidence file:line

## Package-level probes (always run first)

P-pkg-1: All 11 files `00_README.md` through `10_VERIFIER_PROBES.md` exist in `docs/operations/task_2026-05-21_mainline_completion_authority/`. Verify via `ls`.

P-pkg-2: Each phase file (04-08) has these required sections: scope citation, dossier intent citation, current code state, target outcome, schema impact, verifier probes, anti-pattern explicit list.

P-pkg-3: `01_AUTHORITY_CHAIN.md` lists 6 authority rows with explicit supersession order. Row 1 is v4 §M; row 2 is dossier; row 5 is `origin/main` code.

P-pkg-4: `02_MAIN_STATE_INVENTORY.md` matches actual `git tag --list 'phase*'` output (compare verbatim). Schema versions in inventory match `git show origin/main:src/state/db.py | grep SCHEMA_VERSION`.

P-pkg-5: `09_WORKFLOW.md` references the `~/.claude/skills/orchestrator-delivery/SKILL.md` skill and applies its model-tier table to Zeus phases.

## Phase 3 (Shoulder) probes — `04_PHASE_3_SHOULDER.md`

P-3-1: Dossier §7.3 cited verbatim — 21-field object model present in `04_PHASE_3_SHOULDER.md` (verifier recount 2026-05-21: 21 rows from `is_open_shoulder` through `no_trade_reason`; header updated from "20" to "21").

P-3-2: Five variants in §7.4 enumerated with verdicts matching dossier.

P-3-3: Kelly haircut range `[0.05, 0.20]` cited from §7.5 verbatim.

P-3-4: Six stress scenarios (§7.5) enumerated.

P-3-5: Schema impact: T2 ships `no_trade_events` table-rebuild (CREATE new with expanded CHECK + INSERT old + DROP + RENAME, ATTACH+SAVEPOINT per INV-37) AND `tail_stress_scenarios` NEW table — both under single SCHEMA_VERSION 15→16 bump. T3 ships `shoulder_exposure_ledger` NEW table — SCHEMA_VERSION 16→17. All additive.

P-3-6: 6 new SHOULDER_* NoTradeReason members enumerated.

P-3-7: Day0-bound interaction §7.6 acknowledged + xfail relationship test specified. Verify `test_shoulder_day0_bound_eliminates_tail` exists as `@pytest.mark.xfail(reason="pending Phase 5/6 Day0BoundState 6-class upgrade per dossier §6.2", strict=False)` — verbatim reason string required.

P-3-8: Phase 3 planner v2 output at `docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md` cross-references this file's design.

P-3-9: Verifier runs `git show origin/main:src/engine/evaluator.py | grep -n "buy_no.*is_shoulder"` and confirms exactly 3 hardcoded shoulder branch sites exist (line numbers shift per merge — as of 2026-05-21: L1482/L1498/L1514; use grep not hardcoded lines). Confirms `cycle_runner.py` mirror site exists via `git show origin/main:src/engine/cycle_runner.py | grep -n "shoulder"`. Replacement is Phase 3 T2 work, not yet landed.

P-3-10: `make_hypothesis_family_id` current signature on origin/main lacks `source` + `regime` kwargs (Phase 3 T1 extension is not yet landed).

P-3-11: `tail_correlation_cluster_for(city, regime)` function does NOT exist on origin/main (proof: `git grep -n "tail_correlation_cluster_for" origin/main` returns empty).

P-3-12: Phase 3 T1/T2/T3 are strictly sequential per planner output; T1 ships `WeatherRegimeTag` enum first.

P-3-13: `shoulder_buy` remains `live_status: blocked` at Phase 3 end — verify `architecture/strategy_profile_registry.yaml` shoulder_buy entry still shows `live_status: blocked` after all 3 tracks merge. No code in Phase 3 changes shoulder_buy live_status to `shadow` or `dormant_redesign`.

## Phase 4 (FDR + Candidates) probes — `05_PHASE_4_FDR_FAMILY_CANDIDATES.md`

P-4-1: v4 §M line 1101 cited verbatim ("FDR family-ID `spread_bucket` extension — Phase 4 `selection_family.py` work (Critic 2 P8)").

P-4-2: Spread bucket thresholds match Phase 0 PR 2+7 (`≤$0.05`, `≤$0.10`, `>$0.10`).

P-4-3: Six candidate strategies listed (`stale_quote_detector`, `liquidity_provision_with_heartbeat`, `neg_risk_basket`, `resolution_window_maker`, `cross_market_correlation_hedge`, `weather_event_arbitrage`).

P-4-4: BH math basis stated correctly (sort p-values, threshold `p_(k) ≤ k·α/m`).

P-4-5: Each candidate's `live_status` target = `shadow` (never live in Phase 4).

P-4-6: Verifier reads each `src/strategy/candidates/*.py` and reports current size + whether it's stub (`def evaluate(): pass`) or partial implementation.

P-4-7: Cross-dependency on Phase 3 T1 `WeatherRegimeTag` for `cross_market_correlation_hedge` explicit.

## Phase 5 (Regime + Shrinkage) probes — `06_PHASE_5_WEATHER_REGIME_CORRELATION.md`

P-5-1: Math spec §15.4 cited (verifier reads `docs/reference/zeus_math_spec.md` §15.4 if section exists; reports if anchor missing).

P-5-2: Ledoit-Wolf intensity formula `δ* = π / (γ × n)` stated; semantic components `π` and `γ` defined.

P-5-3: `ShrinkageEstimate` dataclass model present with required fields.

P-5-4: Per-regime correlation cache concept present; explicitly tied to `WeatherRegimeTag` from Phase 3 T1.

P-5-5: `cluster_exposure_for_bankroll` integration point cited (verifier checks function exists on main via `git grep -n "cluster_exposure_for_bankroll" origin/main src/`).

P-5-6: Synthetic test plan present (AR(1) residuals → intensity converges).

P-5-7: Regime-conditional behavior asserted (heat dome under-allocates vs normal).

## Phase 6 (EvidenceLadder) probes — `07_PHASE_6_EVIDENCE_LADDER.md`

P-6-1: Dossier §9 cited; 8-tier promotion rule enumerated (0 IDEA → 7 LIVE_NORMAL).

P-6-2: `EvidenceTier` IntEnum design present; comparison semantics noted.

P-6-3: `ShadowExperiment` immutability discussed — config mutation triggers new experiment_id.

P-6-4: Regret decomposition matches dossier §6.6 (7 components).

P-6-5: Bayesian small-N tier promotion gate referenced from dossier §9 layer 10.

P-6-6: `LiveReadinessTribunal` adjudication object specified; PROMOTE/HOLD/DEMOTE verdict.

P-6-7: `StrategyProfile.is_runtime_live()` extended with tier check; verifier confirms this is a behavior change, not just metadata.

P-6-8: Schema bumps explicit (2 tables: `shadow_experiments`, `regret_decompositions`).

## Phase 7 (Settlement Type-Gate) probes — `08_PHASE_7_SETTLEMENT_TYPE_GATE.md`

P-7-1: v4 §M line 1104 cited verbatim.

P-7-2: `SettlementOutcome` 10-member IntEnum design including edge cases (DISPUTED, UMA_UNKNOWN_50_50, SOURCE_REVISION).

P-7-3: Monotonic forward transition rule stated; reversion via dedicated DISPUTED/SOURCE_REVISION states.

P-7-4: `Position.lifecycle_state` field plan present; default `UNRESOLVED`.

P-7-5: `SettlementCaptureVerifier` purpose stated; checks (`fact_known_time, source_published_time, venue_resolved_time, redeemed_time`) coherence per dossier §6.4.

P-7-6: Backfill script plan present (idempotent + dry-run + chunked).

P-7-7: CI antibody `grep -rn "umaResolutionStatus ==" src/` should return 0 post-Phase-7 stated.

P-7-8: Phase 6 dependency on `EvidenceTier` framework acknowledged.

## Workflow probes — `09_WORKFLOW.md`

P-w-1: Per-phase loop has 10 steps in order (read authority → planner → critic → SCAFFOLD → SCAFFOLD-critic → production → PR-fix-loop → merge → wave-closure → registry-update).

P-w-2: Model tier routing matches `~/.claude/skills/orchestrator-delivery/SKILL.md` "Tier routing — empirical haiku underuse and opus-critic overuse" section.

P-w-3: Authority verbatim cite rule present with example block.

P-w-4: PR fix-loop conditions: mergeable + CLEAN + threads N/N + CI green + age≥600s + BOTH Copilot+Codex bots fired.

P-w-5: Schema bump procedure includes daemon restart + explicit `init_schema(get_world_connection()); c.commit()`.

P-w-6: P0 single-purpose merge discipline cited.

## Verifier output format

```
=== PACKAGE PROBES ===
P-pkg-1: CONFIRM (evidence: ls output)
P-pkg-2: CONFIRM
...

=== PHASE 3 PROBES ===
P-3-1: CONFIRM (file 04_PHASE_3_SHOULDER.md §"Required object model" lines 19-39 contain dossier §7.3 verbatim 20 fields)
P-3-2: DISPUTE (variant 4 verdict in package says "RESEARCH_ONLY" but dossier §7.4 line 4 says "RESEARCH_ONLY" — actually CONFIRM; correct verdict)
...

=== SUMMARY ===
Total probes: 56
CONFIRM: <N>
DISPUTE: <N>
NOT_VERIFIABLE: <N> (reason per probe)
RECOMMENDATION: <APPROVE | REQUEST_CHANGES with specific gaps>
```

DISPUTE rate above ~5% triggers package revision before next orchestrator dispatches a track.
