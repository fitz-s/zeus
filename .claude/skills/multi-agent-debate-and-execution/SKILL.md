---
name: multi-agent-debate-and-execution
description: Complete lifecycle skill for running long-horizon multi-agent engineering work — adversarial debate that produces verdicts, critic-gated batch execution that ships work in small reviewable units, and closure-phase multi-angle review before main-branch merge. Auto-loads when task involves "adversarial debate", "critic-gate", "longlast teammate", "packet execution", "batch dispatch", "multi-batch", "verdict drift", "PR closure review", "stage-gated revert", "honest disclosure", "cite-CONTENT discipline", "MEDIUM-risk surface pre-flag", "PATH A precision-favored", "sibling-coherence", "carry-forward LOW", "5th outcome category", "anti-rubber-stamp template", "co-tenant safe staging", "stash-and-patch", "plumbing-merge", "cross-module orchestration seam", or when work spans 30+ hours / 5+ batches / 2+ rotating sessions. Generic to any project; case-study Zeus R3 §1 #2 (5 packets / 32 critic cycles / 100% anti-rubber-stamp / 1 earned REVISE / 0 BLOCK).
---

# Multi-Agent Adversarial Debate + Critic-Gated Execution Lifecycle

A complete reusable workflow for long-horizon engineering work that needs high-trust verification across rotating sessions.

## §0 Scope — when this skill applies

Use this skill when:
- Engineering work will span 30+ hours / 5+ batches / multiple rotating sessions
- High-risk surfaces (live modules, schemas, calibration stores, retrain pipelines) are touched
- Work requires verifiable trust across session boundaries
- The cost of a silent regression > the cost of explicit review gates
- A team of multiple agents must coordinate without a single agent owning end-to-end context

Do NOT use this skill for:
- One-shot small fixes (a single commit, no batch decomposition)
- Pure exploration with no implementation deliverable
- Tasks where one agent can hold full context end-to-end

## §1 Lifecycle overview — three phases

```
PHASE A — DEBATE                PHASE B — EXECUTION              PHASE C — CLOSURE
────────────────                ────────────────────             ──────────────────
R1: Pro / Con / cross-exam      Per-packet boot + 3-batch        Multi-angle review
R2: Alt-system / synthesis      GO → DONE → REVIEW cycle         (architect/critic/
R3: Capital allocation /        Critic-gate per batch             explore/scientist/
    sequencing                  K3-surface pre-flag               verifier)
                                Carry-forward LOW
Output: verdict.md              Output: shipped commits           Output: PR-ready
                                + critic evidence trail            with full review
```

The DEBATE phase is fully covered by the existing methodology doc at `docs/methodology/adversarial_debate_for_project_evaluation.md` (§0-§8). This skill focuses on **EXECUTION** + **CLOSURE** which were previously implicit in §5.

## §2 Pre-execution setup

### §2.1 Spawn longlast teammates

You need at minimum two persistent teammates (`team` skill or equivalent):
- **executor** — implements batches; reads context; surfaces KEY OPEN QUESTIONS in boot evidence; commits with critic-gate APPROVE
- **critic** — independently reviews each batch; runs 10-ATTACK probes; produces APPROVE / APPROVE-WITH-CAVEATS / REVISE / BLOCK verdicts

Optionally:
- **document-specialist** — for SDK/API documentation lookups during execution
- **explore** — for codebase searches that would clutter executor's context

### §2.2 Idle-only bootstrap

Spawn each teammate with an **idle-only** bootstrap prompt:
```
You are <name> in team <team-name>. Judge: team-lead.

ROLE: <one sentence>

This is BOOT-ONLY. Do NOT engage substantively. Do NOT take action.

Read these files:
- <root authority doc>
- <relevant invariants>
- <recent verdict / dispatch>

Then write to <evidence-path>/_boot_<name>.md (≤300L) with:
- §0 read summary
- §1 expected challenges
- §2 known limitations
- §3 idle commitment

SendMessage team-lead: "BOOT_ACK_<NAME> path=<abs path>". Then idle.

DO NOT engage. Wait for explicit dispatch.
```

