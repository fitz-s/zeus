File: docs/governance/zeus_top_tier_decision_register.md
Disposition: NEW
Authority basis: docs/governance/zeus_autonomous_delivery_constitution.md; foundation package; current repo runtime truth surfaces; Session 1 and Session 2 dossiers; official OMX/OMC/OpenClaw/Codex docs.
Supersedes / harmonizes: prior-session compressed judgments as stand-alone decision memory.
Why this file exists now: high-stakes changes need one auditable register for irreversible choices, not-now boundaries, randomness rules, maintenance economics, and rollback doctrine.
Current-phase or long-lived: Long-lived, with per-decision review.

# Zeus Top-Tier Decision Register

## 0. Primary path choice

Decision: **Use dual-track delivery with OMX-first primary runtime and OMC as approved secondary runtime.**

Why now:
- AGENTS-first repo law must be installable in the runtime that actually loads layered `AGENTS.md`.
- OMC remains too orchestration-centered to serve as repo-law center without reintroducing runtime-law coupling.

Why not the alternatives:
- OMC-first would optimize swarm execution before authority collapse.
- OMX-only would throw away useful team/runtime diversity.
- deferring the runtime choice would let both toolchains continue as shadow primaries.

Confidence: 0.74  
Reversibility: Medium  
Rollback difficulty: Moderate (runbook and operator retraining)  
Blast radius: Medium  
Dependent surfaces: `AGENTS.md`, `.claude/CLAUDE.md`, command cookbook, operator runbook, boundary note  
Evidence basis: tool docs, Codex AGENTS loading, Session 1/2 dossiers, current repo instruction drift  
What would overturn this: stable evidence that OMC becomes the better repo-law surface or OMX becomes unreliable for AGENTS-first work  
Operator implication: start Zeus repo work in OMX unless there is a concrete reason not to  
Delivery implication: optimize docs and instructions for OMX first, keep OMC interoperable

## 1. Irreversible Decisions Register

### 1.1 Root/scoped `AGENTS.md` become the primary repo instruction surface
Decision: install root + scoped `AGENTS.md` as primary repo instruction surface

Why decide now:
- there is currently no repo-local AGENTS stack
- leaving `.claude/CLAUDE.md` primary keeps authority drift active

Why not defer:
- every future autonomous session would continue loading or citing the wrong center

Reversibility level: Medium  
Rollback difficulty: Low-to-moderate  
Blast radius: High on daily workflow; low on runtime semantics  
Dependent surfaces: `AGENTS.md`, scoped AGENTS, `.claude/CLAUDE.md`, runbook, cookbook  
Evidence basis: Codex AGENTS loading model; Session 1/2 locks  
What would overturn this: repo abandoning AGENTS-based instruction loading entirely

### 1.2 `.claude/CLAUDE.md` becomes compatibility-only, not principal authority
Decision: retain `.claude/CLAUDE.md` only as transition shim

Why decide now:
- the file still routes work toward old authority claims
- some users and runtimes still rely on it

Why not defer:
- leaving it ambiguous preserves live parallel authority

Reversibility level: High  
Rollback difficulty: Low  
Blast radius: Medium  
Dependent surfaces: `.claude/CLAUDE.md`, AGENTS root, authority index  
Evidence basis: current repo `.claude/CLAUDE.md`, foundation CLAUDE patch, dossiers  
What would overturn this: human decision to retire `.claude` entirely after stable AGENTS adoption

### 1.3 Keep two-axis authority explicitly encoded
Decision: preserve descriptive vs normative split explicitly

Why decide now:
- current runtime and mature target are not fully converged
- flattening them would create false-complete illusions

Why not defer:
- every file written now would otherwise overclaim maturity

Reversibility level: Low  
Rollback difficulty: High  
Blast radius: High  
Dependent surfaces: constitution, decision register, delta ledger, runbooks  
Evidence basis: current repo mixed truth surfaces; foundation target law; Session 1 cross-axis doctrine  
What would overturn this: actual runtime convergence plus parity proof

### 1.4 `strategy_key` stays the sole governance key
Decision: no new governance center beyond `strategy_key`

