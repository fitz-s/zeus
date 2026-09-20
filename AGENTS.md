# Zeus AGENTS

## Boot Digest (loader only)

A SessionStart prefix is orientation, not the contract. Before acting, read this file in full and the scoped `AGENTS.md` for each touched subtree. Already loaded in full means no duplicate read.

**END OF DIGEST.**

Root owns cross-cutting law; scoped AGENTS own subtree routes/hazards. Direct instructions override AGENTS. Runtime snapshots (SHAs, PIDs, bankrolls, receipts, packet diaries, current config/version posture) belong on existing evidence surfaces, never here.

## 0. Mission And Money Path

Zeus trades Polymarket weather derivatives: `contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`. Every non-trivial change identifies its place, upstream truth and re-decision behavior; downstream optimization cannot guess contract/source/settlement truth.

Convert a real-time multi-source probability lead into realized profit through fills: admission connects settlement-aware belief, symmetric YES/NO executable edge and Fractional Kelly sizing. Probability improvement alone is not trading edge. Prevent total loss by re-deciding before position expectation reaches zero; hold to settlement/redeem while computation remains correct and the action law supports holding. These objectives waive no execution/risk/sizing law and guarantee no exit fill.

The scheduled/event-woken cycle revisits forecasts, observations, books and deadlines; cadences are config. Re-emit entries with fair round-robin coverage of the full city x metric universe. Screen confirmed resting maker entries against current same-side best bid, never ask cost; pull/re-decide on book drift or belief decay, escalate rest to cross at deadline. Each monitor cycle passes fresh probability/quote in `ExitContext` through `Position.evaluate_exit`. Re-evaluation continues through exit, settlement and learning; no decision is final before settlement.

Facts carry source-issued, fetched and written timestamps. Stale forecasts/observations/quotes cause DATA_DEGRADED, never stale-as-fresh. Re-fetch executable truth at submit or fail closed (FC-03). Learning is walk-forward: de-bias/calibration use only outcomes settled before the decision; settlement skill-attribution grades only the immutable decision-time probability certificate. No look-ahead.

Probability authority: replacement chain (`docs/authority/replacement_final_form_2026_06_09.md`), single-q regime (`docs/authority/regime_unification_2026-06-12.md`). Source-clock live posteriors use current provider center `mu*`, same-cycle causal target-specific ENS within-spread, absolute ENS-center disagreement and simultaneous provider between-spread: `sigma_pred = sqrt(within^2 + ens_center_delta^2 + between^2)`; integrate `N(mu*, sigma_pred)` over settlement preimages for current-evidence `q`, `q_lcb`, `q_ucb`.

Missing or invalid same-cycle current ENS shape blocks that posterior. No fallback to historical residual sigma, constant/fitted floors, fitted uniform/city mixtures, fitted affine center shifts, stale ENS shapes or legacy ENS/Platt/market fusion; these are offline evidence only, never another live probability regime. Persist the current-evidence semantics revision in shape/posterior identity. Certificates from another revision must be recomputed by the existing seed/materialization loop before entry or held-position belief consumes them.

## 1. Authority, Facts, And Proof

Use the narrowest proof surface: source/tests, `architecture/invariants.yaml` and manifests establish behavior/ownership/gates; authority docs carry durable law, with drift resolved against code/manifests/runtime. Config, canonical DBs, processes and receipts prove current facts. References explain; derived context (graphs, topology, reports, `architecture/history_lore.yaml`), archives and scratch are not current authority. CodeGraph/Code Review Graph answer where to inspect, never what settles, which source is valid, what runtime does or which authority wins.

Keep these proof obligations separate; one verdict cannot substitute for another.

Live/armed/trading/blocked/safe: loaded SHA/state file, launchd/process, fresh heartbeat, active config, canonical DB path, latest receipt/event rows and current rejection reasons.

Strategy/probability: source path, active config, materialized posterior/receipt fields; owning authority doc when changing law.

Settlement/source: `SettlementSemantics`, current source/data evidence, city/date/source contract and market text or resolver evidence.

DB truth: canonical SQLite file, ownership manifest, write path and transaction boundary.

Position/execution: Chain/CLOB facts, then chronicler/event log, then portfolio/local cache.

Docs/packet state: `docs/operations/current_state.md`, `docs/operations/AGENTS.md`, active package manifest and receipt path.

`docs/operations/current_state.md` is a control pointer, not live-SHA/liveness/submit/source/DB proof. Mark stale proof stale; stop using it as current fact and recheck authority, never memory/old logs/summaries/archives. Price, probability, sizing, fill, lifecycle and settlement are separate facts.

## 2. Trading Machine Invariants

Canonical truth flows `chain/CLOB -> canonical DB/events -> projections/status -> derived reports`. `state/status_summary.json` can retain stale PID/status after respawn. Commit DB truth before exporting JSON, CSV or reports.

