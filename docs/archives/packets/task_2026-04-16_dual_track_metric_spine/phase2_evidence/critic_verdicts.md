# Phase 2 Critic Verdicts

## First pass: REVISE (wide exploratory)

### CRITICAL findings
- **C1 — model_skill drop breaks live ETL**: `scripts/etl_historical_forecasts.py:126-173` actively writes `DELETE FROM model_skill` + `INSERT OR REPLACE INTO model_skill`. The DROP in v2_schema would break ETL on next daemon restart.
- **C2 — store_artifact pre-commits**: `src/state/decision_chain.py:210` (and sibling `:251` for `store_settlement_records`) calls `conn.commit()` internally, defeating `commit_then_export`'s rollback contract. Tests passed only because they used custom db_op.

### MAJOR findings
- **M3 — platt_models_v2 city/date columns are semantic pollution**: executor added `city TEXT`, `target_date TEXT` to make a test pass. Bucket-family keyed; columns would silently accumulate NULL in production.
- **M4 — ChainPositionView.state permanently CHAIN_UNKNOWN**: `from_chain_positions()` and `empty()` never call `classify_chain_state()`; field was dead.
- **M5 — fetched_at=now needs comment**: behavior correct but reasoning invisible.
- **M6 — mid-cycle commits**: `_emit_rescue_event:273`, `_track_exit:1316` bypass choke point.

### Exploratory findings (widening paid off)
- Found `scripts/capture_replay_artifact.py:182` standalone `store_artifact` caller that main-thread and architect both missed.
- 7 total standalone callers needing explicit commit after internal-commit removal.

## Main-thread fixup

- Fix A: kept `model_skill`, dropped 3 other dead tables.
- Fix B: removed internal commits; audited all 9 callers across src/scripts/tests; added explicit commits to 7 standalone sites.
- Fix C: dropped city/target_date columns; rewrote Gate A platt assertion to use `COUNT(DISTINCT temperature_metric)`.
- Fix D (Option 4b): removed `ChainPositionView.state` field entirely (zero external readers).
- Fix E: added 2-line fetched_at=now comment.
- Fix F: annotated `_emit_rescue_event` + `_track_exit` with `# INFO(DT#1):` explaining exemption (authoritative writes, not derived exports).

## Second pass: PASS

- F1-F6: all verified clean.
- W1-W8 exploratory: no new CRITICAL/MAJOR. ChainPositionView justified as typed container.
- W6 MINOR: `src/main.py:341` has pre-existing Python 3.14 SyntaxError (`global` after assignment) that blocks `test_pnl_flow_and_audit.py` collection. Unrelated to Phase 2; noted for separate chore.
- Z1 Big-picture: `commit_then_export` at `canonical_write.py:16-57` is the structural antibody. Makes DT#1 impossible by construction; `detect_stale_portfolio` provides startup-side detection of any split. This is immune-system code, not patch.
- INV-13 `require_provenance("kelly_mult")` at `kelly.py:74` verbatim preserved.
- 23/23 Phase 2 tests GREEN; 4 Phase 0b stubs now GREEN; Phase 1 unchanged GREEN.

## Commit: pending (this archive committed alongside)
