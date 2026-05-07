# Zeus AGENTS

Zeus is a **live quantitative trading engine** operating in Polymarket weather derivatives.

It converts atmospheric data into sized limit orders with positive expectation, bound by market settlement mechanics and dynamic risk limits.

**THE MONEY PATH (Your Primary Mental Model):**
The pipeline is causal and linear. Every component evaluates against this chain:
`contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`

**THE PROBABILITY CHAIN (How We Trade):**
The mathematical construction of an edge:
`51 ENS members -> per-member daily max -> Monte Carlo (sensor noise + ASOS rounding) -> P_raw -> Extended Platt (A·logit + B·lead_days + C) -> P_cal -> α-weighted Market Fusion -> P_posterior -> Edge & Double-Bootstrap CI -> Fractional Kelly -> Position Size`

**TOPOLOGY NAVIGATION:**
Before modifying code, run the topology doctor. It returns which files you may
change, which you must not touch, what tests must pass, and when to stop — scope
that grep cannot provide:
`python3 scripts/topology_doctor.py --navigation --task "<your task>" --files <files>`

For pipeline-impacting tasks (pricing, data, risk, settlement), also load the
boot profile and answer the proof questions before modifying code:
`python3 scripts/topology_doctor.py --task-boot-profiles`

Read this file first to establish the money path, then route directly to the specific execution module or manifest governing your task.

## 1. The Trading Machine

Zeus turns weather forecasts and settlement observations into calibrated market
probabilities, sized positions, execution decisions, monitoring, exits, and
settlement follow-through.

The runtime entry points are:

- `src/main.py` - live daemon entry
- `src/engine/cycle_runner.py` - cycle orchestration
- `src/engine/evaluator.py` - candidate to decision pipeline
- `src/execution/executor.py` - live order placement
- `src/engine/monitor_refresh.py` and `src/execution/exit_triggers.py` -
  monitoring and exits
- `src/execution/harvester.py` - settlement and learning follow-through

The truth path is:

`chain/CLOB facts -> canonical DB/events -> projections/status -> derived reports`

Zeus is dual-track. High and low temperature families share local-calendar-day
geometry and do not share physical quantity, observation field, Day0 causality,
or calibration family.

### Settlement mechanics

Polymarket weather markets settle on integer temperatures reported by Weather
Underground. Settlement is discrete, not continuous. A real temperature of
74.45°F → sensor reads 74.2°F → METAR rounds → WU displays 74°F.

`SettlementSemantics.assert_settlement_value()` gates every DB write. Three bin
types exist:

| Type | Example | Cardinality |
|------|---------|-------------|
| `point` | 10°C resolves on {10} | 1 |
| `finite_range` | 50-51°F resolves on {50, 51} | 2 |
| `open_shoulder` | 75°F+ (unbounded) | unbounded |

Shoulder bins are not symmetric bounded ranges. Do not infer bin semantics from
label punctuation or continuous-interval intuition.

**Key file**: `src/contracts/settlement_semantics.py`

### Risk levels

Risk levels change runtime behavior. Advisory-only risk is forbidden (INV-05).

| Level | Behavior |
|-------|----------|
| GREEN | Normal operation |
| YELLOW | No new entries, continue monitoring |
| ORANGE | No new entries, exit at favorable prices |
| RED | Cancel all pending, sweep all active positions |

Overall level = max of all individual levels. **Genuine computation error → RED, fail-closed (cancel pending, sweep active). Missing or stale truth input → DATA_DEGRADED, YELLOW-equivalent (block new entries, preserve held positions, alert). The distinction**: RED attests to a known boundary breach; DATA_DEGRADED attests that we cannot prove a breach but cannot disprove one either, so we hold rather than force-sell at unfavorable prices on a transient glitch. Both modes block new entries; only RED sweeps active positions.

**Key file**: `src/riskguard/risk_level.py`

### Position lifecycle

9 states in `LifecyclePhase` enum:

`pending_entry → active → day0_window → pending_exit → economically_closed → settled`

Terminal states: `voided`, `quarantined`, `admin_closed`.

Exit intent is not closure. Settlement is not exit. No code may invent phase
strings outside the enum.

**Key file**: `src/state/lifecycle_manager.py`

### Chain reconciliation

Every cycle reconciles local state against on-chain truth:

`Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache)`

| Condition | Action |
|-----------|--------|
| Local + chain match | SYNCED |
| Local exists, NOT on chain | VOID immediately (local state is a hallucination) |
| Chain exists, NOT local | QUARANTINE 48h (unknown asset, forced exit eval) |

**Key file**: `src/state/chain_reconciliation.py`

### Strategy families

Zeus operates four independent strategy families with distinct alpha profiles:

| Strategy | Edge source | Alpha decay |
|----------|-------------|-------------|
| Settlement Capture | Observed fact post-peak | Very slow (observation speed) |
| Shoulder Bin Sell | Retail cognitive bias | Moderate (competition narrows) |
| Center Bin Buy | Model accuracy vs market | Fast (easily competed away) |
| Opening Inertia | New market mispricing | Fastest (bot scanning) |

`strategy_key` is the sole governance identity for attribution, risk policy,
and performance slicing.

### Durable trading rules

- Canonical DB/event truth outranks derived JSON, CSV, reports, notebooks.
- Live may act. Backtest may evaluate. Shadow is observe-only.
- Settlement values flow through `SettlementSemantics`.
- High and low rows must not mix in calibration, Platt fitting, replay bin
  lookup, or settlement rebuild identity.
- DB commits must precede derived JSON export writes.
- `RED` risk must cancel pending orders and sweep active positions.
- Authority-loss must degrade monitor/exit lanes to read-only, not kill
  the cycle.

For the complete mathematical specification — worked examples, formula
derivations, strategy taxonomy, and failure case studies — read
`docs/reference/zeus_domain_model.md`.

## 2. Platform Operations & Change Control

The repository utilizes a change-control layer to ensure trading logic remains
isolated and explicitly versioned. A cold-start agent must answer four
operational questions before pushing changes:

1. What is current law?
2. What is active right now?
3. What is derived context, not authority?
4. Where does history live without becoming default context?

The durable workspace kernel is:

- machine manifests under `architecture/**`
- `architecture/module_manifest.yaml` for the dense module-reference layer
- scoped `AGENTS.md` routers
- `docs/reference/modules/**` for dense module books when a module router or
  manifest sends you there
- `docs/operations/current_state.md`, `docs/to-do-list/known_gaps.md`, and the
  active packet folder
- derived context engines such as `topology_doctor`, source rationale, history
  lore, and Code Review Graph

### Cross-session merge protocol

Merges from another worktree/session into a Zeus branch (`plan-pre5`,
`main`, etc.) use a conflict-first protocol, not an unconditional critic
gate:

1. Inspect the merge surface first (`git merge-tree`, `git merge --no-commit`,
   or the equivalent cherry-pick/rebase conflict view).
2. If there are no conflicts, merge normally and keep the commit message honest.
3. If conflicts are narrow and mechanical, resolve them directly or manually
   choose the correct side; run the affected tests/checks.
4. Escalate to critic verdict evidence only when conflicts are broad,
   cross-zone, high-risk (K0/K1, schema, lifecycle, DB/control/live surfaces),
   or semantically ambiguous.

Mechanism: see `.agents/skills/zeus-ai-handoff/SKILL.md` §8.8 and
`architecture/worktree_merge_protocol.yaml`. `.claude/hooks/pre-merge-contamination-check.sh`
is advisory by default and validates `MERGE_AUDIT_EVIDENCE` only when the
operator/agent has escalated to the critic-evidence path.

This protocol exists because of the 2026-04-28 contamination event: a
parallel session merged 9 commits into `plan-pre5` without enough conflict
surface inspection; 6 drift items resulted, including 815k mislabeled
production rows. The antibody is conflict-first escalation, not making every
clean merge pay a critic tax. (Evidence archived at
`docs/archives/packets/task_2026-04-28_contamination_remediation/`.)

### Commit & PR protocol

Commits: `type(scope): subject` — body only when why is non-obvious. Use `[skip-invariant]` for governance/docs-only commits that intentionally bypass the invariant baseline.

Worktree isolation: `git add -A`/`--all`/`.` is hook-blocked in the main worktree; allowed in any linked worktree (isolated index). Bypass: `COTENANT_GUARD_BYPASS=1`. Verify `git log -1` after heredoc commits.

