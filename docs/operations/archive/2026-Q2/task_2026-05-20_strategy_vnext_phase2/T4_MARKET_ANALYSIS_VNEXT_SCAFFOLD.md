# T4 MarketAnalysisVNext — SCAFFOLD Design Doc

**Authority**: PHASE_2_ULTRAPLAN.md v3.1 §7, sha `00c2399742`
**Branch**: `feat/phase2-t4-market-analysis-vnext-20260520`
**Base**: origin/main @ `9b63ec1cc8`
**Status**: PRODUCTION — shipped in PR #238 (2026-05-21)
**Created**: 2026-05-20
**Production merge**: 2026-05-21

---

## §1 Problem Statement

Two independent findings per §7.1 (grep-verified):

**G4 PASS** — `market_end_anchor_source()` at `src/strategy/market_phase.py:252` has zero
production callers. Its docstring (lines 262-265) states: "deferred to Phase 1
settlement-chain hardening. The DB column defaults to 'gamma_explicit' for all Phase 0 rows."
The function was introduced but never wired. Phase 2 T4 completes that original purpose.

**G5 PASS** — `polymarket_end_anchor_source` column has 10+ writers. Key callers:
- `src/contracts/execution_intent.py:676` (line re-derived via `git show origin/main` grep) —
  `_context_text(context.get("polymarket_end_anchor_source"))` passes through static context
  value; callers that don't populate this key get `''` → stored as empty or 'unknown_legacy'.
- `src/state/db.py:846` default: `'unknown_legacy'` for legacy rows.
- `src/state/db.py:1333` CHECK constraint.

The `market_end_anchor_source()` function computes the correct value
(`'gamma_explicit'` when market dict has `market_end_at`/`endDate`/`end_date`, else
`'f1_12z_fallback'`). T4 wires it to replace the static passthrough.

Separate scope: `spread_observed_window_ms` field was deferred from PR 2 path-a
(verify comment at `src/state/snapshot_repo.py:78`). T4 lands the windowed observer
as part of `MarketAnalysisVNext`.

---

## §2 Surface (6 Artifacts)

| Artifact | Path | Status |
|---|---|---|
| Analytics class | `src/analysis/market_analysis_vnext.py` (new) | SCAFFOLD created |
| Anchor-source wire | `src/contracts/execution_intent.py:~676` | SCAFFOLD comment + TYPE_CHECKING import added |
| Antibody A | `tests/test_inv_vnext_substitution_consistency.py` | xfail created (strict=True) |
| Antibody B | `tests/test_inv_bin_grid_propagation.py` | xfail created (strict=True) |
| Antibody C | `tests/test_inv_anchor_source_real_value.py` | xfail created (strict=True) |
| Design doc | this file | SCAFFOLD created |

**Deferred to production pass** (SCAFFOLD does NOT implement):
- `market_microstructure_snapshots` table DDL on forecasts DB
- `SCHEMA_FORECASTS_VERSION 4 → 5` bump in `src/state/db.py`
- `day0_nowcast_runs ADD COLUMN bin_grid_id TEXT` ALTER
- `MarketAnalysisVNext.compute()` production body
- Thread `market` dict to execution_intent.py call site
- `spread_observed_window_ms` field on `ExecutableMarketSnapshotV2` (deferred from PR 2)

---

## §3 Wiring Plan (production pass)

### 3.1 market_end_anchor_source wire-up

**Target file**: `src/contracts/execution_intent.py`

**Current state** (origin/main:execution_intent.py line re-derived):
```
# line ~676 (re-derived: git show origin/main:src/contracts/execution_intent.py | grep -n "polymarket_end_anchor_source" → line 673 in from_forecast_context)
polymarket_end_anchor_source=_context_text(context.get("polymarket_end_anchor_source")),
```

**Target state** (production pass):
```python
from src.strategy.market_phase import market_end_anchor_source
# ... in from_forecast_context():
polymarket_end_anchor_source=market_end_anchor_source(market) if market else _context_text(context.get("polymarket_end_anchor_source")),
```

Constraint: `market` dict must be threaded to `from_forecast_context()` as an
optional parameter (default `None` for backward compat with callers that don't
have access to the raw market dict). This requires updating all call sites.

### 3.2 MarketAnalysisVNext.compute() production body

1. Derive `spread_observed_window_ms` from `history` rows using the
   windowed observer (window size TBD at production pass).
2. Call `market_end_anchor_source(market)` → `polymarket_end_anchor_source`.
3. Propagate `bin_grid_id` from `ensemble_snapshots_v2` row at evaluator caller site
   (NOT from `cycle_runtime.bins` — no propagation path per Phase 1 T2 finding).
4. Write `MicrostructureMetrics` row to `market_microstructure_snapshots`
   (forecasts DB) via caller-provided conn (INV-37).

