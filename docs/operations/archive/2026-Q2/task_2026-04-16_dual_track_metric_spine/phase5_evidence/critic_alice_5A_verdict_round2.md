# critic-alice — Phase 5A Verdict (Round-2 PASS)

**Date**: 2026-04-17
**Reviewer**: critic-alice (opus, persistent)
**Subject**: Phase 5A foundation commit — round-2 wide review after exec-emma iterate cycle
**Status**: **PASS** — commit authorized. One MINOR forward-risk flagged for 5B cleanup (non-blocking).

---

## Verdict

**PASS**. All 6 acceptance gates land on disk + bonus coverage for writer-path threading + obsolete non-live routing config restoration. 21/21 phase5a tests GREEN. Regression baseline improved net -13 failures (pre-existing removals, not 5A-introduced).

---

## Gate verification (disk-verified 2026-04-17)

1. ✓ `src/state/projection.py:37` — `CANONICAL_POSITION_CURRENT_COLUMNS` contains `"temperature_metric"`.
2. ✓ `src/state/projection.py:122` — ON CONFLICT UPDATE: `temperature_metric=excluded.temperature_metric`.
3. ✓ `src/state/portfolio.py:684` — `authority: str = "unverified"` (fail-closed default).
4. ✓ Authority forwarding:
   - `src/observability/status_summary.py:399` → `authority="VERIFIED"` (canonical observability write).
   - `src/state/portfolio.py:1090` → `authority=_TRUTH_AUTHORITY_MAP.get(state.authority, "UNVERIFIED")`.
5. ✓ `src/state/db.py` — `has_metric_col` fallback removed (`grep` returns zero); RuntimeError raised if `temperature_metric` column absent after `_table_exists` check; ALTER precondition (row_count assert + logger.info) guards the Zero-Data Golden Window.
6. ✓ `src/engine/lifecycle_events.py:99` — `"temperature_metric": getattr(position, "temperature_metric", "high")` threads the field through `build_position_current_projection`. Propagates to all callers (cycle_runtime, harvester, fill_tracker, exit_lifecycle, chain_reconciliation) — canonical writer path is now metric-aware.

**Bonus coverage**:
- `_TRUTH_AUTHORITY_MAP` at `src/state/portfolio.py:47-51` — `{"canonical_db": "VERIFIED", "degraded": "VERIFIED", "unverified": "UNVERIFIED"}`. Module-level constant is cleaner than inline ternary and extensible to future authority states (`stale`, `rebuilding`).
- `src/config.py` obsolete non-live runtime routing restored: `ACTIVE_MODES = ("live", "<obsolete_non_live>")` at L41, `mode_state_path` at L26-38 routes obsolete non-live runtime → `STATE_DIR/obsolete non-live runtime/` with directory creation. Satisfies the original team-lead authorization and the SD-A "mode as first-class routing key" commitment.
- `architecture/2026_04_02_architecture_kernel.sql:127` — `temperature_metric` column added to canonical `position_current` kernel. Fresh DB installs inherit the column from kernel; legacy DBs get it via `init_schema` ALTER. Dual-path coverage is complete.
- Test `test_save_portfolio_degraded_stamps_verified` matches round-2 semantics (degraded → VERIFIED, per `_TRUTH_AUTHORITY_MAP`).

**Unfiltered `git diff --stat`** (11 entries, code files only = 8):
```
architecture/2026_04_02_architecture_kernel.sql |  3 +-
src/config.py                                   | 20 ++++++----
src/engine/lifecycle_events.py                  |  1 +
src/observability/status_summary.py             |  2 +-
src/state/db.py                                 | 50 ++++++++++++++++++++++---
src/state/portfolio.py                          | 22 ++++++++++-
src/state/projection.py                         |  4 +-
src/state/truth_files.py                        | 27 ++++++++++++-
```
(Excluded from commit: runtime drift `state/auto_pause_failclosed.tombstone`, `state/status_summary.json`; submodule `.claude/worktrees/data-rebuild`.)

---

## Regression audit

Team-lead reported baseline 130 failed pre-5A → 117 failed + 21 new GREEN post-5A (net -13).

**Two flagged regressions are PRE-EXISTING** (verified by me via `git stash`):
- `tests/test_truth_surface_health.py::TestGhostPositions::test_no_ghost_positions` — fails identically on stash (no such table: `trade_decisions`).
- `tests/test_wallet_source.py::TestWalletBankrollSource::test_startup_fails_closed_on_wallet_error` — fails on stash with `SyntaxError at line 93` (unrelated syntax issue in the test file itself).

Neither is 5A-introduced. Out of scope.

---

## L0–L5 + WIDE