Why decide now:
- attribution drift already exists
- protective and learning layers depend on stable governance identity

Why not defer:
- deferred grammar creates cumulative cleanup cost

Reversibility level: Low  
Rollback difficulty: High  
Blast radius: High across learning/protection/analytics  
Dependent surfaces: kernel manifest, state code, risk logic, docs  
Evidence basis: foundation spec + manifest; Session 2 lock  
What would overturn this: a principled redesign of governance identity backed by architecture spec revision

### 1.5 Canonical target remains append-first `position_events` + `position_current`, but current repo does not claim it has already landed
Decision: preserve target kernel while refusing false present-tense convergence

Why decide now:
- target direction is mature enough to lock
- current code and schema do not yet realize full projection truth

Why not defer:
- target ambiguity would leak back into architecture work

Reversibility level: Low on target direction; high on rollout timing  
Rollback difficulty: Medium  
Blast radius: High  
Dependent surfaces: spec, kernel manifest, migrations, state code, parity  
Evidence basis: foundation package + current repo state  
What would overturn this: evidence that event/projection target is itself wrong, not merely incomplete

### 1.6 Venus and OpenClaw stay external to inner Zeus authority
Decision: external host/operator surfaces remain out-of-repo authority

Why decide now:
- repo audit logic already references workspace files
- boundary confusion is a live risk

Why not defer:
- host memory would keep contaminating repo law

Reversibility level: Low  
Rollback difficulty: High  
Blast radius: High on governance and operator behavior  
Dependent surfaces: boundary note, audit script, contracts, runbook  
Evidence basis: current repo scripts + external runtime docs  
What would overturn this: an explicit future decision to collapse repo and workspace authority into one governed surface

### 1.7 Live cutover timing remains human-gated
Decision: architecture can define criteria, but not choose cutover timing autonomously

Why decide now:
- cutover has operational risk, possible irreversibility, and market impact

Why not defer:
- a silent autonomous cutover is unacceptable

Reversibility level: Low once executed  
Rollback difficulty: High  
Blast radius: Very high  
Dependent surfaces: cutover plan, migration packets, operator runbook  
Evidence basis: runtime mixed state + lack of full parity  
What would overturn this: none without explicit human policy change

## 2. Autonomy Envelope Under Randomness

| Condition | Can agent continue autonomously? | Allowed adaptation | Forbidden adaptation | Required evidence before continue | When to stop | When to rollback | When to escalate to human |
|---|---|---|---|---|---|---|---|
| tmux worker death | Yes, if packet scope is unchanged | restart same lane, replace with single-owner execution, re-run read-only checks | widening scope, skipping review | worker failure noted; git clean; packet still valid | repeated death or stale state | if partial writes unclear | if same package dies twice and state is ambiguous |
| CLI worker partial failure | Yes, cautiously | switch advisory provider, retry once, fall back to owner execution | changing task objective | preserved logs/artifacts; no hidden side effects | provider disagreement changes plan | if partial mutation cannot be verified | if failure is on high-sensitivity packet |
| rate limit / quota exhaustion | Yes for non-live work | pause, queue resume, switch to read-only review | relaxing gates, bypassing evidence | quota/rate signal captured | execution requires time-sensitive write | not usually rollback; just hold | if live-control timing is affected |
| context loss / stale subagent context | Only after re-reading authority | restart lane, require scoped reads, reissue packet summary | continuing on stale assumptions | confirmed read order; packet restated | authority or file scope uncertain | revert stale edits | if stale edit touched K0/K1/schema |
| partial package failure | Sometimes | split packet, freeze failed subtask, continue unaffected parts | silently merging partial success as full completion | package status map | failure blocks acceptance criteria | revert failed sub-part if diff unsafe | if owner cannot separate safe vs unsafe parts |
| CI red | Usually no for merge; maybe yes for local investigation | debug locally, add verifier lane, narrow fix | merge anyway, downgrade gate by convenience | failing gate output and cause hypothesis | if blocker is architecture/gate related | revert if red came from current patch | if blocker implies law contradiction |
| schema mismatch | No, unless packet is schema-only and not live | inspect current schema, hold writes, patch migration plan | ad hoc table edits, silent drift acceptance | schema diff + migration plan | always stop if live write path affected | revert staged migration branch | always if live DB or cutover is involved |
| stale authority read | No | reread authority, restate precedence, restart lane | continuing under mixed or historical law | explicit authority basis note | immediately | revert any edits made under stale law | if stale authority already merged or applied live |
| false audit signal | Sometimes | corroborate with second source, mark advisory | triggering control actions from one noisy signal | second source or runtime confirmation | if signal would cause live action | rollback only if action already taken | if signal concerns live controls |
| false-complete illusion | No | reopen packet, record missing evidence, demote claim | claiming done because docs/code “look aligned” | explicit acceptance checklist | immediately when discovered | revert misleading docs or status | if illusion affected operator decisions |
| hook noise / notification spam | Yes | disable noisy hooks, narrow callback tags, continue core work | treating notifications as authority | notification source identified | if hooks interfere with judgment | disable hook configuration | human if disabling affects org policy |
| replay/parity failure | No for cutover; maybe yes for non-cutover docs | investigate mismatch, keep parity advisory | force cutover, waive without note | parity logs and mismatch summary | immediately on cutover packets | revert dual-write/cutover branch | always for live migration |
| control-plane contradiction | No for autonomous action | freeze control action, request status, escalate | issuing resume or widening control scope | contradiction captured from contracts/status | immediately | rollback temporary safety actions only if safe and explained | always if resume/unpause or strategy re-enable is involved |

