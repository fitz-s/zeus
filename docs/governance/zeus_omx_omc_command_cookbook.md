File: docs/governance/zeus_omx_omc_command_cookbook.md
Disposition: NEW
Authority basis: official OMX / OMC / Codex docs as of 2026-04-02; docs/governance/zeus_autonomous_delivery_constitution.md; current Zeus repo constraints.
Supersedes / harmonizes: ad hoc operator memory of commands; dossier-only runtime summaries.
Why this file exists now: Zeus needs one repo-local command source that separates primary path, secondary path, and unsafe convenience shortcuts.
Current-phase or long-lived: Long-lived, with monthly command review.

# Zeus OMX / OMC Command Cookbook

## 0. How to read this file

- OMX commands are the **primary** repo runtime path.
- OMC commands are the **approved secondary** orchestration path.
- “One-key” means bootstrap + approved execution pattern, not packet-less autonomy.

## 0.1 Current-phase execution gate

Before foundation-mainline planning or team opening, close the remaining current-phase packet queue in this order:

1. `P-BOUND-01`
2. `P-ROLL-01`
3. `P-STATE-01`
4. `P-OPS-01`

Only after those four packets close may Zeus:
- write the foundation-mainline architecture plan
- prepare team execution

## 1. OMX install / setup / doctor

```bash
npm install -g @openai/codex oh-my-codex
omx setup
omx doctor
```

### Standard launch
```bash
omx
```

### Trusted launch
Use only in a trusted or externally sandboxed environment.

```bash
omx --xhigh --madmax
```

### Reasoning mode helpers
```bash
omx reasoning high
omx reasoning xhigh
```

## 2. OMX canonical workflow surfaces

### Clarify
```text
$deep-interview "Clarify the Zeus packet scope, non-goals, blast radius, and evidence burden."
```

### Planning lock
```text
$ralplan "Produce the approved Zeus work packet for P-STATE-01. Preserve current-vs-target truth split."
```

### Single-owner completion loop
```text
$ralph "Execute approved Zeus packet P-STATE-01 to completion. Do not widen scope beyond the packet."
```

### Parallel execution
```text
$team 3:executor "Execute approved Zeus packet P-MATH-01 in parallel."
```

Use `$team` only after packet approval and after the current-phase `P-*` queue is closed when work is still in the tribunal lane.

## 3. OMX team runtime

### Start
```bash
omx team 3:executor "execute approved Zeus packet P-MATH-01"
```

### Status
```bash
omx team status <team-name>
```

### Resume
```bash
omx team resume <team-name>
```

### Shutdown
```bash
omx team shutdown <team-name>
```

### Non-default worker launch mode
```bash
OMX_TEAM_WORKER_LAUNCH_MODE=prompt omx team 2:executor "task"
```

## 4. OMX advisory and exploration surfaces

### Read-only repo lookup
```bash
omx explore "find references to position_events and position_current"
```

### Direct shell inspection
```bash
omx sparkshell "grep -R "opening_inertia" -n src"
```

### Provider advisor
Published examples focus on local advisor CLIs such as Claude and Gemini.

```bash
omx ask claude "review this Zeus packet for authority drift"
omx ask gemini "brainstorm operator runbook failure cases"
omx ask claude --agent-prompt executor "draft verification steps for this packet"
```

## 5. OMC install / setup / doctor

Inside Claude Code:

```text
/plugin marketplace add https://github.com/Yeachan-Heo/oh-my-claudecode
/plugin install oh-my-claudecode
/oh-my-claudecode:omc-setup
/oh-my-claudecode:omc-doctor
```

Some current docs and issues also show short aliases such as `/omc-setup`, `/omc-doctor`, or `/setup` depending on environment. Use the fully-qualified commands above when you need the least ambiguity.

## 6. OMC canonical runtime surfaces

### Deep interview
```text
/deep-interview "Clarify this Zeus package before any code is written."
```

### Plan
Use `ralplan` or the explicit plan surface when you need a locked plan rather than raw execution.

