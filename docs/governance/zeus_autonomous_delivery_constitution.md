File: docs/governance/zeus_autonomous_delivery_constitution.md
Disposition: NEW
Authority basis: docs/architecture/zeus_durable_architecture_spec.md; docs/governance/zeus_change_control_constitution.md; architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/zones.yaml; architecture/negative_constraints.yaml; architecture/maturity_model.yaml; current repo runtime truth surfaces (src/config.py, src/state/db.py, src/state/portfolio.py, src/control/control_plane.py, src/observability/status_summary.py, src/supervisor_api/contracts.py, scripts/healthcheck.py, scripts/audit_architecture_alignment.py); Session 1 and Session 2 dossiers as compressed judgment only.
Supersedes / harmonizes: .claude/CLAUDE.md workflow guidance; WORKSPACE_MAP.md authority claims; docs/specs/zeus_spec.md; docs/architecture/zeus_blueprint_v2.md; docs/architecture/zeus_design_philosophy.md; docs/plans/zeus_live_plan.md; prior-session dossiers as navigation only.
Why this file exists now: Zeus needs one repo-local delivery law that tells humans and agents how work is allowed to move from request to rollout without manufacturing new parallel authority.
Current-phase or long-lived: Long-lived, with quarterly review.

# Zeus Autonomous Delivery Constitution

## 0. Executive verdict

Zeus should run a **dual-track delivery model with one primary runtime**.

- **Primary runtime:** OMX / Codex / AGENTS-first.
- **Approved secondary runtime:** OMC / Claude Code team orchestration.
- **External host layer only:** OpenClaw and Venus.

That is the main path because Zeus's immediate problem is not “more orchestration.” It is **authority compression, scoped instruction loading, zone discipline, and truthful migration from mixed runtime reality to canonical event/projection authority**. OMX and Codex align directly with repo-local `AGENTS.md` loading and scoped instruction precedence. OMC remains valuable, but as a secondary execution lane rather than the authority center.

## 1. Governing doctrine

### 1.1 Two-axis authority rule

Zeus now operates with two explicit axes.

#### Descriptive authority: what is true in runtime today
Use current code, schema, contracts, and live state conventions for present-tense runtime facts.

#### Normative authority: what controls design and change
Use the mature foundation stack for design, change control, migration direction, and what may land next.

When the two disagree:
- descriptive authority wins for “what exists right now”
- normative authority wins for “what changes are allowed and what target shape must be protected”
- the disagreement must be recorded as migration drift, never hand-waved away

### 1.2 Precedence
1. Machine-checkable semantic authority
2. Principal architecture authority
3. Governance / change-control authority
4. Current repo runtime truth surfaces
5. Operator brief / runbook / orientation docs
6. Prior-session dossiers
7. Historical rationale
8. Comments, convenience assumptions, model glue

### 1.3 Non-negotiable theorem
No agent, runtime, hook, dossier, or operator brief may override:
- kernel manifests
- invariants
- zones
- negative constraints
- canonical truth-contract boundaries
- human escalation requirements in this constitution

## 2. Primary path decision

Decision: **Dual-track but one primary runtime: OMX-first. OMC remains approved secondary orchestration.**

Why now:
- Root/scoped `AGENTS.md` are the repo’s next instruction center.
- Codex loads `AGENTS.md` natively and in layered root-to-leaf order.
- OMX is explicitly a workflow layer for Codex CLI, not a replacement runtime.
- OMC’s strongest documented center of gravity is canonical team orchestration and tmux worker management, not repo-law-through-AGENTS.

Why not the alternatives:
- **Not OMC-first:** better for orchestration than authority collapse; would keep repo law too close to a Claude-side runtime surface.
- **Not pure OMX-only:** rejects a useful secondary path for team execution, cross-model review, and Claude-side swarm operations.
- **Not “current-phase split” as the only answer:** that describes migration posture, not runtime choice.

Confidence: 0.74  
Reversibility: Medium  
Blast radius: Medium on operator workflow, low on semantic kernel if authority remains AGENTS/manifests first  
Evidence basis:
- official Codex AGENTS loading behavior
- OMX workflow docs
- OMC team-runtime docs
- Session 1/2 dossiers
- current repo absence of repo-local AGENTS surfaces
What would overturn this:
- Codex/OMX materially regressing on AGENTS loading or stable runtime operation
- OMC becoming the clear, documented, repo-law instruction-loading surface
- repo moving away from AGENTS-based instruction loading entirely
Operator implication:
- default Zeus repo work starts in OMX unless there is a specific reason to use OMC
- OMC is explicitly allowed, but not as the repo’s authority center
Delivery implication:
- root/scoped `AGENTS.md` become primary instruction surfaces
- `.claude/CLAUDE.md` becomes compatibility-only

## 3. Current phase vs end state

### 3.1 Current phase
Zeus is in **normative-authority-installed / runtime-mixed-transition** mode.

