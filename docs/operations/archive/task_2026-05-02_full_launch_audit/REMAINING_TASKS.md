# Zeus Remaining Tasks (non-strategy)

**Last refreshed**: 2026-05-04 (post PR46+PR47 merge to main; bankroll truth-chain resolved)
**Scope**: Everything that is NOT a trading-strategy design decision. For strategy-design decisions, see `STRATEGIES_AND_GAPS.md` in this folder.

> Categorized by priority and theme. Each task has a current task ID for cross-reference with the in-session task list.

## Live-readiness state snapshot (2026-05-04)

- **PR #46 MERGED** `595c93bb` into main (2026-05-03 20:43 CDT). **PR #47 MERGED** `cd882ee9` into main (2026-05-03 20:45 CDT). main = `cd882ee9`.
- `config/settings.json:entry_forecast.rollout_mode = "blocked"` — Phase C commit `b9350930` (phase-C-4+5: dead-knob removal) reset rollout_mode from the cb4beb6c "live" flip back to "blocked".
- **Bankroll truth chain P0 + retired fixed-capital literal hardcode both RESOLVED** in commit `43e745b2` (Bankroll truth-chain cleanup: kill the retired fixed-capital literal fiction). `capital_base_usd` removed from config and all 11 production sites; on-chain wallet is sole source. Daemons need restart for fix to take effect.
- **DDD v2** landed within PR #46 (commits `c9c444ef` Two-Rail trigger redesign, `b719d199` live wiring, `650136bd` Paris re-inclusion).
- Phase A remediation: done (`5acdb3a8`). Phase B: done (`f9aca68e`). Phase C: done (`8c3876f7`, `b9350930`, `49de7965`, `734012fa`, `433737c4`).
- Rollout gate `evaluate_entry_forecast_rollout_gate` is now wired behind `ZEUS_ENTRY_FORECAST_ROLLOUT_GATE` env flag (Phase C-1). Entry readiness writer behind `ZEUS_ENTRY_FORECAST_READINESS_WRITER` flag (Phase C-3).

---

## A. Live-blocking ops (fix soon)

These directly prevent or degrade live trading right now or imminently.

| ID | Title | Status | Notes |
|---|---|---|---|
| #50 | Restart data-ingest daemon + verify catch-up | **DONE** | forecasts table now fresh; structural fix landed in #42 |
| #44 | TIGGE backfill `stage_failed:download_mx2t6_high` | OPEN | Operator: backfill is for HISTORICAL calibration only; not blocking live. Defer until catch-up structural fix lands |
| #45 | riskguard-live daemon fail-closed (DB + proxy) | **DONE** | Readiness substrate and fail-closed logic merged in PR #45 |
| #61 | riskguard-live flapping GREEN↔DATA_DEGRADED | **RESOLVED** | Duplicate stale PIDs from launchd kickstart issue; current `riskguard-live` PID writes Tick complete: GREEN |
| #46 | Verify `ZEUS_HARVESTER_LIVE_ENABLED=1` propagated | **DONE** | `ps eww` confirms env=1 in PID 9692 (live) + 30921 (data-ingest). harvester.py:24 + harvester_truth_writer.py:32 both use `os.environ.get(..., "0") != "1"` gate. ENABLED. |
| #47 | Restart live-trading daemon for opening_hunt 15min | **DONE** | Landed with PR #44; opening_hunt cadence now 15min |
| #15 | Live trading daemon restart (post oracle fix) | DONE 2026-05-02 15:30 CDT | Daemon GREEN, $199.40 wallet, no halts |

---

## B. Hardening / resilience (PRs in flight)

| ID | Title | Status | Notes |
|---|---|---|---|
| #13 | Data-ingest daemon: never-offline resilience | **DONE** | Boot-time staleness detection + force daily_tick merged in PR #42 |
| #14 | Comprehensive data collection design (hourly+daily Max+Min all sources) | OPEN, design only | |
| — | TIGGE T+2 same-day extraction | IN FLIGHT | Operator-claimed; same-day extraction catch-up; not for sub-agent pickup |

---

## C. Audit follow-ups (from haiku audits 2026-05-02)

| ID | Title | Status | Source |
|---|---|---|---|
| #32 | Audit ExpiringAssumption configs for `halt_trading` action | **DONE** | All 11 instances hardcode `kill_switch_action="revert_to_fallback"`. |
| #33 | PhysicalBounds graceful fallback to lat-band heuristics | **DONE** | try/except wrapper for IngestionGuard merged in PR #44 (`ebdc77e0`) |
| #25 | PR-A: oracle path centralization to `~/.openclaw/storage/zeus/oracle/` | OPEN | Deferred half of original oracle plan; PR-B (gate removal) merged in #40 |

---

## D. PR review / merge backlog

