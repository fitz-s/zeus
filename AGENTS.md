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
- Distinguish program, packet, and execution slice.
- Narrow scope first.
- Evidence before claims.
- No convenience rewrite of authority.
- No broad repo edits.
- No “while here” side migrations.

Program / packet / slice rules:

- A program phase is larger than a packet.
  Examples: `FOUNDATION-MAINLINE`, `P0`, `P1`.
- A packet is the atomic authority-bearing unit of execution.
  Examples: `P0.2 attribution freeze`, `P0.1 exit semantics split`.
- An execution slice is a commit-sized step inside one still-open packet.
- Do not confuse “one slice completed” with “packet completed”.
- If the active packet remains open, the next slice is clear, and no new authority/risk boundary is crossed, continue autonomously after commit/push instead of stopping for a human “continue”.
- Stop only when:
  - the packet is actually complete,
  - the next slice would widen scope,
  - the next slice would change phase/packet,
  - the next slice would cross into a higher-risk zone,
  - or a real blocker / contradiction appears.

Micro-event logging rule:

- Do not dump every small attempt into `architects_progress.md`.
- Small events, retries, scout findings, timeout notes, and experiment breadcrumbs belong in `.omx/context/<packet>-worklog.md`.
- `architects_progress.md` is packet-level durable state only.
- `architects_task.md` is active control state only.
- Spark scouts may draft or append micro-event worklog entries.
- Spark scouts must not directly edit `architects_progress.md` or `architects_task.md`.
- The leader is responsible for promoting a worklog fact into `architects_progress.md` only when it becomes a real packet state transition, blocker, or accepted evidence item.

Preferred micro-event format:

```md
## [timestamp local] <packet> <slice-or-event>
- Author:
- Lane:
- Type: scout | retry | timeout | evidence | blocker | note
- Files:
- Finding:
- Evidence:
- Suggested next slice:
- Promote to architects_progress/task?: yes | no
```

Post-P0.5 autonomy rule:

- Before `P0.5` is complete and accepted:
  - no broad autonomous multi-packet team execution
  - no “open team from momentum”
- `P0.5` does not self-authorize team autonomy while it is still the active packet being implemented.
- After `P0.5` is complete, accepted, and pushed **and** after a later `FOUNDATION-TEAM-GATE` packet is frozen and accepted:
  - later phases may use autonomous **packet-by-packet** team execution
  - still only one frozen packet at a time
  - owner, file boundary, acceptance gate, and blocker policy must still be frozen before team launch
- Even after `P0.5`:
  - final destructive/cutover work remains human-gated
  - `P7` is never fully autonomous for final cutover/delete transitions
  - “destructive” includes, at minimum:
    - live cutover timing decisions
    - data/archive/delete transitions
    - irreversible migration/cutover switches
    - authority-surface deletion/demotion that changes the active law stack

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

Additional phase gate:

- Before `P0.5` is complete, do not use team mode as a broad execution default for the foundation mainline.
- After `P0.5`, team mode becomes allowed for later phases only on one frozen packet at a time and only after `FOUNDATION-TEAM-GATE` is accepted.

## 8. Model routing and reasoning-effort policy

If you are a Codex / GPT-family model, the routing policy below applies.
If you are not a Codex / GPT-family model, do not treat the model names below as required defaults; map the intent to your local runtime equivalent instead.

Current routing contract for this repo:

- Normal work in this repo should be covered by exactly three preferred models:
  `gpt-5.4`, `gpt-5.4-mini`, and `gpt-5.3-codex-spark`.
- Do not recommend or auto-route to `gpt-5.3-codex`, `gpt-5-codex`, or `gpt-5-codex-mini` unless the user explicitly asks for compatibility testing or model-comparison work.

Current observed Codex runtime ceilings on this machine:

- `gpt-5.4`: about `272k` context window
- `gpt-5.4-mini`: about `272k` context window
- `gpt-5.3-codex-spark`: about `128k` context window

Preferred working budgets:

- `gpt-5.3-codex-spark`: prefer `<= 40k` input budget
- `gpt-5.4-mini`: prefer `<= 140k` input budget
- `gpt-5.4`: prefer `<= 220k` input budget

Treat these as routing safety rails, not theoretical hard ceilings.

- `gpt-5.4` is the leader model.
  Use it for architecture authority, contract freezing, cross-zone reasoning, packet judgment, final integration, and final acceptance.