PRs: batch multiple slices into one PR — every open triggers automated review that surfaces substantive issues. Resolve all review comments before merging. Template: `.github/pull_request_template.md`.

**Post-merge cleanup** (soft, agent decides; hook prints checklist on `gh pr merge`):
- worktree → `git worktree remove <path> && git worktree prune` when task done.
- ops packet → **delete by default** (git = backup); archive only when packet holds evidence `git log` can't summarize (contamination verdict, design rationale).
- context → `/compact` before ending a long session.

## 3. Navigation & Task Routing

**Step 1 — Run the topology digest for your task.** This is not optional. The
digest returns your scoped change set, forbidden files, safety gates, and stop
conditions — information that grep cannot provide.

```
python3 scripts/topology_doctor.py --navigation --task "<your task>" --files <files>
```

The output contains:
- `required_law` — invariants you must not violate
- `allowed_files` — the files you may change
- `forbidden_files` — do not touch
- `gates` — tests/checks that must pass before merge
- `downstream` — files affected by your change
- `stop_conditions` — scope boundaries that trigger "stop and plan"
- `source_rationale` — per-file zone, hazards, and write routes
- `history_lore` — relevant historical failure lessons

Topology is the routing entrypoint, not a process-tax generator. T0/T1 work stays
light: cite routed files, make focused edits, and run focused checks. Escalate
only when risk or claims demand it: T2 source behavior needs scoped AGENTS and
relationship tests; T3 governance/runtime tooling needs planning-lock for
governed files, with receipts/critics only for packet closeout, explicit
completion claims, or semantic ambiguity; T4 live/prod/data side effects stop
until explicit operator-go, dry-run evidence, and rollback path exist.

`evidence.md`, `findings.md`, work logs, receipts, and critic/verifier records
are conditional artifacts. Create/update them only when an active packet,
closeout gate, handoff, or completion claim consumes them.

At the end of a complete operation, recycle context into a compact feedback
capsule instead of leaving lessons in chat memory. For direct work, include it
in the final response; for packet closeout, append it to the already-required
work log or receipt. Cover three points: what scratch/runtime context was
promoted, summarized, discarded, or left local; one to three actionable Zeus
improvement insights observed during the operation; and a topology usage note
that names what helped and what blocked or slowed the work. A useful topology
note is not just "passed/blocked": it should compactly name the route/admission/risk
outcome, whether that matched the semantic task, one concrete help, one concrete
friction, and one next topology delta or `none_observed`. The capsule must not
create standalone `evidence.md`/`findings.md` files, widen the diff, or turn a
suggestion into implementation scope without an explicit route.

Use the first-class route when an agent needs to classify this closeout habit:
`python3 scripts/topology_doctor.py --navigation --task "direct operation feedback capsule: context recovery, Zeus improvement insights, topology helped/blocked" --intent "direct operation feedback capsule" --write-intent read_only`.
Pass an existing packet `work_log.md` or `receipt.json` only when that packet
already consumes it; `.omx/context/*handoff*` remains local runtime scratch, not
a repo persistence target. Old `.omc/state/agent-replay-*.jsonl` files are safe
to delete when no active recovery is in progress — they accumulate across sessions
and have no cross-session authority.

When the capsule's "1-3 actionable improvement insights" name a project-level
lesson (not a personal preference, not a one-off observation), append a row to
`architecture/improvement_backlog.yaml` so the insight survives chat history.
The capsule's final-response section still summarises; the YAML is the
permanent record.  Use status `proposed` for fresh entries (operator triages
to `accepted` later) or `implementing` if you are landing the work in the
same change.  The capsule MUST NOT widen scope to implement an insight in
the same change unless the operator explicitly approves — the backlog is the
queue, not the doer.

**Step 2 — Read the scoped AGENTS.md** for the module you will touch. These
contain domain rules, common mistakes, and hazard classifications specific to
that package.

**Step 3 — Read reference docs only when the task requires pipeline knowledge.**
Do not default-read all references. The digest profile tells you which laws
apply; read the reference that explains those laws:
- `docs/reference/zeus_domain_model.md` — domain model, worked examples, strategy taxonomy
- `docs/reference/zeus_math_spec.md` — probability chain formulas, calibration math
- `docs/reference/zeus_market_settlement_reference.md` — bin topology, settlement semantics, sensor physics
- `docs/reference/zeus_execution_lifecycle_reference.md` — lifecycle state machine, chain reconciliation, executor
- `docs/reference/zeus_risk_strategy_reference.md` — risk levels, Kelly sizing, edge decay
- `docs/reference/zeus_data_and_replay_reference.md` — database topology, data ingestion, dual-track identity
- `docs/reference/zeus_failure_modes_reference.md` — code-grounded failure modes with invariant anchors