`state/zeus-world.db` (`WORLD_CLASS`) owns markets and world/provenance records; position tables there are legacy ghost shells. `state/zeus-forecasts.db` (`FORECAST_CLASS`) owns observations, settlements, calibration pairs, ensemble snapshots, source runs and market events. `state/zeus_trades.db` owns `position_current`, `position_events`, lifecycle projections, order state, venue commands and execution records.

Table ownership is machine-checked by `architecture/db_table_ownership.yaml` through `src/state/table_registry.py`. No write transaction may span canonical DBs on independent connections (INV-37); the only sanctioned cross-DB write paths are `get_forecasts_connection_with_world()` and `trade_connection_with_world_flocked()`.

HIGH and LOW share local-calendar-day geometry, not physical quantity, observation field, Day0 causality, calibration family, replay identity, Platt fitting, settlement-rebuild identity or attribution slices.

Settlement is discrete integer Weather Underground temperature with sensor/METAR/WU rounding/display semantics. Every settlement DB write must pass `SettlementSemantics.assert_settlement_value()` in `src/contracts/settlement_semantics.py`. Types: `point` (one integer), `finite_range` (finite integer set), `open_shoulder` (unbounded). Never infer bin semantics from label punctuation/continuous intervals or treat shoulders as symmetric bounded ranges. Discovery/writes: `src/execution/harvester.py`; post-2026-02-21 resolver/Gamma semantics: `architecture/settlement_dual_source_truth_2026_05_07.yaml`.

Risk is max(individual levels); advisory-only risk is forbidden (INV-05). GREEN permits normal operation; YELLOW blocks new entries and continues monitoring; ORANGE blocks new entries and exits at favorable prices; RED cancels pending orders and sweeps active positions. Only RED sweeps. Genuine computation error causes RED fail-closed. Missing/stale truth causes DATA_DEGRADED, YELLOW-equivalent: block entries, preserve held positions and alert. Authority loss makes monitor/exit lanes read-only, not dead cycles. Owner: `src/riskguard/risk_level.py`.

Lifecycle is a monotonic, enum-governed progression in `src/state/lifecycle_manager.py`: `pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled`; terminals are `voided`, `settled`, `admin_closed`; `unknown` is recovery/transient only. Do not invent phase strings or restore `quarantined`. A confirmed-fill/chain-absence dispute retains its true `active`/`pending_exit` phase and uses typed `ReviewWorkItem` in `src/contracts/review_work_item.py`. Chain-only assets are typed `ChainOnlyFact`, never Position phases. Exit intent is not closure; settlement is not exit.

Reconcile `Chain/CLOB > Chronicler/event log > Portfolio/cache` in `src/state/chain_reconciliation.py`: matches sync; chain-absent local hallucinations are voided, subject to the confirmed-fill dispute rule above. Materialize chain-only facts, block entries only for their condition_id/market family, count worst-case exposure in risk caps and evaluate forced exit.

Every fail-closed condition must declare adjacent SCOPE/DRAIN/RESET or is presumed defective (INV-47). SCOPE: narrowest blocking identity, not an unscoped incident-table `COUNT(*)`. DRAIN: clearing action, cadence and dependencies; scope for slow drainage if it can defer/misfire. RESET: a real return to false, not frozen provenance required to equal an always-newest reader. A PR adding/widening a gate must state SCOPE/DRAIN/RESET; absence warrants rejection. False-positive fixes check the low/high, bid/ask, entry/exit or maker/taker twin in the same change.

Every live venue BUY or SELL must submit a finite unit price in inclusive `[0.05, 0.95]`; its current authorizing executable quote must also be in that band. This covers entry, reduce-only exit, single and batch paths, without strategy, side or lifecycle exceptions. An in-band floor cannot legalize an out-of-band bid. Preserve already-realized venue facts without using them to authorize new actions. Reject out-of-band submissions at command persistence, submission envelope and an independent final SDK boundary. Tick/range, minimum size, identity, tradeability, fees, depth, action-law economics and Kelly are cumulative requirements, never band waivers.

Statistical BUY/SELL authority is probability-witness typed. Day0 statistical actions may be feasible when the current probability witness, holding, wealth and executable book are exact and reproduced at submit; temporal maturity upgrades observation evidence to absorbing hard-fact authority, not permission to suppress continuous statistical redecision. Parameter bounds are confidence evidence, not fixed-action expected payoff. BUY/statistical SELL sizes and fill-prefixes use posterior-predictive-mean expected log wealth and EV, never relabeled `robust_*` values. After each action passes its own law, globally rank fixed proposals on the same posterior-mean expected-log-growth axis; direction cannot license incomparable scores.

