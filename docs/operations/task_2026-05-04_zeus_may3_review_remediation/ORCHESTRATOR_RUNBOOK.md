# Zeus May3 Remediation Orchestrator Runbook

**Created:** 2026-05-04
**Status:** `LOCK_CANDIDATE_COMPANION` — procedural companion to `MASTER_PLAN_v2.md`.
**Authority basis:** `.claude/skills/orchestrated-delivery`, `.claude/skills/multi-agent-debate-and-execution`, `.claude/skills/team`, `.claude/skills/plan`, `.claude/skills/ralph`, `.claude/skills/verify`, plus Zeus `AGENTS.md` and `MASTER_PLAN_v2.md`.

This runbook does not authorize implementation. It defines how the operator/coordinator should dispatch agents after `LOCK_DECISION.md`, T-1, and T0 exist.

---

## 1. Skill-Derived Principles

1. **Coordinator only.** The main session coordinates, validates gates, dispatches workers, and records state. It does not implement source changes.
2. **Disk is canonical.** SendMessage and chat summaries are delivery only. Boot evidence, phase files, critic reviews, verifier receipts, and closeouts live on disk.
3. **Stage-gated team pipeline.** Use `team-plan -> team-prd -> team-exec -> team-verify -> team-fix`, with handoff files between stages.
4. **Phase JSON per executable unit.** Each packet/phase gets `phase.json` using `orch.phase.v1` with files, tests, invariants, and expected artifact.
5. **Idle-only boot.** Executor, critic, verifier, security-reviewer, and git-master start with boot-only prompts, write `_boot_<role>.md`, send `BOOT_ACK`, then idle.
6. **No direct jump from prompt to edits.** The coordinator must issue explicit `GO_BATCH_X_<PACKET>` after boot evidence is reviewed.
7. **Critic-gated batches.** Every batch gets independent 10-ATTACK critic review, not executor self-validation.
8. **Verifier owns evidence.** Completion claims require verifier-owned receipts with exact commands, cwd, exit code, pass/fail, and unverified residuals.
9. **Co-tenant safe staging.** No broad staging. No commit if unrelated dirty ownership is unclear. Diff-stat sanity check before every staged file.
10. **Operator-only facts stay operator-only.** Agents cannot approve launchd state, venue open orders, credential redaction, production DB truth, or dirty-file ownership ambiguity.

---

## 2. Run State Layout

Use both orchestrated-delivery state and packet-local durable evidence.

```text
.claude/orchestrator/
  state/active_run
  runs/zeus-may3-remediation-20260504/
    state/agent_registry.jsonl
    state/invariants.jsonl
    state/sibling_contracts.jsonl
    tasks/<task_id>/phases/<phase_id>/
      phase.json
      plan/
      execution/
      critic/
    progress.md

.omc/handoffs/
  team-plan.md
  team-prd.md
  team-exec.md
  team-verify.md
  team-fix.md

docs/operations/task_2026-05-04_zeus_may3_review_remediation/
  PLAN.md
  MASTER_PLAN_v2.md
  ORCHESTRATOR_RUNBOOK.md
  LOCK_DECISION.md
  PLAN_LOCKED.md
  scope.yaml
  T-1_*.md
  T0_*.md
  INVARIANTS_LEDGER.md
  phases/
    T1A/
      phase.json
      scope.yaml
      boot/executor.md
      boot/critic.md
      boot/verifier.md
      topology/route_card.md
      execution/execution_result.md
      critic/review.md
      verifier/verification.md
      security/security_review.md
      git/staging_evidence.md
      closeout.md
      receipt.json
    T1F/
    T1BD/
    T1C/
    T1E/
    T1G/
    T1H/
```

Runtime state under `.claude/orchestrator` and `.omc/handoffs` is resume machinery. Packet-local evidence under `docs/operations/.../phases/` is durable evidence.

---

## 3. Team Topology And Division Of Labor