### Digest profiles

The digest engine matches your task description against configured profiles.
Named profiles: `change settlement rounding`, `edit replay fidelity`,
`add a data backfill`, `add or change script`, `extract historical lore`,
`reference artifact extraction`. Unmatched tasks get a generic profile.

For a task-only digest without health checks:
```
python3 scripts/topology_doctor.py digest --task "<task>" --files <files>
```

### Semantic boot (pipeline-impacting tasks)

For settlement, source, observation, Day0, or calibration work, the digest alone
is not enough. Run the boot profile check:
```
python3 scripts/topology_doctor.py --task-boot-profiles
```

Answer the profile's proof questions before treating code structure as sufficient
context. The profile names required current-fact surfaces — read them:
- `docs/operations/current_source_validity.md`
- `docs/operations/current_data_state.md`
- `architecture/task_boot_profiles.yaml`
- `architecture/fatal_misreads.yaml`

`architecture/city_truth_contract.yaml` defines the stable source-role schema;
it is not a current per-city truth table.

### Additional topology commands

- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files>` — check if changes require planning evidence
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files <files>` — check companion registry updates
- `python3 scripts/topology_doctor.py --code-review-graph-status --json` — Code Review Graph freshness
- `python3 scripts/topology_doctor.py impact --files <files>` — source impact summary
- `python3 scripts/topology_doctor.py context-pack --task "<task>" --files <files>` — full agent context packet

### Code Review Graph

Two stages. Stage 1 (required): semantic boot via
`python3 scripts/topology_doctor.py semantic-bootstrap --task-class <class>
--task "<task>" --files <files> --json` — locks task class, current facts,
fatal misreads, and required proof questions. Stage 2 (optional): graph
context via `python3 scripts/topology_doctor.py --code-review-graph-status
--changed-files <files> --json` — file discovery, callers, impacted tests,
blast radius, review order. Stage 2 NEVER supplies semantic truth, source
validity, current facts, or authority rank. Prefer official upstream
operations (`code-review-graph status/update/watch`) over repo-local
inventions. Manifest at `architecture/code_review_graph_protocol.yaml` is
DEPRECATED — kept as validator stub only; this section is the authoritative
summary.

### High-risk zero-context work

When touching K0/K1, schema, governance, control, lifecycle, or DB authority,
add these reads after the topology digest:
- `architecture/self_check/zero_context_entry.md`
- `architecture/self_check/authority_index.md`

The runtime mode manifest is `architecture/runtime_modes.yaml`; the supported
discovery modes are `opening_hunt`, `update_reaction`, and `day0_capture`.

## 4. Operational Reference

### Authority classification

**Authority** — surfaces that grant permission, define truth, or enforce
behavior: system/developer/user instructions, machine-checkable manifests and
tests, active packet control surfaces, executable source and canonical DB truth.

**Derived context** — helps routing and review but never outranks authority:
`topology_doctor` digests, `architecture/source_rationale.yaml`,
`architecture/history_lore.yaml`, `architecture/code_review_graph_protocol.yaml`,
`.code-review-graph/graph.db`.

**History** — visible interface (`docs/archive_registry.md`) and dense lessons
(`architecture/history_lore.yaml`). Archive bodies are cold storage, not
default-read. Label archive-derived claims as `[Archive evidence]`.

### Planning lock

Stop and plan before touching: `architecture/**`, `docs/authority/**`,
`.github/workflows/**`, `src/state/**` truth ownership / schema / projection /
lifecycle write paths, `src/control/**`, `src/supervisor_api/**`, cross-zone
changes, more than 4 changed files, anything described as canonical truth /
lifecycle / governance / control / DB authority.

Machine check:
`python3 scripts/topology_doctor.py --planning-lock --changed-files <files...> --plan-evidence <plan file>`

### Change classification

