# Phase 5B → 5C Scout Learnings

# Lifecycle: phase5_evidence/phase5b_to_phase5c_scout_finn_learnings.md
# Purpose: Value extraction before team retirement — latent issues, forward hazards, scout inheritance notes.
# Reuse: Fresh 5C scout reads §3-4 first. Team-lead reads §1-2.

Author: scout-finn (sonnet, retiring)
Date: 2026-04-17
Covers: Phase 5 (5A + 5B) reconnaissance across src/, scripts/, tests/, zeus_dual_track_refactor_package_v2_2026-04-16/

---

## 1. Latent system issues — things I saw but didn't flag

**Dead code: `src/data/wu_daily_collector.py`** — scout-dave flagged this in Phase 4. Still wired at `src/main.py:73` via a `try/except ImportError` guard (lazy import, swallowed on failure). The successor `daily_obs_append.py` is the real live path (`main.py:92`, `main.py:196`). `wu_daily_collector.py` remains on disk and importable. Risk: a future agent sees it imported in `main.py` and treats it as canonical. Verdict: `DEAD_DELETE` — safe to remove once the lazy-import guard at `main.py:73-82` is cleaned too.

**Dead code: `_extract_causality_status` in `scripts/ingest_grib_to_snapshots.py:107`** — defined but never called. The 5B ingest contract wires causality through `validate_snapshot_contract()` in `src/contracts/ingest_snapshot_contract.py`, not through this helper. The team_lead_handoff.md §5B-follow-up backlog item confirms this. Confirmed dead by grep: only definition, zero call sites.

**Misleading function name: `p_raw_vector_from_maxes` in `src/signal/ensemble_signal.py:166`** — used for both high AND low tracks. For low-track member values these are per-member daily minimums, not maximums. The function name lies to any reader encountering it in the low-track context. Scout-dave flagged this as "misleading but math path is the same." A comment exists in `rebuild_calibration_pairs_v2.py` (line ~224). Risk: Phase 7 or a fresh executor edits the function thinking it is exclusively a max operation. Forward fix: rename to `p_raw_vector_from_member_extrema` or add a prominent module-level note. Low priority but should ride a Phase 7 naming pass.

**Misleading function name: `remaining_member_maxes_for_day0` in `src/signal/day0_window.py:21`** — already handles low-track (`is_low()` branch at L67 returns `slice_data.min(axis=1)`), but the name says "maxes". Any Phase 6 executor touching the Day0 split will be confused whether to use this function or write a new one. The comment at L32 documents the dual behavior, but the name is wrong. Phase 6 split will need to either rename this or fork it — flag the decision now.

**Phase-6 TODO marker as live code: `evaluator.py:825`** — `member_mins_remaining=remaining_member_extrema` passes the MAX array as the MIN input. This is annotated with a comment (`# Phase-6 TODO marker`) but is live code that will execute silently for any low-track Day0 candidate that makes it past the `NotImplementedError` guard in `Day0Signal.__init__`. Currently safe because `Day0Signal` raises `NotImplementedError` on `is_low()` at line 86. But if that guard is ever removed without the Phase 6 split being complete, low-track Day0 candidates will silently use max values for their min computation. This is a category-B death trap: the guard and the fix are decoupled. Phase 6 executor must remove the guard AND fix `evaluator.py:825` in the same commit, never separately.

**Hardcoded absolute paths in Zeus core scripts** — two confirmed in Zeus `scripts/` (not just `51 source data/scripts/`):
- `scripts/generate_monthly_bounds.py:37` — `PRODUCTION_DB = Path("/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db")`
- `scripts/heartbeat_dispatcher.py:19` — `HEARTBEAT_LOG = Path("/Users/leofitz/.openclaw/logs/zeus-heartbeat-dispatch.log")`

These are in Zeus core, not in the external 51-source scripts. Both should use `Path(__file__).resolve().parent.parent / "state" / ...` or an env var. Low blast radius (diagnostic scripts, not runtime path), but they will fail silently on any non-Fitz machine.

**Duplicate utility bodies across extractors** — `compute_manifest_hash` exists at `scripts/extract_tigge_mx2t6_localday_max.py:115` (high extractor) but is absent from `scripts/extract_tigge_mn2t6_localday_min.py`. Three smaller utilities ARE duplicated: `_overlap_seconds` (mx2t6:455, mn2t6:571), `_now_utc_iso` (mx2t6:475, mn2t6:591), `_city_slug` (mx2t6:479, mn2t6:595). These will drift as soon as one extractor is patched without the other. The 5B-follow-up backlog item `scripts/_tigge_common.py` is the right fix. Not blocking but will cause subtle divergence bugs in the next GRIB-related edit.

**Provenance headers missing on new 5B files** — `scripts/extract_tigge_mn2t6_localday_min.py` and any new test files added in 5B should carry the `# Lifecycle: / # Purpose: / # Reuse:` block per `architecture/naming_conventions.yaml`. Not verified post-5B commit (I retired before 5B landed), but the pattern from earlier phases suggests these are added inconsistently. Fresh 5C critic should check.

---

## 2. Cross-phase patterns I noticed