## 3. Maintenance Economics

### 3.1 Gates worth keeping long term
- manifest consistency checks
- semgrep forbidden-move rules
- module-boundary checks
- architecture contracts tests
- root/scoped AGENTS discipline
- typed contract boundary review

### 3.2 Gates that are current-phase or staged
- replay parity as blocking gate
- work-packet grammar for trivial low-risk K2/K3 edits
- audit script checks that depend on external workspace files not versioned inside repo

### 3.3 Advisory before required
- `scripts/replay_parity.py` stays advisory until dual-write and `position_current` exist
- packet grammar may be advisory for trivial low-risk work, required for K0/K1/schema/control work
- boundary audit remains advisory until repo/workspace contract is stabilized and external surfaces are intentionally managed

### 3.4 Highest drift-risk instruction surfaces
- scoped AGENTS in low-touch directories
- `.claude/CLAUDE.md` shim
- operator cookbook/runbook as upstream tool commands evolve
- WORKSPACE_MAP if left as anything more than orientation

### 3.5 Governance mechanisms whose cost can exceed value
- mandatory team usage for every package
- forcing packet templates on every two-line docs fix
- requiring dual-runtime execution for the same packet
- notification-heavy hook ecosystems for normal work
- blocking on external workspace artifacts outside repo control

### 3.6 Sunset-review surfaces
- `.claude/CLAUDE.md` shim
- runtime delta ledger
- historical-doc demotion banners
- any temporary advisory gate justified only by current-phase transition

## 4. Not-Now List