This avoids the failure mode where teammates start work before the team-lead has reviewed boot evidence and confirmed scope.

### §2.3 Team config + disk-first protocol

- Use `~/.claude/teams/<team-name>/config.json` (or platform equivalent)
- All inter-agent state lives on disk first; SendMessage is delivery only
- Teammates write evidence files BEFORE sending status messages
- SendMessage drop pattern is empirical — disk is canonical

### §2.4 Critic gating discipline

The critic MUST:
- Be a **separate persistent agent** (not Agent-tool-spawned subagent which dies on session restart)
- Have its own SKILL bootstrap loaded
- Run independently of executor's claims (re-runs tests, re-verifies cites, re-greps surfaces)
- Use the 10-ATTACK template (§9 below)
- Never write "narrow scope self-validating" or "pattern proven without test" — these are rubber-stamp tells

## §3 Per-packet boot template

A "packet" is a coherent body of work that decomposes into 2-5 batches. Each packet starts with a BOOT phase.

### §3.1 Team-lead dispatches DISPATCH_<PACKET>

```
DISPATCH_<PACKET_NAME> (one-line dispatch)

Authority basis: <verdict path + section anchor>

PACKET SCOPE per <plan source §X>:
  "<verbatim quote of what the packet covers>"

Risk rating: LOW / MEDIUM / HIGH / HIGHEST per <criterion>

DEFAULT FRAMING (open to your boot challenge):
  PATH A measurement-only first (precision-favored; see §6)

PROVISIONAL N-BATCH DECOMPOSITION:
- BATCH 1 (~Xh): <description>
- BATCH 2 (~Yh): <description>
- BATCH 3 (~Zh): <description>

NOT-IN-SCOPE (will reject expansion):
- <list of files/surfaces NOT to touch>
- <list of explicitly deferred items>

CARRY-FORWARD from prior packet (fold into BATCH N):
- LOW-X: <description>

BOOT-ONLY DISPATCH:
- Read <list of context files>
- Verify K1 read-only contract for <surface>
- Write boot evidence to <abs path> (~150-300L) with §0/§1/§2/§3/§4/§5/§6 structure
- SendMessage `BOOT_ACK_<EXECUTOR>_<PACKET> path=<abs>`. Then idle.

DO NOT execute BATCH 1 until you receive explicit GO_BATCH_1_<PACKET>.
```

### §3.2 Executor writes boot evidence (§0-§6 standard structure)

```markdown
# <PACKET> packet — executor boot

Created: YYYY-MM-DD
Author: <executor>@<team>
Source dispatch: DISPATCH_<PACKET>
Plan-evidence basis: <verdict>

## §0 Read summary
| Source | What I learned |
|---|---|
| <file:line> | <one-line takeaway> |

## §1 KEY OPEN QUESTIONS (the load-bearing findings)
### KEY OPEN QUESTION #1 — <structural reality finding>
**Dispatch said:** "<verbatim quote>"
**Reality at HEAD:** <what I actually found>
**Implication:** <PATH A/B/C choice>

## §2 Per-batch design sketch
### BATCH 1 — <function-or-module> + tests (~Xh)
**Files**: <list>
**Function signature**: <code>
**Tests** (~N tests): <numbered list>

## §3 Risk assessment per batch
| Batch | Risk | Mitigation |

## §4 Discipline pledges
- ARCH_PLAN_EVIDENCE = <path>
- file:line cites grep-verified within 10 min before commit
- LOW-CAVEAT-XX-N-M lessons applied
- Co-tenant safe staging
- NO commits without critic-gate APPROVE

## §5 Out-of-scope
- <NOT-IN-SCOPE items reaffirmed>

## §6 Open clarifications for team-lead (defaults if no specific guidance)
1. **<question>**: option A / B / C. **Default: <recommendation>**
```

