File: docs/governance/zeus_omx_omc_operator_runbook.md
Disposition: NEW
Authority basis: docs/governance/zeus_autonomous_delivery_constitution.md; docs/governance/zeus_omx_omc_command_cookbook.md; current repo runtime truth surfaces; official OMX / OMC docs.
Supersedes / harmonizes: implicit operator habits and prior-session oral memory.
Why this file exists now: operators need a single practical guide for choosing OMX vs OMC, starting large tasks, and recovering cleanly when sessions or workers fail.
Current-phase or long-lived: Long-lived.

# Zeus OMX / OMC Operator Runbook

## 0. Default operating rule

Use **OMX** unless one of the specific OMC conditions below applies.

## 1. Bootstrap checklist

### Repo bootstrap
1. create/checkout branch
2. confirm repo root
3. read `AGENTS.md`
4. read the relevant scoped `AGENTS.md`
5. verify whether planning lock is required
6. verify whether the packet already exists

### OMX tool bootstrap
```bash
omx setup
omx doctor
```

### OMC tool bootstrap
Inside Claude Code:
```text
/oh-my-claudecode:omc-setup
/oh-my-claudecode:omc-doctor
```

## 2. When to choose OMX primary

Choose OMX when:
- authoring or revising repo law
- working from root/scoped `AGENTS.md`
- doing packet-first repo work
- the task is single-owner but may later need advisory reviewers
- you need Codex-native AGENTS loading and closest-scope instructions

Typical Zeus examples:
- authority install
- decision register edits
- package-map updates
- targeted state drift patches
- architecture-safe math changes

## 3. When to choose OMC primary for a packet lane

Choose OMC for an execution or review lane when:
- the packet is already approved
- tmux worker orchestration is the main value
- cross-model review is useful
- Claude-side delegation and staged team pipeline are preferable

Typical Zeus examples:
- doc rewrite lane after law is set
- review-only `omc ask codex`
- bounded implementation lane for K2/K3 work
- cross-provider critique of operator messaging

## 4. When dual-stack is allowed

Dual-stack is allowed when:
- OMX remains the package owner runtime
- OMC is used for advisory or secondary execution lanes
- outputs return through the packet owner before acceptance

Dual-stack is not allowed when:
- both runtimes are acting as co-equal authority centers
- packet scope is not explicit
- the same irreversible decision is being made independently in both runtimes

## 5. Large-task start sequence

### Preferred start for high-stakes packets
1. OMX launch
2. `$deep-interview` if the request is still ambiguous
3. `$ralplan` to produce or confirm the packet
4. assign owner
5. only then use `$team` or OMC review lanes if needed

### Example
```text
$deep-interview "We need to patch Zeus authority drift without claiming runtime convergence."
$ralplan "Create the approved packet and review blast radius, rollback, and evidence."
```

## 6. Package kickoff templates

### Single-owner execution
```text
$ralph "Execute approved Zeus packet WP-BOUND-01 only. Do not widen scope."
```

### OMX parallel lane
```text
$team 2:executor "Execute approved Zeus packet WP-MATH-01 only."
```

### OMC review lane
```bash
omc ask codex --prompt "Review the Zeus diff only for authority drift and control-plane boundary violations."
```

## 7. Verifier / critic / git-hygiene closeout

### Verifier lane
- check file scope
- run listed gates
- confirm no forbidden files changed
- confirm unresolved uncertainty is still stated

### Critic lane
- ask “what hidden decision got made here?”
- ask “did this change broaden authority or control by accident?”
- ask “what would make rollback hard?”

### Git hygiene
```bash
git status --short
git diff --stat
git diff
```

Do not proceed if unrelated files or runtime artifacts are mixed into the packet.

## 8. Stop / resume / shutdown / recovery

### Stop
Stop when:
- packet scope is exceeded
- runtime authority becomes ambiguous
- a high-sensitivity file is touched without packet permission
- CI or architecture gates fail
- team state becomes inconsistent

### Resume
Before resume:
- re-open packet
- re-read root/scoped AGENTS
- inspect `.omx/` or `.omc/` state when relevant
- verify git cleanliness
- verify worker/session names

### Shutdown

#### OMX
```bash
omx team status <team-name>
omx team shutdown <team-name>
```

After shutdown:
- verify `.omx/state/team/<team>` is gone if expected
- verify detached worktrees are cleaned if worktree mode was used
- verify no stray diffs remain

#### OMC
```bash
omc team status <team-name>
omc team shutdown <team-name>
omc team shutdown <team-name> --force
```

After shutdown:
- verify team state is terminal
- verify no pane/session is still running stale work
- verify pending tasks are not being mistaken for done

## 9. `.omx/` / `.omc/` state checks

### `.omx/`
Treat as runtime/plan state, not repo authority.
Check for:
- active mode
- team state
- plans/logs relevant to the current packet

### `.omc/`
Treat as execution artifacts and team/advisor outputs.
Check for:
- `.omc/artifacts/ask/*`
- `.omc/state/team/*`
- session artifacts relevant to current packet

Never treat either directory as semantic authority.

## 10. OpenClaw integration enablement

### Enable only when
- operator notifications or session callbacks are genuinely useful
- command gateway is bounded and policy-reviewed
- boundary note has been read

### Avoid or disable when
- hooks create noise
- operators start using notification outputs as authority
- callback automation begins widening control or scope

## 11. Failure handling shortcuts

### Worker dies
- stop widening scope
- relaunch same task only
- capture logs
- continue single-owner if needed

### Provider is rate-limited
- hold execution
- switch to advisory review or single-owner planning
- do not waive evidence because a provider was unavailable

### CI red
- treat as real until disproven
- do not downgrade the gate in the same emotional moment
- debug in a fresh lane

## 12. Current operational cautions

- OMC Codex tmux workers should not be your default critical executor until their status-sync behavior is stable in your environment.
- OMX team shutdown and worktree cleanup should be verified, not assumed.
- The repo’s checked-in `state/` directory is not trustworthy as live-state evidence by itself.
