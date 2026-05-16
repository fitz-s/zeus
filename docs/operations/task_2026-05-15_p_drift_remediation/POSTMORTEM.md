# Drift Remediation Postmortem

Created: 2026-05-15
Authority: Task 2026-05-15 drift remediation (6 claims from DRIFT_REPORT.md)

---

## Summary

Drift audit identified 6 STALE claims across the CLAUDE.md/AGENTS.md chain.
After independent re-investigation: 3 were fixed, 3 were reclassified (audit was wrong).
One audit attribution error discovered (wrong source file named for a claim).

---

## Per-Claim Detail

### CLAIM 1 — FIXED
- **Source file**: `AGENTS.md` line 97
- **Original text**: `9 states in \`LifecyclePhase\` enum:`
- **Corrected text**: `10 states in \`LifecyclePhase\` enum:` + added UNKNOWN as transient/recovery state
- **Evidence**: `src/state/lifecycle_manager.py` lines 9-19 enumerate 10 entries:
  PENDING_ENTRY, ACTIVE, DAY0_WINDOW, PENDING_EXIT, ECONOMICALLY_CLOSED,
  SETTLED, VOIDED, QUARANTINED, ADMIN_CLOSED, UNKNOWN.
  UNKNOWN transitions to QUARANTINED|VOIDED (non-terminal, recovery transient).
  The prior doc listed 6 progression + 3 terminal = 9, missing UNKNOWN entirely.
- **Commit**: `3f838f0d3a` (worktree `agent-a541b544f788ba744`)

### CLAIM 2 — FIXED (with attribution correction)
- **Audit-claimed source**: `HIDDEN_BRANCH_LESSONS.md`
- **Actual source file**: `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md` line 293
- **Original text**: `consolidate the 19 \`topology_doctor_*.py\` sub-modules`
- **Corrected text**: `consolidate the 18 \`topology_doctor_*.py\` sub-modules`
- **Evidence**: `docs/operations/task_2026-05-15_p10_module_consolidation_planning/INVENTORY.md`
  lists 18 modules (confirmed count 18 at "Total modules inventoried:" line).
  On-disk count on this branch = 17 (one module may be in a different branch state).
  INVENTORY.md is the P10 authoritative count; using 18.
  HIDDEN_BRANCH_LESSONS.md contains NO "19" claim — audit misattributed the source file.
- **Commit**: `e9d94c5630` (main zeus repo, deploy branch)

### CLAIM 3 — FIXED (direct file edit, no git)
- **Source file**: `/Users/leofitz/.openclaw/CLAUDE.md` line 33
- **Original text**: `100+ scheduled jobs, primarily finance reporting`
- **Corrected text**: `42 scheduled jobs, primarily finance reporting`
- **Evidence**: `cron/jobs.json` structure: top-level keys are `version` + `jobs`;
  `jobs` array has 42 entries (counted via `python3 -c "import json; d=json.load(open('cron/jobs.json')); print(len(d['jobs']))"`).
  Audit claimed "2" (counted top-level keys, not job entries) — audit was also wrong
  but in the opposite direction. "100+" was written when the system was larger or
  intended scale; 42 is the current actual count.
- **Commit**: N/A — `~/.openclaw/CLAUDE.md` is not tracked in any git repository.
  Edit applied directly to file.

---

## Per-Claim Reclassifications (Audit Was Wrong)

### RECLASSIFIED 1 — "4 active agents" (CORRECT, not stale)
- **Audit verdict**: STALE — claimed actual = "10 agent directories in agents/"
- **Re-investigation finding**: `agents/` directory contains 10 entries:
  claude, codex, default, expat, jupiter, main, neptune, opus, umbriel, venus.
  Of these, most are auth profiles for model providers (claude, codex, default, opus)
  or inactive/test agents (expat, umbriel). `openclaw.json` `agents.list` contains
  exactly 4 running personality agents: main, Venus, jupiter, Neptune — matching
  the CLAUDE.md table row count (Mars/main, venus, jupiter, neptune).
- **Verdict**: RECLASSIFIED — claim "4 active agents" is CORRECT.
  The `agents/` dir includes auth-only and model-routing profiles that are not
  personality agents. The audit used directory count instead of agents.list count.
- **No edit made.**

### RECLASSIFIED 2 — "4 bot accounts" (CORRECT, not stale)
- **Audit verdict**: STALE — claimed actual = "0 discord.accounts in openclaw.json (null)"
- **Re-investigation finding**: `openclaw.json` has no `discord.accounts` key (audit
  looked in the right place for the wrong structure). Discord routing is configured
  via `bindings` array which has 4 entries, each routing a distinct `accountId`
  (default, venus, jupiter, neptune) via the Discord channel. Discord is enabled
  in `channels.discord.enabled = true`.
- **Verdict**: RECLASSIFIED — claim "4 bot accounts route messages to agents" is CORRECT.
  The audit searched for a `discord.accounts` key that doesn't exist; the bot
  accounts are expressed implicitly through the 4 distinct accountIds in `bindings`.
- **No edit made.**

### RECLASSIFIED 3 — "7 past topology/hook redesign packets" (CORRECT, not stale)
- **Audit verdict**: STALE — claimed actual = "46 folders in docs/archives/packets/"
- **Re-investigation finding**: `HIDDEN_BRANCH_LESSONS.md` opens with "This document
  mines seven past topology/hook redesign packets" and then defines exactly 7 named
  iterations (Iteration 1 through Iteration 7). The document repeatedly references
  "the seven" throughout. The 46 total archive packets is the full historical
  archive — unrelated to the 7 packets this document was explicitly written to analyze.
- **Verdict**: RECLASSIFIED — claim is CORRECT in context. "Seven past packets" refers
  to the 7 packets mined by this specific document, not total archive count.
- **No edit made.**

---

## Cross-Claim Pattern Analysis

All three genuinely stale claims share the same root cause: **numeric counts written
once at system-creation time, never re-verified as the system evolved.**

- "9 lifecycle states" was correct when written; VOIDED, QUARANTINED, ADMIN_CLOSED,
  and UNKNOWN were added later as the system matured.
- "19 topology_doctor modules" reflected a count at planning time; consolidation
  brought it to 18 (or 17 on this branch).
- "100+ scheduled jobs" reflects aspirational scale or an earlier state of the cron
  system; the actual `jobs` array shrank to 42.

The three reclassifications reveal a secondary pattern: **audit agents also
drift-hallucinate** — using wrong key paths (discord.accounts), wrong counting bases
(agents/ dir vs agents.list), and wrong context frames (total archive vs. document
scope). The audit itself had a 50% error rate on its STALE verdicts.

---

## Architecture Files Touched

None. All fixes were documentation-only (AGENTS.md, an ops task PACKET_INDEX.md,
~/.openclaw/CLAUDE.md). No `architecture/**` files were modified. No companion-update
entries required.

---

## Recommendation for P5 (mw-daemon periodic maintenance)

Include a monthly CLAUDE.md/AGENTS.md drift sweep task in P5 mw-daemon that
re-verifies numeric claims against source-of-truth (enum entries, JSON array lengths,
file counts) and flags divergences > 20% for human review — because the audit itself
demonstrated that agent-authored numeric claims drift without external re-verification.

---

## Commit Hashes

| Fix | Hash | Repo |
|-----|------|------|
| lifecycle states AGENTS.md | `3f838f0d3a` | worktree agent-a541b544f788ba744 |
| topology module count PACKET_INDEX.md | `e9d94c5630` | zeus main (deploy branch) |
| cron job count ~/.openclaw/CLAUDE.md | N/A (no git) | direct file edit |