Current truth:
- event spine exists (`position_events`)
- open-position truth is still mixed and still leans on `PortfolioState` / JSON projection surrogates
- `position_current` does not yet exist in current repo reality
- status and control surfaces are still file-first operational contracts
- external workspace assumptions still leak into repo audit logic

Current-phase rule:
- install law now
- do not lie about convergence now
- patch the highest-drift semantics now
- defer full canonical cutover until parity evidence exists

### 3.2 End state
Zeus end state is:

1. semantic atoms / kernel laws frozen
2. manifests / invariants / zones / negative constraints machine-gated
3. schema guarantees append-only `position_events` + deterministic `position_current`
4. runtime truth reads canonical ledger/projection rather than JSON primary state
5. learning spine consumes point-in-time snapshots only
6. protective spine is strategy-aware and executable
7. delivery orchestration uses OMX primary and OMC secondary
8. Venus consumes typed contracts and derived operator exports only
9. OpenClaw remains outer host / notification / memory surface only
10. experiment ring stays outside kernel law and cannot write it back implicitly

## 4. Delivery lifecycle

### 4.1 Package classes
Every task must be classified before execution.

- **Math change:** changes model logic, thresholds, signal/calibration formulas, or feature transforms inside existing semantic contracts.
- **Architecture change:** changes lifecycle grammar, authority ownership, transaction boundaries, zone boundaries, or canonical write/read paths.
- **Governance change:** changes manifests, AGENTS surfaces, constitutions, decision registers, demotion rules, or evidence burden.
- **Schema / truth-contract change:** changes migrations, typed supervisor contracts, file or DB truth contracts, or control-plane semantics.

### 4.2 Planning lock required
Planning lock is mandatory before any of the following:
- touches K0 or K1
- migration / schema / replay / parity work
- control-plane or supervisor contract changes
- changes to AGENTS, manifests, constitutions, or decision register
- cross-zone edits
- more than 4 code files
- any change that claims to modify current truth surface classification
- any package using `$team` or `omc team` against high-sensitivity files

### 4.3 One-key execution doctrine
Zeus does **not** permit true one-key execution before planning lock for high-stakes work.

Safe one-key entry means:
1. bootstrap runtime
2. read authority
3. produce or load an approved packet
4. execute within that packet

Unsafe one-key entry means:
- “autopilot the repo”
- “use team on the whole migration”
- “let hooks/outer host decide schema or authority”

### 4.4 Package lifecycle
1. Intake
2. Task class
3. Planning lock
4. Packet issue
5. Scoped reads
6. Execution
7. Verification
8. Evidence assembly
9. Merge / hold / rollback
10. Cutover only if cutover criteria are satisfied

## 5. Ownership model

### 5.1 Owner roles
- **Tribunal lead:** approves path, escalation, rollback doctrine
- **Package owner:** owns one packet end to end
- **Verifier:** independent test/contract/gate check
- **Critic:** contradiction / blast-radius review
- **Operator:** runtime, tooling, resume/shutdown, incident handling
- **Human gate:** approves irreversible changes and live cutover

### 5.2 Single-owner rule
Every high-sensitivity package must have one named owner even if reviewers or teams contribute work.

### 5.3 Team usage rule
`$team` / `omc team` may assist execution, but they do not dissolve ownership.

## 6. Team runtime policy

### 6.1 OMX team
Use `$team` / `omx team` when:
- packet is already approved
- work is parallelizable
- files are mostly K2/K3, tests, docs, or bounded verification lanes
- detached worktrees and cleanup can be verified before completion

Do not use when:
- packet is still discovering authority
- schema cutover is unresolved
- migration rollback is undefined
- live-state deletion or archival is in scope

### 6.2 OMC team
Use `/team` / `omc team` when:
- Claude-side orchestration is valuable
- cross-model worker panes are useful
- the work is execution or review, not repo-law authorship

Do not use as primary path for:
- AGENTS authority installation
- manifest changes
- decision-register decisions
- migration law

### 6.3 Advisory vs executor use
- **Advisory allowed:** `omx ask`, `omc ask`, `/ccg`, research, design critique, review
- **Executor allowed:** `$ralph`, `$team`, `/team`, `omc team`, but only after packet approval
- **Forbidden:** autonomous authority rewrite without packet or human gate

## 7. Evidence model

Every package must supply:

- authority basis
- touched zones
- invariants touched
- files changed
- tests/gates run
- runtime evidence or explicit waiver
- rollback note
- unresolved uncertainty note

### 7.1 Minimum evidence by class
- **Math:** targeted tests + strategy/risk evidence + no truth-contract drift
- **Architecture:** tests + manifests/invariants reference + gate output + rollback
- **Governance:** contradiction check + precedence check + affected-surface mapping
- **Schema:** migration SQL + rollback + parity/replay status + cutover note

## 8. Minimum required / recommended / allowed deviation / non-negotiable boundary

