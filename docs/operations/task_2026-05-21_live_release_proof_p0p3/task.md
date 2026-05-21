# Live Release Proof P0-P3 Task Ledger

Created: 2026-05-21  
Branch/worktree: `fix/live-release-proof-p0p3-20260521` /
`/Users/leofitz/.openclaw/workspace-venus/zeus-live-release-proof-p0p3-20260521`

This task starts from latest `origin/main` at
`1d63ad4450085e6b1c0ef7ab84fa92436768e8d9`. The source analysis recorded
`656e73fe5a71893ef7751ac4cac7de6003540ea8`; keep both facts visible because
the branch intentionally follows the newest main while preserving the original
analysis context.

## Source Reference

- `analysis_live_release_proof_p0p3.md`

## Mandatory Work Rule

Before starting a new finding repair, read the corresponding original section
in `analysis_live_release_proof_p0p3.md` and record the section id in this
ledger. Do not start P1 before all P0 rows are either FIXED with tests or
explicitly marked BLOCKED with evidence. Do not start P2/P3 cleanup before P1
runtime/code findings are handled.

## Finding Ledger

| ID | Source section | Required action | Status | Last source re-read | Evidence / next action |
| --- | --- | --- | --- | --- | --- |
| P0-1 | §5 P0-1, §15, §16 step 1/6/7 | Hard release gate proving loaded SHA, DB schema/hash, clean command/redeem state, source/forecast/snapshot freshness, and paper lifecycle proof before live entries | FIXED | 2026-05-21 §5 P0-1 / §15 / §16 step 7 | Added `scripts/check_live_release_gate.py`, required `money-path-release-gate` workflow, focused tests, and self-test fixture. Gate always reports `live_entries_allowed=false`; PASS only means release-proof blockers are absent |
| P0-2 | §5 P0-2, §8 row #184/#186/#199 vs #252/#253, §16 step 1 | Unified negRisk tradeability object across scanner, snapshot assertion, persisted reader, executor preflight | FIXED | 2026-05-21 §5 P0-2 | `ExecutableTradeabilityStatus` persisted as `tradeability_status_json`; scanner capture builds it from parent/child Gamma + CLOB facts; submit gate uses `executable_allowed`; focused tests green |
| P0-3 | §5 P0-3, §10, §16 step 2 | Live schema fail-closed; only paper/backfill may downgrade no-trade reasons as degraded/excluded from live trust | FIXED | 2026-05-21 §5 P0-3 / §10 | Live writer asserts current no_trade_events schema; compatibility downgrade is explicit opt-in and writes `schema_compatibility='degraded'`; current schema is now 22 after P1-3 |
| P0-4 | §5 P0-4, §11, §14, §16 step 3 | Integrated crash/recovery/redeem replay across command/order/trade/position/redeem lifecycle | FIXED | 2026-05-21 §5 P0-4 / §11 / §14 / §16 step 3 | Added `tests/test_money_path_lifecycle_replay.py`: decision/no-trade, command submit unknown, ack/order fact, partial fill/trade fact/position lot, cancel remainder terminal truth, redeem request, tx hash, receipt reconcile, with DB close/reopen crash boundaries. Replay exposed and fixed `decision_events` schema-version CHECK drift; current schema is now 22 after P1-3 |
| P1-1 | §6 P1-1 | Align decision/no-trade failure policy: live fail-closed, paper/backfill degrade with marking | FIXED | 2026-05-21 §6 P1-1 | Implemented with P0-3: default writer path is live fail-closed; degraded compatibility requires opt-in |
| P1-2 | §6 P1-2 | Live executable entry requires fresh CLOB/orderbook; discovery fallback cannot authorize live execution | FIXED | 2026-05-21 §6 P1-2 / §15 | P0-2 prevents raw Gamma routing labels from authorizing executable submit, and P0-1 release gate requires fresh runtime status/source evidence before live promotion |
| P1-3 | §6 P1-3, §16 step 4 | Split `REDEEM_OPERATOR_REQUIRED` semantic overload or add `autoretry_eligible` | FIXED | 2026-05-21 §6 P1-3 / §16 step 4 | Added `settlement_commands.autoretry_eligible`; submit/reconcile mark only auto-retryable rows; reseat requires both the explicit marker and allowlisted errorCode, and clears the marker on promotion. Focused reseat tests pass; schema bumped to 22 |
| P1-4 | §6 P1-4, §8 family row | Canonical `WeatherFamilyExposure` reducer consumed by evaluator/runtime/no-trade | FIXED | 2026-05-21 §6 P1-4 / §8 family row | Added `WeatherFamilyExposureReducer` and `resolve_weather_family_exposures`; runtime now uses the canonical resolver instead of assembling trade/portfolio fallbacks locally. Compatibility wrappers delegate to the reducer; relationship tests verify resolver/wrapper parity and runtime callsite |
| P1-5 | §6 P1-5, §13, §16 step 5 | Dynamic EffectiveKellyContext proof across live modes/order types | FIXED | 2026-05-21 §6 P1-5 / §13 / §16 step 5 | Added dynamic live-mode/order-surface matrix for opening/day0/imminent plus taker, passive, sub-dollar passive, wide-spread, low-depth, and missing-context fail-closed cases |
| P1-6 | §6 P1-6, §10 | Reports treat `unknown_legacy` as non-authoritative | FIXED | 2026-05-21 §6 P1-6 / §10 | Added `classify_polymarket_end_anchor_source` and `settlement_command_report_rows`; report rows expose `anchor_source_evidence_class`, `anchor_source_report_trust`, and `anchor_source_verified`, excluding `unknown_legacy` from verified anchor evidence while distinguishing Gamma/CLOB/chain anchors |
| P1-7 | §6 P1-7, §16 step 7 | Required money-path CI, not advisory full sweep | FIXED | 2026-05-21 §6 P1-7 / §16 step 7 | Added `.github/workflows/money-path-release-gate.yml` without `continue-on-error`; it runs schema pin, release gate tests, negRisk snapshot tests, no-trade live fail-closed tests, and the release-gate self-test fixture |
| P2-1 | §7 P2 docs/scaffold drift, §16 step 8 | Refresh authority headers after runtime verification | DEFER_UNTIL_RUNTIME_FIXES | pending | Docs cleanup last |
| P2-2 | §7 topology registries | Runtime-to-registry assertion in CI | OPEN | pending | Add focused registry assertion if no existing one |
| P2-3 | §7 monitor nowcast fail-soft | Counter/alert on persistent nowcast write failures | OPEN | pending | Inspect monitor nowcast writer |
| P2-4 | §7 hardcoded strategy/cluster maps | Move risk metadata to reviewed config with tests | OPEN | pending | Inspect current maps; avoid broad refactor if already config-backed |
| P3-1 | §7 sed-revert antibodies | Convert key antibodies to relationship/replay tests | IN_PROGRESS | §14 | P0-4 now has an integrated lifecycle replay; remaining P1/P2 findings still need relationship tests |
| P3-2 | §7 pre-existing failures | Track failure IDs centrally | OPEN | pending | Add/update failure registry only if routed |

