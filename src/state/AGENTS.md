# src/state AGENTS — Zone K0 (Kernel)

## WHY this zone matters

State is the **truth and transition zone** — where lifecycle events are recorded, positions are projected, and the DB serves as canonical authority. This is the most drift-prone zone in the repo because every other module wants to write state "just this once."

The lifecycle manager is the **sole state authority** (INV-01). No other module may transition lifecycle states. The DB is canonical truth (INV-03) — JSON/CSV exports are derived, never promoted back.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `db.py` | SQLite connection, schema, canonical queries | CRITICAL — truth surface |
| `portfolio.py` | Position model + portfolio state | CRITICAL — runtime position truth |
| `portfolio_loader_policy.py` | Explicit DB-vs-fallback portfolio load policy | HIGH — truth source routing |
| `lifecycle_manager.py` | 9-state lifecycle FSM + `LEGAL_LIFECYCLE_FOLDS` | CRITICAL — INV-01 enforcer |
| `chain_reconciliation.py` | Chain > Chronicler > Portfolio (3 rules) | HIGH — truth reconciliation |
| `chronicler.py` | Append-only event log | HIGH — event spine |
| `ledger.py` | Event ledger — position_events + position_current projection | HIGH — event persistence |
| `projection.py` | Event → position_current projection logic + column definitions | HIGH — derived state |
| `decision_chain.py` | Decision logging — records what happened AND why things didn't happen | MEDIUM |
| `strategy_tracker.py` | Derived strategy attribution tracking (not runtime authority) | MEDIUM |
| `truth_files.py` | Mode-aware truth-file helpers + legacy-state deprecation tooling | LOW |

## Current reality (post-Phase 1)

- `position_events` is the canonical event spine
- `position_current` is the canonical projection surface
- **Live reads must come from canonical DB truth.** JSON fallback was eliminated in P4 (commit 1fc14ab). `load_portfolio` reads from DB projection only; JSON exports (`positions-live.json`) are write-only caches, never read back as authority.
- Settlement iteration queries `position_current` for authoritative phase (P6, commit 189912a) before processing
- DB is `zeus_trades.db` (live trade state) and `zeus-world.db` (weather/calibration data)
- `zeus_backtest.db` is derived diagnostic output only. It is for `wu_settlement_sweep` and `trade_history_audit` reports, carries `authority_scope='diagnostic_non_promotion'`, and must never become live trade or world-data authority.

## Domain rules

- `strategy_key` is the sole governance key (INV-02) — no fallback buckets
- Event append + projection update must be in one SQLite transaction (INV-08)
- Point-in-time truth beats hindsight truth (INV-06) — snapshots preserve decision-time state
- Missing data is first-class truth (INV-09)

## Common mistakes

- Promoting JSON exports (`positions.json`, `status_summary.json`) back to authority → INV-03 violation
- Creating new shadow persistence surfaces (another JSON file "just for debugging") → truth divergence
- Defaulting unknown strategy to a governance bucket → INV-09 violation
- Schema or truth-path changes without packet + rollback → architectural drift
- Bypassing `LEGAL_LIFECYCLE_FOLDS` with direct state assignment → INV-08 violation

## Forbidden

- Defaulting unknown strategy to a governance bucket
- Silent fallback to legacy settlement truth when canonical truth should exist
- Schema or truth-path changes without packet + rollback
