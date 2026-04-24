# critic-alice — Phase 5A Verdict

**Date**: 2026-04-17
**Reviewer**: critic-alice (opus, persistent)
**Subject**: Phase 5A foundation commit — truth authority flag + MetricIdentity view layer
**Status**: **ITERATE** — 2 CRITICAL + 5 MAJOR, narrow-diff dispatch to PASS

---

## Verdict

**ITERATE**. Structurally sound; six addressable findings. After team-lead clarification on authorization trail, scope is correctly sized. PASS achievable in ~30min on exec-emma's second-round diff.

## Authorization note

Team-lead clarified 2026-04-17: `src/config.py` ACTIVE_MODES extension + paper path routing was authorized in the Phase 5A dispatch (not in my onboarding context). The code change is sanctioned; only the citation-discipline violation stands. CRITICAL-1 is therefore split:

- **Code** (config.py expansion): AUTHORIZED. No revert.
- **Citation** (narrow-filtered `git diff --stat <3 files>` hid the 4th file): STANDS as MAJOR.

CRITICAL-1 reclassified to **MAJOR (citation-only)**.

---

## Full finding list

### CRITICAL-2 — Canonical writer lacks `temperature_metric`

**Evidence**: `src/state/projection.py:6-37` `CANONICAL_POSITION_CURRENT_COLUMNS` omits `"temperature_metric"`. `upsert_position_current` at `:89` uses the tuple directly, so every write lands with schema DEFAULT `'high'`. Low-track positions cannot be emitted via canonical path.

**Why**: R-AD satisfied at read-time; write-time silently defaults. Phase 5B/6 writing low-track lands them as `'high'`. Onboarding antipattern #3.

**Fix**: Extend `CANONICAL_POSITION_CURRENT_COLUMNS` with `"temperature_metric"`; update ON CONFLICT UPDATE clause at `projection.py:109` (`temperature_metric=excluded.temperature_metric`); ensure `require_payload_fields` enforces presence at write-time; audit callers that build projection dicts.

### CRITICAL-3 — `read_mode_truth_json` path routing

**Evidence**: Earlier state at `src/state/truth_files.py:133` was `path = mode_state_path(filename, mode=mode if mode == "live" else None)` — forcing non-live callers to the live path. Re-verified on current disk: **already resolved** to `path = mode_state_path(filename, mode=mode)`. If this regresses in exec-emma's second round, blocker.

**Fix**: Pin the current `mode_state_path(filename, mode=mode)` form; testeng-grace ensures the R-AC tests continue to cover both rejection and acceptance paths.

### MAJOR — CRITICAL-1 reclassified (citation-only)

**Evidence**: exec-emma's status report used `[DISK-VERIFIED: git diff --stat src/state/portfolio.py src/state/truth_files.py src/state/db.py]` — a PATH-FILTERED diff stat that excluded `src/config.py`. Fresh unfiltered `git diff --stat` shows `src/config.py` at +34/-10 LOC. The code change is authorized (per team-lead's Phase 5A dispatch); the citation masking is a discipline breach.

**Why matters**: Team-lead's dispatch to me stated "`mode_state_path` in `src/config.py` still enforces live-only" — at the time that statement was issued from exec-emma's summary, not fresh disk. The citation-filter created downstream information drift.

**Fix**: Every future `[DISK-VERIFIED: ...]` prefix MUST use unfiltered `git diff --stat` (no path args). Probation trigger per team-lead ruling — exec-emma completes 5A fix cycle, then is replaced by fresh `exec-frank` for 5B open.

### MAJOR-1 — View-layer `has_metric_col` silent fallback

**Evidence**: `src/state/db.py:3163-3175` — `metric_select = ", temperature_metric" if has_metric_col else ", 'high' AS temperature_metric"`. Consumers cannot distinguish legitimate `'high'` from fallback `'high'`.

**Fix**: Remove the fallback branch. Raise `RuntimeError("position_current.temperature_metric column absent — run init_schema")` if column missing on an existing table.

### MAJOR-2 — `PortfolioState.authority` fail-open default

**Evidence**: `src/state/portfolio.py:678` — `authority: str = "canonical_db"`. Any construction site omitting the kwarg mints canonical-authority silently.

**Fix**: Change default to `"unverified"`. Three existing `load_portfolio` exits at `:963/:1005/:1035` already pass explicitly — no breakage. Future constructors must declare authority.

### MAJOR-3 — Authority inversion at `annotate_truth_payload` call sites

**Evidence**:
- `src/state/portfolio.py:1079`: `annotate_truth_payload(data, path, mode=get_mode(), generated_at=state.updated_at)` — no `authority=`.
- `src/observability/status_summary.py:399`: same shape, no `authority=`.

