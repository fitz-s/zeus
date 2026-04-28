# Stage 4 Plan — Process Gates A-E Implementation Plan

Author: team-lead@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Verdict reference: `verdict.md` §6 Stage 4 (judge-can-encode-now portion)
Cost estimate (judge): ~15-25h aggregate (verdict §6); per-gate breakdown below

---

## §0 TL;DR

5 process gates A-E to permanently make the cross-session contamination CATEGORY impossible per Fitz Constraint #1 (make the wrong code unwritable, not patch each instance). All 5 gates run in PARALLEL with Stages 1+2+3+5; this plan covers the judge-unilateral encoding work that does NOT require operator authorization for production change.

| Gate | What it prevents | Encoding location | Cost | Operator approval? |
|---|---|---|---|---|
| **A** | Cross-session merge bypassing critic-gate | Root `AGENTS.md` §2 + `.agents/skills/zeus-ai-handoff/SKILL.md` §8 (new §8.8) | ~3-4h | No |
| **B** | Worktree-merge with no contamination check | New `architecture/worktree_merge_protocol.yaml` + new `.claude/hooks/pre-merge-contamination-check.sh` + `.claude/settings.json` register | ~4-6h | **YES — hooks change affects all Bash; light validation** |
| **C** | "Continue" prompt expanding declared scope without re-auth | `.agents/skills/zeus-ai-handoff/SKILL.md` §3 mode-selection (add scope-lock subclause) | ~2-3h | No |
| **D** | Methodology not loading on new session boot | Root `AGENTS.md` §3 Required Reads + new `.claude/skills/zeus-methodology-bootstrap/SKILL.md` | ~3-5h | **YES — new auto-loading SKILL may surprise; light validation** |
| **E** | No quantitative trigger for "audit-first mode required" based on prior errata frequency | Methodology §5.Z3 quantitative subsection | ~1-2h | No |

**Total ~13-20h** (judge §6 honest range ~15-25h with integration testing per gate).

**Sequencing**: Gates A/C/E are pure-text additions (parallelizable within ~1h each). Gates B/D require new files + cross-references + hook registration / SKILL auto-trigger validation; sequential safer.

---

## §1 Gate A — Cross-session critic-gate REQUIRED

### Why

Contamination root cause: another Claude session in another worktree merged 9 commits into `plan-pre5` via `53a21ad` worktree-merge **without dispatching a critic-opus on the diff**. Zeus's harness governs in-session work but had no defense at the cross-session boundary.

### What to encode

**Insertion 1**: Root `AGENTS.md` §2 (Platform Operations & Change Control), after the "durable workspace kernel" list, add new subsection:

```markdown
### Cross-session merge protocol

Any merge from another worktree/session into a Zeus branch (`plan-pre5`,
`main`, etc.) requires a **critic-opus dispatch on the merging branch's
diff against the target branch BEFORE the merge commits**. The critic
verdict (APPROVE / REVISE / BLOCK) is mandatory; merging without it is a
process violation regardless of how clean the diff appears.

Mechanism: see `.agents/skills/zeus-ai-handoff/SKILL.md` §8.8 for the
operational protocol; `.claude/hooks/pre-merge-contamination-check.sh`
provides the deterministic gate.

This protocol exists because of the 2026-04-28 contamination event: a
parallel session merged 9 commits into `plan-pre5` without independent
critic gate; 6 drift items resulted, including 815k mislabeled
production rows. See `docs/operations/task_2026-04-28_contamination_remediation/`.
```

**Insertion 2**: `.agents/skills/zeus-ai-handoff/SKILL.md` §8 — add new subsection §8.8 after existing §8.7:

