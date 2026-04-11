# src/state AGENTS — Zone K0 (Kernel)

## WHY this zone matters

State is the **truth and transition zone** — where lifecycle events are recorded, positions are projected, and the DB serves as canonical authority. This is the most drift-prone zone in the repo because every other module wants to write state "just this once."

The lifecycle manager is the **sole state authority** (INV-01). No other module may transition lifecycle states. The DB is canonical truth (INV-03) — JSON/CSV exports are derived, never promoted back.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `db.py` | SQLite connection, schema, canonical queries | CRITICAL — truth surface |
| `portfolio.py` | Position model + portfolio state | CRITICAL — runtime position truth |
| `lifecycle_manager.py` | 9-state lifecycle FSM + `LEGAL_LIFECYCLE_FOLDS` | CRITICAL — INV-01 enforcer |
| `chain_reconciliation.py` | Chain > Chronicler > Portfolio (3 rules) | HIGH — truth reconciliation |
| `chronicler.py` | Append-only event log | HIGH — event spine |
| `ledger.py` | Event ledger — position_events + position_current projection | HIGH — event persistence |
| `projection.py` | Event → position_current projection logic + column definitions | HIGH — derived state |
| `decision_chain.py` | Decision logging — records what happened AND why things didn't happen | MEDIUM |
| `strategy_tracker.py` | Derived strategy attribution tracking (not runtime authority) | MEDIUM |
| `truth_files.py` | Mode-aware truth-file helpers + legacy-state deprecation tooling | LOW |

## Current reality

- `position_events` is a real event spine
- Open-position truth is still mixed (DB + JSON coexist transitionally)
- JSON/state-object surfaces still exist as transitional runtime reality
- `position_current` is target-state, not current-state

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