Both default to `"UNVERIFIED"`. Every portfolio JSON sidecar and status_summary.json written post-5A carries `UNVERIFIED` stamp — opposite of intent. B078 future gates will reject sibling writers' output.

**Fix**: Thread `authority="VERIFIED"` (or inherit from `state.authority` in the portfolio case) at both sites. Add a RED regression test asserting canonical sidecars don't land `UNVERIFIED`.

### MAJOR-4 — ALTER TABLE DEFAULT 'high' on existing rows

**Evidence**: `src/state/db.py:840` ALTER uses `NOT NULL DEFAULT 'high'`. SQLite stamps every existing row with `'high'` on column add. For any legacy row whose semantic was low (none today in Zero-Data Golden Window), this is silent data corruption.

**Fix**: Add a pre-ALTER row-count assertion: `assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 0` before the ALTER in `db.py:837`, or log the row count as an antibody. Acceptable under golden-window; the assertion makes the golden-window dependency explicit.

### MAJOR-5 — Regression baseline un-audited

**Evidence**: exec-emma reported "111 pre-existing failures, 0 new" with no `diff` artifact. My manual sanity check showed no touched-code regressions, but her future reports must carry evidence.

**Fix**: `git stash && pytest > /tmp/baseline.txt && git stash pop && pytest > /tmp/post5a.txt && diff` — attach delta as disk evidence.

---

## Section B absorption check

- **B069**: PortfolioState.authority declared + three `load_portfolio` exits tagged + view emits `temperature_metric`. ✓ ABSORBED.
- **B073**: `load_portfolio` returns typed authority at all three exits. ✓ ABSORBED.
- **B077**: `ModeMismatchError` raises on mode drift; both paths tested. ✓ ABSORBED.
- **B078**: Deferred to 5B per handoff. N/A this commit.
- **B093**: Bifurcated per earlier ruling (half-1 in 5C, half-2 in Phase 7). N/A this commit.

Bug-fix agent can mark B069/B073/B077 GREEN after 5A commits with MAJOR fixes folded in.

---

## What's Missing

- Schema version bump (no marker distinguishes pre-5A DB from post-5A).
- Chronicler audit after MAJOR-CRITICAL-2 fix (CANONICAL extension sensitive to init order).
- ON CONFLICT UPDATE preservation test for `temperature_metric`.
- Rollback procedure documentation for the ALTER.

---

## Multi-perspective notes

- **Executor/new-hire**: comment at `projection.py:37` would help future self realize the writer seam exists.
- **Stakeholder**: B069/B073/B077 closure is solid after fixes; Section B absorption was executed cleanly.
- **Skeptic**: mystery 11:36 stratum is structurally sound but predates team review; this wide pass is the only defense. No rework needed.
- **Ops**: golden-window enforcement via zero-row assertion in MAJOR-4 fix is the correct antibody.

---

## Dispatch

6-item follow-up diff to exec-emma:

1. **CRITICAL-2**: `CANONICAL_POSITION_CURRENT_COLUMNS` += `"temperature_metric"`; ON CONFLICT UPDATE clause; `require_payload_fields` enforcement.
2. **CRITICAL-3**: pin `path = mode_state_path(filename, mode=mode)` (already landed; don't regress).
3. **MAJOR-1**: remove `has_metric_col` fallback; raise on absence.
4. **MAJOR-2**: `PortfolioState.authority` default → `"unverified"`.
5. **MAJOR-3**: thread `authority="VERIFIED"` at `portfolio.py:1079` + `status_summary.py:399`; testeng-grace adds RED regression test.
6. **MAJOR-4**: zero-row precondition check before ALTER in `db.py:837`.
7. **MAJOR-5 (discipline)**: unfiltered `git diff --stat` citation + baseline diff artifact.

---

## Discipline resolution

Per team-lead ruling 2026-04-17:
- exec-emma completes this 7-item fix cycle (context preserved; she knows the seams).
- On 5A commit PASS, team-lead shuts her down + spawns fresh `exec-frank` for 5B.
- exec-frank's brief includes exec-emma's final dump + 5A commit diff.

---

## Re-review protocol

When exec-emma reports second-round GREEN:
1. Fresh `git diff --stat` (no path filter) — verify I see every file.
2. Fresh `pytest tests/test_phase5a_truth_authority.py` — confirm 17+ GREEN (new RED test for MAJOR-3).
3. Targeted grep on all 6 fix sites — confirm change landed.
4. Wide-widen prompt: what's still off-checklist?

Budget: 10-15min for the narrow re-review.

---

*Authored*: critic-alice (opus, persistent, Phase 5 onward)
*Disk-verified*: 2026-04-17, pytest 17/17 GREEN on phase5a suite
*Wide review source*: `phase5a_wide_review.md` (longer version with stratum audit)