### §3.3 The KEY OPEN QUESTION pattern is load-bearing

Boot evidence ALWAYS surfaces structural reality mismatches between dispatch's intended axis and HEAD's actual surface. Empirically caught 4 of 5 times in case study:
- WS_POLL: dispatch said "ws_share/poll_share" → HEAD has no `update_source` → PATH A
- CALIBRATION: dispatch said "(city, target_date, strategy_key)" → HEAD persists per-bucket → PATH A
- LEARNING_LOOP: dispatch said "no append-only history (per prior packet)" → HEAD HAS calibration_params_versions → HONEST DISCLOSURE

Skipping this surfaces the misread later as a critic finding (or worse, ships with the misread).

## §4 Per-batch GO / DONE / REVIEW cycle

### §4.1 Team-lead dispatches GO_BATCH_X with §6 resolutions

```
GO_BATCH_X_<PACKET> — boot evidence APPROVED.

§6 clarification resolutions (all <N> ACCEPT-DEFAULT):
1. **<question>**: <decision>
...

EXECUTION ORDER (strict):
- BATCH X → critic-gate review → land if APPROVE
- File:line + content cites grep-verified within 10 min before commit (cite-CONTENT discipline)
- NO `git add -A` (co-tenant safety)
- Update BASELINE_PASSED in pre-commit-invariant-test.sh
- Per-batch critic dispatch is ON ME after each BATCH_X_DONE

Cross-batch reminder: HARD NOT-IN-SCOPE for <surfaces> is the WRITER side.

Idle awaiting BATCH_X_DONE_<PACKET>.
```

### §4.2 Executor implements + sends BATCH_X_DONE

Standard executor message format:
```
BATCH_X_DONE_<PACKET> files=<file1> + <file2> + ... tests=<N> passed <M> skipped vs UPDATED baseline <N>/<M>/<F> → EXACT MATCH baseline=preserved planning_lock=<receipt or N/A>

Commit: <SHA> "<commit subject>"

<paragraph: design summary, key tradeoffs, sibling-coherence cites>

CRITIC PRE-FLAG (per GO_BATCH_X instruction): <surface> is <RISK> per <AGENTS.md cite>. The N read additions are <pure SELECT / no schema mutation / no impact on writers>. Critic-harness <Nth> cycle should verify: (a) ..., (b) ..., (c) ...

<N> tests:
- <category>: <test names>

Carry-forward lessons honored:
- LOW-X-Y honored via <how>

NOT-TOUCHED per dispatch §NOT-IN-SCOPE: <list>

Idle for critic-harness <Nth> cycle review.
```

### §4.3 Team-lead independent verification + critic dispatch

Before forwarding to critic, team-lead independently:
1. Re-runs the cited test count (pytest the specific files)
2. Verifies BASELINE_PASSED arithmetic
3. Greps for K1 violations (INSERT/UPDATE/DELETE in NEW lines only — `git diff PREV..NEW` filtered)
4. Confirms file count matches commit (`git show --stat`)

Then dispatches critic with structured 10-ATTACK probe list (§9).

### §4.4 Critic returns BATCH_X_REVIEW_DONE

Standard critic verdict format:
```
BATCH_X_REVIEW_DONE_<PACKET> <VERDICT> path=<critic evidence file>

<Nth> critic cycle. <VERDICT> (<count> LOW <count> MEDIUM <count> BLOCK <count> REVISE).

<paragraph: which probes PASSED / which surfaced concerns>

Verification (N ATTACK probes all PASS):
- <bullet list>

Cycle-prior LOWs RESOLVED:
- LOW-X-Y-Z: <how it was resolved>

NEW LOWs track-forward:
- LOW-X-Y-Z (severity): <description> + <suggested fix path or DEFERRED>

AUTHORIZE push of <SHA> → <PACKET> BATCH X LOCKED. Ready for GO_BATCH_(X+1).

Cycle metrics: N cycles, A clean APPROVE, B APPROVE-WITH-CAVEATS, C REVISE, D BLOCK.
```

