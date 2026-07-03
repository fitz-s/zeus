# Multi-agent worktree orchestration — first-principles design

# Created: 2026-06-29
# Authority basis: derived from one constraint; frontier-checked (ChatGPT Pro,
#   2026-06-29) against git ff-only / update-ref / merge-base semantics.

## One constraint generates the whole design

A live process runs from MAIN. Therefore:

> **MAIN only fast-forwards, and only to a commit whose full combined tree has
> already passed validation. Exactly one writer ever touches MAIN.**

That is the design. Everything else is forced by it, not chosen.

## Three forced consequences

1. **Editors cannot write MAIN** (only one writer does) → so each produces commits
   in its own isolated worktree. Isolation isn't a feature; it's the only way N
   producers coexist with one writer without clobbering.

2. **Validation precedes the write** ("already passed" — past tense) → assemble the
   combined work OFF main, test it there, then fast-forward. You cannot validate
   after writing: the live process is already running on whatever you wrote.

3. **ff-only + single writer ⇒ atomic, no race, no conflict, no partial state.**
   A non-fast-forward means the candidate wasn't built on current MAIN → rebuild on
   the new MAIN, never force.

## Mechanism (derivable; not part of the design)

How to implement the invariant without footguns — each line reduces to it:

- producer → its own worktree (per-worktree index)
- "validated state" → build the combined candidate in a staging ref, run the full
  suite there, ff MAIN to it only if MAIN still sits at the OID you started from
- "one writer" → a lock; the writer is **code, not a model**
- editors report an exact commit OID and never touch main/shared refs; review and
  integrate that OID, not a moving branch name
- confine each editor to its file set; preserve commits before removing a worktree

If a rule doesn't reduce to the invariant, delete it. (Already deleted:
`pre_branch_create_in_primary` — it only restated harness behavior.)

## Two facts to verify before building

- Does the live daemon tolerate a working-tree update during the ff? If not, the
  strictly simpler design is to stop running it from the mutable checkout: build into
  a release dir, atomic symlink-swap, restart — the process then only ever sees a
  complete snapshot, and the git dance disappears entirely.
- Confirm the harness blocks an agent worktree from writing `refs/heads/<session>`
  (linked worktrees share `refs/`).
