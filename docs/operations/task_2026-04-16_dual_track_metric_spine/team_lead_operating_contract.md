# Team-Lead Operating Contract (post-P5 structural reform)

**Written**: 2026-04-18 post Phase 5 closeout, pre-compact.
**Purpose**: durable reform to team-lead operating model, addressing root causes of Phase 5 "expensive swamp" pattern. This doc OVERRIDES prior methodology where they conflict.

## Diagnosis — what actually went wrong in P5

Not: model failure / discipline breach / hallucination.
Was: **wrong environment model**. Team-lead treated a distributed-concurrency problem as a trust-management problem.

### Four systemic failures

1. **Filesystem treated as atomic oracle**. Is actually eventually-consistent distributed store with multiple writers (agents + VS Code git index + background procs + nested worktrees). Disk-verify captures a snapshot at time T, not authoritative current state. Default hypothesis when report ≠ disk should be "concurrent I/O", but reasoning engine's path of least resistance is "agent lying/hallucinating" (attribution error).

2. **God-Orchestrator serialization**. Team-lead routed every state transition through own context, collapsing parallel N-agent system into single-threaded sync. Communication overhead exceeded execution overhead by ~7500:1 (fix-pack: 300k tokens for 40 LOC). Message race conditions (parallel agent thought vs linear inbox processing) re-spawn nullified work.

3. **Bureaucratic context dilution**. Response to chaos: write more rules. Rules cost tokens forever (every agent every turn reads); structural checks cost zero (one CI hook). Fresh-team onboarding tax (~200k tokens reading methodology + 10 learnings) diluted business-logic attention.

4. **TDD tautology + defensive scope creep**. Testeng reading implementation to write tests = mirror tests that always pass. Fix-pack bloated 3→9 items via "found-it-might-as-well-fix-it" defensive merging.

## Seven Operating Principles (binding for Phase 6 and all future phases)

### P1. Commit is the only state primitive
- Uncommitted working-tree state is INVISIBLE to team-lead for ruling purposes.
- Teammate status reports carry: commit SHA + pytest output + regression delta. Not "file edited".
- Concurrency consistency = git merge semantics, not filesystem reads.
- Corollary: team-lead does NOT disk-verify teammate claims mid-flight. Verify at commit boundary only.

#### P1.1 (added post-P6, coordination-error lesson)
Git INDEX is shared mutable state between team-lead and executors — NOT team-lead private state. Before every `git add`, team-lead runs `git status --short` to see what else is already staged or modified. Any unexpected content in the index is isolated via `git stash -u` or `git reset HEAD -- <file>` BEFORE intentional stage. Never assume `git add <my-file>` operates on a clean index.

Rationale: in P6, team-lead `git add`-ed a doc, not noticing exec-kai had parallel-staged the full Phase 6 implementation. The subsequent `git commit` captured both, landing impl+doc as a single commit mislabeled "docs(...)". Critic review was bypassed accidentally. Root cause was NOT exec discipline failure — exec correctly staged per instructions. Root cause was team-lead mental model: "index is mine". This is the same class of error as the P5 "disk as atomic oracle" failure, one layer down.

### P2. Contract-based scope, not stream protocol
- Phase open = SINGLE contract: "this commit delivers {X, Y, Z} with acceptance = {pytest N/N GREEN + regression ≤ baseline}". Nothing else.
- During phase: team-lead SILENT. No intermediate check-ins. No scope adjustments.
- Phase close = receive {commit hash, pytest output, critic verdict}. Rule PASS or ITERATE.
- Scope adjustment rule: only at phase boundaries. Mid-phase drift = defer to next phase.

#### P2.1 (added post-P6, commit-boundary protocol)
Exec does NOT `git add` or `git commit`. Exec completes work, writes all files, runs tests, then SendMessage team-lead with: (a) list of files to stage, (b) `git diff --stat HEAD` output, (c) pytest tally, (d) full regression delta. Team-lead verifies the announcement, then (per P1.1) runs `git status --short`, isolates unexpected content, stages the announced files, and commits with accurate message. Commit boundary is team-lead's sole responsibility.

Rationale: decouples "work done" from "commit created." Exec can complete-and-announce even if team-lead is still thinking; team-lead creates commit with full scope-accurate message; critic wide-review happens on team-lead's staged candidate BEFORE commit (not after, as P6 accidentally did). Restores the P2 invariant "ONE commit, reviewed, committed, pushed" as sequential steps under team-lead control.

### P3. Checks as code, not rules
- Every rule gets first-asked: "can this be automated?"
- Yes → CI hook / topology_doctor gate / pre-commit check.
- No → methodology file (but periodically prune when automation possible).
- Don't use text rules to fight text problems.

