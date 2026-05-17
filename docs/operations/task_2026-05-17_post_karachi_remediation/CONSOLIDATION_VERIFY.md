# CONSOLIDATION_VERIFY.md
# Post-Karachi 2026-05-17 — Critic Finding Verification
# Generated: 2026-05-17 | Branch: feat/post-karachi-remediation-wave-2026-05-17

| Finding | Verification | Result | Status |
|---|---|---|---|
| F2 — lineage NULL family | `grep -n "_make_rejection_decision" src/engine/evaluator.py \| wc -l` → 32 call sites; `grep -n "EdgeDecision(False" src/engine/evaluator.py` → 0 raw calls | 32 call sites migrated, 0 raw | ✅ |
| F7 — command_id COALESCE | `grep -n "COALESCE\|stored_command_id" src/state/db.py` → lines 5650+ show preservation logic; migration `202605_add_execution_fact_command_id.py` adds nullable column | Column added + preservation logic present | ✅ |
| F8 — occurred_at sentinel | `grep -n "now\|unknown_entered_at" src/state/chain_reconciliation.py \| grep 658` → line 658 uses `now` not literal string; migration `202605_position_events_occurred_at_iso_check.py` adds CHECK + backfill | `now` in scope at rescue site; CHECK migration shipped | ✅ |
| F15 — settlements_v2 backfill | `ls scripts/migrations/202605_backfill_settlements_v2.py` exists; migration contains idempotent INSERT | Migration present | ✅ |
| F18 — INSERT OR IGNORE silent loss | `grep -n "zero.*insert\|inserted.*0" src/data/market_scanner.py` → warning on zero-insert; antibody `tests/test_market_scanner_zero_insert_alert.py` present | Log-on-zero shipped | ✅ |
| F23 — migration runner framework | `python -m py_compile scripts/migrations/__init__.py scripts/migrations/__main__.py` → OK; `python -m pytest tests/test_migration_runner_idempotent.py -q` → 8 passed | Runner operational, 8 antibody tests pass | ✅ |
| F25 — DSI sentinel threading | `grep -n "_PRE_SNAPSHOT_DSI_SENTINEL" src/engine/evaluator.py` → line 288 constant + line 310 usage; 32 rejection sites stamp `<pre_snapshot:rejected>` | All 32 early-rejection paths stamp sentinel | ✅ |
| F26 — writer-lock allowlist dedup (DEFERRED) | Per branch A commit: 74 conftest entries vs 9 canonical — scope-cut to separate PR; `__main__.py` added to conftest allowlist | Deferred; partial fix applied | ⚠️ (deferred) |
| F27/F29 — REVIEW_REQUIRED unique index | `ls scripts/migrations/202605_settlement_commands_ux_review_required.py` exists; migration adds `REDEEM_REVIEW_REQUIRED` to exclusion list | Migration shipped | ✅ |
| F30 — migration header drift gate | `grep -n "_check_header\|_LAST_REVIEWED_RE" scripts/migrations/__init__.py` → lines 18+54; runner refuses migrations without `last_reviewed=` | Header enforcement in runner | ✅ |
| F40+F41+F42 — K1 reader sweep | `grep -n "get_forecasts_connection_with_world\|get_forecasts_connection" scripts/bridge_oracle_to_calibration.py scripts/evaluate_calibration_transfer_oos.py` → K1 pattern used; `tests/test_k1_reader_isolation.py` + `test_k1_reader_isolation_batch2.py` present | 37 BROKEN readers reduced; live scripts repointed | ✅ |
| CB-1 — schema version drift | `grep -n "user_version = 5" scripts/migrations/202605_add_settlement_commands_winning_index_set.py` → line 37; `grep -n "SCHEMA_VERSION = 5" src/state/db.py` → line 829 | PRAGMA user_version=5 in I's migration; SCHEMA_VERSION=5 in db.py | ✅ |
| MINOR A — up(conn) wrapper | `grep -n "^def up" scripts/migrations/202605_add_redeem_operator_required_state.py` → present; `python -m py_compile` → OK | def up(conn) wrapper added | ✅ |
| MINOR B — sentinel naming reconciliation | `grep -n "pre_decision:family" src/engine/evaluator.py` → line 3131 (`decision_id` field); `grep -n "pre_snapshot:rejected" src/engine/evaluator.py` → line 310 (`decision_snapshot_id` field) — two distinct sentinels, correct fields | No code change needed; two sentinels already distinct and correctly assigned | ✅ (doc-only) |
| Hook fix (E) — WorktreeCreate stdout protocol | `grep -n "_STDOUT_PROTOCOL_RESERVED_EVENTS" .claude/hooks/dispatch.py` → line 112+140 (from main commit f52cea1537); antibody `tests/.claude/hooks/test_worktree_create_contract.py` (45 lines) present | Source fix in main; antibody test shipped | ✅ |

## Notes

- `test_structured_overrides.py` collection error is pre-existing (FileNotFoundError on missing fixture path) — not introduced by this merge wave.
- F26 deferred: 65-entry gap between conftest allowlist and canonical db_writer_lock allowlist requires operator decision on grandfather scope before migration can be scripted.
- MINOR H: false alarm per critic (mixed shell/python style) — no action taken.
