# F109 — Verification Log

**Branch**: `fix/f109-position-open-idempotency-2026-05-17`
**Date**: 2026-05-17
**DB tested**: `state/zeus_trades.db` mirrored into `:memory:` via `sqlite3.backup` (read-only on live; all mutations applied to the in-memory copy).

## 1. Antibody suite

```
$ python -m pytest tests/state/test_position_open_idempotency.py -v
... 17 passed in 0.25s
```

Coverage:
- Writer-side check: 5 tests (first insert, same-id upsert, duplicate raise, voided sibling, economically_closed sibling)
- Migration / UNIQUE INDEX: 4 tests (clean apply, refuse-on-dirty, INDEX catches race, allows void-then-reopen)
- Consolidator: 7 tests (noop, OVERBOOK, idempotent, DIVERGENT-no-chain, DIVERGENT-chain-matches, Karachi safety, consolidate_token scope)
- London replay end-to-end: 1 test

## 2. Targeted regression sweep

```
$ python -m pytest tests/test_canonical_position_current_schema_alignment.py \
                   tests/test_position_projection_d6_counters.py \
                   tests/runtime/test_legacy_snapshot_projection_upsert.py \
                   tests/test_provenance_5_projections.py -q
... 38 passed in 1.38s
```

## 3. Broad sweep — `tests/state/`

```
$ python -m pytest tests/state/ -q
... 74 passed, 4 skipped in 2.16s
```

Zero new failures vs baseline.

## 4. Karachi safety dry-run (live-DB mirror)

Procedure: copy `state/zeus_trades.db` into `:memory:` via `sqlite3.backup` (live DB opened `?mode=ro`); run `consolidate(mem)`; assert Karachi row unchanged.

```
Pre-state:
  Tokens with multiple open-phase rows: 1
  token=...733330054946: 2 rows (London 18°C 5/19)
  CHAIN snapshot used: True

Karachi pre:  ('c30f28a5-d4e', 'day0_window', 1.5873)
Karachi post: ('c30f28a5-d4e', 'day0_window', 1.5873)
Karachi safety: OK
```

Consolidator report:
```json
{
  "scanned_tokens": 1,
  "overbook_tokens": ["113959...054946"],
  "divergent_tokens": [],
  "voided_positions": ["0a0e3b72-46e"],
  "chain_snapshot_used": true
}
```

**Karachi `c30f28a5-d4e` is unaffected. Consolidator NO-OPs on single-row tokens (verified empirically).**

## 5. London 5/19 replay verification (live-DB mirror)

After consolidator run on the live-DB mirror:

```
position_id      phase                 shares
cee5fc85-3dd     voided                0.0
2d08b0ec-b2e     voided                0.0
3a6f0728-c50     economically_closed   5.0
7557a029-4ad     pending_exit          6.0   <-- surviving open-phase row
0a0e3b72-46e     voided                0.0   <-- voided by consolidator
```

DB sum (open phases) = 6.0; on-chain CTF balance = 6.0 (latest CHAIN snapshot). **DB now reconciles exactly with on-chain.** London settlement on 5/19 will use the single surviving row → no phantom $6 PnL.

## 6. Live-write safety

The dry-run uses `sqlite3.backup` into `:memory:`. The live DB file is opened `?mode=ro`. No production state was modified during verification. The PR ships:
1. Migration script (deployable via `python -m scripts.migrations 202605_position_current_idempotent_open_per_token`)
2. Consolidator code (importable; invoke `consolidate(conn)` from the daemon-boot hook or a one-shot operator script)
3. Writer-side check (active immediately on import of `src.state.projection`)
4. Antibody tests (CI gate)

Production replay is a deploy-time action, not part of this code PR.

## 7. Hard exclusions verified

- `src/execution/**` — untouched (`git diff --stat origin/main -- 'src/execution/**'` → empty)
- `src/venue/**` — untouched
- Daemons — not restarted
- Operator-void script — NOT created

## 8. Deploy order reminder

The migration `202605_position_current_idempotent_open_per_token` REFUSES to apply if any token still holds >1 open-phase row (raises `RuntimeError`). Operator must run the consolidator FIRST, then the migration. This is enforced in code, not just docs.
