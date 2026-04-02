# Zeus Master Plan For Final Live-Ready Audit
> Updated: 2026-04-01 | Status: IN PROGRESS

## Execution Contract
- `PLAN.md` is the authoritative control surface for the live-ready push.
- Work continues phase-to-phase without pausing for ticket-style handoff.
- Every meaningful finding is classified as `Verified fact`, `Strong inference`, or `Plausible but unverified`.
- Every stabilized fix must land as code, contract, test, linter, preflight, runbook, or audit script.

## Evidence Levels
- `Verified fact`: directly confirmed from code, DB, tests, runtime state, or logs.
- `Strong inference`: best-supported explanation, not yet directly proven.
- `Plausible but unverified`: credible hypothesis still awaiting direct proof.

## Acceptance Matrix
| Area | Status | Evidence | Blocking | Proof source | Durable mechanism |
| --- | --- | --- | --- | --- | --- |
| Math and unit semantics | `verified` | `Verified fact` | `no` | `tests/test_instrument_invariants.py`, `tests/test_structural_linter.py`, `scripts/semantic_linter.py` | Runtime `Bin` contract, settlement semantics, width-aware calibration, AST guards |
| Hardcoded values and thresholds | `risky` | `Verified fact` | `yes` | `src/config.py` is now the single source for cluster taxonomy, maturity thresholds, bootstrap counts, sizing caps, and signal MC settings; `scripts/audit_divergence_thresholds.py` still shows divergence threshold family needs post-change validation | Config helpers, runtime guards, and regression tests now block silent drift; divergence threshold family still needs post-change calibration audit |
| Paper P&L explainability | `verified` | `Verified fact` | `no` | `scripts/audit_paper_explainability.py` now shows `recent_exits_unexplained=0` and `open_positions_with_blind_spots=0` for real paper state | Explainability audit script + attribution backfills + semantic snapshot backfill |
| Replay and attribution completeness | `blocking` | `Verified fact` | `yes` | `scripts/audit_replay_completeness.py` shows null-counts at `0`; `scripts/audit_replay_fidelity.py` shows strict coverage `1.1%`, relaxed snapshot-only coverage `18.3%`, and vector-compatible historical coverage `1.9%` | Additive schema, DB audit script, trade decision backfill, recent exit backfill, semantic snapshot backfill, future replay-vector capture |
| Live / paper separation | `verified` | `Verified fact` | `no` | `scripts/healthcheck.py`, mode-qualified paths in `src/config.py`, healthy paper daemon + RiskGuard | Mode-qualified state paths, mode-aware healthcheck, launchd labels |
| Wiring completeness | `verified` | `Verified fact` | `no` | Cluster taxonomy, MC counts, risk limits, and calibration thresholds now flow from city metadata + config helpers into runtime call sites; no remaining runtime path in these provinces depends on a local duplicate list or literal | Runtime path tests, restored ETL entrypoints, single-source config helpers, migration/backfill script, AST/runtime guard tests |
| Data utilization | `risky` | `Verified fact` | `no` | `solar_daily` and `observation_instants` are now Zeus-owned and load-bearing; many other assets remain outside the chain by design | Zeus-owned schema + ETL + runtime consumers for solar/time data |
| Time semantics | `verified` | `Verified fact` | `no` | `observation_instants` imported (`219,483` rows), ambiguous fallback rows explicitly excluded from stats, multi-source hourly duplication normalized before diurnal aggregation, Day0 runtime uses typed temporal context | `ObservationInstant`, `SolarDay`, `Day0TemporalContext`, time-semantics linter guard, `audit_time_semantics.py` |
| Shared upstream and external resource boundaries | `risky` | `Strong inference` | `no` | Workspace competition remains delegated; no current architecture decision depends on it | Delegated monitoring only |
| Core design philosophy alignment | `verified` | `Verified fact` | `no` | `scripts/audit_architecture_alignment.py` now reports no blocking alignment gaps: `workspace-venus` operator surfaces present, OpenClaw ACP enabled, assumptions load-bearing, replay decision-time guard present, CycleRunner phase logic extracted | External Venus operator surfaces + assumptions validation + architecture audit script + delegated CycleRunner runtime module |
| Promotion gate clarity | `blocking` | `Verified fact` | `yes` | Current remaining blockers are explicit and few: threshold audit, semantic snapshot spine, philosophy alignment, final deep debug | This matrix + audit scripts |