When both execute lawfully, a held SELL exposes immediate-taker and maker-rest as separate fixed proposals on that axis. JIT rebinding preserves the selected mode and its capital-release semantics.

Multiple same-family outcome tokens are not categorically forbidden. Evaluate each sibling-bin BUY against the exact same-family portfolio and unresolved entry commitments using correlated payoff endowment, expected delta-log-wealth/EV, fees, depth and cumulative Kelly target. Command persistence enforces executable truth/risk, not a blanket one-position or one-token family veto.

`strategy_key` governs attribution, risk policy and performance slicing; alpha decays on each strategy's clock. Live may act; backtests use verified settlement joins without mixed regimes. Parallel observe-only runtime modes are forbidden. Do not put market-anchor caps or submit-disabled state in root law. Prove present behavior at task time; change durable strategy law in its owning authority doc/manifest.

## 3. Routing And Gates

For symbols/callers/callees/traces/impact, use CodeGraph before grep when available, then read source. Topology is optional orientation, not permission, paperwork or a refusal path. `topology_doctor.py --navigation` is a legacy route-card hint, not a required step or substitute for CodeGraph/live evidence.

Plan before changing `architecture/**`, `docs/authority/**`, `.github/workflows/**`, `src/state/**` schema/truth/projection/lifecycle writes, `src/control/**`, `src/supervisor_api/**`, cross-zone scope, more than four files, or canonical truth/lifecycle/governance/control/schema/DB authority/live execution/settlement semantics; then execute the authorized bounded slice. In unattended work, planning/critic review is work to complete, not a question-and-wait step. For long work, resume from its existing plan/packet or durable worktree's disk/git state; keep goal, decisions, evidence, remaining dispositions, next action and rollback point there. Missing required proof/review leaves that live change unlanded: record the blocker and continue independent authorized work, without inventing approvals or bypassing operator-only decisions.

Supplemental reads are task-specific. Pipeline: `docs/reference/zeus_domain_model.md` plus targeted module book; definitions/derivations: `docs/reference/glossary.md`, `docs/reference/theory_map.md`.

Settlement/bin/source: `docs/reference/zeus_market_settlement_reference.md`, scoped source AGENTS. Settlement/source/observation/Day0/calibration: also `docs/operations/current_source_validity.md`, `docs/operations/current_data_state.md`, `architecture/task_boot_profiles.yaml`, `architecture/fatal_misreads.yaml`. `architecture/city_truth_contract.yaml` defines source-role schema, not current city truth.

Calibration/replay/probability uses `docs/reference/zeus_math_spec.md` and `docs/reference/zeus_data_and_replay_reference.md`; execution/lifecycle uses `docs/reference/zeus_execution_lifecycle_reference.md`; risk/sizing/strategy uses `docs/reference/zeus_risk_strategy_reference.md`. Source edits use scoped `src/**/AGENTS.md` and `architecture/module_manifest.yaml`.

K0/K1 truth/lifecycle work uses `docs/authority/zeus_current_architecture.md`, `architecture/kernel_manifest.yaml`, `architecture/self_check/zero_context_entry.md` and `architecture/self_check/authority_index.md`. Delivery/governance uses `docs/authority/zeus_current_delivery.md`, the current-state pointer and active packet docs. Historical failures use matched `architecture/history_lore.yaml` cards; read the whole file only for failure-pattern investigation. Adversarial debate, 5+ teammates or contamination remediation uses `docs/methodology/adversarial_debate_for_project_evaluation.md` or its matching repo-local skill.

Runtime entry routes are `src/main.py` (daemon), `src/engine/cycle_runner.py` (cycle), `src/engine/evaluator.py` (candidate decisions), `src/execution/executor.py` (orders), `src/engine/monitor_refresh.py` and `src/execution/exit_lifecycle.py` (monitor/exits), and `src/execution/harvester.py` (settlement/learning).

## 4. Docs, Packets, And Mesh

Keep law, references, current operations and evidence in their existing classes; `docs/archive_registry.md` is the archive interface. Reports, captures and scratch need the existing promotion/registry path to become authority.

Current-fact docs are summary-only, evidence-backed, expiry-bound and fail closed when stale; never update from memory. When recording reference proof on existing evidence surfaces, use `checked=<ISO week or unverified>; basis=<proof>; until=<existing expiry or recheck-on-use>`; this records evidence, not a new renewal requirement. Use `YYYY-Www` for week precision, retaining exact deadlines; never round runtime freshness up to a week. Reading a file is not verifying its claims. Recheck expired claims, not their stamps; filename age alone neither validates current facts nor expires durable law.