| Role | Model Tier | Persistence | Responsibility | May Edit? |
| --- | --- | --- | --- | --- |
| `team-lead` / coordinator | Opus/main | whole run | Gatekeeper, dispatch, state, receipts, stage transitions | Only docs/run-state after admission |
| `planner` | Opus | per tier/major phase | Phase decomposition, `phase.json`, dependencies, file scope | Plan artifacts only |
| `explore` | Haiku | disposable | Grep maps, call graph, route summaries, evidence lookup | No |
| `executor` | Sonnet; Opus for complex T3 | one active in T1 | Implements only current admitted phase | Yes, only phase scope |
| `critic` | Opus | persistent | 10-ATTACK review, verdict, carry-forward LOWs | No |
| `verifier` | Sonnet | persistent | Independent test/command reproduction | No source edits |
| `security-reviewer` | Sonnet/Opus | T1F/T1G/T2D/T2E/T2H/T3 | Live boundary, secrets, venue, env, alert, DB side-effect review | No |
| `writer` | Haiku | on demand | Docs, ledgers, receipts after evidence exists | Docs only |
| `git-master` | Sonnet | every commit boundary | Explicit staging, diff sanity, commit hygiene, co-tenant protection | Git metadata only after approval |

T1 has exactly one active executor at a time. T2 and T3 may parallelize only after planner and critic agree the phases have disjoint file scopes and disjoint invariants.

---

## 4. Stage Pipeline

### team-plan

Entry: `LOCK_DECISION.md`, T-1, and T0 exist.
Agents: planner + explore + architect if needed.
Outputs:

- stage handoff `.omc/handoffs/team-plan.md`
- phase list
- dependency graph
- first `phase.json`
- phase-local `scope.yaml`

Exit: coordinator approves phase scope and all blocking artifacts.

### team-prd

Use when acceptance criteria are incomplete or operator decisions remain.
Agents: analyst + critic.
Outputs:

- testable acceptance criteria
- operator-only decision list
- stop/replan triggers

Exit: no open decisions that can alter tier hierarchy.

### team-exec

Agents: executor, plus explore for read-only probes.
Output: executor evidence on disk.
Exit: `BATCH_X_DONE_<PACKET>` with evidence path, changed files, tests run, and unresolved risks.

### team-verify

Agents: critic + verifier + security-reviewer where relevant + git-master.
Output: review/verifier/git receipts.
Exit: `APPROVE`, `APPROVE_WITH_CAVEATS`, `REVISE`, or `BLOCK`.

### team-fix

Agents: executor or debugger.
Entry: critic/verifier found blocking defect.
Exit: revised evidence and repeat team-verify.

Max fix loops: 3 before coordinator stops and asks for operator/planner intervention.

---

## 5. Phase JSON Template

Every phase must have `phase.json` before `GO_BATCH_1`.

```json
{
  "schema_version": "orch.phase.v1",
  "phase_id": "T1A",
  "task_id": "zeus-may3-remediation",
  "title": "Single source settlement_commands DDL",
  "intent": "DB initialization uses one canonical settlement_commands schema source without changing trading behavior.",
  "files_touched": [
    "src/execution/settlement_commands.py",
    "src/state/db.py",
    "tests/test_settlement_commands_schema.py",
    "architecture/test_topology.yaml"
  ],
  "loc_delta_estimate": 180,
  "introduces_abstraction": false,
  "cross_module_edges": 1,
  "domain": "state/execution-schema",
  "purely_mechanical": false,
  "test_commands": [
    "python -m pytest -q tests/test_settlement_commands_schema.py"
  ],
  "asserted_invariants": [
    {
      "id": "T1A-DDL-SINGLE-SOURCE",
      "text": "settlement_commands DDL has exactly one inline schema definition"
    }
  ],
  "consumed_invariants": [
    "NO-LIVE-DAEMON-DURING-T1",
    "NO-PRODUCTION-DB-MUTATION",
    "NO-BROAD-STAGING"
  ],
  "expected_artifact": "execution/execution_result.md"
}
```