| Item | Why seductive now | Why not now | Failure mode if adopted too early | Trigger to reopen later |
|---|---|---|---|---|
| `omx autoresearch` as delivery default | feels like one-key autonomy | experimental and too unbounded for authority-sensitive work | silent scope drift, noisy artifacts, false-complete research | once research-only ring is isolated from delivery law |
| `omx team --worktree` on authority/migration packets | parallel speed and isolation | recent runtime hardening issues mean cleanup must not be assumed | orphan worktrees, ambiguous state, cleanup debt | after repeated clean shutdown verification in Zeus |
| OMC `autopilot` or `ultrawork` on K0/K1/schema work | maximum throughput | too easy to overrun law and evidence boundaries | authority rewrite without accountable owner | when packets, gates, and rollback are proven on lower-risk domains |
| `omc team N:codex` as primary executor for critical packages | tempting cross-model execution | recent reports show status-sync fragility for Codex workers in team runtime | stale pending tasks, shutdown problems, false status | after stable non-interactive Codex worker handling is documented and verified |
| automatic OpenClaw command gateway driving repo changes | attractive host-side convenience | outer host must not become inner law | boundary collapse and hidden control authority | only for narrowly pre-authorized advisory flows |
| automatic `resume` / strategy re-enable by Venus | operational convenience | unsafe to widen risk without human review | unsafe reactivation after noisy signal or stale status | only after mature verified safety policy exists |
| hard-blocking replay parity now | seems rigorous | current repo does not yet have full canonical projection | red CI theater or waived blockers everywhere | after dual-write and deterministic replay compare land |
| full `position_current` cutover in same batch as authority install | feels “complete” | would fake runtime convergence | brittle migration, rollback confusion | after delta ledger, targeted patches, and parity prep |
| repo-wide scoped AGENTS explosion | feels comprehensive | high maintenance burden | instruction drift and contradictory local rules | after observing actual zero-context failure patterns |
| making Venus docs active inner authority | seems integrated | violates repo/host separation | authority drift and operator confusion | only if repo/workspace are intentionally unified under new law |

## 5. Failure Tree / Rollback Doctrine

### 5.1 If authority install creates conflict
- revert AGENTS/shim/demotion changes together
- restore previous operator brief
- keep decision register entry describing why rollback occurred

### 5.2 If gates create operational drag
- demote gate severity first
- do not silently delete the gate rationale
- review maintenance economics before re-blocking

### 5.3 If migration packet fails
- no cutover
- revert schema/runtime branch
- preserve delta ledger evidence
- freeze packet family until contradiction is resolved

### 5.4 If external boundary remains ambiguous
- keep boundary note normative
- keep audit script advisory
- refuse to infer missing workspace policy from old docs or habit

## 6. Unexpected but load-bearing findings

1. **Foundation package incompleteness:** the provided foundation zip references `tests/test_cross_module_invariants.py`, `docs/reference/zeus_first_principles_rethink.md`, and `ZEUS_PROGRESS.md`, but those files are not present in the uploaded foundation package. This weakens “machine-complete” claims and must be tracked before hardening CI.
2. **Checked-in runtime state mismatch:** the current repo’s checked-in `state/` directory contains unsuffixed JSON plus zero-byte DB placeholders that do not match the code’s expected `state/zeus.db` and mode-qualified file conventions. The zip is therefore not trustworthy as a live runtime-state snapshot.
3. **External workspace gap:** repo audit logic references workspace `AGENTS.md`, `HEARTBEAT.md`, `OPERATOR_RUNBOOK.md`, `IDENTITY.md`, `known_gaps.md`, and OpenClaw config, but those were not supplied in this session. Boundary design is actionable; boundary verification is incomplete.
4. **OMC Codex team-runtime caution:** recent issue reports indicate `omc team N:codex` can fail to synchronize task status back into `.omc/state/team/*`, with `omc ask codex` suggested as workaround for advisory use.
5. **OMX team cleanup caution:** recent OMX runtime hardening work shows detached worker worktree cleanup and mixed-worker startup needed explicit hardening, so shutdown hygiene must be verified, not assumed.

## 7. High-Stakes Uncertainty Doctrine

### Unknown but safe to proceed
- exact long-term retention period for `.claude` shim
- final number of scoped AGENTS beyond the first high-value set
- exact future command aliases in OMX/OMC docs

### Unknown and requires caution
- actual external workspace/OpenClaw surfaces in the user’s environment
- OMC Codex worker status sync stability in Zeus-specific usage
- OMX team worktree cleanup reliability in Zeus-specific usage

### Unknown and must remain open
- historical data migration into final `position_current`
- eventual deletion timing for legacy fallback surfaces
- long-term operator policy for outer-host hooks

### Unknown and blocks rollout
- live cutover timing
- parity proof for canonical truth cutover
- whether checked-in state mismatch reflects deployment reality or stale artifacts

### Unknown and requires human judgment
- destructive archive/delete of legacy truth surfaces
- permanent `.claude` retirement
- widening autonomous control-plane actions beyond narrow safety moves
