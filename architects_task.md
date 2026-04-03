# architects_task.md

## Purpose

This file is the **active execution control surface** for the current Architects packet.

It is for:
- current packet identity
- owner and lane structure
- exact allowed / forbidden edit boundaries
- execution checklist
- stop conditions
- deliverables
- immediate next action

It is **not** the durable historical ledger. Historical state belongs in `architects_progress.md`.

---

## Maintenance Rules

- Keep exactly one packet marked as `ACTIVE` unless a leader explicitly declares a controlled fork.
- When the active packet finishes, update `architects_progress.md` first, then roll this file forward.
- Do not place broad speculative roadmaps here.
- Do not let subagents widen scope beyond the packet boundary frozen by the leader.
- If scope changes materially, freeze a new packet instead of silently mutating this file.

---

## Current Active Packet

### Packet
`P-GATE-01`

### State
`ACTIVE / RALPH_NOW / SLICE 1 LANDED LOCALLY`

### Execution mode verdict
`RALPH_NOW`

### Objective
Activate architecture enforcement in advisory mode before any runtime, schema, or cutover work. The packet is a single-owner governance/verification packet limited to CI/check/test surfaces and explicit gate severity.

### Why this packet is active first
- Zeus is being treated as normative-authority-installed while still in runtime-mixed-transition mode.
- First-phase ordering remains authority install -> enforcement install -> controlled cleanup.
- `P-GATE-01` is positioned after `P-BOUND-01` and before `P-ROLL-01`, `P-STATE-01`, and `P-MIG-01`.
- Replay parity is advisory-first, not hard-blocking yet.

### Owner model
- Required: one named gate owner for execution
- Tribunal/principal architect remains the scope-freezing authority
- Verifier remains independent gate/evidence review
- Critic remains contradiction / blast-radius review

### Planning lane baseline
- leader: `gpt-5.4 xhigh`
- scouts: `gpt-5.3-codex-spark low` read-only
- verifier: `gpt-5.4-mini high`

### Current execution owner
- gate owner: `Architects local lead (current Codex session)`
- verifier/critic support remains advisory-only unless a later bounded slice needs explicit re-review

### Allowed edit surface
Only the following may be edited in this packet:
- `.github/workflows/**`
- `scripts/check_*`
- `scripts/replay_parity.py`
- `tests/test_architecture_contracts.py`
- `tests/test_cross_module_invariants.py`

### Forbidden edit surface
Explicitly forbidden for edits in this packet:
- all non-allowed files
- `src/**`
- `migrations/**`
- `architecture/**`
- `docs/architecture/**`
- `docs/governance/**` including boundary/runbook/cookbook/delta-ledger surfaces
- `.claude/CLAUDE.md`
- root/scoped `AGENTS.md`
- runtime state and cutover surfaces

### Non-goals
- no runtime behavior change
- no schema or migration work
- no live cutover
- no `position_current` rollout
- no historical migration
- no boundary-note/runbook/cookbook authorship
- no authority-file rewrite
- no outer-host automation expansion

### Current blocker
- no active hard blocker for Slice 1 execution
- operational note: use repo `.venv` for Python gate runs; system `python3` in this session is missing `PyYAML`
- advisory carry-forward: semgrep currently reports findings in `src/control/control_plane.py` and `src/state/strategy_tracker.py`, so semgrep must remain advisory in this packet until a later packet triages or fixes them