```markdown
### §8.8 Cross-session merge critic-gate

When merging from another worktree/session into the active Zeus branch:

1. Identify the diff: `git diff <current-branch>...<merging-branch>`
2. Dispatch critic-opus (Agent or longlast critic-* in active team) with:
   - Diff scope summary (files + LOC)
   - Authoring session identifier (which session/worktree produced it)
   - Boundary check: is the merging session subject to the same
     authority files (root `AGENTS.md`, methodology, planning-lock)?
   - Bidirectional grep: do drift-keyword greps trigger on the diff?
3. Critic verdict gates the merge — BLOCK = abort; REVISE = address
   defects per file:line + re-dispatch; APPROVE = proceed with merge
4. Document: critic verdict path in commit message of the merge commit

This extends §8.5 (per-batch critic-gate within a session) to the
**cross-session boundary**. Per memory `feedback_executor_commit_boundary_gate`,
self-review is forbidden; cross-session self-review is the same
violation at session granularity.

Hook enforcement: `.claude/hooks/pre-merge-contamination-check.sh`
requires `MERGE_AUDIT_EVIDENCE` env var pointing to the critic verdict
file before allowing `git merge` / `git pull` / `git cherry-pick` Bash
commands; otherwise prints warning + advisory message.
```

### Verification

- `grep -c "Cross-session merge protocol" AGENTS.md` returns 1
- `grep -c "§8.8" .agents/skills/zeus-ai-handoff/SKILL.md` returns 1
- Cross-references resolvable: §8.5 + memory `feedback_executor_commit_boundary_gate` + Gate B hook
- No regression: pytest baseline 90/22/0 unchanged (text-only additions)

### Cost

- AGENTS.md edit: 30 min (insertion + cross-reference verification)
- SKILL.md §8.8 edit: 45 min (template + cross-reference)
- Test/verify: 30 min
- Documentation update: 30 min
- **Total ~2-3h**, judge §6 estimate 3-4h includes light buffer

---

## §2 Gate B — Worktree-merge contamination check (hook + protocol YAML)

### Why

Gate A is prose-as-law. Gate B is the deterministic hook that enforces it. Per round-2 verdict §K1 (Tier 1 batch B): **hooks > advisory prose for planning-lock + map-maintenance + invariant-test**. Same principle applied to merge gate.

### What to encode

**File 1**: NEW `architecture/worktree_merge_protocol.yaml` (~60-80 lines):

```yaml
# architecture/worktree_merge_protocol.yaml
# Codified protocol for cross-session/worktree merges into Zeus branches.
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: contamination remediation verdict §6 Stage 4 Gate B

version: "1.0"

trigger_commands:
  - "git merge"
  - "git pull"
  - "git cherry-pick"
  - "git rebase"
  - "git am"

trigger_conditions:
  - any_command_in_trigger_commands
  - working_branch in [main, plan-pre5, plan-*, release-*]

required_evidence:
  env_var: MERGE_AUDIT_EVIDENCE
  format: "Path to critic verdict file documenting:
    - diff scope (files + LOC)
    - authoring session identifier
    - bidirectional grep drift-keyword scan results
    - critic verdict (APPROVE/REVISE/BLOCK)
    - critic identifier (critic-opus dispatch ID or critic-* longlast name)"

validation_steps:
  pre_merge:
    - check_env_var_set: MERGE_AUDIT_EVIDENCE
    - check_evidence_file_exists: $MERGE_AUDIT_EVIDENCE
    - check_evidence_contains: ["critic_verdict:", "diff_scope:", "drift_keyword_scan:"]
    - check_critic_verdict: in [APPROVE, REVISE]  # BLOCK means stop
  on_block:
    - exit_code: 2
    - message: "Merge BLOCKED — see $MERGE_AUDIT_EVIDENCE for defects"
  on_revise:
    - exit_code: 2
    - message: "Merge REVISE required — address critic defects then re-dispatch"
  on_approve:
    - exit_code: 0
    - message: "Merge gate PASSED via $MERGE_AUDIT_EVIDENCE"

advisory_mode:
  enabled: false  # set true to warn-only without blocking
  default_state_2026: "blocking"

drift_keywords_for_grep:
  # Bidirectional grep targets (forward + reverse per methodology §5.Y)
  - HKO
  - WU
  - meteostat
  - ogimet
  - tier_resolver
  - verify_truth_surfaces
  - Day0
  - settlement
  - calibration
  - source_role
  - data_version

escalation:
  on_repeated_block: "Operator notify; consider freezing target branch"
  on_advisory_only: "Document warning in branch handoff"

cross_references:
  - .claude/hooks/pre-merge-contamination-check.sh
  - .agents/skills/zeus-ai-handoff/SKILL.md §8.8
  - root AGENTS.md "Cross-session merge protocol"
  - docs/operations/task_2026-04-28_contamination_remediation/verdict.md §6 Stage 4
  - docs/methodology/adversarial_debate_for_project_evaluation.md §5 critic-gate workflow
```