```text
ralplan
/oh-my-claudecode:omc-plan "Produce a Zeus packet and review the tradeoffs."
```

### Canonical team orchestration
```text
/team 3:executor "execute approved Zeus packet WP-MATH-01"
```

### Persistence mode
```text
ralph
```

### Maximum parallel mode
```text
ultrawork
```

### Full autonomous ideation-to-code mode
Not for Zeus authority, schema, or control packages.

```text
autopilot: build a narrow tool or utility around an already-approved Zeus packet
```

## 7. OMC team runtime

### Start tmux workers
```bash
omc team 2:claude "execute approved Zeus packet WP-MATH-01"
omc team 2:codex "review Zeus architecture risks in this patch"
omc team 2:gemini "rewrite operator docs for clarity"
```

### Status
```bash
omc team status <team-name>
```

### Shutdown
```bash
omc team shutdown <team-name>
omc team shutdown <team-name> --force
```

### Team API
```bash
omc team api claim-task --input '{"team_name":"auth-review","task_id":"1","worker":"worker-1"}' --json
```

## 8. OMC provider advisor

Use the wrapper, not raw provider CLI invocation.

```bash
omc ask claude "review this migration plan"
omc ask codex --prompt "identify architecture risks"
omc ask gemini --prompt "propose operator-facing clarity fixes"
omc ask claude --agent-prompt executor --prompt "draft implementation steps"
```

Artifacts land under:
```text
.omc/artifacts/ask/<provider>-<slug>-<timestamp>.md
```

## 9. Package kickoff templates

### OMX primary package kickoff
```text
$ralplan "Packet P-STATE-01.
Objective: remove governance drift from strategy attribution and timezone fallback.
Allowed files: src/state/strategy_tracker.py, src/data/observation_client.py, targeted tests/docs only.
Forbidden files: migrations/**, docs/architecture/**, docs/governance/**.
Required evidence: targeted tests, architecture-contract review, rollback note."
```

### OMC secondary kickoff
```text
/team 2:executor "Execute approved Zeus packet P-MATH-01 only. Respect repo AGENTS, scoped AGENTS, and forbidden files."
```

### Advisory review kickoff
```bash
omc ask codex --prompt "Review this Zeus diff for authority drift, truth-contract drift, and unsafe convenience fallbacks."
```

## 10. Verification / review / commit hygiene templates

### Git hygiene
```bash
git status --short
git diff --stat
git diff -- <allowed-file-1> <allowed-file-2>
```

### Zeus gate bundle
```bash
python scripts/check_kernel_manifests.py
python scripts/check_module_boundaries.py
python scripts/check_work_packets.py
pytest -q tests/test_architecture_contracts.py
python scripts/replay_parity.py --ci
```

### Review prompt
```text
Review only for:
- authority precedence violations
- forbidden new truth surfaces
- scope widening
- missing rollback/evidence
```

### Commit template
```text
<packet-id>: <narrow objective>

Authority basis:
- <files>

Evidence:
- <tests/gates>

Rollback:
- <how to undo>
```

## 11. One-key bootstrap commands

### OMX primary bootstrap
```bash
npm install -g @openai/codex oh-my-codex && omx setup && omx doctor && omx
```

### OMX trusted bootstrap
```bash
npm install -g @openai/codex oh-my-codex && omx setup && omx doctor && omx --xhigh --madmax
```

### OMC bootstrap
OMC bootstrap happens inside Claude Code, so it is session-native rather than a single shell line:

```text
/plugin marketplace add https://github.com/Yeachan-Heo/oh-my-claudecode
/plugin install oh-my-claudecode
/oh-my-claudecode:omc-setup
/oh-my-claudecode:omc-doctor
```

## 12. Sharp edges to remember

- Do not use `autopilot` or `ultrawork` on authority, schema, or control-plane packets.
- Do not use team mode before planning lock.
- Treat `omc team N:codex` as cautionary for critical packages until its status-sync behavior is proven stable in your environment.
- Verify `omx team` shutdown/worktree cleanup rather than assuming it.
