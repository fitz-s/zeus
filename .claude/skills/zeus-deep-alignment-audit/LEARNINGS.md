# LEARNINGS — Zeus Deep Alignment Audit

This file is the **evolving brain** of the `zeus-deep-alignment-audit` skill. `SKILL.md` is the protocol; this file is the accumulated wisdom of past audits and changes after every run.

**Update discipline**: only the skill's `Closeout` step rewrites this file. Do NOT hand-edit without recording the rationale and date in `AUDIT_HISTORY.md`'s retrospective section, otherwise the self-evolving loop loses provenance.

**Authority ordering**: when seed categories in `SKILL.md` disagree with active categories below, **this file wins**. Seeds are v0 priors; this is empirical reality.

---

## Active categories (current effective set)

The skill's Boot step 2 reads this table before dispatching workers. Each row = one parallel haiku worker.

| ID | Name | Yield | Last validated | Notes |
|----|------|-------|----------------|-------|
| A | Data provenance holes | UNPROVEN | (seed v0) | — |
| B | Math drift | UNPROVEN | (seed v0) | — |
| C | Statistical pitfalls | UNPROVEN | (seed v0) | — |
| D | Time/calendar | UNPROVEN | (seed v0) | — |
| E | Settlement edges | UNPROVEN | (seed v0) | — |
| F | Cross-module invariants | UNPROVEN | (seed v0) | — |
| G | Silent failures | UNPROVEN | (seed v0) | — |
| H | Assumption drift | UNPROVEN | (seed v0) | — |

**Yield ladder** (set by Closeout after each run):
- `UNPROVEN` — seed only, never validated
- `HIGH` — found SEV-1 or ≥2 SEV-2 in last 3 runs
- `MEDIUM` — found ≥1 SEV-2 or ≥3 SEV-3 in last 3 runs
- `LOW` — found ≤2 SEV-3 in last 3 runs
- `DEAD` — 3 consecutive runs with zero findings; demote (do not delete)
- `ARCHIVED` — all antibodies deployed, category permanently impossible

DEAD categories are skipped on the next 2 runs, then re-tested on every 3rd run as a regression check (assumption drift can resurrect a dead category).

---

## High-signal probes (worth keeping verbatim — reused next run)

Format: `[category] probe phrasing → what it caught → run date`

(none yet — first audit populates)

---

## Anti-heuristics (probes proven low-signal — skip)

Format: `[category] probe phrasing → why it was noisy → run date`

(none yet)

---

## Proposed new categories (awaiting promotion)

Categories proposed by Closeout when a finding didn't fit any active row. Promoted to active after appearing in **2 separate runs** (avoid noise-driven proliferation).

Format:

```
### PROPOSED: <id> <name>
- Definition (3 bullets max):
  - ...
  - ...
  - ...
- First seen: <date> in run <N>
- Validation needed: appear again in 1 more run to promote
```

(none yet)

---

## Deployed antibodies (categories progressing toward ARCHIVED)

When an antibody recommendation from a past report gets shipped (commit lands, operator confirms), record it here. When all antibodies for a category are deployed, mark that category `ARCHIVED` in the active table above.

Format: `[category] antibody description → shipped commit SHA → audit-run-that-recommended → archived?`

(none yet)

---

## Meta-audit log (every 3rd audit, taxonomy restructure)

Records of structural changes to this file beyond per-run updates. Pruning, restructuring, renaming categories, updating SKILL.md seeds.

Format:

```
### Meta-audit <date> (after run #<N>)
- Categories pruned: ...
- Categories restructured: old <X> + old <Y> → new <Z> because ...
- SKILL.md seeds updated: yes/no, what changed
- Rationale: ...
```

(none yet — first meta-audit happens after run #3)

---

## Operating notes for the orchestrator

When you (the opus orchestrator running this skill) read this file in Boot step 2:

1. **Dispatch only ACTIVE categories** — skip DEAD (except every 3rd-run regression check) and ARCHIVED.
2. **Inject high-signal probes verbatim** into the relevant worker's brief. Don't paraphrase — exact phrasing matters because it's the empirically validated wording.
3. **Tell each worker their category's Yield level** so they calibrate effort. HIGH = scan deep. LOW = quick sweep.
4. **Cross-check Proposed categories**: if any finding this run fits a Proposed row, that's the 2nd appearance → promote in Closeout.
5. **Check deployed antibodies**: before flagging a SEV-1 in some category, verify the relevant antibody isn't already DEPLOYED (false alarm on archived issue would erode trust).

The whole point of this file is that future-you arrives smarter than past-you. If a run's `Closeout` doesn't update it, the skill silently devolves into a frozen template and loses its reason for existing.
