# G1 work record — live-readiness gates

Date: 2026-04-27
Branch: plan-pre5
Task: R3 G1 live-readiness gate implementation — 17 CI gates, Q1 Zeus-egress evidence check, staged-live-smoke evidence check, and operator-gated deployment boundary
Status: ENGINEERING PRE-CLOSE APPROVE/PASS; IN_PROGRESS and NOT LIVE-READY because Q1/staged-smoke evidence is absent

Changed files:

Implementation:
- `scripts/live_readiness_check.py`

Tests:
- `tests/test_live_readiness_gates.py`
- `tests/test_digest_profile_matching.py`

Routing / registries / docs:
- `architecture/script_manifest.yaml`
- `architecture/test_topology.yaml`
- `architecture/topology.yaml`
- `architecture/naming_conventions.yaml`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/boot/G1_codex_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/G1_work_record_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/G1.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/G1.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/G1_pre_close_2026-04-27.md`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `architecture/docs_registry.yaml`

Summary:

- Added `scripts/live_readiness_check.py`, a read-only enforcement script with a 17-gate registry, JSON/plain output, explicit Q1/staged-smoke evidence checks, and fail-closed behavior for missing evidence.
- Added `tests/test_live_readiness_gates.py` to lock the 17-gate registry, fail-closed evidence behavior, safe CLI help, and invariant that the script cannot authorize deployment.
- Registered the script in `architecture/script_manifest.yaml` and documented the `live_readiness_check.py` long-lived naming exception because the operator-facing G1 contract fixes that script name.
- Registered the test in `architecture/test_topology.yaml` and moved it to top-level `tests/test_live_readiness_gates.py` because the topology test checker only classifies top-level `tests/test_*.py` files.
- Added a dedicated topology profile and digest regression so G1 routes to the live-readiness profile rather than heartbeat/risk profiles.
- Updated R3 phase status/current state to `G1 IN_PROGRESS` without marking the phase closed.

Verification:

```text
python3 scripts/topology_doctor.py --navigation --task "R3 G1 live readiness gates live_readiness_check 17 CI gates staged-live-smoke INV-NEW-S live-money-deploy-go" --files ...: navigation ok True, profile r3 live readiness gates implementation
python3 -m py_compile scripts/live_readiness_check.py tests/test_live_readiness_gates.py: PASS
python3 scripts/live_readiness_check.py --help: PASS
python3 -m pytest -q -p no:cacheprovider tests/test_live_readiness_gates.py: 5 passed
python3 -m pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_g1_live_readiness_routes_to_g1_profile_not_heartbeat: 1 passed
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase G1: GREEN=0 YELLOW=0 RED=0, STATUS GREEN
python3 scripts/live_readiness_check.py --json: exit 1 expected in this environment; 16/17 engineering gates PASS, G1-02 Q1 Zeus-egress evidence FAIL, staged-live-smoke evidence FAIL, live_deploy_authorized=false
python3 scripts/topology_doctor.py --scripts --json: G1 script issue cleared; remaining failures are unrelated pre-existing scripts/ingest manifest-stale entries
python3 scripts/topology_doctor.py --tests --json: G1 test issue cleared; remaining failures are unrelated pre-existing top-level test classifications
python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md: topology check ok
python3 scripts/topology_doctor.py --map-maintenance --changed-files ... --map-maintenance-mode closeout: topology check ok
python3 scripts/topology_doctor.py closeout --changed-files ... --plan-evidence ... --work-record-path ... --receipt-path ... --summary-only: closeout ok, changed_files=19
pre-close critic Mill the 2nd: ENGINEERING APPROVE; phase close/live-ready BLOCKED by missing external evidence
pre-close verifier Tesla the 2nd: ENGINEERING PASS; current missing-evidence refusal PASS
python3 scripts/topology_doctor.py --navigation ... after review artifact: navigation ok True, profile r3 live readiness gates implementation
python3 scripts/topology_doctor.py closeout --changed-files ... after review artifact/status update: closeout ok, changed_files=20
```

Known non-goals / risks:

- G1 did not place, cancel, redeem, deploy, mutate production DB/state, activate credentials, promote strategies, or run a live smoke command.
- `scripts/live_readiness_check.py` reads staged-smoke artifacts only; it intentionally fails closed when Q1/staged-smoke evidence is absent.
- The readiness script reports `live_deploy_authorized=false` even if all checks pass; operator `live-money-deploy-go` remains the final live-money gate.
- Global topology script/test checks still have unrelated pre-existing failures outside the G1 slice; G1-specific script/test issues are cleared.

Next:

- Re-run map-maintenance/closeout after this work record and receipt refresh.
- Obtain real Q1 Zeus-egress and staged-live-smoke evidence from the authorized operator/staging path; do not fabricate evidence and do not auto-run live smoke.
- Obtain pre-close critic + verifier before changing G1 to COMPLETE. After any close, run the required post-close third-party critic+verifier before freezing next work.

## Post-verification remediation pass — 2026-04-27

A broad critic/security/verifier pass after F3 found that G1 was not live-ready and that several non-operator hardening seams were still too permissive or under-tested.  These were remediated without creating operator evidence, activating credentials, mutating production DB/state, or executing live venue side effects.

Additional changed files:

- `src/control/ws_gap_guard.py`
- `src/ingest/polymarket_user_channel.py`
- `tests/test_user_channel_ingest.py`
- `src/data/ensemble_client.py`
- `tests/test_forecast_source_registry.py`
- `src/calibration/retrain_trigger.py`
- `tests/test_calibration_retrain.py`
- `src/data/polymarket_client.py`
- `tests/test_v2_adapter.py`
- `src/execution/settlement_commands.py`
- `tests/test_settlement_commands.py`
- `src/risk_allocator/governor.py`
- `tests/test_risk_allocator.py`
- `src/execution/exit_lifecycle.py`
- `tests/test_exit_safety.py`
- `scripts/live_readiness_check.py`
- `tests/test_live_readiness_gates.py`
- `tests/conftest.py`

Hardening completed:

- User-channel guard now fails closed when unconfigured; unit tests must explicitly clear the guard.
- User-channel CONFIRMED/MATCHED projections now fall back from executor runtime `position_id` to numeric `decision_id`, so live executor-shaped commands do not silently skip `position_lots`.
- Registered TIGGE ingest configuration errors now propagate instead of being hidden as `None`.
- Calibration retrain corpus loading now requires explicit cluster/season/temperature_metric/data_version/input_space identity in CONFIRMED facts and excludes mismatched identities.
- `PolymarketClient.cancel_order()` checks `CutoverGuard` before adapter cancel side effects.
- R1 `submit_redeem()` checks `CutoverGuard.redemption_decision()` before `REDEEM_SUBMITTED` and adapter redeem side effects.
- Global risk allocator defaults now fail closed until the cycle runner/configured test harness publishes allocator state.
- Exit lifecycle can thread latest fresh executable snapshot evidence into executor sell intents; missing snapshot remains executor fail-closed.
- G1 legacy SDK scan now uses AST import detection and catches nested `from py_clob_client.client import ...` imports without flagging `py_clob_client_v2`.

Verification:

```text
python3 scripts/topology_doctor.py --task-boot-profiles: topology check ok
per-slice topology navigation for M3/F3/F2/Z2/R1/A2/M4/G1/T1 remediation: navigation ok for edited scopes; planning-lock with ULTIMATE_PLAN_R3.md evidence: topology check ok
pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_forecast_source_registry.py tests/test_calibration_retrain.py tests/test_v2_adapter.py tests/test_settlement_commands.py tests/test_exit_safety.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py: 109 passed, 20 warnings
pytest -q -p no:cacheprovider tests/test_cutover_guard.py tests/test_v2_adapter.py tests/test_executable_market_snapshot_v2.py tests/test_heartbeat_supervisor.py tests/test_collateral_ledger.py tests/test_provenance_5_projections.py tests/test_command_grammar_amendment.py tests/test_unknown_side_effect.py tests/test_user_channel_ingest.py tests/test_exit_safety.py tests/test_exchange_reconcile.py tests/test_settlement_commands.py tests/test_fake_polymarket_venue.py tests/test_strategy_benchmark.py tests/test_calibration_retrain.py tests/test_tigge_ingest.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py: 295 passed, 8 skipped, 25 warnings
python3 -m py_compile src/control/ws_gap_guard.py src/ingest/polymarket_user_channel.py src/data/ensemble_client.py src/data/tigge_client.py src/calibration/retrain_trigger.py src/data/polymarket_client.py src/execution/settlement_commands.py src/risk_allocator/governor.py src/execution/exit_lifecycle.py scripts/live_readiness_check.py tests/conftest.py: PASS
python3 scripts/live_readiness_check.py --json: exit 1 expected; 16/17 gates PASS; G1-02 Q1 Zeus-egress evidence FAIL; staged-live-smoke evidence FAIL; live_deploy_authorized=false
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py: GREEN=241 YELLOW=0 RED=0, STATUS GREEN
git diff --check -- remediation files: PASS
python3 scripts/topology_doctor.py --map-maintenance --changed-files remediation files plus companion registries --map-maintenance-mode closeout: topology check ok
```

Remaining blockers:

- G1 remains `IN_PROGRESS`; do not close it until real Q1 Zeus-egress and staged-live-smoke evidence exist and pre-close critic+verifier review passes.
- `scripts/live_readiness_check.py --json` correctly remains FAIL in this environment because those external evidence artifacts are absent.
- Operator decisions remain open for Q1 egress, CLOB v2 cutover, Q-FX-1, TIGGE ingest go-live, calibration retrain go-live, staged smoke, and final `live-money-deploy-go`.


## Second-round security/code-review remediation — 2026-04-27

Independent security and code-review passes found additional non-operator bypasses.  These were remediated without fabricating Q1/staged-smoke evidence, changing CutoverGuard live state, activating credentials, mutating production DB/state artifacts, or placing/canceling/redeeming on the live venue.

Additional hardening completed:

- G1 readiness evidence is now signed JSON only: Q1 and staged-smoke artifacts require `schema_version=1`, matching `evidence_type`, canonical payload SHA-256, and HMAC over `evidence_type
payload_sha256`; production CLI no longer accepts arbitrary `--evidence-root` overrides.
- Generic cancel seam `request_cancel_for_command()` now checks `CutoverGuard` before persisting `CANCEL_REQUESTED` or invoking a supplied cancel callable.
- `ws_gap_guard.configure_status()` and `clear_for_test()` are rejected outside test runtime.
- F2 operator retrain token now requires a signed `v1.<operator_id>.<nonce>.<hmac_sha256>` token using `ZEUS_CALIBRATION_RETRAIN_OPERATOR_TOKEN_SECRET`; a non-empty arbitrary token is no longer sufficient.
- User-channel trade ingest now creates positive `position_lots` only for `ENTRY`/`BUY`; `EXIT`/`SELL` WS trade facts still persist venue truth and command events but do not mint active exposure.
- Script/test topology registry gaps reported by verifier were closed: stale nested `scripts/ingest/*` top-level script entries were removed from `architecture/script_manifest.yaml`; seven previously unclassified tests were registered; `tests/test_p0_hardening.py` skip status was recorded.

Second-round verification:

```text
pytest -q -p no:cacheprovider tests/test_live_readiness_gates.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_calibration_retrain.py: 48 passed, 20 warnings
pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py::test_exit_sell_confirmed_trade_does_not_mint_positive_exposure_lot tests/test_user_channel_ingest.py::test_confirmed_event_finalizes_trade_and_permits_canonical_pnl: 2 passed, 6 warnings
pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_forecast_source_registry.py tests/test_calibration_retrain.py tests/test_v2_adapter.py tests/test_settlement_commands.py tests/test_exit_safety.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py: 114 passed, 22 warnings
pytest -q -p no:cacheprovider tests/test_cutover_guard.py tests/test_v2_adapter.py tests/test_executable_market_snapshot_v2.py tests/test_heartbeat_supervisor.py tests/test_collateral_ledger.py tests/test_provenance_5_projections.py tests/test_command_grammar_amendment.py tests/test_unknown_side_effect.py tests/test_user_channel_ingest.py tests/test_exit_safety.py tests/test_exchange_reconcile.py tests/test_settlement_commands.py tests/test_fake_polymarket_venue.py tests/test_strategy_benchmark.py tests/test_calibration_retrain.py tests/test_tigge_ingest.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py: 300 passed, 8 skipped, 27 warnings
python3 -m py_compile second-round modified source/tests: PASS
python3 scripts/live_readiness_check.py --json: exit 1 expected; 16/17 gates PASS; G1-02 Q1 Zeus-egress evidence FAIL; staged-live-smoke evidence FAIL; live_deploy_authorized=false
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py: GREEN=241 YELLOW=0 RED=0, STATUS GREEN
python3 scripts/topology_doctor.py --scripts --json: ok true
python3 scripts/topology_doctor.py --tests --json: ok true
git diff --check -- second-round modified files: PASS
pytest -q -p no:cacheprovider --maxfail=30: 30 failed, 779 passed, 57 skipped, 16 deselected, 1 xfailed; NOT cited as green and triaged separately
```

Updated remaining blockers:

- G1 remains `IN_PROGRESS`; missing Q1 Zeus-egress and staged-live-smoke signed evidence are intentional external/operator blockers.
- Broad R3 unit-harness evidence is not production fail-closed evidence by itself; explicit fail-closed antibodies cover WS default blocking and risk allocator default blocking, and live-readiness still requires staged evidence.
- Full-repo pytest is not green and must not be represented as live readiness.  The current sampled failures include stale fixtures against newer metric/slippage laws and possible follow-up live plan omissions (Day0 runtime/window, auto-pause, strategy-key surfaces) requiring separate triage before any claim of global suite health.


## Post-interruption verification and residual blocker update — 2026-04-28

The network interruption did not change the authority state: G1 remains `IN_PROGRESS`, external-evidence blocked, and live deploy remains **NO-GO**. The resumed pass continued only safe, non-operator remediation and verification. It did not transition `CutoverGuard` to `LIVE_ENABLED`, create Q1/staged smoke evidence, activate credentials, place/cancel/redeem/wrap/unwrap live orders, or mutate production DB truth.

Additional hardening / compatibility repairs:

- `src/control/cutover_guard.py` now binds `LIVE_ENABLED` operator evidence to a JSON readiness report with `status=PASS`, `gate_count=17`, `passed_gates=17`, `staged_smoke_status=PASS`, and `live_deploy_authorized=false`; arbitrary note files or failing readiness reports are rejected.
- Active transition shell scripts `scripts/resume_backfills_sequential.sh` and `scripts/post_sequential_fillback.sh` no longer export a plaintext WU key fallback; they require operator-provided `WU_API_KEY` before WU-dependent steps.
- `scripts/rebuild_settlements.py` was added/registered as a dry-run-by-default, verified-observation-only high-track settlement repair helper for authority tests; no production DB mutation was performed.
- Legacy test compatibility was repaired around ENS member extrema, settlement helper objects created via `__new__`, injected Polymarket client adapters, P0 preflight harnesses, complete bin topologies, and explicit metric/unit identity fixtures.
- `tests/test_pe_reconstruction_relationships.py` now skips explicitly when its external reconstruction plan artifact is absent, and that skip is registered in `architecture/test_topology.yaml`.

Verification after resume:

```text
pytest -q -p no:cacheprovider <17 residual tests plus tests/test_pe_reconstruction_relationships.py>: 15 passed, 15 skipped in 1.87s
pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_forecast_source_registry.py tests/test_calibration_retrain.py tests/test_v2_adapter.py tests/test_settlement_commands.py tests/test_exit_safety.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py tests/test_cutover_guard.py: 128 passed, 2 skipped, 22 warnings in 5.53s
python3 scripts/live_readiness_check.py --json: exit 1 expected; status FAIL; gate_count=17; passed_gates=16; failing gate G1-02 Q1 Zeus-egress evidence missing; staged_smoke_status=FAIL; live_deploy_authorized=false
pytest -q -p no:cacheprovider --maxfail=30: 30 failed, 2566 passed, 91 skipped, 16 deselected, 1 xfailed, 1 xpassed, 7 warnings; full-suite green is not claimed
python3 scripts/topology_doctor.py --scripts --json: ok true
python3 scripts/topology_doctor.py --tests --json: ok true
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py: GREEN=241 YELLOW=0 RED=0, STATUS GREEN; wrote r3/drift_reports/2026-04-28.md
python3 -m py_compile scripts/live_readiness_check.py scripts/rebuild_settlements.py scripts/r3_drift_check.py src/control/cutover_guard.py src/data/daily_obs_append.py src/data/polymarket_client.py src/engine/evaluator.py src/execution/executor.py src/signal/ensemble_signal.py src/strategy/market_analysis.py src/signal/day0_signal.py: PASS
git diff --check: PASS
```

Residual blockers / not only an operator switch:

- Missing signed Q1 Zeus-egress evidence and missing signed staged-live-smoke evidence still keep `scripts/live_readiness_check.py` at `16/17` and `staged_smoke_status=FAIL`.
- G1 is still `IN_PROGRESS`; close requires real evidence plus the required critic/verifier sequence before any phase freeze.
- Full-suite failures remain real blockers or explicit-waiver candidates. The current major clusters are `riskguard` canonical/fallback contract drift, `harvester` return/settlement-contract drift, runtime guard harness signature/telemetry drift, two rebuild-pipeline settlement tests, and strategy-tracker/audit fixture drift.
- TIGGE/data training is not live-training-ready from this workspace alone: local switch/stub tests are green, but real payload/data availability, signed retrain evidence, staged smoke, and Q1 evidence are absent.
- A1 strategy benchmark evidence is not proof of live-market alpha; live strategy promotion still needs current data, calibration, benchmark, risk, and staged/live readiness evidence.


## Worktree merge assessment — 2026-04-28

Read-only inventory after the network interruption found six worktrees:

| Worktree | Branch | HEAD | Merge status vs `plan-pre5` | Dirty state | Action |
|---|---|---:|---|---|---|
| `zeus/` | `integration/r3-g1-f11-reconcile-2026-04-28` | `eb7df50` | new local integration branch created from `plan-pre5` for the current dirty R3 workspace | 332 dirty/untracked paths | future commits should land here, not directly on `plan-pre5` |
| `zeus-fix-plan-20260426` | `claude/zeus-full-data-midstream-fix-plan-2026-04-26` | `9890ab8` | already ancestor of `plan-pre5` (`branch_only=0`) | runtime `state/*` projections only | no merge needed; preserve/ignore runtime projections |
| `zeus-live-readiness-2026-04-26` | `claude/live-readiness-completion-2026-04-26` | `6c42aa7` | already ancestor of `plan-pre5` (`branch_only=0`) | `.code-review-graph/graph.db` only | no merge needed; do not import derived graph DB |
| `zeus-pr18-fix-plan-20260426` | `claude/pr18-execution-state-truth-fix-plan-2026-04-26` | `2d443c3` | already ancestor of `plan-pre5` (`branch_only=0`) | runtime `state/*` projections only | no merge needed; preserve/ignore runtime projections |
| `.claude/worktrees/quizzical-bhabha-8bdc0d` | `claude/quizzical-bhabha-8bdc0d` | `874e00c` | same as `main`, already ancestor of `plan-pre5` | `.code-review-graph/graph.db` only | no merge needed |
| `.claude/worktrees/mystifying-varahamihira-3d3733` | `claude/mystifying-varahamihira-3d3733` | `5bd9be8` | **not** merged; `base_only=2`, `branch_only=9` | `.code-review-graph/graph.db` only | candidate merge after R3 snapshot; requires manual integration with R3 forecast-source changes |

Dry-run merge evidence:

```text
git merge-tree plan-pre5 vs claude/mystifying-varahamihira-3d3733: no conflict markers against committed plan-pre5 HEAD
temporary worktree git merge --no-commit --no-ff claude/mystifying-varahamihira-3d3733 at updated HEAD 5bd9be8: merge_exit=0; 28 staged/merged paths; git diff --check clean
mystifying targeted pytest at updated HEAD 5bd9be8: 66 passed in 0.17s for tests/test_backtest_purpose_contract.py tests/test_backtest_skill_economics.py tests/test_backtest_training_eligibility.py tests/test_dissemination_schedules.py tests/test_forecasts_writer_provenance_required.py
```

Risk assessment:

- The three older top-level paused branches and `quizzical` should not be merged again; their committed content is already contained in `plan-pre5`, and their remaining dirty files are runtime/derived artifacts that must not be used as authority.
- `mystifying` is valuable but not a blind merge into the current dirty R3 workspace. It adds F11/backtest decision-time truth work (`src/backtest/*`, `src/data/dissemination_schedules.py`, forecast issue-time/availability-provenance scripts/tests) plus planning-only F11 apply runbook and WU empty-provenance triage docs; it overlaps current dirty files: `architecture/script_manifest.yaml`, `architecture/source_rationale.yaml`, `architecture/test_topology.yaml`, and `src/data/forecasts_append.py`.
- The `src/data/forecasts_append.py` overlap is semantic, not just textual: current R3 work stamps forecast-source registry identity (`source_id`, `raw_payload_hash`, `captured_at`, `authority_tier`), while F11 adds `forecast_issue_time` + `availability_provenance`. The correct integration is to preserve both families of provenance in one writer/schema contract.
- Current topology navigation rejects the F11 new files until their companion registry/source-rationale updates are included. That is expected for pre-merge evaluation, but it means F11 must be merged as an explicit packet/integration slice, not as an unreviewed side import.

Revised integration plan:

1. Current R3 uncommitted work has been moved off `plan-pre5` onto local branch `integration/r3-g1-f11-reconcile-2026-04-28`; still snapshot/commit it before any real merge attempt.
2. Mark already-ancestor worktrees as informational only; no merge and no import of `.code-review-graph/graph.db` or `state/*` dirty projections.
3. Create/assign a separate integration branch for F11, then merge `claude/mystifying-varahamihira-3d3733` into a clean integration worktree or apply it manually on top of the R3 snapshot.
4. Resolve the four overlapping files deliberately, especially `src/data/forecasts_append.py`, by combining R3 source identity and F11 availability provenance rather than choosing one side.
5. Run topology navigation/planning-lock after the F11 files are present, then targeted tests (`66 passed` group), R3 targeted gates, `scripts/live_readiness_check.py --json`, and a full-suite sample.
6. Re-evaluate live plan only after the F11 merge: F11 can improve training/backtest readiness by removing forecast hindsight leakage, but it does **not** satisfy Q1 Zeus-egress, staged-live-smoke, TIGGE payload, calibration retrain, or `live-money-deploy-go` evidence.