## Phase Status
| Phase | Status | Current truth |
| --- | --- | --- |
| 1. Time semantics hardening | `mostly complete` | `observation_instants` is now Zeus-owned, DST-safe, and feeds diurnal statistics and Day0 temporal context |
| 2. Day0 math deep audit and integration | `in progress` | Day0 now consumes temporal context and a stronger multiplicative observation-weight fusion; further audit still needed |
| 3. Attribution / replay completion | `mostly complete` | `market_hours_open`, semantic snapshots, recent exit attribution, runtime lifecycle fields, and future decision-log replay vectors are now persisted; replay fidelity remains open |
| 4. Wiring and separation audit | `complete enough` | Time/data/runtime wiring and host alignment are closed; A-E single-source config cleanup is now in place; remaining concern is strategy/replay fidelity rather than missing call-paths |
| 5. Data utilization uplift | `in progress` | Sunrise/sunset and DST-safe hourly instants are now in the main chain |
| 6. Deep debug and final verdict | `pending` | Not allowed until blockers reduce to operator/governance choices |

## Verified Facts Locked In
- `observation_instants` exists in Zeus and contains `219,483` rows.
- `solar_daily` exists in Zeus and contains `31,198` rows.
- `etl_hourly_observations.py` now exists and rebuilds compatibility hourly rows from `observation_instants`.
- `etl_diurnal_curves.py` now rebuilds both `diurnal_curves` and `diurnal_peak_prob` from DST-safe instants, excludes ambiguous fallback hours, and collapses multi-source hourly rows to one canonical city/date/hour sample.
- `scripts/backfill_recent_exits_attribution.py` matched `22/22` real recent exits and backfilled replay fields into `positions-paper.json`.
- `scripts/audit_time_semantics.py` confirms real DST-active rows (`118,984`), ambiguous fallback rows (`20`), and successful Day0 temporal-context probes for NYC, London, and Tokyo.
- `scripts/backfill_semantic_snapshots.py` reconstructed semantic snapshots into all `65` current `trade_decisions`, all `14` open positions, and `22` real recent exits.
- `scripts/audit_architecture_alignment.py` now reports zero blocking architecture-alignment gaps against the real Venus/OpenClaw host surfaces.
- The architecture audit now validates the real Venus/OpenClaw host surfaces at [HEARTBEAT.md](/Users/leofitz/.openclaw/workspace-venus/HEARTBEAT.md), [OPERATOR_RUNBOOK.md](/Users/leofitz/.openclaw/workspace-venus/OPERATOR_RUNBOOK.md), [known_gaps.md](/Users/leofitz/.openclaw/workspace-venus/memory/known_gaps.md), [AGENTS.md](/Users/leofitz/.openclaw/workspace-venus/AGENTS.md), [IDENTITY.md](/Users/leofitz/.openclaw/workspace-venus/IDENTITY.md), and [openclaw.json](/Users/leofitz/.openclaw/openclaw.json).
- `scripts/validate_assumptions.py` is now load-bearing and wired into startup health checks and `scripts/healthcheck.py`.
- `CycleRunner` heavy phase logic has been extracted into [cycle_runtime.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py), leaving [cycle_runner.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runner.py) as an orchestration facade.
- `scripts/audit_replay_completeness.py` now reports `null_settlement_semantics = 0`, `null_epistemic_context = 0`, `null_edge_context = 0`.
- `scripts/audit_paper_explainability.py` now reports `recent_exits_unexplained = 0` and `open_positions_with_blind_spots = 0`.
- Replay now requires an actual decision reference and only selects snapshots with `available_at <= decision_time`.
- Full-suite verification after the current architecture / lifecycle round is green: `336 passed, 3 skipped`.
- `scripts/healthcheck.py` currently returns `healthy=true` with paper daemon and RiskGuard both alive.
- `scripts/audit_replay_completeness.py` now reports:
  - `null_market_hours_open = 0`
  - `null_fill_quality = 0`
  - `null_selected_method = 0`
  - `null_runtime_trade_id = 29` on historical rows
  - `null_settlement_semantics = 0`
  - `null_epistemic_context = 0`
  - `null_edge_context = 0`