Planner must regenerate this per phase. Executor must not widen `files_touched` without re-planning.

---

## 6. Coordinator Master Prompt

Use this as the first orchestrator prompt after `LOCK_DECISION.md`, T-1, and T0 exist.

```text
# Zeus May3 Remediation Coordinator

You are the coordinator only. You do not implement source changes, call launchctl, touch venue orders, inspect secrets, or approve operator-only facts.

Authority order:
1. Official Polymarket/CLOB facts
2. Current source/runtime call graph
3. DB schema, command journal, position lots, migrations
4. Tests and CI gates
5. Operator T-1/T0 artifacts
6. MASTER_PLAN_v2.md / PLAN_LOCKED.md / ORCHESTRATOR_RUNBOOK.md
7. Historical plan prose

Read first:
- AGENTS.md
- docs/operations/task_2026-05-04_zeus_may3_review_remediation/PLAN.md
- docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md or PLAN_LOCKED.md
- docs/operations/task_2026-05-04_zeus_may3_review_remediation/ORCHESTRATOR_RUNBOOK.md
- docs/operations/task_2026-05-04_zeus_may3_review_remediation/scope.yaml
- LOCK_DECISION.md
- all T-1 artifacts
- all T0 artifacts

Hard gate:
Stop before Tier 1 if LOCK_DECISION.md is absent, T-1/T0 is incomplete, T0_PROTOCOL_ACK.md does not say proceed_to_T1, daemons are not operator-proven stopped, venue quiescence is not operator-proven, or dirty-file ownership is unclear.

Team topology:
- planner: phase decomposition and phase.json
- explore: read-only scans
- executor: one admitted packet only
- critic: independent 10-ATTACK review
- verifier: independent command reproduction
- security-reviewer: live/venue/secrets/env/DB side-effect review
- writer: docs/receipts only after evidence
- git-master: explicit staging and commit hygiene

Stage flow:
team-plan -> team-prd if needed -> team-exec -> team-verify -> team-fix loop.
Write a handoff before each stage transition under .omc/handoffs/.

For each phase:
1. Planner writes phase.json and phase-local scope.yaml.
2. Coordinator validates topology admission and planning-lock.
3. Executor/critic/verifier/security/git-master receive BOOT-ONLY prompts.
4. Coordinator reviews boot evidence.
5. Coordinator sends GO_BATCH_1.
6. Executor writes evidence, not just chat summary.
7. Critic runs 10-ATTACK review.
8. Verifier independently reproduces commands.
9. Git-master checks exact staging and diff sanity.
10. Coordinator advances only after evidence, review, verification, and required operator approval.

Never allow:
- broad staging
- live daemon control
- venue side effects
- secret inspection
- production DB mutation unless the phase explicitly owns it
- corrected-live enablement by env var
- docs as runtime proof
- executor self-approval
```

---

## 7. Idle-Only Bootstrap Prompts

### Executor Boot

```text
You are executor for <PACKET> in Zeus May3 remediation.

BOOT ONLY. Do not edit files. Do not run tests that mutate state. Do not stage or commit. Do not spawn subagents.

Read:
- AGENTS.md
- scoped AGENTS.md for allowed files
- MASTER_PLAN_v2.md sections for <PACKET>
- ORCHESTRATOR_RUNBOOK.md
- phase.json
- phase-local scope.yaml
- required prior artifacts listed in phase.json

Write boot evidence to:
docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/<PACKET>/boot/executor.md

Boot evidence must include:
## 0 Read Summary
## 1 KEY OPEN QUESTIONS
## 2 File/Scope Confirmation
## 3 Risk Map
## 4 Planned Batches
## 5 Tests Expected To Fail Before Fix
## 6 Out-Of-Scope Reaffirmation
## 7 Defaults If Coordinator Does Not Override

Send: BOOT_ACK_EXECUTOR_<PACKET> path=<absolute path>. Then idle.
```

### Critic Boot

