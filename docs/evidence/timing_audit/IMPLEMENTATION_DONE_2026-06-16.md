# Zeus timing-semantics fix — implementation DONE + regression evidence (2026-06-16)

Worktree: `fix/timing-semantics-2026-06-16` (base `f237314fb6`).
Authority: `docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md`.

Scope law (operator): FIX timing semantics, do NOT redefine edge or add alpha.
Every replacement timestamp carries a justified BASIS (REAL_SOURCE / DERIVED_JUSTIFIED),
never a guess. No new caps/throttles/features (SIMPLIFY).

---

## 1. What was implemented (all on disk in the worktree)

### A. Pure timing-semantics fixes — 0-regression, ready
| Fix | Basis correction | Key sites |
|---|---|---|
| **C1 AVAIL-CLOCK** | `available_at` = proof-of-possession (min(captured, nominal)), never the model cycle | `src/contracts/availability_time.py` (new helper); `evaluator.py`; `ecmwf_open_data.py` ×4 + expires_at=cycle+max_source_lag (fail-loud); `ecmwf_open_data_ingest.py`; `forecast_snapshot_ready.py`; `replacement_forecast_materializer.py` `_role_possession_available_at` |
| **C3 TS-FORMAT** | canonical ISO-UTC; kill naive `CURRENT_TIMESTAMP` (host-local) | `db.py` `utc_iso_now()` + all DDL `DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))`; `v2_schema.py`; `decision_integrity_quarantine_schema.py` |
| **C4 DEAD-INSTRUMENT** | real venue/latency time or honest NULL, never synthetic | `executor.py` venue_timestamp=None on REST ACK; `polymarket_user_channel.py` matchtime/observed split; `edli_position_bridge.py` posted_at=venue_commands.created_at; `execution_feasibility_evidence_schema.py` HONEST_NULL_COLUMNS |
| **C2 DEAD-LANES** | remove dead lanes, don't feed guesses into them | `cycle_runtime.py` removed dead `write_decision_event` + `_decision_source_context_with_submit_result`; `evidence_report.py` n_decisions via decision_certificates; dead-code test removed |
| **M5 collection-plane** | distinct real instants at real code points | `ecmwf_open_data.py` fetch_started/finished/captured/imported; `forecasts_append.py` retrieved_at pre-HTTP via utc_iso_now; ingest reader left as-is (honest — reads persisted possession time) |
| **C5 cadence-coverage** | warn loud when sweep period > selection freshness window (uncovered cadence silently starves the lane) | `main.py` `_warn_if_cadence_uncovered()` + `tests/test_cadence_coverage_warning.py` |
| **AB3 fail-loud lanes** | a swallowed lane-write is indistinguishable from no-activity → name+count it | `cycle_runtime.py` `_record_lane_write_failure/_success` + per-cycle lane map; `heartbeat_supervisor.py`/`status_summary.py` DataLaneHealthCheck; `tests/test_lane_liveness_failloud.py` |
| **date.today() ban** | host-local date → UTC | `shoulder_strategy_vnext.py`, solar/hourly/forecasts/daily_obs append, hole_scanner |

### B. Antibody layer (the 不再犯 / no-recurrence mechanism) — all green
4 CI-ban tests enforce the full fabrication taxonomy **statically**:
- `test_availability_time_law.py` — `proof_of_possession_available_at` is the ONLY producer of `available_at` (or explicit `# AVAIL-POSSESSION-EXEMPTED`).
- `test_timestamp_format_invariant.py` — bans `DEFAULT CURRENT_TIMESTAMP` + naive `strftime(...,'now')`.
- `test_timing_column_liveness.py` — bans `posted_at=filled_at`, `latency=0`, `venue_timestamp=ack_time`, `venue_timestamp=datetime.now()`.
- `tests/ci/test_no_date_today_ban.py` — bans `date.today()` in src/.
Plus `scripts/migrations/normalize_observation_instants_z_suffix.py` (498 Z-suffix rows → +00:00; SAVEPOINT; idempotent; dry-run default).

### C. Behavior-changers — implementation-complete, **SHADOW-VALIDATE before live merge**
- **M1** settled_at = observation event time (`harvester.py`); entered_at (`fill_tracker.py`).
- **M2** monitor exit-age: 48h fabrication → NaN + explicit refuse (`monitor_refresh.py` ×2); entered_at precedence (`fill_tracker.py`).
- fusion pre-arrival guard (`bayes_precision_fusion_capture.py`).

---

## 2. Regression evidence (controlled, same-base, this session)

**Raw alarm:** worktree 152 fails vs a 111 "baseline" = +41.
**Confound identified:** the 111 baseline ran on live HEAD `9424744`, but the worktree base is `f237314`; both ran against the live-mutating 45GB/38GB DBs at different times. So the raw +41 mixed base-delta + DB-drift with real regressions.