**`NotImplementedError` guards as phase markers** — used in three places across Phase 4-5: `ingest_grib_to_snapshots.py:259` (removed in 5B), `Day0Signal.__init__:86` (live, Phase 6), `evaluator.py:820` comment (coupled to the Day0 guard). This is a deliberate pattern: guards are breadcrumbs pointing to the next phase's work. But they create silent-corruption risk if the guard is removed without also fixing all its dependents. Pattern to expect in 5C: the replay migration guard (or lack thereof) will be the analogous breadcrumb.

**Structural refactors always leave one asymmetric `_from_maxes` name in the common path** — happened with `p_raw_vector_from_maxes`, `remaining_member_maxes_for_day0`. Expect Phase 7 rebuild to uncover more `_high`-flavored names in the calibration / platt layer that need renaming or documentation.

---

## 3. Forward hazards for 5C / Phase 6 / Phase 7

**5C — replay migration seams:**
- `src/engine/replay.py:242-259` (`_forecast_rows_for`) + `L261-296` (`_forecast_reference_for`) + `L298+` (`_forecast_snapshot_for`): all query legacy `forecasts` table. Critic-alice confirmed `historical_forecasts_v2` is the target. No scaffold yet. The three functions are closely coupled; rewriting them in isolation risks leaving a partial migration that compiles but queries different tables for different call paths.
- `replay.py` also caches at `self._decision_ref_cache` — cache key must include `temperature_metric` once the table migration lands, or cross-metric cache pollution occurs.

**Phase 6 — Day0 split shape:**
- `src/signal/day0_signal.py` is 272 LOC, monolithic. The `NotImplementedError` guard is at line 86. The split will extract `Day0HighSignal` (most of the existing class) and create `Day0LowNowcastSignal` (new). The evaluator at `L814` will need a routing branch — currently hardwired to `Day0Signal(...)`. The `member_mins_remaining=remaining_member_extrema` dead-code-but-live assignment at `evaluator.py:825` MUST be fixed in the same Phase 6 commit as the guard removal.
- `remaining_member_maxes_for_day0` in `day0_window.py` already handles both tracks. Phase 6 executor should NOT rewrite it — they should rename or leave it, not fork it.

**Phase 7 — metric-aware rebuild cutover:**
- `scripts/rebuild_calibration_pairs_canonical.py` (legacy, v1 path) is still on disk and potentially still callable. Phase 7 must explicitly `DEAD_DELETE` it after cutover, not just stop calling it.
- `scripts/backfill_tigge_snapshot_p_raw.py` (v1) is the legacy parallel to the v2 snippet in the DT package. Same pattern — delete explicitly at Phase 7, don't orphan.

---

## 4. Fresh scout inheritance

**Tell, don't re-derive:**
- `src/state/db.py` is 3800+ lines. Never read it fully — use `lsp_document_symbols` or grep for the specific function. The file has three conceptual zones: DDL/schema (lines 1-800), query helpers (800-2500), writer helpers (2500+).
- `src/engine/evaluator.py` is the central decision hub. The Day0 path starts at ~L784. The metric guard is at L800. Don't read above L750 unless asked about candidate selection.
- `scripts/topology_doctor*.py` files (8 files) are diagnostic, not production. Don't read them unless debugging topology; they are not part of the ingest or training pipeline.
- The `zeus_dual_track_refactor_package_v2_2026-04-16/` directory is the authoritative spec anchor for structural decisions. Always check it before inventing a new API shape.

**Key first-skim files for any fresh scout:**
1. `AGENTS.md` root (law + mental model)
2. `docs/authority/zeus_dual_track_architecture.md` (dual-track law)
3. `src/types/metric_identity.py` (all identity constants)
4. `src/contracts/ingest_snapshot_contract.py` (boundary + causality law)
5. `docs/operations/.../team_lead_handoff.md` (phase state)

**Anti-patterns to expect:**
- Files named `*_canonical.py` or `*_v1.py` in `scripts/` are legacy; `*_v2.py` are current.
- `src/data/` has both `daily_obs_append.py` (live) and `wu_daily_collector.py` (dead). Don't confuse them.
- Any `backfill_*` script in `scripts/` may be a one-shot tool that's already been run and is now orphaned. Check git log before recommending re-execution.

---

## 5. Tooling / workflow observation

- **`lsp_document_symbols` is faster than Read for large files.** `db.py` (3800 LOC) and `evaluator.py` (1000+ LOC) should never be read fully. Get the symbol outline first, then `Read` with `offset`/`limit` to the specific function.
- **`grep -n "def "` is a cheap symbol outline** when LSP is slow or unavailable. Use it on any file > 200 LOC before committing to a full Read.
- **`wc -l` before every Read** — saves context budget. Files in `scripts/` range from 50 to 1500+ LOC; the budget cost is unpredictable without this check.
- **Compound grep patterns (`"A\|B"`)** fail shell escaping silently in some contexts (team_lead_handoff §"Critic role" lesson). Use two separate greps or Python `-E` flag. The bug cost one Phase 5 critic cycle.
- **`topology_doctor.py`** is the repo's own navigation tool — it knows the task-to-file map. Use `python scripts/topology_doctor.py --navigation --task "<task>"` for entry-point routing on unfamiliar tasks before spelunking manually.
