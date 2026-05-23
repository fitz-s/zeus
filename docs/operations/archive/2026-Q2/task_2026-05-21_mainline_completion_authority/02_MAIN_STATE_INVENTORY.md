# Origin/main State Inventory (snapshot 2026-05-21)

**Volatile**: re-verify with `git fetch origin && git log --oneline origin/main -50` before locking any plan.

## Phase 0 (v4 ultraplan 7 structural defects)

| PR | Subject | Merge sha | Tag | Defects closed (v4 §0) |
|---|---|---|---|---|
| #190 (or descendants — verify via `git log --grep=ResolutionEra`) | PR 1: ResolutionEra + INV-37 ATTACH + 2829-row backfill | resolved on main | `phase0_pr1_landed` | Defect 1: settlements_v2 era-blind harvester gate |
| #191 / #192 | PR 4: DecisionGroupId NOT NULL + §14.9 eps decision (Option 1) + §14.10 ±inf | resolved | `phase0_pr4_landed` | Defect 2: 91M+53M NULL `decision_group_id`; Defect 3: §14.9 eps 100× deviation |
| #193 | PR 5: Day0 BoundClassification scaffold + CelsiusBox propagation + DST | resolved | `phase0_pr5_landed` | Defect 7: Day0 bound classification & DST handling |
| #194 (or descendants) | PR 2+7 bundle: WideSpread/Depth/Window snapshot fields + graded EffectiveKellyContext | resolved | `phase0_pr27_landed` | Defect 4: kelly_size silently over-bets under wide-spread display substitution |
| (cross-cutting) | PR 3+6 coordinated: DecisionSourceContext extension + alpha proxy + chain finality split + clock skew | resolved | `phase0_pr36_landed` | Defects 5+6: causality chain + forecast/submission timestamp chain incomplete |
| (follow-up) | Phase 0 closure follow-up fixes | resolved | `phase0_closure_followup_landed` | Cross-cutting cleanup post Wave-B |

All 7 v4 §0 defects closed before Phase 1 dispatch.

## Phase 1 (decision_events + Day0Nowcast — Scope C selected by operator)

| Track | Subject | Tag |
|---|---|---|
| T1 | `decision_events` natural-key 5-tuple (`market_slug, temperature_metric, target_date, observation_time, decision_seq`) + `allocate_decision_seq()` UNION-under-writer-lock | `phase1_track1_landed`, `phase1_track1_a_landed`, `phase1_track1_b_landed` |
| T2 | `Day0Nowcast` writer wiring | `phase1_track2_landed` |
| (closure) | Phase 1 closure handoff | `phase1_closure_landed` |

## Phase 2 (post-Phase-1 mainline; T1-T5 + P0-1)