### 8.1 Authority packages
Minimum required:
- read authority index, spec, constitution, manifests
- planning lock
- named owner
- verifier
Recommended path:
- OMX primary
- single owner drafts
- secondary advisory review from OMC or ask-provider
Allowed deviation:
- OMC may draft if output is reviewed against AGENTS/manifests before acceptance
Non-negotiable boundary:
- no team-first authorship
- no historical doc may be cited as active law
- no `.claude/CLAUDE.md` override

### 8.2 Math packages
Minimum required:
- identify whether change stays inside K3
- prove no truth-contract/lifecycle/control impact
Recommended path:
- OMX with single owner
Allowed deviation:
- OMC advisory or review lane
Non-negotiable boundary:
- no direct writes to lifecycle, control, or canonical truth surfaces
- no new governance keys, unit fallbacks, or phase strings

### 8.3 Schema / migration packages
Minimum required:
- planning lock
- rollback doctrine
- parity/replay status
- human gate for destructive or live cutover changes
Recommended path:
- single owner + verifier + critic
Allowed deviation:
- team may help generate tests or dry-run checks
Non-negotiable boundary:
- no direct live cutover without human signoff
- no silent migration of historical truth claims

### 8.4 Control-plane and Venus boundary packages
Minimum required:
- typed-contract review
- boundary note update
- operator review
Recommended path:
- single owner + human-aware review
Allowed deviation:
- advisory review from OMC ask/cross-model lanes
Non-negotiable boundary:
- Venus/OpenClaw do not gain direct DB/code authority
- control-plane stays narrow

## 9. Autonomy envelope under randomness

Autonomy is permitted only inside bounded adaptation.
The agent may adapt execution path.
The agent may not adapt law.

### 9.1 Allowed autonomous adaptation
- retrying a failed read-only lookup
- replacing a dead worker with same packet scope
- falling back from team to single-owner execution
- switching advisory provider when one local CLI is unavailable
- pausing execution when evidence cannot be gathered

### 9.2 Forbidden autonomous adaptation
- widening file scope beyond packet
- relaxing gates because tools failed
- promoting historical docs or outer workspace files to authority
- changing schema or control-plane semantics to “make progress”
- rewriting packet objectives after execution has begun
- auto-resuming live trading behavior after safety-triggered pause

### 9.3 Expanded matrix
See `docs/governance/zeus_top_tier_decision_register.md` for the full randomness matrix.
That matrix is part of this constitution by reference.

## 10. Stop / handoff / resume / rollback / escalation doctrine

### 10.1 Stop now
Stop immediately if:
- authority surface disagreement changes file-selection or truth-surface choice
- packet scope is exceeded
- CI fails on manifests, boundaries, or architecture contracts
- parity/replay fails on a supposed canonical migration
- a worker uses stale or contradictory authority
- an outer tool suggests direct DB or authority mutation

### 10.2 Handoff
Handoff requires:
- packet ID
- state of work
- changed files
- gates run
- unresolved risks
- next required reads

### 10.3 Resume
Resume is allowed only if:
- same packet or an explicit superseding packet exists
- changed scope is acknowledged
- stale team state has been checked
- `.omx/` or `.omc/` runtime state is reviewed when relevant

### 10.4 Rollback
Rollback doctrine:
- docs/AGENTS/constitution: revert file set and restore previous authority note
- schema: apply defined rollback or halt before cutover
- runtime semantics: revert branch and freeze packet family
- team runtime accidents: stop workers, clean worktrees/sessions, verify git cleanliness before continuing

### 10.5 Escalate to human
Always escalate for:
- live cutover timing
- destructive archive/delete of runtime truth or historical data
- schema cutover
- control-plane expansion
- permanent `.claude` retirement
- any contradiction between operator safety and execution convenience

## 11. OpenClaw and Venus boundary

OpenClaw and Venus are external control-plane and operator-host surfaces.

They may:
- read derived status
- consume typed contracts
- issue narrow control-plane actions inside allowed rules
- raise audits, gaps, and packets

They may not:
- become repo authority
- bypass packets
- write directly to DB truth surfaces
- mutate manifests, constitutions, or AGENTS
- widen strategy/control semantics by convenience

## 12. Maintenance economics doctrine

Keep forever:
- authority index
- root/scoped AGENTS
- manifests/invariants/zones/negative constraints
- architecture contracts tests
- module-boundary checks
- semgrep forbidden-move checks

Keep only for transition unless they prove durable value:
- `.claude/CLAUDE.md` compatibility shim
- historical-doc demotion banners
- runtime delta ledger
- repo/workspace audit assumptions that depend on external files

Advisory first, required later:
- replay parity
- packet grammar for low-risk K2/K3 work
- boundary audits that depend on missing external workspace files

## 13. Review cadence

- weekly during initial rollout
- biweekly once AGENTS and gates are installed cleanly
- quarterly after canonical cutover

## 14. Final constitutional rule

Zeus keeps top-tier model autonomy by narrowing the field it is allowed to improvise inside.
The system is not safe because agents are passive.
The system is safe because **law is explicit, runtime truth is honest, deviations are bounded, and irreversible moves are not delegated by accident**.