### §4.5 Team-lead pushes (if APPROVE) and dispatches next batch

After APPROVE-or-better:
1. `git push origin <branch>` (FF expected)
2. Dispatch GO_BATCH_(X+1) with carry-forward LOWs from this cycle folded in
3. Update task tracker

If REVISE:
1. Forward critic's defects list to executor
2. Wait for executor's BATCH_X_REVISE commit
3. Re-dispatch critic for follow-up review

## §5 3-batch decomposition pattern

For measurement/observation packets, the recurring decomposition is:

**BATCH 1 — Pure-data projection**
- Read-only K1-compliant surface
- Returns dict[bucket_key, snapshot_dict]
- Sample-quality boundaries (e.g., 10 / 30 / 100 thresholds)
- ~6-10h, ~9-15 tests
- Mesh registration: source_rationale.yaml + test_topology.yaml

**BATCH 2 — Detector**
- Pure-Python over BATCH 1 outputs
- Ratio test or KL-divergence (sibling defaults: 1.5x warn / 2.0x critical)
- Verdict dataclass: `kind` Literal + `severity` Optional + `evidence` dict
- insufficient_data graceful (trailing_std<=0 or n_windows<min)
- ~4-6h, ~6-7 tests

**BATCH 3 — Weekly runner + AGENTS.md + e2e tests**
- CLI script: `--end-date / --window-days / --critical-cutoff / --override-bucket KEY=VALUE / --db-path / --report-out / --stdout`
- JSON output: `report_kind=<name>_weekly, report_version=1, ...`
- Exit 0 if no detection; exit 1 if any (cron-friendly)
- Sibling-symmetric with prior weekly runners (script_manifest.yaml entry field-by-field same shape)
- AGENTS.md sections: Scope / Output schema / Threshold defaults TABLE / KNOWN-LIMITATIONS / Severity tier rationale / Operator runbook
- ~3-5h, ~5-7 e2e tests

For other packet shapes (action / mutation), this decomposition may not fit — adapt or use a different decomposition. The pattern works for OBSERVABILITY packets specifically.

## §6 Risk framing patterns

### §6.1 PATH A / B / C decision tree

When dispatch's intended axis doesn't match HEAD's substrate:

| Path | Framing | Use when |
|------|---------|----------|
| **PATH A** (precision-favored) | Drop the unsupported axis from contract; measure only what's supported | Default. Honest. Documented limitation. Mirrors prior 4-of-5 packets in case study. |
| **PATH B** (recall-favored heuristic) | Use a heuristic classifier or proxy join | Only when operator explicitly authorizes; risks invented-data critique. |
| **PATH C** (writer extension) | Modify the upstream writer to add the missing axis | Out-of-scope by default; requires explicit operator dispatch as separate packet. |

### §6.2 Surface classification (K0 / K1 / K2 / K3)

Define for your project (or import from existing AGENTS.md):
- **K0** — frozen kernel (DB schema, contracts); never mutate without schema migration packet
- **K1** — read-only projections; pure SELECT; aggregation in memory; safe extension surface
- **K2** — derived/auxiliary state; controlled mutation OK with critic-gate
- **K3** — live execution path; touching writer side requires HIGH-risk gate + operator dispatch

### §6.3 K3-adjacent surface pre-flag pattern

When a packet needs to add a read-only function to a K3 module (e.g., `list_active_X(conn) -> list[dict]`):
1. Add ONLY pure-SELECT (zero INSERT/UPDATE/DELETE)
2. Pre-flag in commit message + dispatch + boot evidence
3. Critic dispatch includes explicit "attack hardest here" instruction with specific verification (read filter exactly mirrors existing reader; no cross-coupling; pre-table-missing graceful)
4. Sibling-coherent with prior K3 read additions