```text
You are critic for <PACKET> in Zeus May3 remediation.

BOOT ONLY. Do not edit files. Do not approve anything yet.

Read:
- MASTER_PLAN_v2.md
- ORCHESTRATOR_RUNBOOK.md
- phase.json
- phase-local scope.yaml
- required prior artifacts
- scoped AGENTS.md for touched surfaces

Write boot evidence to:
docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/<PACKET>/boot/critic.md

Include:
## 0 Read Summary
## 1 Attack Plan
## 2 Packet-Specific Failure Modes
## 3 Operator-Only Claims You Cannot Approve
## 4 Expected Evidence Before Review
## 5 Anti-Rubber-Stamp Pledge

Send: BOOT_ACK_CRITIC_<PACKET> path=<absolute path>. Then idle.
```

### Verifier Boot

```text
You are verifier for <PACKET> in Zeus May3 remediation.

BOOT ONLY. Do not edit source. Do not trust executor test claims.

Read phase.json and identify exact verification commands, cwd, environment assumptions, expected pre-fix failures, and unverified manual facts.

Write boot evidence to:
docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/<PACKET>/boot/verifier.md

Send: BOOT_ACK_VERIFIER_<PACKET> path=<absolute path>. Then idle.
```

---

## 8. GO / DONE / REVIEW Protocol

### GO_BATCH_X

```text
GO_BATCH_<X>_<PACKET>

Boot evidence approved:
- executor: <path>
- critic: <path>
- verifier: <path>

You may implement only batch <X> for <PACKET>.

Allowed files:
<exact files from phase-local scope>

Forbidden files:
<exact forbidden list>

Required companion updates:
<manifest/header/docs entries>

Commands to run before editing:
- pwd
- git rev-parse --show-toplevel
- git branch --show-current
- git rev-parse --short HEAD
- git worktree list
- git status --short
- python3 scripts/topology_doctor.py --navigation --task "<packet>" --files <explicit files> --intent "<typed intent>" --write-intent edit --operation-stage edit --side-effect repo_edit
- python3 scripts/topology_doctor.py --planning-lock --changed-files <explicit files> --plan-evidence docs/operations/task_2026-05-04_zeus_may3_review_remediation/PLAN.md

Execution rules:
- Write failing relationship tests first where applicable.
- Implement minimally.
- Update manifests/headers before using new tests/scripts as evidence.
- Do not stage or commit.
- Write execution result to disk before reporting.
```

### BATCH_DONE

Executor must report only after writing:

```text
docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/<PACKET>/execution/execution_result.md
```

Required content:

```markdown
# <PACKET> Batch <X> Execution Result

## Summary

## Files Changed

## Tests Added Or Updated

## Commands Run
| Command | Cwd | Exit | Result |

## Expected Pre-Fix Failures Observed

## Invariants Established

## Invariants Consumed And Preserved

## Manifest/Header Updates

## Deviations From Phase Scope

## Residual Risks

## Ready For Critic
```

### Critic Review Dispatch

Critic gets:

- phase.json
- scope.yaml
- executor boot evidence
- execution_result.md
- git diff
- test output
- prior invariants ledger
- sibling contracts

Critic verdicts:

- `APPROVE`
- `APPROVE_WITH_CAVEATS`
- `REVISE`
- `BLOCK`

`APPROVE_WITH_CAVEATS` must emit carry-forward LOWs with IDs.

---

## 9. Critic 10-ATTACK Template

Every critic review must include at least these probes:

1. **Independent test reproduction.** Re-run the narrow tests from a fresh shell.
2. **Expected pre-fix failure evidence.** Confirm the test would have failed before the fix, or explain why verification-first is acceptable.
3. **Diff/file-count verification.** Compare executor claims to `git diff --stat` and file list.
4. **Scope verification.** Confirm all changed files are in phase scope or allowed companions.
5. **Cite-content verification.** Check cited files contain the claimed content, not just line numbers.
6. **K0/K1/K2/K3 surface attack.** Verify live/schema/DB/venue/write surfaces are not touched outside the phase contract.
7. **Manifest/header verification.** Scripts/tests/docs registries updated where required.
8. **Operator-only claim rejection.** Flag any claim about launchd, venue, credentials, production DB, or dirty-file ownership without operator artifact.
9. **Semantic invariant attack.** Try to construct the exact forbidden state the packet claims to block.
10. **Co-tenant safety.** Confirm no unrelated dirty files were staged or absorbed.
11. **Rollback viability.** Confirm the rollback path is realistic and scoped.
12. **Runbook actionability.** Confirm closeout tells the next packet what changed and what remains blocked.

Rubber-stamp tells that invalidate review:

- “pattern proven” without naming the test;
- “narrow scope self-validating”;
- “trust executor’s test count”;
- “all tests pass” without command names and exit codes.

---

## 10. Verifier Receipt Template

Verifier writes:

```text
docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/<PACKET>/verifier/verification.md
```

Required content:

```markdown
# <PACKET> Verification Receipt

## Scope Verified

## Commands
| Command | Cwd | Exit Code | Key Output | Verified Claim |

## Existing Tests

## New Tests

## Grep/Static Checks

## Build/Type/Lint Checks

## Manual Evidence Required But Not Available To Verifier

## Failures Or Warnings

## Verdict
VERIFIED | PARTIAL | FAILED
```

Verifier cannot mark `VERIFIED` if any required artifact is missing, any command failed, or any manual operator fact is being inferred from code.

---

## 11. Git-Master Staging Protocol

Git-master runs only after executor, critic, and verifier complete.