Use `evidence.md`, `findings.md`, `work_log.md`, `receipt.json` only for an active packet, closeout gate, audit/review or consuming handoff, never T0/T1 appearance-only paperwork. Close with durable promotions, local/scratch residue and topology friction or `none_observed`. No standalone capsules, root coordination/scratch/research, ad hoc handoffs or backlog entries unless explicitly requested.

Add/rename/delete/reclassify files with their existing owner registry. Update scoped AGENTS only for local route/registry changes; `workspace_map.md` only for directory structure/visibility changes. Missing registry rows are coverage gaps, not permission.

Registry routes: `src/**` -> `architecture/source_rationale.yaml`; `scripts/*` -> `architecture/script_manifest.yaml`; `tests/test_*.py` -> `architecture/test_topology.yaml`; `docs/reference/zeus_*.md` -> `docs/reference/AGENTS.md`, `architecture/reference_replacement.yaml`, `architecture/docs_registry.yaml`; `docs/reference/modules/*.md` -> module router, docs registry, `architecture/module_manifest.yaml`; `docs/authority/*.md` -> `docs/authority/AGENTS.md`; `docs/operations/task_*` -> `docs/operations/AGENTS.md`; DB ownership -> `architecture/db_table_ownership.yaml`.

Report changed-surface docs checks separately from unrelated pre-existing repo-wide registry drift. Do not repair unrelated drift to make a narrow change appear globally clean.

## 5. Change Control

`live` is traded continuously. Never directly commit, amend or edit its checkout, or switch/reset/force-move its git state. `maintree_git_state_guard` has no agent bypass. `live` accepts only verified hot-fix cherry-picks or merged PRs; verified cherry-pick is the only local landing command.

All work uses its durable role worktree, persisting across tasks until operator retirement. Prove there, land promptly by urgency/blast radius: live money-path defect -> smallest correct hot-fix plus behavioral antibody, reviewed/proven to restore correct operation without new risk, then cherry-pick; functional milestone -> PR into `live`, required gates/review, merge. Never weaken freshness/fail-closed gates to accelerate landing.

Main thread = integrator/landing authority; one owner per file/slice, parallel editors in distinct assigned role worktrees. Each repair commit needs a verified defect, behavioral antibody and zero new regressions; remaining findings are fix/refute/defer-with-rationale before landing. Integrate disjoint cherry-picks against current live tip, prove base-vs-integrated diff, rebase to current tip before landing. Dirty live checkout means coordinate, never force/clobber live-ops. Use the lowest fitting model tier; top tier for outcome-deciding money logic. Protocol: `docs/operations/current/plans/live_branch_workflow_2026-07-20.md`; its older disposable-worktree/archival prose cannot override durable roles.

Math stays within semantic contracts; architecture changes canonical read/write paths, lifecycle grammar, truth ownership, schema, point-in-time semantics or zones; governance changes manifests, AGENTS, packets, constitutions, routing or control.

Preview conflicts in the worktree (`git merge-tree` or equivalent). Merge clean surfaces normally; resolve narrow mechanical conflicts and run affected checks. Obtain critic evidence for broad/cross-zone/high-risk/schema/lifecycle/DB/control/live/semantically ambiguous conflicts; missing evidence blocks that integration, not unrelated work. Mechanism: `architecture/worktree_merge_protocol.yaml`.

Commit as `type(scope): subject`; body for non-obvious rationale/test scope/residual risk. `[skip-invariant]` is only for governance/docs-only baseline bypass. Avoid broad main-worktree staging unless explicitly permitted; this cannot authorize editing/committing on `live`.

PRs are for complete features, invariant plus antibodies, security gates, covered schema migrations or equivalent milestones. Non-urgent single-function fixes, partial work, incremental docs and packet iterations stay in the worktree until bundled; urgent money defects use hot-fix. Batch related work before paid automated PR review. Template: `.github/pull_request_template.md`.

Never run destructive git (`reset --hard`, `checkout .`, `clean -f`, force-push to `live`) or overwrite unrelated dirty work. Preserve runtime artifacts, untracked inputs, other packets and user edits unless explicitly governed by the active packet.

## 6. Review Tasks

For any review, read `REVIEW.md` first; it owns runtime-risk ordering, the skip-list, PR AI Review Scope and coverage reporting. Empty findings with partial coverage is not a clean pass. Deeper doctrine/scope: `docs/review/code_review.md`, `docs/review/review_scope_map.md`; owner: `docs/review/AGENTS.md`. Cite `architecture/invariants.yaml` IDs for protected behavior. Severity drift among `REVIEW.md`, `docs/review/code_review.md`, `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` is an Important Tier 3 finding.

## 7. Code And Comment Discipline

Use first principles: entities and comments earn their existence. Prefer the fewest moving parts that preserve exact behavior and the shortest names unambiguous in scope. Fix syntax/semantics mismatches in the shape, not with explanatory patches around the defect.
