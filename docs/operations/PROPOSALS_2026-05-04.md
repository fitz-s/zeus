# Improvement proposals — operator review needed

**Created:** 2026-05-04 (post PR #55 + #56 + #58 merge capsule).
**Status:** proposal only — each item needs operator sign-off on
trigger criteria / persistence target before implementation.

These three items came out of the PR-merge capsule; they are not
yet implemented because each one has a non-obvious "when does this
fire" question that benefits from operator judgment before going
into hooks / topology / shared helpers.

---

## P1 — Pre-merge identity-collision detection

**Pain observed:** PR #55 (open since 09:15) and PR #56 (merged 13:50)
both invented `ForecastCalibrationDomain` independently with
incompatible field shapes.  The conflict surfaced only at merge time
and cost ~1 hour of careful hand-resolution.  The structural fault
was not "two PRs touched the same file" (acceptable); it was "two
PRs added the same identity dataclass with different shapes" (not
acceptable, because both think they own the canonical definition).

**Why a hook is the right surface (not a CI check or a meeting):**
- a hook fires on `gh pr create` / `gh pr ready`, before reviewer
  attention is consumed on the new PR.
- the lookback window is OPEN PRs only (small, fast).
- the human signal is "you and PR #N both invent type X — pick one
  before merging".

**Trigger criteria (need operator sign-off):**
The hook should NOT fire on every file-overlap.  Proposed criteria:

1. The PR adds (`+++ /dev/null` → `+++ b/...`) a class declared with
   `@dataclass`, `class ...(NamedTuple)`, or `class ...(BaseModel)`.
2. AT LEAST ONE other open PR adds a class with the SAME bare class
   name OR adds to the same file under `src/types/`,
   `src/calibration/forecast_*`, `src/contracts/`, or
   `src/strategy/strategy_profile.py`.
3. The two PRs have ≠ base commits (i.e., were branched
   independently, not stacked).

If all three hold, the hook prints:

```
⚠ identity-collision risk: this PR and PR #N both add `ClassName`.
  PR #N branch: <branch>
  Resolve before merge:
   (a) one PR rebases the other's design, OR
   (b) name the classes differently with explicit ownership.
```

It is advisory (warn-only) — false positives are tolerable, blocking
is not.  The bar is "human sees the collision before merge", not
"machine prevents merge".

**Open questions for operator:**
- Is the file-prefix list above the right scope, or should it be a
  configurable allowlist?
- Should rebased / stacked PRs be excluded entirely, or just warned
  with a different message?
- Where should the hook live? `.claude/hooks/pre-pr-create.sh`?
  `gh` extension?  GitHub Action?

## P2 — Schema-presence detection helper standardization

**Pain observed:** during PR #55 review-round-1 fixes, two test
fixtures were silently failing because they constructed
`platt_models_v2` / `calibration_pairs_v2` without the Phase 2
stratification columns added by `migrate_phase2_cycle_stratification.py`.
The fix added an inline `_v2_table_has_stratification(conn, table)`
helper in `store.py` that gates the migrated SELECT/INSERT form.

**Why standardize:**
Schema-presence detection is a fundamental "loader is downstream of
migration" pattern.  Every place a Phase-N migration adds optional
columns will need the same dance.  Inlined helpers per-module
fragment.

**Proposed shape:**
- Move `_v2_table_has_stratification` and
  `_v2_pairs_table_has_stratification` from `src/calibration/store.py`
  to a shared location.  Candidate: `src/state/db.py` (already houses
  schema-related helpers) or `src/state/schema/column_presence.py`
  (new tiny module, easy to discover by name).
- Public signature: `has_columns(conn, table, *cols, attached: str | None = None) -> bool`.
- Refactor existing callers in `store.py` to use the shared form.

**Open questions for operator:**
- `src/state/db.py` vs new module?  `db.py` is already large; a new
  module is cleaner but adds an import surface.
- Should the helper raise on `OperationalError` (table missing) or
  return False?  Current `store.py` returns False — propose keeping
  that semantic.
- Should there be a topology rule that flags new
  `_v2_table_has_*` style helpers as candidates for consolidation?
  (Probably yes, but low priority.)

## P3 — Capsule topology delta

**Pain observed:** the AGENTS.md context-capsule route currently
returns `admission_status: advisory_only`, `risk_tier: T0`,
`persistence_target: final_response`.  When a capsule surfaces an
**actionable** Zeus improvement insight (like P1 / P2 above), the
insight has nowhere to go except the chat output — which the next
session won't see unless the operator manually copies it forward.

**Why this is a topology gap:**
The capsule is supposed to "recycle context into a compact feedback
capsule instead of leaving lessons in chat memory" (AGENTS.md §3).
But the route's only persistence target is `final_response` — which
IS chat memory.  That's a contradiction at the route level.

**Proposed delta:**
Add a second admission decision to the capsule route — when the
capsule emits a structurally-shaped improvement insight (P-tagged or
similar), admit `notepad_write` to a designated lessons surface.
Candidates for the lessons surface:
- `docs/operations/improvement_backlog.md` — append-only operator
  triage queue (one entry per insight, with status).
- `architecture/improvement_backlog.yaml` — typed registry that
  topology_doctor can validate.
- `~/.claude/projects/.../memory/MEMORY.md` — only if the insight
  is local-to-this-collaborator, not project-level.

**Open questions for operator:**
- Project-level (`docs/`) or local (`MEMORY.md`)?  Most of the
  capsule's insights are project-level (P1, P2 above are clearly
  project-level), so `docs/` seems right.
- YAML (typed, lints) or markdown (loose, easy)?  Lean YAML for
  topology validation, but markdown is more readable.
- What's the closeout signal for an entry?  Anchor PR merged?
  Operator marks `[done]`?  Time-based archival?

---

## Recommendation

Implement P2 first — it's a pure refactor with low risk, the helper
already exists and works.

P1 and P3 need operator design decisions before code lands.  P1's
trigger-criteria allowlist and P3's persistence-target shape are the
gates.  Both are valuable but neither is urgent — the next bloat
event will tell us which one matters more.
