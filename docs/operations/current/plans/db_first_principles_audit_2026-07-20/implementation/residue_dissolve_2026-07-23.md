# Residue dissolve increment — 2026-07-23

Authority basis: this plan dir (db_first_principles_audit_2026-07-20) + operator directive
2026-07-23 ("执行第一部分" — dissolve the audited residue). Findings source: three-agent
residue audit (critic + dead-code sweep + capital fork), cross-confirmed.

## Scope (pure reduction, no behavior change on the money path)

1. **DISSOLVE world-side snapshot ghost creation** — `src/state/db.py` init_schema no longer
   calls `init_snapshot_schema(conn, include_latest=False)`. Evidence: all 6 production
   insert_snapshot call sites and all readers use the trade connection; live world copy has
   0 rows; registry entry is `legacy_archived` (non-owning) so `assert_db_matches_registry`
   passes with or without the table. The on-disk empty ghost in the live world DB is
   RETAINED (drop requires separate authorization). `settlement_commands` Tier-1 world-ATTACH
   lookup is fail-soft (falls to Tier 2/3) and keeps working against the retained live ghost;
   on a fresh world DB it misses to Tier 2 by design.
2. **DELETE dead symbols** (zero references repo-wide): `TERMINAL_TRADE_DECISION_STATUSES`
   (db.py), `log_exit_retry_released_event` (db.py).
3. **COLLAPSE checkpoint trio (db.py)** — `checkpoint_world_wal`/`checkpoint_trades_wal`/
   `checkpoint_forecasts_wal` were byte-identical except the DB path. Replaced by one
   `checkpoint_wal(db_path)`; incident history consolidated in its docstring.
4. **COLLAPSE checkpoint cycle trio (main.py)** — one `_make_wal_checkpoint_cycle` factory;
   the forecasts cycle's missing `_defer_for_held_position_monitor` guard is PRINCIPLED
   (held-position monitor writes world+trade, not forecasts) and survives as `defer=False`.
5. **Duplicate logger** — snapshot_repo.py `_pr2_logger` residue removed; module logger used.
6. **Stale docs** — IMPLEMENTATION_STATUS.md compact-table line corrected;
   db_table_ownership.yaml world snapshot entry annotated (creation removed, ghost retained).

## Deliberately NOT done (considered, rejected)

- **capture_trigger write-boundary ValueError stays.** Critic flagged it as guarding literals
  that cannot mismatch — but with the DB CHECK removed (O(rows) boot-scan), this raise is the
  ONLY taxonomy enforcement; a future call site's typo'd string is a real case, and the cost
  is one frozenset lookup per insert. Matches consult re-review 2026-07-22.
- **check_db_table_delta.py stays whole.** Its non-redundant value is shift-left: catching an
  unregistered table pre-merge instead of as a boot FATAL on the live money daemon. Narrowing
  to forecasts-class would remove exactly the protection that matters most (world/trade).
  Each false-positive exclusion in the file traces to an observed FP class, not hypothetical.
- **13 test-only db.py query/log functions + SNAPSHOT_TABLE**: flagged, but each needs a
  per-symbol provenance audit (some are operator ad-hoc ops tooling) — follow-up, not this PR.
- **Live-state residue (0-byte legacy DB files, stale edli_live_order_projection rows)**:
  live state dir, not repo — separate operator action.

## Test impact

~35 test files used `init_schema(mem)` as a combined-DB convenience and implicitly depended
on it creating `executable_market_snapshots`. Fixed by adding the explicit
`init_schema_trade_only(conn)` where the test actually exercises snapshot functions —
making the tests honest about which DB owns the table.