**File 2**: NEW `.claude/hooks/pre-merge-contamination-check.sh` (~60-80 lines):

```bash
#!/usr/bin/env bash
# Pre-merge contamination check hook
# Reads CLAUDE_TOOL_INPUT (JSON) from stdin via Claude Code hooks API
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: contamination remediation verdict §6 Stage 4 Gate B

set -euo pipefail

# Read JSON tool input from stdin
input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // empty')

# Detect merge-class commands
case "$command" in
  *"git merge"*|*"git pull"*|*"git cherry-pick"*|*"git rebase"*|*"git am"*)
    is_merge=1
    ;;
  *)
    is_merge=0
    ;;
esac

if [ "$is_merge" -eq 0 ]; then
  exit 0  # Not a merge command; pass through
fi

# Check current branch is in protected set
current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
case "$current_branch" in
  main|plan-pre5|plan-*|release-*)
    is_protected=1
    ;;
  *)
    is_protected=0
    ;;
esac

if [ "$is_protected" -eq 0 ]; then
  exit 0  # Working on non-protected branch; merge gate not required
fi

# Check MERGE_AUDIT_EVIDENCE env var
if [ -z "${MERGE_AUDIT_EVIDENCE:-}" ]; then
  cat <<'EOF' >&2
[GATE B BLOCK] Merge command on protected branch requires MERGE_AUDIT_EVIDENCE env var.

Per architecture/worktree_merge_protocol.yaml + AGENTS.md "Cross-session
merge protocol", merging from another session/worktree into a protected
Zeus branch requires a critic-opus dispatch verdict on the merging diff.

To proceed:
1. Identify diff: git diff $current_branch...<merging-branch>
2. Dispatch critic-opus (Agent or longlast critic-*) per
   .agents/skills/zeus-ai-handoff/SKILL.md §8.8
3. Save critic verdict to a file (e.g.
   docs/operations/.../merge_critic_verdict.md)
4. Re-run with: MERGE_AUDIT_EVIDENCE=<path> git merge ...

To override (operator emergency only):
  MERGE_AUDIT_EVIDENCE=OVERRIDE_<reason> git merge ...
  (logged to docs/operations/current_state.md drift table)
EOF
  exit 2
fi

# Check evidence file exists
if [ "${MERGE_AUDIT_EVIDENCE}" != OVERRIDE_* ] && [ ! -f "$MERGE_AUDIT_EVIDENCE" ]; then
  echo "[GATE B BLOCK] MERGE_AUDIT_EVIDENCE file not found: $MERGE_AUDIT_EVIDENCE" >&2
  exit 2
fi

# Check evidence file contains required fields (basic format check)
if [ "${MERGE_AUDIT_EVIDENCE}" != OVERRIDE_* ]; then
  for field in "critic_verdict:" "diff_scope:" "drift_keyword_scan:"; do
    if ! grep -q "$field" "$MERGE_AUDIT_EVIDENCE"; then
      echo "[GATE B BLOCK] $MERGE_AUDIT_EVIDENCE missing required field: $field" >&2
      exit 2
    fi
  done
  # Check critic_verdict is APPROVE or REVISE (not BLOCK)
  verdict=$(grep "critic_verdict:" "$MERGE_AUDIT_EVIDENCE" | head -1 | sed 's/.*critic_verdict:[[:space:]]*//;s/[[:space:]]*$//')
  case "$verdict" in
    APPROVE|REVISE)
      echo "[GATE B PASS] $MERGE_AUDIT_EVIDENCE verdict=$verdict" >&2
      ;;
    BLOCK|*)
      echo "[GATE B BLOCK] $MERGE_AUDIT_EVIDENCE critic verdict is $verdict; address defects + re-dispatch" >&2
      exit 2
      ;;
  esac
else
  echo "[GATE B OVERRIDE] MERGE_AUDIT_EVIDENCE=$MERGE_AUDIT_EVIDENCE; logged to current_state.md" >&2
fi

exit 0
```