| Track | Subject | Merge sha | Tag | Schema impact |
|---|---|---|---|---|
| T1 | `book_hash_transitions` table | (origin/main) | `phase2_track1_landed` | world `SCHEMA_VERSION` 13→14 |
| T2 | `NoTradeReason` StrEnum + `no_trade_events` natural-key table | `ce4862a4d2` (PR #222) | `phase2_track2_landed` | world `SCHEMA_VERSION` 14→15 |
| T3 | `FreshnessRegistry` centralized gate + 10-callsite migration | `dfe0a20b15` (PR #234) | `phase2_track3_landed` | no schema |
| T4 | `MarketAnalysisVNext` + `MicrostructureMetrics` + `T4_MERGE_DATE` real ISO + anchor-source wire via `get_world_connection()` (INV-37) | `6e17d90195` (PR #238) | `phase2_track4_landed` | forecasts `SCHEMA_FORECASTS_VERSION` 4→5 + new `market_microstructure_snapshots` table |
| T5 | `Position.market_slug` JSON-only + `_maybe_write_day0_nowcast()` filled | `7d3b24ffac` (PR #236) | `phase2_track5_landed` | no schema (JSON-only) |
| P0-1 Stage A | Family-exclusive entry gate (interim env-flag dedup) | `fc54e6930f` (PR #225/#235) | (no track tag — bundled under generic title) | no schema |
| Cleanup | T4_MERGE_DATE + SCAFFOLD leak removal | `5c471cd51f` (PR #239) | (folded) | no schema |

Umbrella: `phase2_landed` on `5c471cd51f`.

## Cross-cutting infrastructure already on main (consumable by Phase 3+)

- INV-37 cross-DB ATTACH+SAVEPOINT idiom (`get_world_connection_flocked()`, `trade_connection_with_world_flocked()` — verify via `git show origin/main:src/state/db.py | grep -nE 'flocked|ATTACH'`)
- `db_writer_lock` (single-writer per process) at `src/state/db_writer_lock.py`
- 3-DB split: `state/zeus-world.db` (truth), `state/zeus-forecasts.db` (derived), `state/zeus_trades.db` (execution/positions/lots)
- `architecture/db_table_ownership.yaml` (table ownership canonical)
- `architecture/topology.yaml` + `topology_doctor.py` (route admission)
- `decision_events` natural-key writer (Phase 1 T1)
- `no_trade_events` natural-key writer (Phase 2 T2) + `NoTradeReason` enum (66 members per W6 verifier; spec target was 66 — verify count freshly)
- `FreshnessRegistry` IntEnum `FRESH=0, DEGRADED=1, STALE=2, EXPIRED=3` (Phase 2 T3)
- `MarketAnalysisVNext` + `MicrostructureMetrics` (Phase 2 T4) consuming `ExecutableMarketSnapshotV2` fields
- `EffectiveKellyContext` graded haircut composer (Phase 0 PR 2+7)
- `ResolutionEra` 2-member enum (Phase 0 PR 1) — `UMA_OO_V2` + `INTERNAL_RESOLVER_POST_2026_02_21`
- `BoundClassification` scaffold (Phase 0 PR 5) — 3-class: `DETERMINISTIC`, `BOUNDED_LIVE`, `UNBOUNDED_NO_OBS_YET` (NOTE: dossier §6.2 demands 6-class system; Phase 5 or 6 likely upgrades this)
- `Day0Nowcast` writer pathway (Phase 1 T2 + Phase 2 T5 wiring) — gates on `market_slug` non-null + `hours_remaining ≤ 6`

## Pending Phase 2 deferred items (carry into Phase 3+ planning)

- **#59 P0-1 cross-module integration test** — operator critic Major #1; existing test at `tests/test_inv_family_exclusive_sizing.py:151` calls `dedup_mutually_exclusive_families()` directly (unit-flavor). Need cross-module path through `cycle_runtime.evaluate_cycle`.
- **P0-1 Stage B** — full `WeatherFamilyDecision` + `ExclusiveOutcomePortfolio` + `vector_kelly` optimizer per operator's authoritative spec (path TBD; spec file referenced in session notepad).
- **P0-1 audit-trail repair** — Stage A merged under bundled commit; future P0 ships single-purpose.

## What is NOT on main (mainline still ahead)

Per v4 §M line 1100-1106 minus what Phase 2 absorbed:
1. **Shoulder strategy refinement** (Phase 3 — `ShoulderStrategyVNext` per dossier §7)
2. **Candidate stubs production** (Phase 4 — `src/strategy/candidates/*` already-stubbed files: `stale_quote_detector.py`, `liquidity_provision_with_heartbeat.py`, `neg_risk_basket.py`, `resolution_window_maker.py`, `cross_market_correlation_hedge.py`, `weather_event_arbitrage.py` — verify status with `git show origin/main:src/strategy/candidates/`)
3. **FDR family-ID `spread_bucket` extension** (Phase 4 — `selection_family.py` work, per v4 §M line 1101)
4. **`WeatherRegimeTag` + math spec §15.4 correlation-matrix-via-shrinkage** (Phase 5)
5. **`EvidenceLadder` + promotion gates + `ShadowExperimentRegistry`** (Phase 6 — per dossier §9 + §13.5)
6. **Settlement social→type-gate migration** (Phase 7 — per v4 §M line 1104; depends on `ResolutionEra` already landed)

Plus from dossier §6.2 — `Day0BoundState` 6-class enum (current is 3-class), `Day0NowcastDistribution` richer than current, `Day0OpportunityDetector`, `ObservationAvailabilityRecord`, `BookHashEvent`, `AlphaDecayMeter`, `BookHeartbeatMeter` — these slot into Phase 4/5/6 work, not a dedicated phase.

## Re-verify command set

```bash
# Tag list
git tag --list 'phase*' | sort

# Last 30 main commits
git log --oneline origin/main -30

# Schema versions
git show origin/main:src/state/db.py | grep -E "SCHEMA_VERSION|SCHEMA_FORECASTS_VERSION" | head -5

# NoTradeReason members (count)
git show origin/main:src/contracts/no_trade_reason.py | grep -cE "^\s+[A-Z_]+ = "

# Candidate stubs status
for f in stale_quote_detector liquidity_provision_with_heartbeat neg_risk_basket resolution_window_maker cross_market_correlation_hedge weather_event_arbitrage; do
  echo "=== $f ==="
  git show origin/main:src/strategy/candidates/${f}.py | head -20
done
```

Cite this file's date (2026-05-21) as snapshot; subsequent PRs may shift contents.