- `scripts/audit_paper_explainability.py` now reports:
  - `recent_exits_non_mock = 22`
  - `recent_exits_unexplained = 0`
  - `open_positions_with_blind_spots = 0`
- `scripts/audit_divergence_thresholds.py` now reports:
  - `divergence_exits = 22`
  - `divergence_exit_pct = 100.0`
  - historical paper exits were fully dominated by the divergence-threshold family
  - runtime now uses `soft_threshold = 0.20`, `hard_threshold = 0.30`, with soft divergence requiring adverse velocity confirmation
- `scripts/audit_divergence_hindsight.py` now reports:
  - `divergence_exits_analyzed = 22`
  - among exits with later ticks, average `last_tick` native-side delta was `+0.0358`
  - average `last_tick` PnL delta was `+$0.76`
  - this is concrete evidence that at least a material subset of historical divergence exits were early
- `scripts/audit_replay_fidelity.py` now reports:
  - `covered_settlements = 15 / 1385`
  - `coverage_pct = 1.1`
  - `snapshot_only_covered_settlements = 254 / 1385`
  - `snapshot_only_coverage_pct = 18.3`
  - `snapshot_vector_compatible_settlements = 26 / 1385`
  - `snapshot_vector_compatible_pct = 1.9`
  - `invalid_temporal_rows = 0`
  - replay no longer uses uniform market prior
  - replay no longer uses flat edge thresholding
  - replay now uses `MarketAnalysis + FDR + Kelly`
  - future `decision_log` trade/no-trade artifacts now carry `bin_labels`, `p_raw_vector`, `p_cal_vector`, `p_market_vector`, `decision_snapshot_id`, and timestamped decision references
  - `future_ready_capture_present = true`
  - `shadow_signals = 10`
  - relaxed `run_replay(..., allow_snapshot_only_reference=True)` now actually replays `254 / 1385 = 18.3%` settlements end-to-end
- Time-semantics guard is now repo-wide:
  - `scripts/semantic_linter.py` fails runtime code that reintroduces raw `local_hour/current_local_hour` outside the approved time layer.
- Cluster taxonomy is now single-sourced from [src/config.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/config.py); calibration manager and TIGGE refit loop both consume the canonical helper instead of any local cluster list.
- `load_cities()` now fails fast if a city is missing from `CITY_CLUSTERS`, instead of silently falling back to `"Other"`.
- `correlation_matrix()` now fails fast if settings omit a canonical cluster or introduce an unknown cluster key.
- Calibration maturity thresholds and bootstrap counts are now runtime-sourced from config in [src/calibration/manager.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/calibration/manager.py), [src/calibration/platt.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/calibration/platt.py), [src/strategy/market_analysis.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/strategy/market_analysis.py), and [scripts/refit_platt.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/refit_platt.py).
- Risk sizing caps are now instantiated from config at runtime via [src/strategy/risk_limits.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/strategy/risk_limits.py); [src/engine/cycle_runner.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runner.py) and [scripts/capture_replay_artifact.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/capture_replay_artifact.py) no longer rebuild the same limits manually.
- Signal-layer MC counts now route through config helpers in [src/signal/ensemble_signal.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/ensemble_signal.py), [src/signal/day0_signal.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/day0_signal.py), and [src/engine/monitor_refresh.py](/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/monitor_refresh.py); [scripts/validate_assumptions.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/validate_assumptions.py) now enforces helper-based sourcing instead of checking for hardcoded `5000`.
- Canonical cluster taxonomy is now owned by [config/cities.json](/Users/leofitz/.openclaw/workspace-venus/zeus/config/cities.json), not a parallel dict in `src/config.py`.
- Europe and Asia are now explicitly subdivided instead of being continent-wide singleton calibration buckets:
  - `Europe-Maritime` / `Europe-Continental`
  - `Asia-Northeast` / `Asia-East-China`
- US taxonomy has also been made more specific and symmetric with the non-US split:
  - `US-GreatLakes`, `US-Pacific-Northwest`, `US-Southeast-Inland`, `US-Florida`, `US-Texas-Triangle`, `US-California-Coast`, `US-Rockies`