**File 3 update**: `.claude/settings.json` — register hook in PreToolUse Bash matcher (extend existing matcher block):

```json
{
  "matcher": "Bash",
  "hooks": [
    {
      "type": "command",
      "command": ".claude/hooks/pre-commit-invariant-test.sh",
      "description": "Run pytest test_architecture_contracts.py before any `git commit`"
    },
    {
      "type": "command",
      "command": ".claude/hooks/pre-merge-contamination-check.sh",
      "description": "Block git merge/pull/cherry-pick/rebase/am on protected branches without MERGE_AUDIT_EVIDENCE (verdict §6 Stage 4 Gate B)"
    }
  ]
}
```

### Verification

- `bash .claude/hooks/pre-merge-contamination-check.sh < <(echo '{"tool_input":{"command":"git status"}}')` returns 0 (non-merge passes through)
- `MERGE_AUDIT_EVIDENCE="" bash .claude/hooks/pre-merge-contamination-check.sh < <(echo '{"tool_input":{"command":"git merge other-branch"}}')` returns 2 with informative message
- Mock evidence file with all required fields + APPROVE verdict → returns 0
- Mock evidence file with BLOCK verdict → returns 2
- pytest baseline 90/22/0 unchanged
- Settings.json valid JSON; hook registered

### Cost

- YAML schema: 1h
- Hook script: 1.5-2h (includes edge cases: detached HEAD, no git repo, JSON parsing safety)
- Settings registration: 15 min
- Test cases: 1-1.5h (mock evidence files + edge cases)
- Light operator validation: 30 min
- **Total ~4-5h**, matches judge §6 estimate 4-6h

---

## §3 Gate C — Scope authorization freezing

### Why

Contaminated session admitted: *"Misinterpreted user's 'continue' as solo-execute authorization"* + *"Expanded scope from 'TIGGE remainder' to '全量 suite 扫尾' without operator approval"*. The "continue" semantic was load-bearing for the contamination.

### What to encode

`.agents/skills/zeus-ai-handoff/SKILL.md` §3 mode selection — add new paragraph after the "Default if uncertain" sentence:

```markdown
### §3.1 Scope-lock subclause

When operator uses approval words ("continue", "proceed", "go", "推进",
"ok", etc.) AFTER an initial task description, that approval applies
ONLY to the previously declared task class. **Approval words do NOT
expand scope to adjacent task classes.**

Specifically:
- Previously declared task: "fix X in module Y" → "continue" means
  proceed with X-in-Y, NOT "fix anything else you find in module Y"
- Previously declared task: "run cycle 1 phases A-D" → "continue" means
  next phase in the SAME cycle, NOT "start cycle 2"
- Previously declared task: "audit drift item #1" → "continue" means
  finish #1, NOT "audit all 6 drift items"
- Previously declared task: "TIGGE remainder cleanup" → "continue" does
  NOT authorize "全量 suite 扫尾" (the 2026-04-28 contamination root)

If scope expansion appears warranted mid-task, **stop and request explicit
operator re-authorization** with the new scope explicitly named. Format:
"Discovered ADJACENT_TASK while doing DECLARED_TASK; requires explicit
authorization to proceed."

This subclause exists because of the 2026-04-28 contamination event:
"continue" was interpreted as scope-expansion authorization across
multiple cycles, accumulating 9 commits of contamination before
discovery. See `docs/operations/task_2026-04-28_contamination_remediation/`.

Memory reference: when this rule activates against future "continue"
prompts, the agent should cite this §3.1 explicitly in the request for
re-authorization.
```

### Verification

- `grep -c "§3.1" .agents/skills/zeus-ai-handoff/SKILL.md` returns 1
- Cross-reference to contamination packet resolvable
- Pytest baseline 90/22/0 unchanged

### Cost

- SKILL.md §3.1 draft: 1h (template + examples + memory reference)
- Cross-reference + memory tag: 30 min
- Test/verify: 30 min
- **Total ~2h**, matches judge §6 estimate 2-3h