Used 3× in case study (CALIBRATION store.py + LEARNING retrain_trigger.py); each verified clean by critic.

### §6.4 HIGHEST-risk surface boot-then-confirm

When packet inherently touches HIGH-risk modules (writers, retrain triggers, calibration store mutators):
1. Dispatch BOOT-ONLY first (no GO_BATCH_1 in same message)
2. Executor surfaces full risk surface map in §3 of boot evidence
3. Team-lead reviews with explicit veto power on each touched surface
4. Operator can intervene at boot-evidence stage before any code changes

## §7 Discipline patterns (load-bearing antibodies)

### §7.1 Carry-forward LOW pattern

Each critic cycle produces 0-N LOW caveats. They aren't blocking but they accumulate. Pattern:
- Cycle N produces LOW-X-N-M (e.g., LOW-CITATION-CALIBRATION-1-1 from cycle 27)
- Cycle N+1 dispatch explicitly folds LOW-X-N-M into BATCH (N+1) carry-forward instructions
- Cycle N+1 executor addresses it; cycle N+1 critic verifies the fix
- LOWs not addressed by next cycle become "tracked forward" — eligible for follow-up commits

Empirically: 24 of 32 cycles produced APPROVE-WITH-CAVEATS; ALL LOWs resolved by packet close.

### §7.2 Cite-CONTENT discipline (cycle-29 sustained)

Beyond grep-verifying file:line citations, also verify the cited CONTENT actually says what the citation claims. Cycle 29 caught a `src/calibration/AGENTS.md L14-22 alpha-decay rationale` cite where L14-22 was a danger-level table, not the alpha-decay rationale.

Empirical dividend: cycle 30 immediately caught a substrate misread (claim "no append-only history" → reality `calibration_params_versions` exists in retrain_trigger.py). The discipline note IS an antibody — Fitz Constraint #3 immune system pattern.

### §7.3 HONEST DISCLOSURE pattern

When you discover a prior packet/cycle made a substrate-misread claim:
1. Surface in current packet's module docstring
2. Add cross-link correction in prior packet's AGENTS.md (§CORRECTION subsection)
3. Cite the cycle-N discipline lesson that caught it
4. Acknowledge without dramatic framing (executor stays calm; critic verifies independently)

This converts a near-miss into a methodology dividend — the operator sees the discipline producing measurable value.

### §7.4 Boundary tests rigor (LOW-CAVEAT-EO-2-2)

For every threshold (warn vs critical, ratio vs absolute, days vs counts):
- Pin EXACTLY the threshold value (e.g., ratio==1.5 → within_normal; ratio==1.501 → drift)
- Test BOTH directions of the boundary
- Make strict-vs-inclusive semantics explicit in test names

This catches off-by-one defects that ratio-test detectors are otherwise vulnerable to.

### §7.5 Co-tenant safe staging

When operator (or another agent) has unstaged work in the same repo:
1. NEVER `git add -A` (absorbs co-tenant changes)
2. Stage SPECIFIC paths: `git add -- path1 path2 ...`
3. Verify staged set with `git status --short` before commit
4. If a shared file (e.g., `architecture/test_topology.yaml`) needs your single-line addition AND has co-tenant edits:
   - Stash the file: `git stash push -- <file>`
   - Re-edit your single-line change in clean state
   - Stage YOUR file
   - `git stash pop` to restore co-tenant edits (still unstaged)

Verified successful in CALIBRATION_HARDENING BATCH 3 + LEARNING_LOOP BATCH 3.

### §7.6 Plumbing-merge for hook-bypass FF

When a clean FF merge is correct but pre-commit hooks fail due to co-tenant WIP test failures:
```bash
TREE=$(git merge-tree --write-tree main feature)
COMMIT=$(git commit-tree $TREE -p main -m "merge(sync): ...")
git update-ref refs/heads/main $COMMIT
```
This bypasses working tree + index + hooks. Only use when:
- The merge is provably FF (no conflicts)
- The hook failures are confirmed co-tenant WIP (not real regressions)
- Operator has explicitly authorized

