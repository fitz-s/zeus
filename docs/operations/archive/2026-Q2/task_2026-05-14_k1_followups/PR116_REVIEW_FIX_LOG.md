# PR #116 Review Fix Log
**Date:** 2026-05-14
**PR:** https://github.com/fitz-s/zeus/pull/116
**Branch tip before fixes:** `79c644c7ec`
**Branch tip after fixes:** `77fe3a9d0a`

---

## Comments Fetched

- Inline review comments: 11 (Copilot bot)
- Suppressed low-confidence: 2 (not shown as threads, addressed via doc fixes)
- Reviews: 2 (Codex — no suggestions; Copilot — 11 inline threads)

---

## Classification Table

| Thread ID | File:Line | Class | Action | Commit |
|-----------|-----------|-------|--------|--------|
| PRRT_kwDOR0ZtZc6CRZT_ | hole_scanner.py:308 | BUG | Fixed: pass forecasts_conn to HoleScanner in ingest_main._k2_hole_scanner_tick | 5fccf6fdbe |
| PRRT_kwDOR0ZtZc6CRZUB | drop_world_ghost_tables.py:90 | BUG | Fixed: replace invalid sentinels (venue_states/market_opportunities/strategy_tracker_current) with real WORLD_CLASS tables (data_coverage, job_run, zeus_meta) | 5fccf6fdbe |
| PRRT_kwDOR0ZtZc6CRaQA | main.py:761 | STYLE_NIT | Fixed: reword comment — "boot wiring deferred, not applied" | 77fe3a9d0a |
| PRRT_kwDOR0ZtZc6CRaQX | hole_scanner.py:392 | BUG | Fixed: narrow `except Exception` → `except sqlite3.OperationalError` | 5fccf6fdbe |
| PRRT_kwDOR0ZtZc6CRaQn | test_live_safety_invariants.py:89 | BUG | Fixed: assert `.upper() != "VERIFIED"` guard pattern instead of weak substring | 5fccf6fdbe |
| PRRT_kwDOR0ZtZc6CRaQ7 | db.py:819 | STYLE_NIT | Fixed: docstring now describes world-class + legacy_archived ghost copies accurately | 77fe3a9d0a |
| PRRT_kwDOR0ZtZc6CRaRR | test_p2_byte_equivalence.py:83 | MISUNDERSTANDING | Fixed docstring: ghost copies by design (not a violation); legacy_archived is expected per architecture/db_table_ownership.yaml | 77fe3a9d0a |
| PRRT_kwDOR0ZtZc6CRaRp | connection_pair.py:195 | BUG | Fixed: remove stale "world_view/ scheduled for retirement in P3" — world_view already retired | 5fccf6fdbe |
| PRRT_kwDOR0ZtZc6CRaRz | check_writer_signature_typing.py:43 | STYLE_NIT | Fixed: comment now lists actual patterns (no _create/_update) | 77fe3a9d0a |
| PRRT_kwDOR0ZtZc6CRaSD | check_table_registry_coherence.py:8 | STYLE_NIT | Fixed: replace retired manifest reference with db_table_ownership.yaml | 77fe3a9d0a |
| PRRT_kwDOR0ZtZc6CRaSO | drop_world_ghost_tables.py:262 | OUT_OF_SCOPE | Fixed: added Next Steps warning about init_schema recreation; architectural fix (removing ghost CREATEs from db.py) deferred to future phase | 77fe3a9d0a |

**Suppressed low-confidence threads (not shown as inline threads):**
- db.py:2792 — same recreation concern as PRRT_kwDOR0ZtZc6CRaSO; addressed by Next Steps doc note (OUT_OF_SCOPE)
- test_p2_byte_equivalence.py:171 — `test_v2_forecast_tables_not_created_by_world_init` vacuous concern (MISUNDERSTANDING — test is intentionally vacuous with docstring; LEGACY_ARCHIVED is expected design)

---

## Commits Added

| SHA | Scope |
|-----|-------|
| `5fccf6fdbe` | fix(pr116): bugs — sentinels, hole_scanner forecasts_conn, VERIFIED guard, stale world_view ref, A8 allowlist update |
| `77fe3a9d0a` | docs(pr116): stale/misleading comments — main.py, db.py, byte-equiv test, writer patterns, registry coherence script, drop ghost Next Steps |

---

## pytest Results

After commit A (5fccf6fdbe): 161 passed, 4 skipped, 1 deselected (pre-existing failure)
After commit B (77fe3a9d0a): 161 passed, 4 skipped, 1 deselected (pre-existing failure)

Pre-existing deselected: `tests/test_no_raw_world_attach.py::TestNoRawWorldAttach::test_no_get_trade_connection_with_world_in_trading_lane`

---

## Threads Resolved

All 11 threads resolved via GraphQL `resolveReviewThread`.

---

## Push

SUCCESS. New tip: `77fe3a9d0a87b2ce98588b78636d1189b53714fb`

---

## STOP-Flagged Items

None. No antibody weakening, no settlement/execution defect, no architectural revert suggested.

Notable: Thread PRRT_kwDOR0ZtZc6CRaQn (VERIFIED guard) touched `test_live_safety_invariants.py` — this is a Tier 0 settlement-adjacent surface. The fix STRENGTHENED the guard (specific pattern match vs substring), not weakened it. No escalation required.

---

## Bundle: TIGGE bridge cherry-pick

**Date:** 2026-05-14
**Cherry-pick SHA:** `36f4313883` (orig `cd93c1bdfd` from `fix/calibration-tigge-opendata-bridge-2026-05-11`)
**Files changed:** 2 (`src/calibration/manager.py` +268/-17 LOC, one other)
**Push tip:** `36f4313883776cdea6f4ff5ba69f66d76c53ceaa`

**Cherry-pick:** SUCCESS (no conflicts — K1 P1/P4 did not touch manager.py)

**pytest results:**
- tests/test_calibration_manager.py + tests/state/: 72 passed, 4 skipped
- tests/test_no_raw_world_attach.py + test_table_registry_coherence.py: 18 passed, 1 deselected (pre-existing)

**PR description updated:** YES — appended "## Bundled (post-open additions)" section with TIGGE bridge scope, CI gitleaks note, and reviewer focus addendum citing `_low_purity_doctrine_2026_05_07`.

**New auto-review triggered:** YES (CI checks in pending state post-push: gitleaks, replay-correctness-gate, pr-loc-budget, check).

**STOP-flagged:** None.