---

## §4 Gate D — Methodology cross-session propagation

### Why

Methodology doc (`docs/methodology/adversarial_debate_for_project_evaluation.md`) is the 4-for-4 case-study antibody library + 5-cycle distilled wisdom. **It must load on every Claude session boot, especially adversarial-debate / multi-agent / contamination tasks.** Currently it's only loaded if the agent happens to read it; cross-session propagation requires explicit mechanism.

### What to encode

**Insertion 1**: Root `AGENTS.md` §3 Navigation, in "What to read by task" — add new bullet under "Always start with the topology digest":

```markdown
- **Adversarial debate / multi-agent / contamination remediation**:
  add `docs/methodology/adversarial_debate_for_project_evaluation.md`
  (5-cycle methodology with §5.X-Z3 case-study antibodies). The
  methodology load is REQUIRED for any cycle ≥ 2 of debate, any task
  invoking 5+ teammates, or any remediation triggered by drift items
  ≥ 3 per memory `feedback_critic_prompt_adversarial_template`.
```

**Insertion 2**: `.agents/skills/zeus-ai-handoff/SKILL.md` §1 Required Reads — promote methodology from conditional (line 31) to always-conditional-on-pipeline:

Already present at `docs/methodology/adversarial_debate_for_project_evaluation.md (if mode D)`. Strengthen to: `docs/methodology/adversarial_debate_for_project_evaluation.md (REQUIRED for mode D; RECOMMENDED for mode C with critic-gate; load on session boot for any architecture/governance work)`.

**File 3**: NEW `.claude/skills/zeus-methodology-bootstrap/SKILL.md` (~60-80 lines):

```markdown
---
name: zeus-methodology-bootstrap
description: Auto-loads the Zeus adversarial-debate methodology on session boot when the task involves multi-agent coordination, adversarial debate, contamination remediation, or any cycle ≥ 2 of structured pro/con dialogue. Triggers on keywords: "debate", "critic-gate", "verdict", "concession", "remediation", "drift", "contamination", "methodology", "anti-drift", "phase discipline", "longlast teammate", "cross-session". Returns the methodology doc as required reading + the 4-for-4 case study summary + the 5th outcome category from contamination remediation 2026-04-28.
---

# Zeus Methodology Bootstrap

## Purpose

Ensure the adversarial-debate methodology (`docs/methodology/adversarial_debate_for_project_evaluation.md`) loads into the active session's context BEFORE any debate-class work begins. Prevents the cross-session knowledge loss pattern that contributed to the 2026-04-28 contamination event (other session did not know about §5.Z2 codified gates → bypassed bidirectional grep → produced 6 drift items).

## When this SKILL fires

This SKILL auto-triggers on session boot when ANY of these hold:
- Task description contains keywords listed in `description` frontmatter
- Active task is a Mode C (longlast multi-batch) or Mode D (adversarial debate) per `zeus-ai-handoff` SKILL §3
- Operator references "another cycle", "next round", "the methodology", "critic gate"
- Recent commits in active branch reference `task_*_harness_debate/` or `task_*_contamination_remediation/`

## What this SKILL provides

1. **Read methodology doc**: `docs/methodology/adversarial_debate_for_project_evaluation.md` (~700+ lines)
2. **4-for-4 case study summary** (the empirical track record):
   - §5.X BATCH D INV-16/17: prescribed DELETE → audit found 9 hidden tests → REVERT + add tests
   - §5.Y Phase 2 auto-gen registries: prescribed AUTO-GEN → audit found 95% intentional curation
   - §5.Z Phase 3 module_manifest: prescribed REPLACE → audit 21 KEEP / 4 HYBRID / 0 REPLACE
   - §5.Z2 Phase 4 @enforced_by: prototype works for uniform domain; HYBRID for cross-cutting
3. **5th outcome category** (from contamination remediation 2026-04-28): CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION (Stage-gated revert with conditional restoration discipline)
4. **§5.Z2 codified gates**: bidirectional grep BEFORE locking ANY "X% of Y lacks Z" claim
5. **§5.Z3 4-outcome categories** (now 5 with contamination cycle): Falsified / Confirmed-bounded / Confirmed-unbounded / Inconclusive / Stage-gated

## Cross-references

- `.agents/skills/zeus-ai-handoff/SKILL.md` (Mode C/D selection)
- `.claude/skills/zeus-phase-discipline/SKILL.md` (Mode C per-batch)
- Root `AGENTS.md` (Required Reads for adversarial work)
- `docs/operations/task_2026-04-27_harness_debate/` (4-cycle case studies)
- `docs/operations/task_2026-04-28_contamination_remediation/` (5th cycle + 5th outcome)

## Maintenance

This SKILL is **living**. Update after each methodology cycle that surfaces new patterns or new outcome categories. Cite this SKILL in new methodology cycles' TOPIC.md as the boot context loader.

## Lineage

v1 (2026-04-28): created during contamination remediation Stage 4 Gate D. Replaces the implicit "methodology will be loaded if agent thinks to" pattern with explicit auto-load on debate-class tasks.
```

