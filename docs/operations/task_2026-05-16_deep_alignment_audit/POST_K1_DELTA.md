# POST-K1 DELTA — finding verdicts after merging main @ `a924766c8a`

**Date**: 2026-05-16
**Audit branch HEAD before merge**: `64cd9b93a4`
**Main commits merged in (6, non-merge)**: `584cf92432` → `37b0dd5993`
**Why**: K1 DB-split fixes (harvester writer authority, forecasts-only readers, boot authority cleanup) potentially silently resolved or downgraded several open findings. Re-verify before writing the fix-plan PR series.

## Spot-verification results

| # | Pre-K1 verdict | Post-K1 verdict | Evidence | Action delta |
|---|---|---|---|---|
| **F1** | STILL-OPEN — `assert_db_matches_registry()` unwired at boot | **STILL-OPEN, but reframe** | `src/main.py:860-862`: `validate_world_schema_at_boot RETIRED in P2 (K1 followups §5.5/D6)` + `boot wiring is deferred — not called here`. K1 followups `P2_IMPLEMENTATION_REPORT.md` §D6: `Replaced by a comment noting P3 will wire assert_db_matches_registry`. | Reframe PR-E F1 portion as "execute K1 P3 promised wiring" (not new design). Reuse K1 followups P3 plan as authority basis. |
| **F4 writer** | RESOLVED via PR #121 | **CONFIRMED RESOLVED** | `src/ingest/harvester_truth_writer.py:395` docstring: `Writes ONLY to forecasts_conn (settlements, settlements_v2, market_events_v2).` | No action — keep PR-A scope as residue purge only. |
| **F4 residue** | 2,112 stranded rows on `world.market_events_v2` | **CONFIRMED 2,112 rows** (unchanged) | `sqlite3 state/zeus-world.db 'SELECT COUNT(*) FROM market_events_v2'` → `2112`. `world.settlements` rows = `0` (no shadow-settlement issue, only market_events_v2). | Single-table cleanup migration. |
| **F5** | SEV-1 live-blocking lock storm | **SEV-2 latent** — symptom gone, code defect persists | `tail -1000 logs/zeus-ingest.err \| grep -c "database is locked"` = `0`. Last 3 occurrences: 2026-05-14 06:06 / 06:36 / 07:06 — right when K1 5b/5c landed and moved harvester writes to forecasts.db. **However** `src/state/collateral_ledger.py:164-166` still opens raw `sqlite3.connect(str(db_path), check_same_thread=False)` — no WAL pragma, no busy_timeout. K1 removed the contention, not the vulnerable code path. | Downgrade urgency (not Karachi-blocking). Keep PR-B fix (route through `get_trade_connection()`) as latent-defect cleanup. |
| **F7** | STILL-OPEN — missing `tag=weather` filter + bounded retry | **PARTIAL** — implicit weather filter via city-matching; retry exists in caller; explicit `tag=weather` still missing | `src/ingest/harvester_truth_writer.py:682` city-matching path; bounded retry observed in caller code. | Reduce PR-C scope to: (a) explicit `tag=weather` filter at category source + (b) regression test asserting non-weather markets are filtered. |
| **F15** | STILL-OPEN — settlements (5582) vs settlements_v2 (3999) asymmetric, reader uses `settlements` | **CONFIRMED unchanged** | `src/execution/harvester_pnl_resolver.py:76` reads `FROM settlements`. Row counts unchanged. | No action change; PR-J still needed. |

## Findings NOT spot-verified (assumed unchanged from FINDINGS_REFERENCE.md)

F2, F3, F6, F8, F9 (false-positive), F10, F11, F12, F13, F14, F16, F17, F18, F19, F20.

Rationale: none of the 6 K1 commits touch the affected surfaces. If FIX_PLAN drafting surfaces doubt about any, re-verify before publishing the plan.

## Net deltas to PR groupings in FINDINGS_REFERENCE.md

- **PR-A** unchanged in scope (F2 + F4 residue) — but residue-purge migration is the ONLY F4 work left; writer fix already on main.
- **PR-B** F5 downgrade SEV-1 → SEV-2; still ships, no longer Karachi-blocking.
- **PR-C** F7 scope reduced to explicit tag filter + regression test (retry path already present).
- **PR-E** F1 fix-spec changes: reframe as "land K1 P3 promised wiring", anchor on K1 followups P3 plan.
- **All other PRs (D, F, G, H, I, J, K, L, M)**: no scope change.

## Karachi 5/17 implication

**Only Karachi-blocking finding remains F14 (PR-I)** — `submit_redeem` cascade halt has zero production callers. Manual fallback (KARACHI_2026_05_17_MANUAL_FALLBACK.md) is still authoritative if PR-I cannot land before T-0.

F5 downgrade means the ingest-slowness vector pre-Karachi is no longer a concern; settlement pull won't be starved.