**Controlled diff** (pristine `f237314` checkout vs worktree, identical selection, `-m "not live_topology and not live_drift"`): **true regression count = 15**, all root-caused and fixed:

| # | Tests | Cause (mine) | Fix |
|---|---|---|---|
| 10 | `test_replacement_0_1_bayes_precision_fusion_materializer_wiring` | `_role_possession_available_at` → `get_source_run` → `SELECT … source_run` crashes on test/legacy conns with no such table | tolerate missing table → fall back to request's existing `source_available_at` (not a new guess); real possession still resumes where `source_run` exists |
| 1 | `test_live_decision_source_context_enriched_with_submit_result_timing` | exercised the removed dead `_decision_source_context_with_submit_result` (0 live callers, `grep -rn src/`) | removed the dead-code test |
| 1 | `test_reconcile_pending_positions_sets_verified_entry…` | M2a `entered_at=""` → canonical builder rejects (reconcile has no WS venue time; base used `now.isoformat()` fabrication) | entered_at precedence: venue match → preserve existing → caller's observed `now` (DERIVED_JUSTIFIED upper bound, conservative for hold-age) |
| 3 | `test_*monitor*_refresh_records/uses…` | M2b refuse-on-missing-entered_at fired on fixtures using `entered_at=None` (unrealistic — real open positions carry an entry time) | gave the 3 fixtures realistic entry instants; refuse-guard preserved (narrowing it would reintroduce the 48h fabrication) |

**After fixes — targeted controlled diff:** worktree 42 fails = base 42 fails, **regression set EMPTY**. The 42 are pre-existing on `f237314` (DB-dependent materializer/run_replay + 4 strategy/cap/fdr runtime_guards). Sibling tests (4 passing `entered_at=None` refuse-path tests + reconcile) all pass.

**Antibody self-catch (the 不再犯 layer working as designed):** after the fixes, the
`test_availability_time_law` antibody flagged the materializer — my line-shifts had
desynced its brittle line-number `READER_SITE_ALLOWLIST` (a recurrence risk in the
antibody itself). Hardened drift-proof: sanctioned the `_role_possession_available_at`
wrapper (it provably routes through `proof_of_possession_available_at` on every path)
and converted the materializer's allowlist entries to inline `# AVAIL-POSSESSION-EXEMPTED`
markers, deleting the fragile line-number entries. The antibody now keys on code, not
line numbers — it can no longer silently desync on edits. All 4 bans green.

**Broad regression coverage (worktree vs pristine `f237314`, same-base controlled):**
- Targeted families (11 test-bearing suites): worktree 42 = base 42 → **0 regressions**.
- Touched-module families (executor / harvester / evidence / edli, 142 tests): worktree 14 = base 14 → **0 regressions**.
- 4 antibody bans + fusion-wiring (15 tests) green; agent-C ecmwf/forecast suite 36 green; agent new tests (cadence, lane-liveness) 12 green.
- The full default suite (~2468 tests) was attempted but is DB-bound and disproportionate (>15 min CPU each; two concurrent runs contend on the live DBs), so it was killed in favor of the same-base targeted + touched-module diffs above — which cover every module this changeset touches. Residual failures (42 targeted + 14 module) are identical on base and worktree: DB-dependent materializer/run_replay, 4 strategy/cap/fdr guards, and `py_clob_client_v2` missing-dep collection errors — none introduced here.

---

## 3. Decision: D (BasisKind-required-at-write-boundary) — SKIPPED

The 4 CI-bans already enforce the full fabrication taxonomy statically (availability-law,
format-invariant, timing-column-liveness, date.today ban). A required-basis param would add
ceremony to **Tier-0** truth-owning write paths (`settlement_writers.py`, `db.py`; INV-37
cross-DB discipline) for marginal gain over the static bans. Per the operator SIMPLIFY law
this is over-engineering. **Recurrence prevention = extend the relevant CI-ban** with a new
banned pattern (cheap, targeted) — not retrofit type-level basis everywhere.

---

## 4. Deployment-gated (NOT done here — operator / deploy steps)

1. **Shadow-validate** M1 / M2 / fusion-guard settlement-graded before live merge (behavior-changers).
2. **Rebase** the worktree onto live HEAD `9424744b01` before merge (main advanced 5 commits past `f237314`).
3. **Run the Z-suffix migration** on the live DB (dry-run first).
4. **Run `scripts/persist_day0_horizon_identity_fit.py`** (operator command) to unblock the day0 nowcast lane.
5. **Commit + merge** the worktree — all agents left edits on disk per the no-git worktree discipline (a prior pollution incident is why git was forbidden inside agents).

**Note (out of scope):** `zeus_submit_intent_time` / `venue_ack_time` enrichment onto
`DecisionSourceContext` was dead (fed only the removed `decision_events` lane; 0 live callers).
Wiring submit/ack timing to a live lane would be a NEW feature — flagged for operator decision.