### Verification

- `grep -c "Adversarial debate / multi-agent / contamination remediation" AGENTS.md` returns 1
- `ls .claude/skills/zeus-methodology-bootstrap/SKILL.md` exists
- SKILL frontmatter valid YAML
- Description keywords match relevant task patterns (test by simulating "start debate cycle 6 about X")
- Pytest baseline 90/22/0 unchanged

### Cost

- AGENTS.md insertion: 30 min
- zeus-ai-handoff SKILL §1 strengthening: 30 min
- New zeus-methodology-bootstrap SKILL: 1.5-2h (template + cross-references + keyword matrix tuning)
- Light operator validation (auto-trigger surface area): 30-60 min
- Test simulating debate-class boot: 30 min
- **Total ~3-5h**, matches judge §6 estimate

---

## §5 Gate E — Erratum-frequency trigger

### Why

Methodology §5.Z3 currently has 4 outcome categories but no quantitative trigger for "audit-first mode required" based on prior errata frequency. The 5-cycle history shows: when 3+ errata accumulate in a single cycle, the cycle's underlying audit discipline was insufficient. Future cycles should default to audit-first mode in this case.

### What to encode

`docs/methodology/adversarial_debate_for_project_evaluation.md` §5.Z3 — append new subsection §5.Z3.1:

```markdown
### §5.Z3.1 Quantitative erratum-frequency trigger

If a single methodology cycle produces ≥3 errata (post-implementation
falsifications of prior debate / verdict / plan claims), the **next
methodology cycle MUST start with audit-first mode**:

1. Bidirectional grep on EVERY load-bearing % claim BEFORE locking concession
2. Intent-aware audit on EVERY structural change prescription (DELETE,
   AUTO-GEN, REPLACE, REVERT) BEFORE adopting it as verdict direction
3. Default-deny on grep-only evidence; require positive citation +
   reverse confirmation
4. 5-criterion weighing per §8 must explicitly note erratum-frequency
   trigger as procedural context

This trigger exists because the 5-cycle empirical history shows:
- Cycle R1 verdict: 1 erratum (post-implementation BATCH D RE-SCOPE) — within tolerance
- Cycle R2 verdict: 2 errata (Phase 2 auto-gen falsified + Phase 3 replace falsified) — within tolerance
- Cycle R3 verdict: 1 erratum (Knight Capital citation re-scoping) — within tolerance
- Tier 2 implementation: 0 verdict-level errata (all 4 phases met or surfaced bounded honest verdicts) — clean
- **Cycle 5 (contamination remediation)**: TBD post-implementation; if Stage 1+2+3+5 produce ≥3 NEW errata against this verdict, gate E auto-triggers for any cycle 6+

Threshold rationale: 3+ errata in a cycle indicates the cycle's audit
discipline was insufficient — not the implementation's fault, but the
debate's fault for not surfacing the issues earlier. Audit-first mode
is the structural antibody.

Cross-reference: `feedback_critic_prompt_adversarial_template` (memory)
+ `.agents/skills/zeus-ai-handoff/SKILL.md` §8.7 (verdict-level erratum
pattern).
```

### Verification