- [scripts/backfill_cluster_taxonomy.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/backfill_cluster_taxonomy.py) has been executed against real state:
  - `calibration_pairs_updated = 1409`
  - `platt_models_cleared = 11`
  - `portfolio_cluster_rows_updated = 32`
  - `refit_count = 13`
- Post-backfill real `platt_models` now use the new taxonomy, e.g. `Europe-Maritime_DJF`, `Europe-Continental_MAM`, `US-GreatLakes_MAM`, `US-Texas-Triangle_DJF`.
- Full-suite verification after the taxonomy migration is green: `355 passed, 3 skipped`.
- Unsuffixed legacy Zeus truth files are no longer readable as current truth:
  - `state/status_summary.json`
  - `state/positions.json`
  - `state/strategy_tracker.json`
  have been archived into `state/legacy_state_archive/` and replaced with explicit tombstones.
- Mode-suffixed truth files now carry explicit truth metadata:
  - `mode`
  - `generated_at`
  - `source_path`
  - `stale_age_seconds`
  - `deprecated`
- Loader-level fail-fast is now in place:
  - `load_portfolio()` rejects deprecated legacy truth files
  - `load_tracker()` rejects deprecated legacy truth files
- Reporting scripts that previously read unsuffixed truth files have been migrated to mode-aware sources:
  - [scripts/equity_curve.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/equity_curve.py)
  - [scripts/analyze_paper_trading.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/analyze_paper_trading.py)
  - [scripts/profit_validation_replay.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/profit_validation_replay.py)
  - [scripts/data_completeness_audit.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/data_completeness_audit.py)
- Host operator guidance now explicitly forbids using unsuffixed Zeus state files as current truth; see [OPERATOR_RUNBOOK.md](/Users/leofitz/.openclaw/workspace-venus/OPERATOR_RUNBOOK.md).
- Divergence exit counterfactual tooling has been hardened into [scripts/audit_divergence_exit_counterfactual.py](/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/audit_divergence_exit_counterfactual.py), which now reports `+1h / +3h / +6h / settlement` deltas and honest coverage counts.
- Current counterfactual coverage truth is:
  - `divergence_exits_analyzed = 22`
  - `with_held_token_id = 22`
  - `with_any_future_tick = 0`
  - `with_settlement_truth = 0`
  meaning the tool exists and is correct, but the current price/settlement estate still cannot validate those exits post-hoc.
- Full-suite verification after the truth-layer cleanup is green: `362 passed, 3 skipped`.
- Full-suite verification after the single-source cleanup is green: `353 passed, 3 skipped`.

## Framework-Level Missing Pieces Found This Round
1. Divergence / threshold audit is still open, but the runtime rule has been tightened.
   Root cause:
   Historical paper exits were all divergence-triggered, so one threshold family had effectively controlled the whole exit surface.
   Repair layer:
   Strategy / risk audit.
   Regression guard:
   Existing hardcoded-note tests plus the new divergence-threshold audit surface and split soft/hard trigger contract.
   Current evidence:
   Historical hindsight audit shows positive average post-exit delta, so the old divergence rule was too aggressive.

2. Replay fidelity still trails replay provenance.
   Root cause:
   Decision-time snapshot selection is fixed and replay logic now uses MarketAnalysis/FDR/Kelly. The remaining historical blocker is data compatibility: strict coverage is 1.1%, relaxed snapshot-only replay reaches 18.3%, but only 1.9% of historical settlement-overlap rows are vector-compatible.
   Repair layer:
   Replay engine.
   Regression guard:
   Replay audit scripts plus final promotion gate.

3. Divergence counterfactual validation is now tool-complete but still data-empty.
   Root cause:
   The audit path now measures `+1h / +3h / +6h / settlement` honestly, but the current token-price / settlement estate provides zero post-exit future ticks and zero settlement closes for the 22 divergence exits under review.
   Repair layer:
   Audit / observability / historical evidence.
   Regression guard:
   `scripts/audit_divergence_exit_counterfactual.py` + `tests/test_divergence_exit_counterfactual.py`.

## Exact Next Actions Already In Progress
1. Re-audit divergence-driven exits against the split soft/hard rule once post-change real samples appear.
2. Improve historical replay vector compatibility and coverage, not replay main logic.
3. Run final deep debug with the updated PnL / replay / architecture audit surfaces and reduce the remaining blockers to a go/no-go set.