| Item | Status | Notes |
|---|---|---|
| PR #40 (oracle gate removal) | **MERGED** | `5fb06141` |
| PR #41 (PR #37 followup: legacy projection upsert + m5 latch race) | **MERGED** | `ef9ff379` |
| PR #42 (data-ingest boot-time catch-up resilience) | **MERGED** | `82fda114` |
| PR #43 (pre-commit hook skip-invariant marker) | **MERGED** | `07658bbb` |
| PR #44 (P0/P1 live blockers) | **MERGED** | `6e3b6a53` |
| PR #45 (data daemon readiness) | **MERGED** | `47d11d45` |
| PR #46 (healthcheck-riskguard-live-label + bankroll fix + DDD v2) | **MERGED** | `595c93bb` (2026-05-03 20:43 CDT). Contains bankroll truth-chain cleanup, DDD v2, Phase A/B/C remediation. |
| PR #47 (live entry forecast target coverage contract) | **MERGED** | `cd882ee9` (2026-05-03 20:45 CDT). Merged into main as separate merge commit after PR #46. |
| Local-only integration branch `live-restart-integration-2026-05-02` | **CLOSED** | Reconciled into main via PR #40-45 |

---

## E. Operational data work

| ID | Title | Status |
|---|---|---|
| #12 | Mop up remaining ~231 DST gap fills | OPEN |
| #18 | UMA timing alignment for shadow snapshot | OPEN |
| #23 | Bridge OGIMET snapshot parsing bug (Istanbul / Moscow / Tel Aviv corridors) | BACKLOG |

---

## F. ACTIVE WORK (Newly Proposed)

| Task | Status | Plan Dir |
|---|---|---|
| Opening-hunt entry data contract | PROPOSED | `docs/operations/task_2026-05-02_live_entry_data_contract/` |
| Strategy update execution sequence | PROPOSED | `docs/operations/task_2026-05-02_strategy_update_execution_plan/` |

---

## F2. Future / Deferred (recovered from session 59195a96 + harness-debate-2026-04-27)

Tasks explicitly queued as "Future" or left pending when sessions ended. Source: `.claude/tasks/` JSON files.

| ID | Title | Origin | Status | Notes |
|---|---|---|---|---|
| s7 | **Future: re-rebuild calibration_pairs_v2 at n_mc=10000** | 59195a96 #7 | DEFERRED | After live deployment stable. Training=5000 vs runtime=10000 causes ~10⁻³σ Platt fit diff (undetectable now). Cost: ~32h at n_mc=10000. Prerequisite: task s8 (vectorize MC loop) first to make cost feasible. |
| s8 | **Future: vectorize p_raw_vector_from_maxes MC loop** | 59195a96 #8 | DEFERRED | `src/signal/ensemble_signal.py:215` uses Python `for _ in range(n_mc)` loop. Vectorize to `(n_mc, n_members)` numpy broadcast → 10-100× speedup. Required: equivalence test (bit-precise vs current) + p_raw_vector regression suite. Not deployment-blocking. Unblocks s7. |
| s3 | Add climate_zone field to config/cities.json | 59195a96 #3 | OPEN | LAW 6 in `docs/reference/zeus_calibration_weighting_authority.md`. 51 cities need enum: `tropical_monsoon_coastal \| temperate_coastal_frontal \| inland_continental \| high_altitude_arid`. Propose mapping for operator review first. |
| s4 | Write 11 antibody tests per calibration weighting LAW | 59195a96 #4 | OPEN | Tests: `test_calibration_weight_continuity`, `test_per_city_weighting_eligibility`, `test_no_temp_delta_weight_in_production`, `test_weight_floor_nonzero_for_ambig_only`, `test_high_track_unaffected_by_low_law`, `test_rebuild_n_mc_default_bounded`, `test_runtime_n_mc_floor`, `test_rebuild_per_track_savepoint`, `test_no_per_city_alpha_tuning`, + 2 more per spec. |
| s6 | Queue PoC v6 cluster-level α tuning | 59195a96 #6 | OPEN | `_poc_weighted_platt_2026-04-28/poc_v6_cluster_alpha.py` — 4-zone α grid search using climate_zone partition. Compare aggregate Brier vs B_uniform baseline + per-zone Brier. Run on rebuilt calibration_pairs_v2. Blocked by s3 (climate_zone field). |
| h46 | **FUTURE: WS_PROVENANCE_INSTRUMENTATION (PATH C deferred)** | harness-debate #46 | DEFERRED | Extend `token_price_log` writer to tag `update_source` ('ws'\|'poll') for latency attribution. Documented in `src/state/ws_poll_reaction.py` docstring + `docs/operations/ws_poll_reaction/AGENTS.md`. Operator decides timing. Unlocks true ws_share/poll_share analytics. |
| h91-93 | R3 G1 closure pack (pytest triage + data evidence + sign-off) | harness-debate #91-93 | OPEN | Three-part: (A.1) full-suite pytest ~30 failures triage; (A.2) TIGGE/calibration/live-alpha evidence inventory per bucket; (A.3) G1-close evidence pack for operator sign. Prerequisite for R3 G1 close. |