- `grep -c "§5.Z3.1" docs/methodology/adversarial_debate_for_project_evaluation.md` returns 1
- Cross-references resolvable
- Quantitative threshold (≥3) consistent with empirical 5-cycle history table
- Pytest baseline 90/22/0 unchanged

### Cost

- Methodology §5.Z3.1 draft: 45 min
- Erratum-history table + threshold validation: 30 min
- Cross-reference + memory tag: 15 min
- **Total ~1.5h**, matches judge §6 estimate 1-2h

---

## §6 Sequencing + dependencies

```
Gate A (text-only) ───┐
                       ├──> commit 1 (gates A+C+E text additions)
Gate C (text-only) ───┤      ~6h aggregate
                       │
Gate E (text-only) ───┘

Gate B (hooks + YAML) ──> commit 2 (gate B implementation; needs operator validation)
                          ~5h
Gate D (SKILL + AGENTS) ──> commit 3 (gate D propagation; needs operator validation)
                            ~4h
```

**Recommended order**:
1. Gates A + C + E first (pure text, low risk, fast iteration)
2. Gate D next (new SKILL; auto-trigger surface area; light operator validation)
3. Gate B last (hooks affect ALL Bash commands; highest validation surface area)

**Total wall-clock**: ~15h split across 3 commits if sequential; ~10h if parallel-edit (gates A+C+E concurrent).

---

## §7 Verification per gate (combined)

After all 5 gates encoded:

1. **Pytest baseline**: `.venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_inv_prototype.py tests/test_digest_profiles_equivalence.py -q --no-header` — expect 90 passed / 22 skipped / 0 failed (no regression from text additions; hooks don't affect pytest)
2. **Topology doctor**: `python3 scripts/topology_doctor.py --planning-lock --changed-files <gate-A/C/E touched files>` — must return PASS for `architecture/**` not touched (only Gate B adds new YAML, planning-lock will require evidence)
3. **Hook integration**: simulate `git merge` Bash with + without `MERGE_AUDIT_EVIDENCE`; verify behavior matches `architecture/worktree_merge_protocol.yaml` spec
4. **SKILL auto-trigger**: simulate session boot with task description containing methodology keywords; verify zeus-methodology-bootstrap SKILL fires
5. **Cross-references resolvable**: each new file references resolve to existing artifacts (no broken paths)

---

## §8 Operator approval points (light validation only)

Per verdict §0 + §6, Stage 4 is judge-unilateral execution. However, 2 gates touch surface area that benefits from light operator validation:

- **Gate B**: hooks fire on every Bash command. Operator should validate:
  - Hook doesn't false-positive on legitimate `git status`, `git diff`, `git log` (it shouldn't — only matches merge-class patterns)
  - Hook BLOCK behavior is acceptable (vs ADVISORY) — currently set to BLOCK per "default state 2026: blocking"
  - Override path (`MERGE_AUDIT_EVIDENCE=OVERRIDE_<reason>`) works for emergencies
- **Gate D**: new SKILL auto-loads methodology doc. Operator should validate:
  - Keyword set in SKILL description matches actual debate-class task patterns
  - SKILL doesn't falsely-fire on routine non-debate work
  - Methodology doc context size (~700+ lines) is acceptable as auto-load

These are light validations (~30-60 min total) not full-stack reviews. If operator delegates these to executor, judge can encode without operator validation.

---

## §9 Erratum protocol for Stage 4 itself

Per gate E + methodology §5.Z3 erratum pattern: if any of gates A-E reveal a defect during implementation, append `STAGE4_ERRATUM_<gate>_<date>.md` to this packet documenting:
- Original gate spec
- What audit / implementation found
- What changes (and what doesn't change)
- Whether gate E quantitative threshold (≥3 errata) is approached

If 3+ Stage 4 errata accumulate, escalate to operator before completing Stage 4 (gate E auto-trigger applies recursively).

---

## §10 Status

Plan complete. Awaits:
- critic-harness REVIEW_VERDICT_REMEDIATION on `verdict.md` (BLOCK / REVISE / APPROVE)
- Operator direction on whether to start Gate A/C/E (low-risk text additions) immediately or wait for critic verdict