## §8 Cross-module composition seam

When a weekly runner needs to compose outputs from sibling packets (e.g., parameter_drift result feeds into learning_loop_stall detector):

**Anti-pattern**: detector module directly imports + reads sibling module's DB → cross-coupling, harder to test, breaks K1 purity

**Pattern**: caller-provided seam
```python
# Bad: detector reads cross-module
def detect_X(conn, ...):
    drift = compute_other_thing(conn, ...)  # cross-module DB read
    ...

# Good: caller provides cross-module result
def detect_X(history, *, drift_detected=None, ...):  # pure Python
    if drift_detected is None:
        # honest tri-state: we can't tell yet
        ...
```

The orchestration happens in the runner (BATCH 3), which is allowed to call multiple sibling modules. Detector modules stay pure.

**Tri-state honesty**: when CALIBRATION's parameter snapshot history is insufficient, runner records `drift_detected=None` (not False). False would imply "we checked and found no drift"; None correctly says "we can't tell yet". Operator runbook documents the distinction.

## §9 Critic 10-ATTACK template

Standard critic dispatch includes 10-12 ATTACK probes. Categories that empirically catch defects:

| # | Probe category | Example |
|---|---|---|
| 1 | Independent test reproduction | "Re-confirm baseline X/Y/Z independently in a fresh shell" |
| 2 | Independent CLI/REPL probe | "cd /tmp && python3 /repo/scripts/<runner> ... → exit 0 + parseable" |
| 3 | Surface coupling check | "Verify NEW code does NOT cross-import K3 active surfaces" |
| 4 | Cite-content verification | "Read cited file:lines and confirm content matches claim" |
| 5 | Boundary semantic | "ratio==threshold → which side? Is strict-vs-inclusive pinned?" |
| 6 | K1 compliance | "Grep `INSERT|UPDATE|DELETE` on NEW lines only (git diff filter)" |
| 7 | Co-tenant safety | "Confirm exactly N files in commit; co-tenant unstaged left alone" |
| 8 | Honest tri-state | "When data is insufficient, does function return None vs False vs True?" |
| 9 | Sibling coherence | "Defaults match prior packets; dataclass shape mirrors siblings" |
| 10 | Operator runbook actionability | "AGENTS.md tells operator what to do per outcome?" |

Plus packet-specific probes for HONEST DISCLOSURE / KEY OPEN QUESTION verification.

### §9.1 Anti-rubber-stamp tells

