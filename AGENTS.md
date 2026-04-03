File: AGENTS.md
Disposition: NEW
Authority basis: architecture/self_check/authority_index.md; docs/governance/zeus_autonomous_delivery_constitution.md; docs/architecture/zeus_durable_architecture_spec.md; docs/governance/zeus_change_control_constitution.md; architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/zones.yaml; architecture/negative_constraints.yaml; current repo runtime truth surfaces.
Supersedes / harmonizes: .claude/CLAUDE.md as primary instruction hub; ad hoc guidance in WORKSPACE_MAP.md; historical authority claims in docs/specs and docs/architecture.
Why this file exists now: Codex/OMX and future zero-context agents need one repo-native instruction surface that loads before work starts.
Current-phase or long-lived: Long-lived.

# Zeus repo AGENTS

You are working inside Zeus, a position-management system under authority hardening.
Your job is not to “improve the repo.”
Your job is to change only what the active packet allows while protecting kernel law, truth contracts, and boundary discipline.

## 1. Read this first

Before editing anything, read in this order:

1. `architecture/self_check/authority_index.md`
2. `docs/governance/zeus_autonomous_delivery_constitution.md`
3. `docs/architecture/zeus_durable_architecture_spec.md`
4. `docs/governance/zeus_change_control_constitution.md`
5. `architecture/kernel_manifest.yaml`
6. `architecture/invariants.yaml`
7. `architecture/zones.yaml`
8. `architecture/negative_constraints.yaml`
9. scoped `AGENTS.md` in the directory you are editing
10. then the code

If current runtime facts conflict with target-law docs:
- use runtime code/contracts for present-tense facts
- use authority docs for change permission and target direction
- record the mismatch in the packet or delta ledger

Imported source-package note:
- `zeus_mature_project_foundation/` is preserved as a source import for provenance and comparison.
- Active authority lives in the mirrored repo surfaces under `architecture/`, `docs/architecture/`, `docs/governance/`, and `docs/rollout/`.
- Do not edit the source-package copy as if it were the live law surface unless the packet explicitly targets source-package maintenance.

## 2. Required working posture

- Work packet first.
- Narrow scope first.
- Evidence before claims.
- No convenience rewrite of authority.
- No broad repo edits.
- No “while here” side migrations.

## 3. Planning lock is mandatory when

You must stop and produce or load an approved packet before changing anything if the task touches:

- `architecture/**`
- `docs/governance/**`
- `migrations/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `src/state/**` truth ownership, schema, projection, or lifecycle write paths
- `src/control/**`
- `src/supervisor_api/**`
- cross-zone edits
- more than 4 files
- anything described as canonical truth, lifecycle, governance, or control

## 4. What counts as each change class

### Math change
Allowed only when the change stays inside existing semantic contracts.
Examples:
- scoring formulas
- calibration logic
- signal thresholds
- feature generation
- exploration heuristics

A math change becomes architecture/governance work if it touches:
- lifecycle states or phases
- `strategy_key` grammar
- unit semantics
- point-in-time snapshot rules
- control-plane behavior
- DB/file truth contracts
- supervisor contracts

### Architecture change
Any change to:
- canonical write/read paths
- lifecycle grammar
- event/projection transaction boundaries
- truth-surface ownership
- zone boundaries
- state authority

### Governance / schema / truth-contract change
Any change to:
- manifests, constitutions, AGENTS, decision registers
- migrations
- control-plane file semantics
- supervisor API contracts
- derived-vs-canonical truth classification

## 5. Forbidden moves

Never do any of the following:

- Treat `.claude/CLAUDE.md` as the top authority.
- Treat historical docs as active law unless the packet explicitly extracts rationale from them.
- Treat `zeus_mature_project_foundation/` as the active authority location after mirrored authority files are installed.
- Promote JSON exports back to canonical truth.
- Invent or widen governance keys beyond `strategy_key`.
- Add strategy fallback defaults when exact attribution exists or should exist.
- Assign lifecycle phase/state strings ad hoc outside the lifecycle kernel.
- Let math code write or redefine lifecycle/protective/control semantics.
- Let Venus/OpenClaw or workspace docs become repo authority.
- Rewrite broad authority files and runtime files in one unbounded patch.
- Claim convergence that runtime does not yet have.

## 6. Zero-context safety questions

Before you edit, answer:

- What is the authoritative truth surface here?
- What zone is being touched?
- Which invariant IDs matter?
- Which files are allowed to change?
- Which files are forbidden?
- Is this math, architecture, governance, or schema work?
- What evidence is required before completion?

If you cannot answer those, stop and plan.

## 7. Team usage

You may enter `$team`, `omx team`, `/team`, or `omc team` only when:
- there is an approved packet
- work is parallelizable
- one owner remains accountable
- team members are not being asked to redefine authority

Do not teamize:
- `architecture/**`
- `docs/governance/**`
- migration cutover decisions
- `.claude/CLAUDE.md` compatibility policy
- supervisor/control-plane semantics
- packet-less exploratory rewrites

Use advisory lanes instead:
- `omx ask ...`
- `omc ask ...`
- `/ccg`
- read-only critique/review

## 8. External boundary

OpenClaw and Venus are outside repo authority.

- Repo law lives in repo files.
- Workspace memory/docs may inform operator context, but do not outrank repo authority.
- Zeus exposes typed contracts and derived status outward.
- Outward tools must not directly mutate repo truth, schema, or authority.
- Never read or write external workspace state as if it were repo canonical truth unless the packet is explicitly boundary-focused.

## 9. Evidence before completion

Completion requires:
- changed files listed
- tests/gates run
- any waived gate explained
- rollback note
- unresolved uncertainty stated plainly

## 10. Write style for agents

Keep edits delta-shaped.
Patch authority drift instead of rewriting everything.
If you add a new surface, say what it harmonizes, what it supersedes, and why it does not create parallel authority.
