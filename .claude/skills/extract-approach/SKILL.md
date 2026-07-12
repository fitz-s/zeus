---
name: extract-approach
description: Use after every non-trivial solved problem — extract the reusable approach into a learnings note before moving on. A solution without its learnings note is unfinished work.
---

# extract-approach

Law: after every non-trivial solved problem, run this before moving on.
A solution without its learnings note is unfinished work.

## What counts as non-trivial

Multi-step debugging, a migration, a recovery, a fix that required >1 wrong
hypothesis, anything an agent burned >20 minutes on, anything that surprised you.
Typo fixes and mechanical renames don't qualify.

## Steps

1. **Name the problem in one line** — the symptom as first observed, not the
   root cause (the next person searches by symptom).
2. **State the root cause in one line** — the fact that, had you known it,
   would have collapsed the search.
3. **Extract the approach** — the shortest path from symptom to fix as you
   would walk it NOW, knowing the answer: which probe discriminated between
   hypotheses, which probes were wasted. 2-6 bullet steps.
4. **Name the trap** — what pattern-matched to a wrong known failure, what
   guard/tool lied or misled, what assumption cost the most time.
5. **Route the note** (one destination, not several):
   - Cross-project heuristic → memory file
     (`~/.claude/projects/<project>/memory/`, one fact one file, index line ≤120 chars).
   - Repo-specific operational fact → the owning doc/runbook/AGENTS scoped file
     (registry-routed per AGENTS.md §4); a new incident heuristic with no owner →
     `architecture/fatal_misreads.yaml` or `history_lore.yaml`, whichever fits.
   - Fix already fully self-documenting in code/comment/commit → say so; no note.
6. **Supersede, never accrete** — if an existing note covers it, sharpen that
   note instead of adding a duplicate.

## Output contract

End with `LEARNINGS: <path>` (or `LEARNINGS: none — self-documenting`) so the
conductor can verify the law was followed.
