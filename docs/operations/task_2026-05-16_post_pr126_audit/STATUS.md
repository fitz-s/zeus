# STATUS — F1–F24 post-PR-126 + post-PR-132/133

Baseline: main HEAD `9259df3e9c` (2026-05-17)
Method: targeted probes per RUN_7_findings.md §1 (no full re-audit of older Fs).

| F# | Title (short)                                            | Pre-PR126 | Post-PR126 status | Evidence note |
|----|----------------------------------------------------------|-----------|-------------------|---------------|
| F1 | ZEUS_FORECASTS_DB legacy path                            | FIXED     | FIXED             | unchanged    |
| F2 | decision_id NULL on selection_hypothesis_fact            | OPEN      | **WORSE**         | 100% NULL (was 100% already; row count 693→1518). See F25. |
| F3 | unit-system co-mingling                                  | OPEN      | OPEN              | no PR touched signal/calibration paths |
| F4 | settle_status ghost column                               | FIXED     | FIXED             | column absent from `position_current` schema; verified |
| F5 | collateral_ledger raw sqlite3.connect                    | OPEN      | OPEN-acknowledged | now in `tests/conftest.py:_WLA_SQLITE_CONNECT_ALLOWLIST` as `singleton_persistent_conn`; not removed |
| F6 | candidate_fact orphan rows                               | OPEN      | OPEN              | not re-probed (no relevant PR) |
| F7 | order_intent / venue_command lineage gap                 | OPEN      | OPEN              | not re-probed |
| F8 | observations_v2 dual-write window                        | OPEN      | OPEN              | `observation_instants` + `_v2` both `world_class` per `architecture/db_table_ownership.yaml` |
| F9 | calibration_pairs_v2 not promoted                        | OPEN      | OPEN-progress     | PR #112 added `promote_calibration_v2_stage_to_prod.py` and `promote_calibration_pairs_v2.py` retrofit; promotion not yet run |
| F10| risk_state.db separate-process drift                     | OPEN      | OPEN              | no PR |
| F11| BulkChunker yields LIVE only at chunk boundary           | OPEN-progress | OPEN-progress | `db_writer_lock.py:BulkChunker` shipped per §3.1.5; production retrofit Phase 1+ pending |
| F12| migration script idempotency unverified                  | OPEN      | OPEN              | F23 sibling; only `202605_add_redeem_operator_required_state.py` exists |
| F13| settlement_commands ux index excludes only 2 terminals   | OPEN      | **NEW-SCOPE**     | PR #126 added REDEEM_OPERATOR_REQUIRED but did NOT update index. See F27. |
| F14| redeem cascade liveness contract missing                 | OPEN      | FIXED             | PR #126 added `architecture/cascade_liveness_contract.yaml` + tests |
| F15| chain reconciliation skip_voiding interplay              | OPEN      | OPEN              | not re-probed |
| F16| redeem auto-execution gate missing operator path         | OPEN      | FIXED             | PR #126 added REDEEM_OPERATOR_REQUIRED + `operator_record_redeem.py` |
| F17| forecasts.db user_version drift risk                    | OPEN      | FIXED-held        | `user_version=3`, `SCHEMA_FORECASTS_VERSION=3` at `src/state/db.py:2427` — R2 sentinel held |
| F18| INSERT OR IGNORE silent loss                             | OPEN      | OPEN              | F22 sibling; `market_scanner.py:610` still INSERT OR IGNORE inside raw connect |
| F19| collateral ledger schema not in registry                 | OPEN      | OPEN              | not re-probed |
| F20| position_lots reconciliation                             | OPEN      | OPEN              | not re-probed |
| F21| legacy observation_instants writer active                | OPEN      | OPEN              | `hourly_instants_append.py:229` not touched; both v1 + v2 carry `world_class` in registry — neither marked legacy_archived |
| F22| market_events_v2 dual-write via raw sqlite3.connect      | OPEN      | OPEN-acknowledged | `market_scanner.py:610` now in `_WLA_SQLITE_CONNECT_ALLOWLIST` as `pending_track_a6` |
| F23| migration runner architecturally bare (no ledger)        | OPEN      | OPEN              | only 1 script in `scripts/migrations/`; no `_migrations_applied` ledger; no TARGETS |
| F24| decision_id NULL accelerated 693→1518                   | OPEN      | **WORSE / EXPANDED** | superseded by F25 (3-table systemic snapshot-write failure) |

## New findings (Run #7)
See `RUN_7_findings.md` and `FINDINGS_REFERENCE_v2.md` for F25–F27.