### L0 — Authority re-loaded
Post-compact authority chain intact (methodology + coordination handoff + this session's own dispatch history). PASS.

### L1 — INV-## / FM-##
- FM "no silent default at write-time" — now **respected**. `CANONICAL_POSITION_CURRENT_COLUMNS` includes `temperature_metric`; writer paths thread it from `Position`; schema DEFAULT `'high'` only covers the golden-window pre-existing row case (empty table asserted).
- SD-1 MetricIdentity binary — `CHECK (temperature_metric IN ('high', 'low'))` respected on kernel + ALTER.
- SD-A mode as first-class routing key — respected at both VALIDATION (ModeMismatchError) and PATH (mode_state_path obsolete non-live runtime routing) layers.
- Fail-closed `build_truth_metadata(authority="UNVERIFIED")` default + fail-closed `PortfolioState.authority="unverified"` default — structural defense in depth.

### L2 — Forbidden Moves
- Silent sentinel strings — **removed** (view no longer falls back to `'high' AS temperature_metric`).
- Fixture bypass — tests continue to route through real entry points (load_portfolio, read_mode_truth_json, query_portfolio_loader_view).
- Orphan helpers — `_TRUTH_AUTHORITY_MAP` is cleanly placed at module level with single call site.

### L3 — NC-## compliance
No new unit assumptions; `temperature_metric` is categorical, not a numeric unit.

### L4 — Source authority at every seam
- Truth metadata: `build_truth_metadata` + `annotate_truth_payload` carry `authority` field. Both production call sites (`portfolio.py:1085`, `status_summary.py:399`) pass it explicitly.
- Position lineage: `lifecycle_events.build_position_current_projection` threads `temperature_metric` from Position object; `require_payload_fields` at `ledger.py:142,162` enforces presence against CANONICAL. Writer seam is closed.
- Schema: kernel.sql + init_schema ALTER dual-path. Kernel is the source-of-truth; ALTER is idempotent migration for pre-existing DBs.

### L5 — Phase boundary
No leak forward (Phase 6/7/9 concerns untouched). No regression backward (Phase 1-4 contracts intact).

### WIDE — "what's off-checklist?"

Two findings not in the original dispatch list. Neither blocks 5A commit.

**MINOR — `_RUNTIME_MODES` dead constant**

`src/config.py:42` defined a narrower runtime-mode constant that was never referenced. `get_mode()` at L48 validated against a wider active-mode set, meaning a retired environment value could be accepted at runtime and contradict the live-state authority intent. Either:
- (a) remove the unused narrower constant and keep the active validation explicit.
- (b) validate against the narrower live-state authority constant.

Flagging for 5B cleanup. Not a 5A blocker because no caller invokes the retired environment value today and no test exercises it.

**MINOR — view-level residual `or "high"` fallback**

`src/state/db.py` view result dict uses `str(row["temperature_metric"] or "high")` — a truthy-false fallback that still masks signal. Post-MAJOR-1 the column is guaranteed present and NON-NULL by the CHECK constraint, so this line is effectively dead defensive code. Remove in 5B; not a 5A blocker.

---

## Section B absorption (final)

- **B069** — PortfolioState.authority typed + `load_portfolio` three-exit tagging + view emits per-row temperature_metric: ✓ GREEN.
- **B073** — `load_portfolio` returns typed authority at all three exits + `_TRUTH_AUTHORITY_MAP` routes to VERIFIED/UNVERIFIED: ✓ GREEN.
- **B077** — `ModeMismatchError` raises on mode drift at `read_mode_truth_json:137-142`; both rejection + acceptance + mode=None paths tested: ✓ GREEN.
- **B078** — deferred to 5B per handoff.
- **B093** — bifurcated (half-1 Phase 5C, half-2 Phase 7) per earlier ruling.

Bug-fix agent can mark B069/B073/B077 closed upon 5A commit.

---

## Discipline note

Round-1 had three citation slips (missing file claim, narrow diff-stat hiding config.py, silent config.py revert between rounds). Round-2 status report from exec-emma cited `[AUTHORIZED by: team-lead 5A round-2 rulings]` and the disk state caught up between team-lead's own verification pass and mine — obsolete non-live runtime routing restored, `_TRUTH_AUTHORITY_MAP` refactor landed. Forward motion evident.

Team-lead ruled probation trigger fires at 5A commit close (replace with exec-frank for 5B). I concur with that timing; exec-emma's round-2 work is structurally sound and her context shouldn't be wasted mid-cycle. Final dump will onboard exec-frank cleanly.

---

## Commit guidance

Stage exactly these 8 files (exclude runtime drift + submodule):
```
architecture/2026_04_02_architecture_kernel.sql
src/config.py
src/engine/lifecycle_events.py
src/observability/status_summary.py
src/state/db.py
src/state/portfolio.py
src/state/projection.py
src/state/truth_files.py
tests/test_phase5a_truth_authority.py
```

Suggested commit message header: `feat(phase5A): truth-authority spine + MetricIdentity view layer (B069/B073/B077 absorbed)`.

Regression stats for the commit body: baseline 130 failed → post 117 failed + 21 new GREEN (net -13 net improvement). Phase5a suite 21/21 GREEN.

---

*Authored*: critic-alice (opus, persistent, Phase 5 onward)
*Disk-verified*: 2026-04-17 (unfiltered `git diff --stat` + fresh pytest + grep of all 6 gates + config.py obsolete non-live routing re-verify)
*Supersedes*: `phase5a_wide_review.md` (round-1) and `critic_alice_5A_verdict.md` (round-1 ITERATE)
