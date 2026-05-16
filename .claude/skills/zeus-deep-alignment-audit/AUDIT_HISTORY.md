# AUDIT_HISTORY — Zeus Deep Alignment Audit Runs

Append-only log of every completed run of the `zeus-deep-alignment-audit` skill. The skill's `Closeout` step appends one row + one retrospective paragraph per run.

**Never edit past entries.** If a past entry was wrong (back-filled discovery, antibody falsified, etc.), add a new dated note in the entry's retrospective section explaining the update — preserve the original.

---

## Run table

| # | Date | Commit | K root gaps | SEV-1 | SEV-2 | SEV-3 | Coverage | Report |
|---|------|--------|-------------|-------|-------|-------|----------|--------|

(no runs yet)

---

## Run retrospectives

Format per run:

```
### Run <N> — <YYYY-MM-DD> — commit <short-SHA>

**One-paragraph summary**: what was surprising, what pattern recurred from prior runs, what the audit MISSED that a later incident revealed (back-filled in subsequent runs).

**Categories that produced findings**: <list with SEV counts>

**Categories that produced nothing**: <list — track consecutive-empty count toward DEAD demotion>

**New patterns observed**: <bullets — if any didn't fit active categories, they should appear in LEARNINGS.md "Proposed">

**Methodology changes triggered**: <bullets — e.g. "Added probe X to LEARNINGS high-signal", "Demoted category Y to DEAD after 3rd empty run">

**Hand-edits to LEARNINGS.md beyond Closeout** (rare, should be justified): <bullets>
```

(no runs yet — first audit will populate)

---

## Post-mortem index

When a Zeus incident later reveals an issue the audit missed, link it back here so the next run knows the gap.

Format: `<incident date> <one-line description> → audit run #<N> failed to catch because <reason> → category <ID> updated to catch in future`

(none yet)

---

## Operating notes for the orchestrator

When you (the opus orchestrator running this skill) read this file in Boot step 3:

1. **Identify repeat-offender categories**: any category appearing in retrospectives 2+ times → escalate its worker's probe depth on this run.
2. **Identify long-stale categories**: any category with no findings for ≥3 runs → it's a DEAD candidate this run (check Active table in LEARNINGS).
3. **Note any back-filled post-mortems**: they reveal the audit's blind spots. Read recent ones before designing this run's worker briefs.
4. **Track meta-audit cadence**: count entries in the Run table. If this would be run #3, #6, #9, … the Closeout MUST do a meta-audit step (see SKILL.md).