---

## G. Structural debt (operator-named, deferred)

These are real structural problems where a band-aid would just kick the can:

*All previously listed items have been resolved. See §H.*

| Topic | Why deferred |
|---|---|
| (none open) | — |

---

## H. Closed/superseded (no action needed)

| ID | Title | Notes |
|---|---|---|
| #40 | Oracle gate removal | Merged PR #40 (`5fb06141`) |
| #41 | PR #37 followups | Merged PR #41 (`ef9ff379`) |
| #42 | Boot catch-up resilience | Merged PR #42 (`82fda114`) |
| #43 | [skip-invariant] pre-commit support | Merged PR #43 (`07658bbb`) |
| #44 | Live blocker fixes | Merged PR #44 (`6e3b6a53`) |
| #45 | Data daemon readiness | Merged PR #45 (`47d11d45`) |
| #61 | riskguard flap | Resolved (stale launchd PIDs) |
| #33 | PhysicalBounds graceful fallback | Merged PR #44 (`ebdc77e0`) |
| #1-#11 | Various data backfill / DST work | Done |
| #16 | Critic adversarial review of oracle fix plan | Done |
| #17 | Fail-closed gate audit | Done; spawned #32 + #33 |
| #24 | PR-B: oracle gate removal | Done = PR #40 merged |
| #26 | git-master merge #38/#39, fast-forward #37 | Done |
| #27, #28 | PR #40 codex review fixes | Done |
| #29, #30 | PR #41 P1 fixes | Done |
| #31 | Hook redesign | Done |
| #34 | Sonnet PR review fixes | Done |
| #35 | retired fixed-capital literal hardcode root cause finding | Done — finding moved to §G; subsequently fixed in PR #46 |
| **PR #46** | healthcheck-riskguard-live-label + bankroll fix + DDD v2 | Merged `595c93bb` (2026-05-03 20:43 CDT) |
| **PR #47** | live entry forecast target coverage contract | Merged `cd882ee9` (2026-05-03 20:45 CDT) |
| **Bankroll truth chain P0** | retired fixed-capital literal fiction eliminated; on-chain wallet sole source | `43e745b2` — all 11 production sites updated; `capital_base_usd` removed from config |
| **retired fixed-capital literal capital_base_usd hardcode** | Structural fix applied (not a band-aid) | Same commit `43e745b2` — `portfolio.py`, `riskguard.py`, `cycle_runtime.py`, `evaluator.py`, `replay.py`, `status_summary.py`, `main.py`, `config.py` |
| **DDD v2** | Two-Rail trigger redesign | `c9c444ef`/`b719d199`/`650136bd` in PR #46 |
| #36 | Wait for first opening_hunt cycle | Deleted (premise was wrong) |
| #37, #38, #51, #52, #53, #55 | Strategy design gaps | Moved to `STRATEGIES_AND_GAPS.md` |
| #39-#43 | Full-launch blocker audit haikus A-E | Done |
| #48, #49 | Scout investigations | Done |
| #54 | Full strategy catalog haiku | Done |

---

## Sub-Agent Eligible (non-strategy, non-data-daemon)

Concrete tasks available for pickup without operator/daemon conflict:

1. **#12 DST gap fills mop-up**: ~231 remaining entries (single-threaded, low risk).
2. **#18 UMA timing alignment**: Align shadow snapshot timings with UMA resolution windows.
3. **#25 PR-A oracle path centralization**: Move all oracle artifacts to `~/.openclaw/storage/zeus/oracle/`.
4. **Live_entry_data_contract Phase 6**: Split `source_health` / `data_coverage` / `entry_readiness` in healthcheck. PR #46 is now merged — check what landed to avoid duplication.

---

## Summary

- **main = `cd882ee9`** (PR #46 + PR #47 merged 2026-05-03 evening CDT).
- **Bankroll truth chain RESOLVED**: retired fixed-capital literal fiction eliminated in `43e745b2`; on-chain wallet is sole source. **Daemons need restart** for this to take effect in live.
- **`entry_forecast.rollout_mode = "blocked"`** (Phase C-4+5 dead-knob removal reset from cb4beb6c "live" flip).
- **DDD v2 live**: Two-Rail trigger + p05 floor + linear curve landed in PR #46.
- **Next major workstreams**: daemon restart post-bankroll-fix, opening-hunt entry data contract, strategy update execution.
