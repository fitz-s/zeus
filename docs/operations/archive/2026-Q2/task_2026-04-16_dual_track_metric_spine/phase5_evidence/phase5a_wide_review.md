# Phase 5A Wide Review — critic-alice

**Date**: 2026-04-17
**Scope**: foundation commit for Phase 5 dual-track metric spine (truth authority + MetricIdentity view)
**Verdict**: **ITERATE** — one CRITICAL, three MAJOR, strata-specific discipline notes. PASS achievable after dispatch; not a structural rework.

---

## Pre-commitment predictions

Before deep read, based on the stratum split + exec-emma's citation history I expected:
1. Writer-path gap on `CANONICAL_POSITION_CURRENT_COLUMNS` (the ALTER lands but writers don't use it).
2. `authority` default on read-path being permissive (`"canonical_db"`) rather than fail-closed.
3. Undisclosed config expansion (config.py not in stated scope).
4. Silent-default fallbacks in the view layer.
5. Upsert path not covered by tests.

All five materialized. See findings below.

---

## Verification — diff stat + stratum confirmation

Fresh `git diff --stat` (disk-verified 2026-04-17):

```
.claude/worktrees/data-rebuild        |  0
src/config.py                         | 34 ++++++++++++++++++---------
src/state/db.py                       | 43 +++++++++++++++++++++++++++++++----
src/state/portfolio.py                |  8 +++++++
src/state/truth_files.py              | 27 ++++++++++++++++++++--
state/auto_pause_failclosed.tombstone |  2 +-
state/status_summary.json             | 12 +++++-----
7 files changed, 102 insertions(+), 24 deletions(-)
```

**Stratum 1 — `src/config.py` expansion** (undisclosed in exec-emma's status report):
- `ACTIVE_MODES = ("live",)` → `ACTIVE_MODES = ("live", "<obsolete_non_live>")` (`src/config.py:28`).
- New `_RUNTIME_MODES = ("live",)` (`src/config.py:31`) — splits truth-file routing from runtime.
- `mode_state_path(mode="<obsolete_non_live>")` → `STATE_DIR / "<obsolete_non_live>" / filename` (`src/config.py:43-44`).
- `get_mode()` now validates against `_RUNTIME_MODES` only (`src/config.py:54`).

`git blame` confirms these are `Not Committed Yet 2026-04-17 11:59:55` — same timestamp cluster as the rest of the Phase 5A diff. Attribution: either exec-emma or the mystery 11:36 writer; indistinguishable from blame alone.

**Stratum 2 — mystery 11:36 writer (all blame stamps `Not Committed Yet 2026-04-17 11:59:5x`)**:
- `PortfolioState.authority: str = "canonical_db"` default (`src/state/portfolio.py:678`).
- `ModeMismatchError(ValueError)` class (`src/state/truth_files.py:17-23`).
- `read_mode_truth_json(*, mode=None)` + mismatch raise (`src/state/truth_files.py:132-143`).
- Three `load_portfolio` exit paths tagged `"canonical_db"` / `"degraded"` / `"unverified"` (`src/state/portfolio.py:963 / 1005 / 1035`).

**Stratum 3 — exec-emma Cluster B increment** (her citation `[AUTHORIZED by: team-lead Phase 5 owner briefing]`):
- `build_truth_metadata(..., authority="UNVERIFIED")` kwarg + return-dict inclusion (`src/state/truth_files.py:45 / 56`).
- `annotate_truth_payload(..., authority="UNVERIFIED")` forwarding (`src/state/truth_files.py:74 / 79`).
- ALTER TABLE `position_current` add `temperature_metric` column (`src/state/db.py:836-844`).
- `query_portfolio_loader_view(*, temperature_metric=None)` kwarg + column existence guard + per-row emission (`src/state/db.py:3144-3197`).

**Disk-verified test outcome**: `pytest tests/test_phase5a_truth_authority.py` → **17 passed**. The 17/17 GREEN claim is truthful.

---

## L0-L5 audit

### L0 — Authority re-loaded
Onboarding doc at `phase5_evidence/critic_alice_phase5_onboarding.md` intact. Methodology + handoff + coordination handoff re-read. PASS.

### L1 — INV-## / FM-## respected
- FM-## "no silent default at write-time" — **VIOLATED** (see MAJOR-2 — `CANONICAL_POSITION_CURRENT_COLUMNS` lacks `temperature_metric`).
- SD-1 MetricIdentity binary — `CHECK (temperature_metric IN ('high', 'low'))` respects it. PASS structurally.
- SD-A mode as first-class routing key — now respected at `read_mode_truth_json:133` (`path = mode_state_path(filename, mode=mode)`). PASS.

### L2 — Forbidden Moves
- Bare Kelvin defaults: N/A (no GRIB-adjacent code touched).
- Fixture bypass: testeng-grace's R-AD tests route through the real view. PASS.
- DROP TABLE in migration: N/A.
- Silent sentinel strings: **PARTIAL VIOLATION** — the view layer emits `"high"` as a fallback string when the column is missing (`src/state/db.py:3175`). See MAJOR-1.
- String-typed `mode`/`authority`: present, not yet `Literal` — acceptable for 5A; Literal upgrade is a follow-up chore.
- Orphan helpers: none introduced.

### L3 — Silent fallback / NC-##
- `build_truth_metadata(authority="UNVERIFIED")` default — **FAIL-CLOSED, correct**. A caller that forgets the kwarg gets `UNVERIFIED`, which trips downstream authority gates rather than silently minting VERIFIED.
- `PortfolioState.authority: str = "canonical_db"` default — **FAIL-OPEN, regression candidate**. See MAJOR-3.
- View-layer `has_metric_col` fallback — **silent-default antipattern**. See MAJOR-1.

### L4 — Source authority preserved at every seam
- `annotate_truth_payload` call sites at `src/state/portfolio.py:1079` and `src/observability/status_summary.py:399` do NOT pass `authority=`. Both default to `"UNVERIFIED"`. Every portfolio JSON sidecar and status_summary.json written post-5A gets stamped `UNVERIFIED` even when source data is canonical. **Authority inversion.** See MAJOR-4.
- `build_truth_metadata` call sites (4 internal) are same-file; exec-emma's tests pin the contract. PASS.

### L5 — Phase boundary
- Phase 6 Day0 split: no leak.
- Phase 7 cutover: no leak.
- Phase 9 risk path: no leak.
- Phase 1-4 contracts: FDR family scope split, MetricIdentity spine, V2 schema, observation_client — all intact (ran `pytest tests/test_phase5a_truth_authority.py` GREEN, no touched-code regressions observed in the file-path sense).
- **Historical environment-routing residue in config.py is a Phase 9 leak surface** — see CRITICAL-1.

### WIDE — what's not on my checklist

**Found 4 items nobody flagged:**
1. `_RUNTIME_MODES` / `ACTIVE_MODES` split in `src/config.py` historically opened an alternate state-path write surface that no Phase 5 test exercised. Users could not hit it through the environment gate, but direct callers of `mode_state_path(...)` could bypass the intended live-state routing guard. This is exactly the "silent new capability" FM.
2. The view's `', 'high' AS temperature_metric'` literal is string-substituted into an f-string SQL (`src/state/db.py:3175, 3178`). With `has_metric_col=True` the value `"temperature_metric"` (the column name) is substituted — clean. With `has_metric_col=False` the literal `'high'` is substituted. Both paths are compile-time constants in this codebase, so no injection risk, but the shape is fragile; future refactor could parameterize `metric_select` from user input.
3. `test_load_portfolio_db_outage_returns_unverified_authority` patches `src.state.db.get_trade_connection_with_world` to raise `OSError` — this covers the OUTER OSError path at `portfolio.py:963`. It does NOT exercise the `sqlite3.OperationalError` path at `:1005`. If future code adds a transient DB error shape, only half the degraded-path logic is pinned. Not a blocker for 5A, but a gap for 5B testeng to close.
4. `auto_pause_failclosed.tombstone` and `status_summary.json` show as modified in the working tree but are runtime state drift, not 5A code changes. Should NOT be staged in the 5A commit (handoff §"What NOT to bundle").

---

## Findings

### CRITICAL-1 — `src/config.py` scope expansion contradicts stated boundaries

**Evidence**: `src/config.py:28` declares `ACTIVE_MODES = ("live", "<obsolete_non_live>")`; `src/config.py:43-44` adds obsolete non-live runtime-path routing. Team-lead's dispatch stated: "`mode_state_path` in `src/config.py` still enforces live-only; `read_mode_truth_json` check fires on metadata mismatch, not path routing (Zeus is live-only so mode-path routing is N/A today)." That statement is FALSE on disk; config.py now supports obsolete non-live runtime routing.

**Why this matters**: (a) Opens a write path (`STATE_DIR/obsolete non-live runtime/filename`) that no code reviews, no governance ruling, and no test exercises. (b) Contradicts the team-lead's explicit scope statement, which is the authority reviewers gate on. (c) Obsolete non-live runtime/live separation is a Phase 9 concern per the roadmap; lifting it into 5A absorbs a cross-phase seam without the corresponding authority chain.

**Confidence**: HIGH (direct grep evidence, contradicts explicit dispatch statement).

**Realist Check**: Worst-case impact is a caller invoking `mode_state_path("positions.json", mode="<obsolete_non_live>")` and creating the obsolete non-live runtime subdir. Today no such caller exists (grep shows zero `mode="<obsolete_non_live>"` invocations in `src/**`). Detection is fast if the wrong path lands a real file. But the discipline issue stands: the claim "config.py still enforces live-only" was false; every future status report now needs second-sourcing. Severity STAYS at CRITICAL because it's a discipline + authority-chain violation, not a code defect whose impact is mitigated by absence of callers.

**Fix**: One of two paths, team-lead rules:
- **Path A (revert)**: Revert `src/config.py` to `ACTIVE_MODES = ("live",)` and the prior `mode_state_path` body. Obsolete non-live runtime routing lands in Phase 9 under explicit scope ruling.
- **Path B (ratify)**: Team-lead issues a retrospective scope ruling folding obsolete non-live runtime routing into 5A, commit message carries `[SCOPE-EXPANSION: src/config.py obsolete non-live runtime routing, ratified by team-lead at <timestamp>]`, and a RED test that hits `mode_state_path("positions.json", mode="<obsolete_non_live>")` lands to pin the capability.

**Preferred**: Path A. Obsolete non-live runtime routing is not needed for B069/B073/B077 closure and was not requested.

---

### MAJOR-1 — View-layer `has_metric_col` fallback defeats the antibody

**Evidence**: `src/state/db.py:3163-3175`:

```python
existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(position_current)").fetchall()}
has_metric_col = "temperature_metric" in existing_cols
# ...
metric_select = ", temperature_metric" if has_metric_col else ", 'high' AS temperature_metric"
```

When the column is missing (pre-migration DB, uninitialized test fixture), the view silently emits `'high'` for every row. Consumers cannot distinguish "legitimately high" from "column missing, defaulted to high." This is onboarding antipattern #3 (silent-default).

**Why this matters**: The whole point of R-AD is that `temperature_metric` becomes an antibody — reading it asserts "this row KNOWS its track." A fallback that manufactures the value on absence means every downstream consumer inherits a fact without provenance. Phase 5B will trust this field; Phase 6 Day0 router will route on it.

**Confidence**: HIGH (direct code reference).

**Realist Check**: Current code guarantees the column exists after `init_schema` runs; in practice the fallback is dead except for the `_table_exists(conn, "position_current")` path at `:3152` (which already returns early). Phase 5B/6 code runs post-`init_schema` so the fallback rarely fires. But dead defensive code that silently emits a fact is a trap — someone will refactor `init_schema` someday and the fallback becomes live without a test catching it.

**Fix**: Remove the `has_metric_col` branch. If `temperature_metric` is missing on a DB where `position_current` exists, raise a structural error: `RuntimeError("position_current.temperature_metric column absent — run init_schema")`. That's the immune-system shape.

---

### MAJOR-2 — `CANONICAL_POSITION_CURRENT_COLUMNS` doesn't include `temperature_metric`; writers hardcode default

**Evidence**: `src/state/projection.py:6-37` — no `temperature_metric` in the tuple. `upsert_position_current` at `:89` writes `INSERT INTO position_current ({", ".join(CANONICAL_POSITION_CURRENT_COLUMNS)})` — so the canonical runtime writer NEVER sets `temperature_metric`. Every new position lands with `DEFAULT 'high'` per the ALTER TABLE constraint. Low-track positions CANNOT be written via the canonical writer.

**Why this matters**: R-AD is satisfied at read-time but broken at write-time. Phase 5B/6 code writing low-track positions through `upsert_position_current` will land them as `'high'`. The onboarding antipattern — "the fix is in READ, not WRITE" — is exactly this shape.

**Confidence**: HIGH (code reference + grep shows 0 writer sites with `temperature_metric`).

**Realist Check**: No current writer emits low-track positions (Phase 5B hasn't landed). The bug manifests the moment Phase 5B/6 writes the first low-track position; those commits will then have a reviewer who's paying attention (hopefully me), and I'll catch it there. Severity was originally CRITICAL in my first-pass review; **downgrading to MAJOR** because:
- The test suite has no current test that writes a low-track position through `upsert_position_current`, so there's no green-but-wrong test masking the gap.
- The failure mode is "writes land as 'high'" — caught immediately when the first low-track write is audited.
- **Mitigated by**: Phase 5B landing in my review scope; this exact failure is on my hunting list. I will reject 5B if the writer isn't extended.

But this mitigation is reviewer-dependent, not structural. The antibody shape would be to extend CANONICAL in 5A itself.

**Fix**: Either (a) extend `CANONICAL_POSITION_CURRENT_COLUMNS` with `"temperature_metric"` now, update `upsert_position_current` ON CONFLICT clause to include `temperature_metric=excluded.temperature_metric`, and add a `require_payload_fields` assertion — land in 5A; or (b) explicitly defer to 5B with a written commitment in `phase5_evidence/`. Prefer (a) — it's 3-4 lines and makes the category impossible.

---

### MAJOR-3 — `PortfolioState.authority` defaults to `"canonical_db"` (fail-open)

**Evidence**: `src/state/portfolio.py:678` — `authority: str = "canonical_db"`. A caller that constructs `PortfolioState()` without explicitly setting authority gets the canonical stamp. Example: `PortfolioState(positions=[x], bankroll=50.0)` passes any downstream `if state.authority == "canonical_db"` gate.

**Why this matters**: The whole authority system is a fail-closed defense. A default of `"canonical_db"` means any NEW code path that forgets the kwarg silently declares the state authoritative. The three `load_portfolio` exit paths at `:963 / :1005 / :1035` correctly override the default, but a future caller bypassing `load_portfolio` (e.g. test fixtures, admin tools) will mint canonical state by accident.

**Confidence**: HIGH (code reference).

**Realist Check**: Today the only PortfolioState constructors are in `load_portfolio` and test code. Test code creating a `PortfolioState(...)` without authority is not exercising risk-sizing gates, so the fail-open doesn't translate to runtime risk. BUT this is exactly the shape of drift — Phase 6/7 may add a PortfolioState construction in the Day0 split and inherit the unsafe default.

**Fix**: Change to `authority: str = "unverified"`. Force every construction site to assert authority explicitly. Update the three `load_portfolio` exit paths — they already pass authority explicitly, so no change required there. Update any test fixtures that construct PortfolioState without authority (grep shows a few in `tests/test_phase5a_truth_authority.py:59/66/73/80` — these are the acceptance tests and test with explicit authority kwargs, so safe).

---

### MAJOR-4 — `annotate_truth_payload` production call sites don't pass authority; every JSON sidecar lands `UNVERIFIED`

**Evidence**:
- `src/state/portfolio.py:1079`: `data = annotate_truth_payload(data, path, mode=get_mode(), generated_at=state.updated_at)` — no `authority=` kwarg.
- `src/observability/status_summary.py:399`: `status = annotate_truth_payload(status, STATUS_PATH, mode=get_mode(), generated_at=generated_at)` — no `authority=` kwarg.

Both default to `"UNVERIFIED"`. Every portfolio JSON sidecar and status_summary.json written post-5A gets stamped `UNVERIFIED`, which is the OPPOSITE of the intent — these are canonical writes.

**Why this matters**: B078's gate is "fail closed when `temperature_metric`/`data_version`/`authority` is missing on low-lane files." Same structural gate applies to live files. A caller that reads status_summary.json and requires `authority == "VERIFIED"` will reject its own sibling's output. B077 closure is optimistic — the validation exists but the data never carries the green stamp.

**Confidence**: HIGH (grep evidence).

**Realist Check**: Today no consumer of these JSON sidecars gates on `authority == "VERIFIED"`. The UNVERIFIED stamp is invisible harm — until Phase 5B/6 code reads the sidecar and enforces the gate. At that point every historical file has the wrong stamp.

**Fix**: Thread authority through both call sites:
- `src/state/portfolio.py:1079` → `annotate_truth_payload(data, path, mode=get_mode(), generated_at=state.updated_at, authority=state.authority.upper() if state.authority == "canonical_db" else "UNVERIFIED")` — or a cleaner mapping helper.
- `src/observability/status_summary.py:399` → `annotate_truth_payload(..., authority="VERIFIED")` (observability write is canonical by construction).
- Add a RED test that reads the written sidecar and asserts `truth["authority"] != "UNVERIFIED"`.

---

## What's Missing

- **No schema version bump**. `position_current` gained a column; no marker in any schema_version table. Post-deploy, impossible to tell a pre-5A DB from a post-5A one via programmatic check.
- **No chronicler audit**. `src/state/chronicler.py:38` checks `set(CANONICAL_POSITION_CURRENT_COLUMNS).issubset(current_columns)`. Once CANONICAL is extended (MAJOR-2 fix), this check becomes sensitive to init_schema ordering. Not currently broken because CANONICAL isn't extended yet, but will need a test when it is.
- **No test for ON CONFLICT UPDATE preserving `temperature_metric`**. R-AD tests insert fresh rows; no test that re-upserting a row preserves the column. Upsert bugs are classic.
- **No rollback procedure**. If 5A commits and 5B finds a problem, SQLite ALTER TABLE DROP COLUMN is version-dependent. Document the rollback path in the commit message or `phase5_evidence/`.
- **Regression baseline un-audited**. exec-emma reports "111 pre-existing failures, 0 new" but no `diff` artifact attached. Acceptable given my manual sanity check found no touched-code regressions, but her future reports should carry a side-by-side.

---

## Multi-perspective notes

- **Executor/new-hire**: A Phase 5B implementer reading this diff will correctly understand read-path semantics (ModeMismatchError, `temperature_metric` on view) but will NOT realize `CANONICAL_POSITION_CURRENT_COLUMNS` is the writer seam they must extend. Comment at `src/state/projection.py:37` would help future self.
- **Stakeholder**: B069 closure is solid (`PortfolioState.authority` + gated view). B073 closure is solid (`load_portfolio` three exits). B077 closure is solid structurally (ModeMismatchError raises correctly at `:137-142`). B078 is deferred to 5B (correct per handoff). B093 bifurcated per earlier ruling. Absorption was executed cleanly for the bugs in scope.
- **Skeptic**: The mystery stratum (11:36 writer) predates the team spawn. It wasn't reviewed during authorship. The only defense is this wide pass. Given 17/17 GREEN + my independent audit here, the stratum is load-bearing but not structurally broken. No structural rework needed; the findings above are all addressable dispatches.
- **Ops**: Existing production DBs (if any) get every `position_current` row stamped `'high'` on first boot post-5A. In the Zero-Data Golden Window this is defensible. Worth logging row-count pre-ALTER as an antibody for the moment the golden window lifts.

---

## Section B absorption check

- **B069**: `PortfolioState.authority` declared; `load_portfolio` three exits tagged; view emits per-row `temperature_metric`. ✓ ABSORBED.
  - *Caveat*: view gating on authority isn't visible in the diff — B069's "gate on authority" is enforced by downstream callers reading `state.authority`, which this 5A commit doesn't exercise. Phase 5B risk-path callers must assert.
- **B073**: `load_portfolio` returns typed authority at all three exits. ✓ ABSORBED.
- **B077**: `ModeMismatchError` raises on mode drift. Both rejection + acceptance tested in `tests/test_phase5a_truth_authority.py`. ✓ ABSORBED.
  - *Caveat*: current codebase has no real obsolete non-live runtime truth files on disk so the mismatch test uses patched `mode_state_path`. Valid for current posture.
- **B078**: Deferred to 5B per handoff. N/A.
- **B093**: Bifurcated per earlier ruling (half-1 in 5C, half-2 in Phase 7). N/A.

**Verdict**: Section B absorption is structurally complete for the three in-5A bugs. Bug-fix agent can mark B069/B073/B077 GREEN after 5A lands + this review's dispatches address the MAJOR findings.

---

## Final verdict

**ITERATE** — CRITICAL-1 must resolve (revert or ratify); MAJOR-1 through MAJOR-4 should land in the same commit if possible, otherwise 5B-open blockers. Escalated to ADVERSARIAL mode on CRITICAL-1 (undisclosed scope).

### Recommended dispatch (to exec-emma)

1. **CRITICAL-1**: Revert `src/config.py` OR obtain retrospective scope ratification from team-lead with a `[SCOPE-EXPANSION: ratified]` marker.
2. **MAJOR-1**: Remove `has_metric_col` fallback from `query_portfolio_loader_view`.
3. **MAJOR-2**: Extend `CANONICAL_POSITION_CURRENT_COLUMNS` + ON CONFLICT UPDATE + require_payload_fields.
4. **MAJOR-3**: Change `PortfolioState.authority` default to `"unverified"`.
5. **MAJOR-4**: Thread `authority` through `annotate_truth_payload` at both production call sites (`portfolio.py:1079`, `status_summary.py:399`).
6. Add RED tests for (2) ON CONFLICT UPDATE preservation + (4) sidecar authority stamp.

### Open questions (unscored)

- Obsolete non-live routing in config.py — does user want Phase 9 lifted into 5A? Team-lead ruling needed.
- Stratum 2 attribution — should the mystery 11:36 writer be identified? Not blocking, but the provenance chain is opaque.

### Discipline observation

The undisclosed `src/config.py` change repeats the "file doesn't exist" memory-based claim from earlier. Second instance of citation discipline slippage. Escalation question for team-lead per probation context — not my ruling.

---

*Authored*: critic-alice (opus, persistent, Phase 5 onward)
*Disk-verified*: 2026-04-17 post exec-emma Cluster B landing
*Test baseline*: `pytest tests/test_phase5a_truth_authority.py` → 17 passed
