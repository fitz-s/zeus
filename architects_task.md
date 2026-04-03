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

## Program Workflow

1. Close the tribunal / current-phase `P-*` governance and enforcement packets.
2. Confirm current-phase authority install is complete without claiming runtime convergence.
3. Only then plan the foundation mainline automation program.
4. Use the foundation mainline to move Zeus from `hardened_transition` -> `governed_runtime` -> `mature_project`.

Current completion ladder:
- `current-phase complete` = tribunal / `P-*` packet family closed
- `governed_runtime complete` = canonical authority + machine gates + strategy-aware protection landed
- `mature_project complete` = shadow persistence demoted/removed and replay/parity can block regressions

---

## Current Active Packet

### Packet
`P-GATE-01-CONSOLIDATE-ADVISORY`

### State
`ACTIVE / RALPH_NOW / VERIFIED / READY TO COMMIT`

### Execution mode verdict
`RALPH_NOW`

### Objective
Consolidate the current advisory gate posture by freezing a machine-checkable workflow verdict for blocking-vs-advisory jobs, semgrep stance, and replay-parity stance before any later severity-promotion packet.

### Why this packet is active first
- the first advisory workflow is already landed but the verdict still depends on scattered workflow text and operator memory
- semgrep is currently advisory because of packet-external findings and path-pattern warnings
- replay parity is currently advisory because `position_current` and dual-write prerequisites do not yet exist
- this is a narrow, bounded, single-owner enforcement-consolidation slice and does not yet need team execution

### Owner model
- Required: one named execution owner for this advisory-consolidation slice
- Tribunal/principal architect remains the scope-freezing authority
- Verifier remains independent gate/evidence review
- Critic remains contradiction / blast-radius review

### Planning lane baseline
- leader: `gpt-5.4 xhigh`
- scouts: `gpt-5.3-codex-spark low` read-only
- verifier: `gpt-5.4-mini high`

### Current execution owner
- execution owner: `Architects local lead (current Codex session)`
- verifier/critic support will be used as attack-only review lanes before final verification

### Allowed edit surface
Only the following may be edited in this packet:
- `work_packets/P-GATE-01-CONSOLIDATE-ADVISORY.md`
- `.github/workflows/architecture_advisory_gates.yml`
- `scripts/check_advisory_gates.py`
- `tests/test_architecture_contracts.py`
- `architects_progress.md`
- `architects_task.md`

### Forbidden edit surface
Explicitly forbidden for edits in this packet:
- all non-allowed files
- all `architecture/**` files including `architecture/ast_rules/**`
- all scoped `AGENTS.md`
- `src/**`
- `migrations/**`
- `docs/**`
- all other workflow/script/test files outside the allowed list
- `.claude/CLAUDE.md`
- runtime state and cutover surfaces

### Non-goals
- no promotion of semgrep to blocking
- no promotion of replay parity to blocking
- no edits to semgrep rules under `architecture/ast_rules/**`
- no runtime/schema/cutover work
- no authority-doc rewrites

### Current blocker
- no active hard blocker at freeze time
- carry-forward fact: advisory workflow already landed in commit `eba4321`
- carry-forward fact: root AGENTS sync already landed in commits `0431f55` and `1866086`
- known advisory issue: semgrep still reports two findings outside this packet boundary and therefore remains advisory
- packet-external carry-forward: semgrep path-pattern warnings and the two current semgrep findings still require a later packet

### Ready-to-commit slice
`Slice 1 — add a machine-checkable advisory gate policy script, bind it into the workflow as a blocking verdict check, freeze the packet in work_packets/, and add a targeted architecture test for the current advisory split.`

---

## Immediate Execution Checklist

### Phase A — session revalidation
- [x] confirm the current session has read the required authority surfaces
- [x] confirm `P-BOUND-01` prerequisite is satisfied in the live repo state
- [x] confirm no one is attempting runtime/schema/cutover work under this packet
- [x] confirm the tribunal workflow now points to closing `P-*` packets before the foundation mainline

### Phase B — allowed-surface inspection
- [x] inspect current advisory workflow
- [x] inspect targetable scripts/tests in the allowed set
- [x] confirm semgrep and replay parity still require advisory posture

Inventory result:
- `.github/workflows/architecture_advisory_gates.yml` -> `present / advisory split exists but not yet machine-checked`
- `scripts/check_advisory_gates.py` -> `missing`
- `tests/test_architecture_contracts.py` -> `present / no workflow-verdict assertion yet`
- `work_packets/P-GATE-01-CONSOLIDATE-ADVISORY.md` -> `missing`
- `architects_progress.md` -> `present / active ledger`
- `architects_task.md` -> `present / active control surface`