### Ready-to-commit slice
`Slice 1 — add the initial advisory architecture workflow under .github/workflows/ that wires the currently implemented manifest, module-boundary, packet-grammar, invariant, semgrep, and replay-parity gates with explicit severity/rationale/review conditions, while keeping semgrep and replay parity advisory.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current session has read the required authority surfaces
- [x] confirm `P-BOUND-01` prerequisite is satisfied in the live repo state
- [x] confirm no one is attempting runtime/schema/cutover work under this packet

### Phase B — allowed-surface inspection
- [x] inspect `.github/workflows/**`
- [x] inspect `scripts/check_*`
- [x] inspect `scripts/replay_parity.py`
- [x] inspect `tests/test_architecture_contracts.py`
- [x] inspect `tests/test_cross_module_invariants.py`
- [x] classify each surface as `present`, `missing`, `partial`, or `drifted`

Inventory result:
- `.github/workflows/**` -> `missing` active workflow; only scoped `AGENTS.md` exists
- `scripts/check_kernel_manifests.py` -> `present`
- `scripts/check_module_boundaries.py` -> `present`
- `scripts/check_work_packets.py` -> `present`
- `scripts/replay_parity.py` -> `present / staged-advisory`
- `tests/test_architecture_contracts.py` -> `present`
- `tests/test_cross_module_invariants.py` -> `present`

### Phase C — bounded enforcement design
- [x] make the first bounded set of gate surfaces explicit with severity and rationale
- [x] keep replay parity warn-only / staged-waiver until dual-write + `position_current` exist
- [x] keep any external-workspace-dependent checks advisory, never blocking
- [x] assign owner, rationale, and sunset/review condition to every gate in Slice 1

Slice 1 gate shape:
- blocking: `architecture-manifests`, `module-boundaries`, `packet-grammar`, `kernel-invariants`
- advisory: `semgrep-zeus`, `replay-parity`
- external-workspace-dependent audits remain out of the workflow and therefore non-blocking in this slice

### Phase D — evidence bundle
- [x] produce blocking vs advisory verdict
- [x] record maintenance-cost note
- [x] capture check/test outputs
- [x] write rollback note
- [x] write unresolved uncertainty note
- [x] write explicit runtime-evidence waiver
- [x] append execution result to `architects_progress.md`

Evidence snapshot for Slice 1:
- blocking verdict baseline: manifest/module-boundary/packet checks pass locally
- advisory verdict baseline: replay parity exits staged; semgrep finds current repo issues and therefore stays advisory in this packet
- rollback note: revert the new workflow file plus the current `architects_progress.md` / `architects_task.md` updates
- unresolved uncertainty: semgrep path-pattern warnings and current findings need later packetized triage before promotion to blocking

Local verification completed:
- workflow yaml parses cleanly
- `python3 scripts/check_kernel_manifests.py` -> pass
- `python3 scripts/check_module_boundaries.py` -> pass
- `python3 scripts/check_work_packets.py` -> pass
- `python3 scripts/replay_parity.py --ci` -> staged / advisory
- `.venv/bin/pytest -q tests/test_architecture_contracts.py tests/test_cross_module_invariants.py` -> `9 passed, 3 skipped`
- `.venv/bin/semgrep --config architecture/ast_rules/semgrep_zeus.yml --severity ERROR src` -> 2 findings, remains advisory

---

## Stop Conditions

Hard stop immediately if any of the following appears:
- required reads are unmet
- `P-BOUND-01` prerequisite is not satisfied
- scope exceeds the allowed edit surface
- authority disagreement changes file selection
- CI fails manifests, boundaries, or architecture tests
- runtime/schema/control-plane spillover appears
- any gate attempts to block on external workspace artifacts

Escalate to human for:
- live cutover timing
- schema cutover
- control-plane expansion
- destructive archive/delete
- permanent `.claude` retirement

---

## Subagent Policy For This Packet

Allowed use of subagents:
- read-only inventory of allowed gate surfaces
- bounded verification
- bounded test drafting
- contradiction / blast-radius review

Not allowed for subagents under this packet:
- workflow authorship without owner approval
- any runtime/codepath edits outside the frozen file set
- authority-surface rewrites
- silent scope expansion

Recommended baton split:
- Scout lane: inventory and classify allowed surfaces only
- Verifier lane: compress findings into acceptance / evidence / risks
- Critic lane: review false-positive risk, maintenance burden, and blast radius

---

## Definition of Done For `P-GATE-01`

`P-GATE-01` is done only when all of the following are true:
- work stayed entirely inside the allowed edit set
- gate surfaces have explicit severity and rationale
- replay parity remains advisory-first / warn-only at CI layer until prerequisites exist
- external-workspace-dependent checks remain advisory
- every gate has owner, rationale, and sunset/review condition
- the evidence bundle is complete
- the result is appended to `architects_progress.md` with any remaining uncertainty clearly stated

---

## Next Required Action

The next owner should do exactly this:
1. Land Slice 1 by committing and pushing the new advisory workflow plus the control-surface updates.
2. Verify the pushed branch shows the new advisory workflow on GitHub.
3. Freeze the follow-on slice that decides whether `semgrep-zeus` can be promoted, split, or must remain advisory because of packet-external findings.
4. Keep recording every blocker/proof transition in `architects_progress.md`.

If this cannot be done without leaving the allowed file boundary, stop and freeze a new packet rather than forcing progress.