Required commands:

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git rev-parse --short HEAD
git worktree list
git status --short
git diff --stat <explicit files>
git diff -- <explicit files> | head -200
```

Rules:

- Never use `git add -A`, `git add .`, `git commit -am`, or broad globs.
- Stage explicit paths only.
- If a changed file has unrelated edits, stop and ask coordinator/operator.
- If diff stat is larger than phase expectation, stop.
- If HEAD moved unexpectedly during the phase, stop and re-run topology/status checks.
- Do not commit unless the operator asked for commits. If commit is authorized, commit one phase only.

Staging evidence path:

```text
docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/<PACKET>/git/staging_evidence.md
```

---

## 12. T1 Execution Division

T1 is serialized. Do not parallelize implementation.

| Phase | Executor | Critic | Security | Verifier | Notes |
| --- | --- | --- | --- | --- | --- |
| T1A | executor-sonnet | critic-opus | optional | verifier-sonnet | Schema single source; no behavior change beyond DDL import. |
| T1F | executor-sonnet | critic-opus | security-reviewer-sonnet/opus | verifier-sonnet | Venue boundary; attack SDK contact and placeholder identity hardest. |
| T1BD | executor-opus or sonnet with opus critic | critic-opus | optional | verifier-sonnet | K0/K1 state/lifecycle; paired counters; no split commits. |
| T1C | executor-sonnet | critic-opus | security optional | verifier-sonnet | Settlement/redeem/learning split; harvester live policy required. |
| T1E | executor-sonnet | critic-opus | security optional | verifier-sonnet | DB lock behavior; T0 timeout policy controls value. |
| T1G | executor-sonnet | critic-opus | security-reviewer-opus | verifier-sonnet | Venue provenance audit; verification-first. |
| T1H | executor-sonnet | critic-opus | optional | verifier-sonnet | Read-only census; script/test manifests required. |

Parallelism allowed during T1:

- read-only explore scans for the next phase;
- critic/verifier/security review after executor DONE;
- writer drafting closeout after evidence exists.

Parallelism forbidden during T1:

- two executor agents editing simultaneously;
- T1BD split into separate B and D commits;
- T1E before T1C if harvester policy can affect DB write paths;
- T1H before T1G if census would classify submit/fill provenance without final SDK path audit.

---

## 13. T2/T3 Parallelism Rules

T2 may parallelize only after planner emits disjoint phase scopes.

Candidate grouping:

- Group A: T2A brake + T2B census expansion.
- Group B: T2C drift checker + T2D semantic gates + T2F sentinel ledger.
- Group C: T2E alert delivery + T2H live-control side-effect SLA.
- Group D: T2G DB isolation decision; operator-gated, may block T3.

T3 should be split into semantic-spine packets:

1. T3-P0 semantic tests and CI gates.
2. T3-P1 contract package.
3. T3-P2 Kelly/executor/FDR/order-policy/same-object journal.
4. T3-P3 exit/fill/PositionLot.
5. T3-P4 cohorts/reporting/source/city/time.
6. T3-P5 telemetry/quarantine/docs/promotion runbook.

For T3-P2 and any schema/migration/live-boundary packet, use opus-tier critic by default.

---

## 14. Stop And Replan Triggers

Stop the packet and return to planner/operator if any of these occur:

- `LOCK_DECISION.md` absent or inconsistent.
- T-1 or T0 artifact missing, malformed, stale, or says stop/revise.
- Topology navigation rejects files or returns advisory-only for edit work.
- Phase scope excludes a file the executor needs.
- `phase.json` loc estimate or file set is materially wrong.
- Live daemon or RiskGuard state is uncertain.
- Venue open-order state is uncertain.
- Credential/secret redaction is uncertain.
- Production DB truth is needed but no operator artifact exists.
- Critic returns `REVISE` or `BLOCK`.
- Verifier cannot reproduce a required claim.
- Dirty worktree ownership is ambiguous.
- HEAD moves unexpectedly during a phase.
- Three fix loops fail on the same packet.

---

## 15. Closure Review

Before claiming the full plan is ready for corrected-live gates, run five parallel closure reviews:

| Role | Focus | Output |
| --- | --- | --- |
| architect | cross-module structure and long-term maintainability | `evidence/closure/architect_review_YYYY-MM-DD.md` |
| critic | adversarial failure modes and edge cases | `evidence/closure/critic_review_YYYY-MM-DD.md` |
| explore | coverage, orphan code, unintended references | `evidence/closure/explore_review_YYYY-MM-DD.md` |
| scientist | empirical/statistical validity where applicable | `evidence/closure/scientist_review_YYYY-MM-DD.md` |
| verifier | evidence adequacy and test sufficiency | `evidence/closure/verifier_review_YYYY-MM-DD.md` |

Team-lead synthesizes closure into `closure_summary.md`. Operator merges or promotes; agents do not.

---

## 16. Minimal Dispatch Checklist

Before sending any executor prompt:

- [ ] `LOCK_DECISION.md` exists and names active plan.
- [ ] `PLAN_LOCKED.md` exists or lock decision points to `MASTER_PLAN_v2.md`.
- [ ] T-1 artifacts complete.
- [ ] T0 artifacts complete and `T0_PROTOCOL_ACK.md` says `proceed_to_T1`.
- [ ] Current `git status --short` recorded.
- [ ] Phase-local `phase.json` exists.
- [ ] Phase-local `scope.yaml` exists.
- [ ] Topology route admitted exact files.
- [ ] Planning-lock passes where required.
- [ ] Map-maintenance expectations known.
- [ ] Boot-only prompts sent and ACKed.
- [ ] Coordinator reviewed KEY OPEN QUESTIONS.
- [ ] Operator decisions resolved.

Before closing any phase:

- [ ] Executor evidence exists.
- [ ] Critic 10-ATTACK receipt exists.
- [ ] Verifier receipt exists.
- [ ] Security review exists for live/venue/env/DB/alert surfaces.
- [ ] Git-master staging evidence exists if committing/staging.
- [ ] Invariants ledger updated.
- [ ] Carry-forward LOWs recorded or resolved.
- [ ] Known gaps updated only where evidence supports it.
- [ ] Next phase dependencies are explicit.