### Phase C — bounded enforcement design
- [x] keep this slice narrow and single-owner
- [x] keep semgrep and replay parity advisory in this packet
- [x] encode the current workflow verdict in a repo-local script/test instead of operator memory only
- [x] keep all other packet families out of scope

Slice 1 shape:
- freeze the packet in `work_packets/`
- add `scripts/check_advisory_gates.py`
- update the advisory workflow to run the new policy check
- add a targeted workflow-verdict test
- keep progress/task synchronized with current execution truth

### Phase D — evidence bundle
- [x] run `python3 scripts/check_advisory_gates.py`
- [x] run `python3 scripts/check_kernel_manifests.py`
- [x] run `python3 scripts/check_module_boundaries.py`
- [x] run `python3 scripts/check_work_packets.py`
- [x] run `.venv/bin/pytest -q tests/test_architecture_contracts.py tests/test_cross_module_invariants.py`
- [x] run `python3 scripts/replay_parity.py --ci`
- [x] run `.venv/bin/semgrep --config architecture/ast_rules/semgrep_zeus.yml --severity ERROR src`
- [x] collect two attack-only adversarial reviews
- [x] append execution result to `architects_progress.md`
- [ ] commit and push the slice

Evidence snapshot for Slice 1:
- affected-surface note: workflow/script/test/packet control surfaces only
- rollback note: revert the work packet, advisory policy script, workflow update, targeted test, and paired Architects ledger updates together
- unresolved uncertainty: semgrep findings remain attack inputs only until a later packet decides whether to fix code or adjust rule scope
- advisory gate policy: `python3 scripts/check_advisory_gates.py` -> `advisory gate policy ok`
- workflow yaml parse: `.venv/bin/python ... yaml.safe_load(...)` -> `workflow yaml ok`
- blocking checks:
  - `python3 scripts/check_kernel_manifests.py` -> `kernel manifests ok`
  - `python3 scripts/check_module_boundaries.py` -> `module boundaries ok`
  - `python3 scripts/check_work_packets.py` -> `work packet grammar ok`
  - `.venv/bin/pytest -q tests/test_architecture_contracts.py tests/test_cross_module_invariants.py` -> `10 passed, 3 skipped`
- advisory checks:
  - `python3 scripts/replay_parity.py --ci` -> `canonical ledger tables not present yet; replay parity is staged`
  - `.venv/bin/semgrep --config architecture/ast_rules/semgrep_zeus.yml --severity ERROR src` -> 2 findings + path warnings, remains advisory
- attack-only reviews:
  - internal adversarial review -> no additional in-scope contradiction beyond verdict brittleness already fixed
  - Gemini artifact -> `.omx/artifacts/gemini-p-gate-01-consolidate-advisory-attack-20260403T042215Z.md`
- architect verification:
  - leader architect pass -> no remaining in-scope contradiction after the attack-only fixes
- in-scope fixes taken after attack review:
  - normalized workflow YAML `on` parsing in script/test
  - reduced test/script duplication by making the test execute the policy script
  - removed stale `architects_task.md` owner indirection from workflow metadata
  - lowered workflow Python version target from `3.14` to `3.13` to reduce CI brittleness

Local verification completed:
- current advisory workflow inspected
- current semgrep advisory rationale inspected
- `git diff --check` will be run before commit
- manual deslop pass completed on changed files; no further dead code or duplication removal was needed after the script/test simplification

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

`P-GATE-01-CONSOLIDATE-ADVISORY` is done only when all of the following are true:
- work stayed entirely inside the allowed edit set
- the advisory workflow has a machine-checkable verdict for blocking vs advisory jobs
- semgrep remains advisory with explicit promotion conditions
- replay parity remains advisory with explicit promotion conditions
- external-workspace-dependent checks remain advisory
- two attack-only adversarial reviews have been captured
- the evidence bundle is complete
- the result is appended to `architects_progress.md` with any remaining uncertainty clearly stated

---

## Next Required Action

The next owner should do exactly this:
1. Commit and push this packet.
2. Reconcile the Architects ledgers to the pushed state if needed.
3. Freeze the next bounded `P-*` packet instead of widening this one.
4. Only after the full `P-*` family closes, plan the foundation mainline automation program.

If this cannot be done without leaving the allowed file boundary, stop and freeze a new packet rather than forcing progress.
