# `docs/operations/` — Lifecycle Policy

**Created:** 2026-05-04
**Last reviewed:** 2026-05-04
**Trigger to write:** the operation-doc bloat observation 2026-05-04 —
112 .md files across 24 dirs with no clear "is this active?" signal.

This policy stops the bloat by making lifecycle states visible AND
forcing closeout receipts.  It does not retroactively delete past
artifacts — see `INDEX.md` triage backlog for one-time cleanup.

---

## §1 — Scope

Applies to:
- new directories under `docs/operations/`
- new top-level `docs/operations/*.md` files

Does NOT apply to:
- the **permanent observation surfaces** listed in `INDEX.md` (they
  are dataset-shaped, not task-shaped).
- existing artifacts created before 2026-05-04.

## §2 — Lifecycle states

Every `task_*` directory and every top-level `.md` is in exactly one of:

- `active` — operation in flight; an anchor PR or branch is open OR
  the operator has declared this directory live in INDEX.md.
- `closed` — the anchor PR is merged OR the operator has marked the
  operation done.  The dir may stay in place but a `STATUS.md` MUST
  exist (see §4) OR it MUST be moved under `archive/`.
- `superseded` — another operation replaced this one.  Same rule as
  `closed` but `STATUS.md` names the replacement.
- `archive_candidate` — closed for ≥30 days with no new commits AND
  no open follow-up PR cites it.  Move to `archive/` on next sweep.

## §3 — Creating a new task directory

1. Pick a unique name: `task_YYYY-MM-DD_<short_topic>/`.  Date is
   the day the operation started; topic is ≤4 words snake_case.
2. **Add a row to `INDEX.md` §"Active operation"** in the same commit
   that creates the directory.  No row → no directory.  This is the
   load-bearing antibody — if you skip it, future-you can't tell what
   the dir is for.
3. The directory itself starts with at most:
   - `PLAN.md` — what we're doing and why (the only required file).
   - one optional `DESIGN_*.md` per non-trivial sub-decision.
4. Don't pre-create empty subsections. Add files only when the work
   needs them.

## §4 — Closing out a task directory

When the anchor PR merges or the operator declares done:

1. Move the row in `INDEX.md` from §"Active operation" to §"Task
   directories — closeout status".  Status: `closed` or `superseded`.
2. Either:
   - **(preferred)** Move the directory to
     `docs/operations/archive/<original-name>/`.  Add a one-line
     redirect file at the original location pointing to archive.
   - **(if cross-references exist)** Leave the directory in place but
     add `STATUS.md` containing:
     - closeout date
     - anchor PR / commit
     - one-paragraph "what landed, what didn't"
     - if `superseded`: pointer to the successor task dir.
3. Drop any unreferenced draft files (`PLAN_v2.md`, `PLAN_v3.md`,
   etc.) — they're noise after closeout.

## §5 — Doc count budget per task dir

A task directory should hold ≤ 10 markdown files.  More than that is
a smell (see `task_2026-05-03_ddd_implementation_plan/` at 38 — a
backlog item).  When you cross 10, pause and ask:

- can sub-designs collapse into one DESIGN.md?
- did sequential _v2 / _v3 drafts replace earlier versions that
  should be deleted?
- is this actually two operations that should split?

The point of the budget isn't a hard limit — it's a forcing function
to compress before bloat compounds.

## §6 — Permanent surfaces are different

Permanent observation surfaces (`activation/`, `attribution_drift/`,
etc.) collect rolling data.  They:

- live indefinitely.
- have their own internal naming convention (usually per-cycle or
  per-date filenames).
- get one row in INDEX.md §"Permanent observation surfaces" — that's
  it.  No STATUS.md, no closeout.

If a new permanent surface is needed, register it in INDEX.md §1.

## §7 — Enforcement (lightweight)

A topology check is proposed (see PROPOSALS_2026-05-04.md) that:

- fails CI if a new `task_*` dir appears without an INDEX.md row.
- warns if INDEX.md hasn't been updated in the same PR that touched
  `docs/operations/task_*`.

Until that lands, this policy is operator-honor-system + the next
session's housekeeping pass.