### 3.3 F4 bin_grid_id retrofit

**ALTER TABLE** (forecasts DB, SCHEMA_FORECASTS_VERSION 5):
```sql
ALTER TABLE day0_nowcast_runs ADD COLUMN bin_grid_id TEXT;
ALTER TABLE day0_nowcast_runs ADD COLUMN bin_schema_version TEXT;
```

**Propagation path**: `ensemble_snapshots_v2.bin_grid_id` → evaluator caller site
→ `day0_nowcast_runs.bin_grid_id`. Python guard: `NOT NULL` enforced by writer
(the column is nullable in DDL per §13 default decision for existing 0-row state).

### 3.4 market_microstructure_snapshots DDL (forecasts DB)

```sql
CREATE TABLE IF NOT EXISTS market_microstructure_snapshots (
    snapshot_id          TEXT NOT NULL,
    event_slug           TEXT NOT NULL,
    condition_id         TEXT NOT NULL,
    captured_at          TEXT NOT NULL,
    wide_spread          INTEGER NOT NULL CHECK (wide_spread IN (0, 1)),
    spread_window_ms     INTEGER,          -- NULL if spread_observed_window_ms not yet populated
    depth_at_best_ask    INTEGER NOT NULL,
    anchor_source        TEXT NOT NULL
        CHECK (anchor_source IN ('gamma_explicit', 'f1_12z_fallback', 'unknown_legacy', '')),
    bin_grid_id          TEXT,
    bin_schema_version   TEXT,
    schema_version       INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id)
);
```

INV-37 honored: all writes single-DB (forecasts); no ATTACH needed.

---

## §4 Production Checklist

- [ ] `SCHEMA_FORECASTS_VERSION 4 → 5` in `src/state/db.py`
- [ ] CREATE TABLE `market_microstructure_snapshots` on forecasts DB
- [ ] ALTER TABLE `day0_nowcast_runs` ADD COLUMN `bin_grid_id TEXT`
- [ ] ALTER TABLE `day0_nowcast_runs` ADD COLUMN `bin_schema_version TEXT`
- [ ] `MarketAnalysisVNext.compute()` production body implemented
- [ ] `execution_intent.py` `from_forecast_context()` updated: thread `market` dict,
      replace static `context.get("polymarket_end_anchor_source")` with `market_end_anchor_source(market)`
- [ ] `spread_observed_window_ms` field added to `ExecutableMarketSnapshotV2`
      (deferred from PR 2 path-a; verify snapshot_repo.py:78 defer comment still present)
- [ ] `T4_MERGE_DATE` constant updated in `src/analysis/market_analysis_vnext.py`
      (use `git log --format=%cI -1 origin/main` after PR-T4 merge)
- [ ] `db_table_ownership.yaml` `market_microstructure_snapshots` entry finalized
- [ ] `source_rationale.yaml` entries for new files finalized
- [ ] All three antibodies transition XFAIL → PASS
- [ ] LOC budget check: target ~600 (§7.6); if >800, split per §13 default decision

---

## §5 Grep-Verify Checklist (SCAFFOLD author)

All citations below verified against `git show origin/main:<path>` (not working tree).

| # | Claim | Command | Result |
|---|---|---|---|
| A | `market_end_anchor_source()` at market_phase.py:252 | `git show origin/main:src/strategy/market_phase.py \| grep -n "def market_end_anchor_source"` | line 252 confirmed |
| B | `polymarket_end_anchor_source` writer at execution_intent.py line ~673 | `git show origin/main:src/contracts/execution_intent.py \| grep -n "polymarket_end_anchor_source"` | line 673 confirmed (in from_forecast_context) |
| C | `SCHEMA_FORECASTS_VERSION: int = 4` in db.py | `git show origin/main:src/state/db.py \| grep -n "SCHEMA_FORECASTS_VERSION: int"` | line 2527 confirmed, value=4 |
| D | `day0_nowcast_runs` schema comment: "bin_grid_id deferred to Phase 2" | `git show origin/main:src/state/db.py \| grep -n "bin_grid_id deferred"` | line 3066 confirmed |
| E | `snapshot_repo.py:78` spread_observed_window_ms defer comment | `git show origin/main:src/state/snapshot_repo.py \| grep -n "spread_observed_window_ms"` | line 78 confirmed |
| F | `src/analysis/` dir exists on origin/main | `git show origin/main:src/analysis/ \| head -1` | tree listing confirmed |
| G | No existing `market_analysis_vnext.py` on origin/main | `git show origin/main:src/analysis/market_analysis_vnext.py 2>&1` | object not found (correct — new file) |
| H | `market_end_anchor_source()` has zero production callers | `git show origin/main:src/strategy/market_phase.py \| grep -n "market_end_anchor_source"` | only definition at line 252; no callers in src/ |