#### P3.1 (added post-P6, critic-beth methodology contribution)
Any commit that INVERTS a contract or REMOVES a guard (e.g. deletes `NotImplementedError`, enables a previously-gated codepath, relaxes a type constraint) MUST be cross-checked against test-naming vocabulary before critic PASS. Critic greps tests for names containing `_refuses_`, `_does_not_`, `_until_phase`, `_rejects_`, `_refused_`, `_forbidden_`, `_blocks_` and verifies each matching test is either (a) updated/repurposed to assert the new invariant, (b) deleted if the old invariant is intentionally retired, or (c) documented as still-valid-but-disjoint. Stale antibody tests that silently flip from GREEN to RED are a Fitz P4 category-impossibility regression even when runtime is unaffected — the class-level structural antibody is lost.

Rationale: in P6, the Phase 1 R2 antibody `test_day0signal_low_metric_refuses_until_phase6` was a stale assertion against a guard Phase 6 intentionally removed. critic's first PASS missed the test-RED state; extra-strict re-review caught it. The heuristic is cheap (one grep per contract-inverting commit) and prevents an entire class of "mechanism-without-matching-antibody" bugs. This is the same diagnostic category as critic-alice's fix-pack miss (`test_read_mode_truth_json_none_mode_does_not_raise`) — two instances establish a pattern worth automating later (P3 prefers automation).

### P4. Spec-first TDD, testeng doesn't grep impl
- R-letter = input/output contract in handoff/scope doc.
- Testeng reads contract, synthesizes inputs, asserts outputs.
- Testeng does NOT grep the function-under-test to see its signature.
- No contract = no test. Spec → tests → code, order inviolable.

### P5. Default to autonomy within scope
- Peer-to-peer (a2a) is the default coordination path.
- Team-lead engages only on: scope decisions, critic escalations, phase transitions.
- Progress tracking = `git log`, not chat channel.
- Teammate idle notifications: informational, not action-demanding.

### P6. Fresh-team onboarding = 3 pointers + 1 executable
- 3 pointer-style docs (< 500 words each): authority, handoff, scope contract.
- 1 bootstrap script: runs tests + emits "ready/not-ready" signal.
- NO mandatory reading of 10-learnings accumulation.
- Teammates pull context on-demand via grep/read on their own time.

### P7. Aggressive deferral, not defensive merging
- Found-mid-flight issue default: → backlog, not → this commit.
- CRITICAL: escalate to team-lead for include-or-defer ruling.
- MAJOR / MINOR: always defer.
- Each phase has ONE scope target. Protect it.

## Phase 6 protocol using these principles

### Phase 6 opens with contract (TO BE WRITTEN post-compact)

```
Commit delivers:
- src/signal/day0_high_signal.py (NEW) — extract Day0Signal HIGH path
- src/signal/day0_low_nowcast_signal.py (NEW) — low nowcast, no historical Platt
- src/signal/day0_router.py (NEW) — route by (metric, causality_status)
- src/engine/evaluator.py:825 — MAX→MIN array fix (co-landing)
- src/monitor_refresh.py:306 — MAX→MIN array fix (co-landing)
- src/signal/day0_signal.py — remove NotImplementedError LOW guard (co-landing)
- src/state/portfolio.py — DT#6 graceful-degradation path
- src/riskguard/riskguard.py — B055 trailing-loss absorption
- tests/test_phase6_day0_split.py (NEW) — R-BA..R-B? invariants
Acceptance:
- pytest test_phase6_day0_split → all GREEN
- full regression ≤ 137 failed / ≥ 1783 passed baseline
- critic-beth wide-review PASS
```

### Phase 6 lanes (independent, parallel, no team-lead mediation)

- exec-ida: state/truth/riskguard lane (DT#6 + B055, extends 5A seam)
- exec-juan: signal/evaluator/monitor lane (Day0 split + co-landing)
- testeng-hank: R-BA..R-B? pre-impl RED tests from contract (no code grep)
- scout-gary: landing-zone scan, one-shot before impl starts, then park
- critic-beth: ONE wide review at commit candidate, L0.0 standing

### Team-lead behavior during Phase 6

- Phase open: issue contract above + lane assignments. Single dispatch.
- Mid-phase: IDLE. Monitor git log passively. Respond ONLY to:
  - scope question requiring ruling (answer in 1-2 sentences, durable, no oscillation)
  - critical escalation from critic (e.g., found structural invariant violation)
- Phase close: receive commit candidate + critic verdict. PASS → team-lead commits. ITERATE → one-cycle fix dispatch.

### Do-not-list during Phase 6

- Do not disk-verify uncommitted state.
- Do not intermediate-check teammate progress.
- Do not add scope mid-flight for any severity below CRITICAL.
- Do not update handoff docs mid-phase.
- Do not write briefs > 500 words.
- Do not onboard fresh agents with > 3 mandatory reads.
- Do not respond to teammate idle notifications unless they carry a question.
- Do not let testeng reshape R-letter scope without explicit ruling request.
- Do not oscillate on scope decisions — first ruling is durable unless CRITICAL evidence emerges.

## Meta-learning

**The P5 expense was structural, not effortful**. Working harder within the wrong architecture makes it worse (more messages, more rules, more context dilution). Working differently (commit as primitive, contract as scope, autonomy as default) reduces cost AND increases quality simultaneously.

This contract lands in git BEFORE compact so post-compact team-lead reads it first — binding operating model, not just historical analysis.