- `gpt-5.4-mini` is the verifier / writer / bounded-review model.
  Use it for evidence collection, targeted review, bounded synthesis, documentation polish, compact follow-up analysis, contradiction extraction, and scout-plus lanes when spark is too small.
- `gpt-5.3-codex-spark` is the default scout subagent model.
  Prefer it for narrow read-only lookup, repo mapping, symbol search, relationship tracing, diff triage, and repeated fact gathering.

Child-agent default posture:

- Prefer native subagents often.
- Hard cap: no more than `6` active native subagents at once.
- Preferred steady-state: `2` to `3` active lanes unless the packet explicitly justifies more.
- Prefer 2 to 4 parallel `gpt-5.3-codex-spark` scout lanes before broad implementation when the task spans multiple files or multiple independent questions.
- Keep spark batons small, concrete, read-only, and evidence-returning.
- When spark is too small but the task is still bounded and non-authoritative, escalate to `gpt-5.4-mini` instead of introducing a second codex-default lane.
- Prefer `gpt-5.4-mini` immediately when the lane needs:
  - more than one small cluster of files
  - contradiction extraction across several docs
  - bounded adversarial review
  - read-only synthesis that would otherwise risk spark timeout
- Keep final judgment, contract freezing, and acceptance on the leader `gpt-5.4`.
- Do not use subagents as an excuse to skip main-thread reading of core law surfaces.

Reasoning-effort policy:

- `low`:
  fast lookup, grep-like exploration, structure mapping, obvious transforms, quick summaries
- `medium`:
  bounded comparison, packet drafting, shortlist building, moderate synthesis, first-pass review
- `high`:
  implementation planning, non-trivial debugging, verifier judgments, code review with blast-radius concerns
- `xhigh`:
  architecture authority, governance / law edits, schema or control-plane decisions, contradictory truth-surface resolution, high-stakes final acceptance

Invocation guidance:

- read-only scout:
  prefer `explore` or another read-only native child lane on `gpt-5.3-codex-spark` with `low`
- broader scout, bounded synthesis, verifier support, or documentation follow-up:
  use `gpt-5.4-mini` with `medium` or `high`
- verifier / writer / targeted reviewer:
  use `gpt-5.4-mini` with `medium` or `high`
- critic / adversarial review:
  use `gpt-5.4` with `high` or `xhigh` for final attack review; use `gpt-5.4-mini` with `high` for pre-critique evidence compression
- architect / critic / final integrator:
  use `gpt-5.4` with `high` or `xhigh`
- only escalate to `omx team` after owner, file boundary, acceptance gate, and blocker policy are frozen

Scout escalation rule:

- If a spark scout times out, returns ambiguous synthesis, or needs repeated retries because the baton is too broad, escalate the lane to `gpt-5.4-mini` instead of retrying spark indefinitely.
- Spark is for narrow scouting, not for pretending medium-context synthesis is cheap.
- If spawn attempts fail because the lane cap is reached, close or reuse an existing lane before creating a new one.

Do not:

- spend `xhigh` on routine scans
- use spark for final architecture claims, governance edits, or overlapping write lanes
- use mini for unresolved cross-zone design or kernel-law decisions
- recommend `gpt-5.3-codex`, `gpt-5-codex`, or `gpt-5-codex-mini` as default lanes for normal Zeus work
- assume long context removes the need for bounded batons
- treat a temporary 1M leader window as permission for unbounded prompts; use it as headroom, not as the default working size

## 9. External boundary

OpenClaw and Venus are outside repo authority.

- Repo law lives in repo files.
- Workspace memory/docs may inform operator context, but do not outrank repo authority.
- Zeus exposes typed contracts and derived status outward.
- Outward tools must not directly mutate repo truth, schema, or authority.
- Never read or write external workspace state as if it were repo canonical truth unless the packet is explicitly boundary-focused.

## 10. Evidence before completion

Completion requires:
- changed files listed
- tests/gates run
- any waived gate explained
- rollback note
- unresolved uncertainty stated plainly

Waiver rule:

- A waived gate is acceptable only when:
  - the gate is explicitly staged/advisory by current law, or
  - the gate is unavailable for an external reason that is recorded as a blocker or limitation.
- A waived gate is **not** acceptable when the real reason is convenience, impatience, or difficulty.
- High-sensitivity architecture/governance/schema packets must not self-waive required gates by prose alone.

## 11. Write style for agents

Keep edits delta-shaped.
Patch authority drift instead of rewriting everything.
If you add a new surface, say what it harmonizes, what it supersedes, and why it does not create parallel authority.