Critic verdicts that contain these phrases need rejection (operator should escalate):
- "Pattern proven" (without citing the test that proves it)
- "Narrow scope self-validating" (translation: I didn't actually verify)
- "Trust the executor's test count" (without independent reproduction)
- "All tests pass" (without naming which N tests / which baseline)

### §9.2 Rubber-stamp resistance via independent reproduction

Critic MUST run at least one independent verification per BATCH:
- Re-run pytest from a fresh shell
- Re-grep cited line ranges
- Issue REPL probe of the new function with synthetic input
- `git show --stat` to verify file count claim

These take 1-2 minutes; they catch ~10% of cycles where executor's narrative doesn't match the actual diff.

## §10 Closure phase — multi-angle review before main-merge

After all batches APPROVED on dev branch, before opening / merging PR to main:

### §10.1 Multi-angle review (5 parallel sub-agents)

Per memory `feedback_multi_angle_review_at_packet_close`, dispatch 5 parallel sub-agents covering different angles:

1. **architect** — high-level structural decisions; cross-module impact; long-term maintainability
2. **critic** — adversarial probes; edge cases; failure modes
3. **explore** — codebase coverage; unintended cross-module references; orphan code
4. **scientist** — empirical validation; metric soundness; statistical correctness (if applicable)
5. **verifier** — completion checks; evidence adequacy; test sufficiency

Each writes to `evidence/closure/<role>_review_YYYY-MM-DD.md`. Team-lead synthesizes into PR description.

### §10.2 PR description template

```markdown
## Summary
<commit count>-commit fast-forward / merge-commit. <conflicts status>. Test baseline X passed / Y skipped / 0 failed.

## What's shipping
### <Packet 1> (<N> commits)
- <commit SHA> <subject>
...

## Architecture
- ZERO touches to K0 frozen / K3 active / schema
- <count> small read-only additions to <surface> (each critic-gated)
- ZERO crossing with mainline-owned files (per <verdict> §1)

## Test baseline
<reproduction command + output>

## Critic review provenance
<N> critic cycles total: A clean APPROVE / B APPROVE-WITH-CAVEATS / C REVISE / D BLOCK
Anti-rubber-stamp 100% maintained.
Per-packet review evidence under <path>.

<Notable methodology dividends>

## Test plan
- [x] FF-merge confirmed
- [x] Pytest baseline reproduced
- [x] All N ephemeral worktree branches verified merged
- [x] Multi-angle closure review (5 sub-agents) completed
- [ ] Operator merges PR

## Follow-ups (tracked, not in this PR)
- <list>
```

### §10.3 Operator-only merge gate

Team-lead does NOT merge. PR sits open until operator reviews multi-angle evidence + merges. This preserves the explicit operator-authorization gate for shared-state actions.

## §11 Failure modes + recovery

### F1: SendMessage drop (executor BATCH_X_DONE not arriving)

Symptom: executor sent message; team-lead never received it.

Recovery: poll disk. Agent evidence files (`<packet>_boot.md`, commits, etc.) are canonical. SendMessage is delivery-only; if message dropped, the work is still there. Read commit log + evidence dir. Resume by team-lead acknowledging the work as if message arrived.

### F2: Crossed-in-flight (executor pre-shipped while dispatch was in transit)

Symptom: executor sends BATCH_X_DONE before team-lead's GO_BATCH_X arrives.

Recovery: verify pre-shipped commit functionally aligns with dispatch (same function signature / same defaults / same tests). If aligned, push without modification + acknowledge crossed-in-flight in next dispatch. If misaligned, dispatch BATCH_X_REVISE.

Empirical: happened once in case study (WS_POLL BATCH 2). Pre-shipped work was fully aligned; pushed without modification.

### F3: Cycle drift (executor's narrative diverges from diff)

Symptom: executor claims "I did X" but `git show` reveals X wasn't done OR was done incorrectly.

Recovery: critic-gate catches this empirically (cycle 22 caught the WP-1-1 row multiplication where executor claimed "n_signals counts unique ticks" but SQL was `SELECT count(*)` over JOIN producing duplicates). Always run independent reproduction.

### F4: Co-tenant absorption

Symptom: `git diff HEAD~1` shows files that weren't in your stage list.

Recovery: `git reset --soft HEAD~1` to unstage; `git stash pop` to recover co-tenant work; re-stage YOUR specific files; re-commit. Critic catches via "exactly N files in commit" probe.

### F5: Citation rot

Symptom: cite to `<file>:<line>` no longer matches the claimed content (file changed since cite was written).

Recovery: cycle-29 cite-CONTENT discipline. Grep-verify CONTENT (not just line number) within 10 min before commit. Citations rot ~20-30%/week per memory `feedback_zeus_plan_citations_rot_fast`.

### F6: Wrong baseline cited

Symptom: hook fails with "PASSED < BASELINE_PASSED" because baseline count was set wrong.

Recovery: independent reproduction. Critic re-runs the exact 11-file baseline command; corrects to actual count; updates hook. Memory `feedback_critic_reproduces_regression_baseline` codifies this.

## §12 Empirical track record (Zeus R3 §1 #2 — case study)

5 packets shipped over ~1 work-session day:

| Packet | Commits | Risk | Cycles |
|---|---|---|---|
| EDGE_OBSERVATION | 3 | LOW | 3 |
| ATTRIBUTION_DRIFT | 3 | LOW | 3 |
| WS_OR_POLL_TIGHTENING | 5 | MEDIUM (1 REVISE) | 4 |
| CALIBRATION_HARDENING | 3 | MEDIUM (K3-adjacent) | 3 |
| LEARNING_LOOP | 3 | HIGHEST (K3 retrain_trigger) | 3 |

**Total**: 17 commits / 32 critic cycles / 144 new tests / 0 BLOCK / 1 earned REVISE / 100% anti-rubber-stamp maintained

**Methodology dividends observed**:
- Cycle-29 cite-CONTENT discipline → cycle-30 caught substrate misread within 24h (Fitz Constraint #3 antibody pattern)
- PATH A pattern caught structural mismatch in 4 of 5 packet boots (avoided invented-data shipping)
- K3-adjacent pre-flag pattern landed 3 read-only surface additions clean (store.py + retrain_trigger.py)
- Carry-forward LOW pattern resolved 100% of LOWs by packet close
- ZERO crossing with R3 mainline (independently audited)

## §13 Cross-references

### Existing methodology
- `docs/methodology/adversarial_debate_for_project_evaluation.md` — DEBATE phase (R1/R2/R3)
- `.claude/skills/zeus-methodology-bootstrap/SKILL.md` — auto-load on debate-class tasks

### Memory feedback notes (memory: load if relevant)
- `feedback_critic_prompt_adversarial_template` — 10-ATTACK template; never write "narrow scope self-validating"
- `feedback_executor_commit_boundary_gate` — executor cannot self-approve over multi-batch work
- `feedback_zeus_plan_citations_rot_fast` — file:line citations rot ~20-30%/week
- `feedback_converged_results_to_disk` — SendMessage drop pattern; disk is canonical
- `feedback_idle_only_bootstrap` — spawn longlast teammates with idle-only boot prompt
- `feedback_no_git_add_all_with_cotenant` — `git add` SPECIFIC files; never `-A`
- `feedback_multi_angle_review_at_packet_close` — 5 parallel sub-agents before DEBATE_CLOSED
- `feedback_critic_reproduces_regression_baseline` — critic always re-runs regression
- `feedback_grep_gate_before_contract_lock` — grep-verify file:line before lock
- `feedback_critic_via_team_not_agent` — critic must be native team member, not Agent-spawned
- `feedback_default_dispatch_reviewers_per_phase` — auto-dispatch critic post-implementation

### Related skills
- `.claude/skills/zeus-phase-discipline/SKILL.md` — Mode C per-batch discipline
- `.claude/skills/zeus-task-boot-*/SKILL.md` — task-specific boot profiles

## §14 Maintenance

This skill is **living**. Update after each methodology cycle that:
- Surfaces a new outcome category (cycle-5 added "5th outcome: stage-gated revert")
- Surfaces a new discipline pattern (cycle-29 added "cite-CONTENT discipline")
- Surfaces a new failure mode (cycle-22 catches like WP-1-1 row multiplication)
- Adds a new sibling-coherence rule

When adding to this skill:
1. Cite the specific cycle that surfaced the pattern
2. Add to §7 (discipline) or §11 (failure modes) or §6 (risk framing) as appropriate
3. Update the case-study metrics in §12
4. Cross-link from `feedback_*` memory note if applicable

## §15 Lineage

v1 (2026-05-03): created post R3 §1 #2 5-packet phase (32 critic cycles, 100% anti-rubber-stamp). Generalizes the EXECUTION + CLOSURE phases that were previously implicit in `adversarial_debate_for_project_evaluation.md` §5. Designed for reuse across any project; case-study Zeus-specific but patterns are generic.

Replaces the implicit "team-lead figures it out from feedback memory + prior session" pattern with an explicit reusable skill.
