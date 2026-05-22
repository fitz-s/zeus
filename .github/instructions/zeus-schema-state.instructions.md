---
applyTo: "src/state/**/*.py,src/state/schema/**/*.py,scripts/migrate_*.py,scripts/migrations/**/*.py,architecture/db_table_ownership.yaml"
---

# Zeus schema + state review

Schema and state are the persistence layer for live positions, orders,
and settlement facts. Errors here produce silent data loss or
irreversible corruption.

## DB split (K1)

Zeus has three DBs: zeus-world (positions, lifecycle), zeus-forecasts
(signals, calibration), zeus-trades (venue commands, order facts).
Cross-DB writes must use ATTACH + SAVEPOINT (INV-37). Any new code
that opens independent connections to two DBs in the same function
is a Critical violation — sqlite3 cannot guarantee atomicity across
independent connections.

## Schema version and migration

`src/state/db.py` carries the canonical schema version number.
A schema change without bumping the version number, or a version bump
without a corresponding migration script, is Important.

Migration scripts must be idempotent (re-runnable without error) and
forward-only. Migrations that DROP columns without a prior deprecation
cycle are Important. Check: does the migration guard on column existence
before ALTER?

## Table ownership

`architecture/db_table_ownership.yaml` is the registry. A new table or
column not registered there is Important. Check that protected tables
(positions, lifecycle_events, venue_commands, venue_order_facts,
venue_trade_facts, settlement_commands) are not mutated outside their
declared owner module.

## Python sqlite3 `with conn:` footgun

`with conn:` in Python sqlite3 commits or rolls back a transaction but
does NOT close the connection. Nested `with conn:` inside a SAVEPOINT
commits and releases the SAVEPOINT, losing atomicity for the outer
transaction. Flag any nested `with conn:` where the outer context is
already in a SAVEPOINT.

## Projection and truth direction

DB is canonical truth. Derived JSON (position cache, state snapshots)
must never be written before the DB commit that produces them (INV-17).
Any code that writes JSON first and DB second is Critical.

`authority="VERIFIED"` must not be assigned to a projection row that
was built from degraded or incomplete facts (INV-23).
