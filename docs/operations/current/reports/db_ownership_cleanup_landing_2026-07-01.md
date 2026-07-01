# DB-ownership cleanup — landing (2026-07-01)

Created: 2026-07-01
Authority basis: market_structure_code_atlas_2026-06-30.md §6C; data-grounded row-probe audit 2026-07-01.

First-principles cleanup of the Zeus DB-ownership redundancy, **in place on the existing three DBs — zero new DBs, zero data moved**. Verdict: the three-DB domain split is already the correct design; only the ownership *sources* were drifting.

## 图谱 (the map) — three runtime DBs by domain

| DB (file) | size | live tables | owns |
|---|---|---|---|
| `world` (state/zeus-world.db) | 68 GB | 62 world_class | contract truth · observations · forecast archive · `settlement_outcomes`-adjacent world data |
| `forecasts` (state/zeus-forecasts.db) | 37 GB | 22 forecast_class | `raw_model_forecasts` → fusion → `forecast_posteriors` → walk-forward de-bias |
| `trade` (state/zeus_trades.db) | 72 GB | 36 trade_class | orders · fills · positions · `executable_market_snapshots` · `market_price_history` |
| `risk_state` (state/risk_state.db) | 232 MB | — | heartbeat kill-switch · control_overrides · risk_actions |
| `backtest` (state/zeus_backtest.db) | 56 MB | — | derived; never runtime authority |

## 接线 (the wiring)

- **Cross-DB is ATTACH + SAVEPOINT only** (INV-37): `get_forecasts_connection_with_world` / `get_trade_connection_with_world` — one connection, one transaction. Never two independent connections. 12 connection accessors total.
- **Root fix = Owner-Routed Writes** (`src/state/owner_routed_write.py`, committed): a table's DB is a *property of the table* (`domains.owner_domain`), not the caller's connection. `require_owner_main` / `owner_write_target` route-or-SKIP (fail-closed). Single ownership source = `src/state/domains.py`, generated from `architecture/db_table_ownership.yaml`.

## Redundancy dispositions

1. **19 registry inversions → 0** (committed earlier): 13 world→trade converged to the data-grounded owner + 6 promoted. **Data-verified 2026-07-01** by row-probe: all 13 converged tables' data lives 100% on `zeus_trades.db` (market_price_history 622k, token_price_log 121k, decision_log 19k, provenance_envelope_events 23k, …); the world copies are 0-row `legacy_archived` ghosts.
2. **3 coherence gates greened** (commit 4277b6697, `tests/state/test_table_registry_coherence.py` 20→23):
   - **a1**: synced the legacy `_FORECAST_TABLES` witness constant (+4 real forecast tables) and reclassified `settlements(forecasts)` `forecast_class → legacy_archived` — the promote conflicted with B3cont (2026-05-28 dropped that shell) and would re-create it via init. Live settlement authority = `settlement_outcomes` (unchanged).
   - **a8**: allowlisted `substrate_observer_daemon.main()` — verified false-positive (sequential per-DB boot preflight, world_conn read-only, no two-connection cross-DB write).
   - **a4-manifest**: wired the 13 converged trade tables' exact live DDL (idempotent CREATE IF NOT EXISTS, 11 indexes + 4 immutability triggers) into `_TRADE_CLASS_DDL` + `_TRADE_CLASS_TABLES` + the test EXPECTED set — completing PR-S4b intent so `init_schema_trade_only` creates them (init==registry==data).
3. **0-byte naming-schism decoy DBs** (`zeus_world.db`, `zeus_forecasts.db`, `zeus-trades.db`, `zeus_live.db` — wrong separator vs the real hyphen `world`/`forecasts`, underscore `_trades`): **already neutralized** by the Owner-Routed Writes guard (rejects a conn rooted at a decoy). Not deleted — removing 0-byte files from the live `state/` dir is risk-without-benefit.
4. **legacy_archived bloat** (world 58 + trade 80 + forecasts 1): the empty world/forecasts ghost copies are on the **existing 90-day retention drop schedule (2026-08-09)**, tooled by `scripts/task_2026-06-09_drop_dead_tables.py`. No new action; the trade copies are the live tables and stay.

## Verification (commit 4277b6697)

- `test_table_registry_coherence` 23/23 green (was 20/23).
- `tests/state/` net 24→21 fails; the −3 are exactly a1/a4/a8. Remaining 21 (position-lots, idempotency, …) are **pre-existing, unrelated** to DB ownership.
- Owner-Routed mechanism 10/10; coherence hook rc=0; schema fingerprint unchanged (world-only); `test_world_only_tables_not_on_trade` 6/6.
- DDL block validated on `:memory:` incl. double-apply idempotency before insertion.