- **Math**: stays inside existing semantic contracts
- **Architecture**: changes canonical read/write paths, lifecycle grammar, truth
  ownership, DB/schema, point-in-time semantics, or zone boundaries
- **Governance**: changes manifests, AGENTS, packets, constitutions, routing, or
  control surfaces

### Mesh maintenance

When adding, renaming, or deleting a file:

1. update the manifest that owns the registry when one exists
2. update the scoped `AGENTS.md` if local routes or file registries change
3. update `workspace_map.md` when directory-level structure or visibility
   classes change

Unregistered files are invisible to future agents.

Registry routes: `src/**` → `architecture/source_rationale.yaml`,
`scripts/*` → `architecture/script_manifest.yaml`,
`tests/test_*.py` → `architecture/test_topology.yaml`,
`docs/reference/*` → `docs/reference/AGENTS.md` and
`architecture/reference_replacement.yaml`.

Check:
`python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory|precommit|closeout`

### What to read by task

Always start with the topology digest (§3 Step 1). The digest gives you
`allowed_files`, `gates`, and `stop_conditions` for your task. The list below
adds supplemental reads the digest cannot provide:

- Pipeline-impacting work: add `docs/reference/zeus_domain_model.md` and the
  targeted module book after the digest
- Source edits: add scoped `src/**/AGENTS.md` and
  `architecture/module_manifest.yaml` after the digest
- K0/K1 truth or lifecycle: add `docs/authority/zeus_current_architecture.md`
  and `architecture/kernel_manifest.yaml` after the digest
- Delivery/governance: add `docs/authority/zeus_current_delivery.md`,
  `docs/operations/current_state.md`, and active packet docs
- Historical failure context: `architecture/history_lore.yaml` (the digest
  includes matched lore cards; read the full file only when investigating
  failure patterns)
- **Adversarial debate / multi-agent / contamination remediation**:
  add `docs/methodology/adversarial_debate_for_project_evaluation.md`
  (5-cycle methodology with §5.X-Z3 case-study antibodies + §5.Z3.1
  quantitative erratum-frequency trigger + 5th outcome category). The
  methodology load is REQUIRED for any cycle ≥ 2 of debate, any task
  invoking 5+ teammates, or any remediation triggered by drift items
  ≥ 3. Auto-loaded via `.claude/skills/zeus-methodology-bootstrap/SKILL.md`
  on matching task descriptions; manual read otherwise.

### Git safety

Never run destructive git commands or overwrite unrelated dirty work without
explicit human approval. Preserve untracked local inputs, runtime artifacts, and
other packets unless the active packet explicitly governs them.

## 5. Code review tasks

For any code review, PR review, `/review`, ultrareview, or automated review
task (Claude Code Review, GitHub Copilot Code Review, Codex review,
manual Claude session):

1. Read root `REVIEW.md` first — self-contained severity model, Tier
   priority order, large-PR rule, evidence rule, reporting template.
2. For deeper context, read `docs/review/code_review.md` (canonical
   long-form doctrine) and `docs/review/review_scope_map.md` (path →
   tier table).
3. Review by **runtime-risk surface**, not by GitHub file order.
   Exhaust Tier 0 (live money / runtime safety) before Tier 1
   (data / probability / persistence). Tier 3 (docs / agents) only
   if budget remains.
4. Default-skip the canonical skip-list in
   `docs/review/review_scope_map.md` (provenance archives, runtime
   caches, generated/cache files, etc.) — unless the change
   demonstrably alters runtime. Do not redefine the skip-list locally.
5. Cite `architecture/invariants.yaml` invariant IDs (`INV-NN`) when
   findings touch invariant-protected behavior.
6. For large PRs, state explicit coverage limits in the review.
   **Empty findings + partial coverage is not a clean pass** — report
   as "partial coverage; clean for reviewed slice (X, Y); paths A, B
   not reviewed; recommend slice review or split PR."
7. The PR template's "AI Review Scope" section declares author-marked
   priority paths; trust it before traversing alphabetically.
8. Severity model is identical across `REVIEW.md`,
   `docs/review/code_review.md`, `.github/copilot-instructions.md`,
   and `.github/instructions/*.instructions.md`. Drift between any two
   is itself an **Important** finding (Tier 3 surface).

The review doctrine surface is owned by `docs/review/AGENTS.md`.