## Work Order

1. P0-2 negRisk tradeability object and E2E tests.
2. P0-3/P1-1 live schema fail-closed plus degraded compatibility marking.
3. P0-1/P1-2/P1-7 release smoke, business-plane live gate, and required CI.
4. P0-4 lifecycle replay harness.
5. P1-3 redeem operator-required split/flag.
6. P1-4/P1-5/P1-6 targeted authority/test repairs.
7. P2/P3 cleanup and registry/docs alignment.

## Verification Log

| Timestamp | Command | Result |
| --- | --- | --- |
| pending | pending | pending |
| 2026-05-21 | `python3 scripts/check_schema_version.py --write-pin && python3 scripts/check_schema_version.py` | PASS: schema hash pinned for `SCHEMA_VERSION=19` |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_executable_market_snapshot_v2.py tests/test_market_scanner_negrisk.py -q --no-header` | PASS: 94 passed |
| 2026-05-21 | `python3 scripts/check_live_release_gate.py --self-test-fixture --json` | PASS: 7/7 gates pass on isolated fixture; `live_entries_allowed=false` |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_release_gate.py -q --no-header` | PASS: 5 passed |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_release_gate.py tests/test_executable_market_snapshot_v2.py tests/test_market_scanner_negrisk.py tests/test_decision_seq_cross_table_no_collision.py tests/test_no_trade_events_check_accepts_all_shoulder_reasons.py tests/state/test_schema_current_invariant.py tests/test_shoulder_strategy_vnext.py::test_p_3_5_schema_version_is_20 -q --no-header` | PASS: 117 passed, 2 skipped |
| 2026-05-21 | `python3 scripts/check_schema_version.py` | PASS: schema hash OK for `SCHEMA_VERSION=20` |
| 2026-05-21 | `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory ...` | PASS |
| 2026-05-21 | `python3 scripts/topology_doctor.py --scripts --json` | FAIL: pre-existing unrelated script-manifest debt; new `check_live_release_gate.py` is registered |
| 2026-05-21 | `python3 scripts/topology_doctor.py --tests --json` | FAIL: pre-existing unrelated test-topology/law-gate debt; new `tests/test_live_release_gate.py` is trusted and categorized |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_money_path_lifecycle_replay.py -q --no-header` | PASS: 1 passed |
| 2026-05-21 | `python3 scripts/check_schema_version.py --write-pin && python3 scripts/check_schema_version.py` | PASS: schema hash pinned for `SCHEMA_VERSION=21` |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_release_gate.py tests/test_money_path_lifecycle_replay.py tests/test_executable_market_snapshot_v2.py tests/test_market_scanner_negrisk.py tests/test_decision_seq_cross_table_no_collision.py tests/test_no_trade_events_check_accepts_all_shoulder_reasons.py tests/state/test_schema_current_invariant.py tests/test_shoulder_strategy_vnext.py::test_p_3_5_schema_version_is_21 -q --no-header` | PASS: 118 passed, 2 skipped |
| 2026-05-21 | `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md` | PASS |
| 2026-05-21 | `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory ...` | PASS |
| 2026-05-21 | `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md` | PASS |
| 2026-05-21 | `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory ...` | PASS |
| 2026-05-21 | `python3 scripts/check_schema_version.py --write-pin && python3 scripts/check_schema_version.py` | PASS: schema hash pinned for `SCHEMA_VERSION=20` |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_decision_seq_cross_table_no_collision.py tests/test_no_trade_events_check_accepts_all_shoulder_reasons.py tests/state/test_schema_current_invariant.py -q --no-header` | PASS: 17 passed, 2 skipped |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_executable_market_snapshot_v2.py tests/test_market_scanner_negrisk.py -q --no-header` | PASS: 94 passed |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_settlement_commands_reseat.py tests/test_reseat_negrisk_misrouted_allowlist.py -q --no-header` | PASS: 21 passed |
| 2026-05-21 | `python3 scripts/check_schema_version.py --write-pin && python3 scripts/check_schema_version.py` | PASS: schema hash pinned for `SCHEMA_VERSION=22` |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_settlement_commands_reseat.py tests/test_reseat_negrisk_misrouted_allowlist.py tests/test_redeem_cascade_liveness.py tests/test_settlement_commands_schema.py tests/test_migration_redeem_operator_required.py -q --no-header` | PASS: 37 passed |
| 2026-05-21 | `python3 scripts/check_schema_version.py` | PASS: schema hash OK for `SCHEMA_VERSION=22` |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_inv_family_exclusive_sizing.py -q --no-header` | PASS: 32 passed |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_inv_kelly_effective.py -q --no-header` | PASS: 23 passed |
| 2026-05-21 | `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_settlement_commands_anchor_source_default.py tests/test_settlement_migration_unknown_legacy_default.py tests/test_inv_anchor_source_real_value.py -q --no-header` | PASS: 11 passed, 1 skipped |
