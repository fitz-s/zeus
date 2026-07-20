# Zeus AGENTS

Root operating contract for `/Users/leofitz/zeus`: durable law, money-path mental models, evidence gates, routing. Never store runtime snapshots here (branches, SHAs, PIDs, bankrolls, receipts, packet diaries). Nested `AGENTS.md` govern their subtrees; direct instructions override all AGENTS files.

## Boot Digest (SessionStart injection slice — NOT a reading surface)

SessionStart injects only a prefix of this file; this digest keeps the whole law in outline under truncation. **Reading contract: the digest licenses orientation only.** Before touching any surface a digest line names, Read that section (§N) plus the scoped `AGENTS.md` of the subtree — the digest omits the tables and gates that make the law executable. Citing "Boot Digest" as authority is itself a misread. Reading this file directly? Skip the digest; read §0–§7.

**Mission [full law: §0].** Zeus trades Polymarket weather derivatives: `contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`. Every non-trivial change states where it sits on that chain and how it behaves on re-decision. The chain is CYCLIC; no decision final until settlement; §0 carries the re-decision lanes this omits.

**Time law [§0].** Freshness gates fail closed (DATA_DEGRADED), never stale-as-fresh; re-fetch executable truth at submit (FC-03). Learning strictly walk-forward; decision probability frozen as immutable certificate; no look-ahead.

**Probability authority [§0 + docs/authority/replacement_final_form_2026_06_09.md].** Replacement chain is strategy of record. On the source-clock live route, current provider center + latest causal target-specific ENS within-spread + absolute ENS-center disagreement + simultaneous provider between-spread produce one settlement-preimage q and current-evidence q bounds; missing current shape fails closed. Historical residual/floor/mixture transforms and legacy ENS/Platt are diagnostics-only on that route.

**Proof discipline [§1 — both tables mandatory before any live/armed/safe claim].** Narrowest authoritative surface wins: direct instructions > AGENTS > executable law > authority docs > current facts > references > derived context (CodeGraph/CRG answer WHERE, never what is true) > archives. Stale surface = say stale, stop using it. Do not collapse §1's per-claim proof lines.

**DBs [§2].** `zeus-world.db` (world/markets), `zeus-forecasts.db` (observations/settlements/calibration), `zeus_trades.db` (positions/orders/execution). Ownership machine-checked; no write transaction spans DBs on independent connections — only the two sanctioned helpers (INV-37).

**Settlement [§2 — read before ANY settlement/bin/source work].** Integer temps from Weather Underground; every settlement write passes `SettlementSemantics.assert_settlement_value()`. Bin types point / finite_range / open_shoulder — never infer semantics from label punctuation. HIGH and LOW tracks share calendar geometry, nothing else.

**Risk & lifecycle [§2].** GREEN/YELLOW/ORANGE/RED = max(individual); advisory-only risk forbidden (INV-05); only RED sweeps. Lifecycle enum-governed `pending_entry -> ... -> settled`; exit intent ≠ closure; settlement ≠ exit. Reconciliation: Chain > Chronicler > Portfolio.

**Routing [§3 — has the per-task supplemental-read table this omits].** Root AGENTS -> scoped AGENTS for touched subtrees -> CodeGraph for structure (don't grep-first for symbols) -> targeted reference per §3 table. STOP AND PLAN before `architecture/**`, `docs/authority/**`, workflows, `src/state/**` truth paths, `src/control/**`, `src/supervisor_api/**`, cross-zone, >4 files, or anything canonical/lifecycle/schema/live-execution/settlement.

**Docs & registries [§4 — registry-route table].** Unregistered files are invisible; every added/renamed/deleted file updates its owning registry. Current-fact docs are summary-only, evidence-backed, expiry-bound. No root coordination/scratch files unless asked.

**Change control [§5].** `main` = live (traded 24/7; never commit direct, never mutate its checkout — `maintree_git_state_guard`); all work in a worktree, land via hot-fix cherry-pick (urgent money-path defect) or milestone PR (paid review once — batch); `type(scope): subject`; no destructive git; preserve unrelated dirty work; gates never weakened to land faster.

**Review [§6 + REVIEW.md first].** Runtime-risk order (Tier 0 live-money before all); empty findings + partial coverage ≠ clean pass.

**Code discipline [§7].** Concise/precise code and comments; trim names; entities earn existence only when the problem demands it; runtime mechanism = precision+simplicity; fix syntax-semantics gaps in the shape, not a comment; minimum tokens, maximum precision.

**END OF DIGEST.** Law starts at §0. Context ends here = boot slice truncated: Read this file in full before acting on any named surface.

## 0. Mission And Money Path

Zeus is a quantitative trading engine for Polymarket weather derivatives. It converts atmospheric data into settlement-aware probabilities, expected edge, position sizes, orders, monitoring actions, settlement records, and learning feedback.

Primary causal chain:

`contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`

Every non-trivial change must say where it sits on that chain, what upstream truth it consumes, and how it behaves on re-decision. A downstream optimization that guesses contract/source/settlement truth is a money-path bug.

### Cyclic, Not One-Shot

The chain above is one pass of a continuously repeating cycle, not a single decision. Zeus runs as a mesh of recurring scheduled jobs whose cadences range from seconds to daily — entry reactor, continuous-redecision screen, maker-rest escalation, held-position monitor/exit, command recovery, settlement skill-attribution, and freshness/heartbeat backstops (specific cadences are config, not root law). Every node on the chain is revisited as wall-clock time advances and as new information arrives: a fresher forecast issue, a moved book, a new observation, an elapsed deadline. No decision is final until settlement.

Re-decision is a first-class lane, not an exception path:

- New-entry candidates are re-emitted every reactor cycle; fair round-robin covers the full city×metric family universe over a few cycles, so a market passed over is reconsidered against fresh evidence rather than abandoned.
- A confirmed resting maker entry stays under continuous re-decision — screened against current same-side best bid (never ask cost), pulled and re-decided when the book drifts past tolerance or belief decays, escalated rest→cross when its deadline elapses.
- A held position is re-evaluated every monitor cycle on a fresh `ExitContext` (refreshed probability and CLOB quote) through `Position.evaluate_exit`: continuous re-evaluation before fill, during holding, through exit, and after settlement.

Time-ordering is law:

- Every fact carries source-issued, fetched, and written timestamps. Freshness gates drop stale forecasts, observations, and quotes and fail closed (DATA_DEGRADED, read-only); they never bridge a gap with stale-as-fresh. Selection-time executable truth is not submit-time truth (FC-03): re-fetch a fresh snapshot at submit or fail closed.
- Learning is strictly walk-forward. De-bias and calibration consume only outcomes settled before the decision; the decision probability is frozen at decision time as an immutable certificate; settlement skill-attribution grades against that frozen certificate. No look-ahead crosses the time boundary.
- Lifecycle is a monotonic time progression (`pending_entry -> ... -> settled`); each strategy's edge decays on its own clock (§2 alpha decay).

### Probability Authority

The replacement chain is the strategy of record. Authority basis: `docs/authority/replacement_final_form_2026_06_09.md`; single-q regime law: `docs/authority/regime_unification_2026-06-12.md`.

Durable chain:

`current provider center mu* -> current target-specific ENS within-spread + absolute ENS-center disagreement + simultaneous provider between-spread -> sigma_pred = sqrt(within^2 + ens_center_delta^2 + between^2) -> settlement-preimage integration of N(mu*, sigma_pred) -> current-evidence q_lcb/q_ucb -> symmetric YES/NO executable edge -> Fractional Kelly -> Position Size`

This decision-time current-evidence form is mandatory for source-clock live
posteriors. A missing/invalid current ENS shape blocks the posterior; it must not
fall back to historical residual sigma, constant/fitted floors, fitted uniform or
city mixtures, or a fitted affine center shift. Older fusion/calibration machinery
may remain as diagnostics or for explicitly non-source-clock carriers, never as a
silent alternate live probability regime.

The current-evidence semantics revision is part of the persisted shape and
posterior identity. A shaped certificate from another revision is not current
probability authority; the existing seed/materialization loop must recompute it
before entry or held-position belief can consume it.

Do not reintroduce market-anchor caps, submit-disabled state, shadow-only gates, version snapshots, bankrolls, or current Kelly multipliers into root law. Present behavior must be proven from executable source, active config, process state, canonical DB rows, and decision receipts at task time. If that proof changes durable strategy law, update the owning authority doc or manifest instead of encoding a runtime snapshot here.

Settlement-graded facts backing the chain: prior-label is algebraically irrelevant under diagonal Sigma; precision weights beat equal weights by 12x SE; legacy AIFS member-vote shape put zero probability on the winning bin in 28% of settled cells; fitted sigma-shape mixture values live in `state/sigma_scale_fit.json` and must be verified live before quoting numeric k/w.

The legacy ENS/Platt/market-fusion baseline is diagnostics-only under the single-q regime. It may appear as receipt provenance such as `baseline_q_lcb_reference`; it is not a second live probability authority and must not be joined back onto the live path without new authority.

Legacy diagnostic chain:

`51 ENS members -> per-member daily max -> Monte Carlo (sensor noise + ASOS rounding) -> P_raw -> Extended Platt -> P_cal -> alpha-weighted Market Fusion -> P_posterior -> Edge & Double-Bootstrap CI -> Fractional Kelly -> Position Size`

## 1. Authority, Facts, And Proof

Use the narrowest authoritative surface that can prove the claim.

| Class | Examples | Role | Forbidden misread |
|---|---|---|---|
| Direct instructions | system/developer/user messages | highest priority this run | AGENTS never override direct instructions |
| Routers | root/scoped `AGENTS.md` | operating law and local hazards | scoped rules do not apply outside their subtree |
| Executable law | `src/**`, tests, `architecture/invariants.yaml`, machine manifests | behavior, invariants, ownership, gates | prose cannot create behavior |
| Authority docs | `docs/authority/**` | durable architecture and delivery law | dated paragraphs can drift; conflict-resolve with code/manifests/runtime proof |
| Current facts | `config/settings.json`, canonical DBs, process/launchd state, receipts, `docs/operations/current_*` | present-tense posture and evidence | current facts expire; they are not reusable root law |
| References | `docs/reference/**`, module books | dense domain explanation | reference docs do not authorize runtime or packet state |
| Derived context | topology digests, Code Review Graph, reports, `architecture/history_lore.yaml` | routing, review, lessons | derived context answers where to inspect, not what is true |
| History/archive | `docs/archive_registry.md`, archive bodies | provenance and lessons | archives are cold storage, not default boot context |
| Scratch/runtime | local scratch, linked worktrees, dumps | session context | scratch is not durable plan, audit, or authority evidence |

`docs/operations/current_state.md` is a live control pointer. It may point at active work and current-fact companions, but it is not proof of live SHA, daemon liveness, submit posture, source validity, or DB truth until those claims are rechecked on live surfaces.

### Claim Proof Gates

Do not collapse separate proof lines into one verdict.

| Claim | Minimum proof |
|---|---|
| live / armed / trading / blocked / safe | loaded SHA/state file, launchd/process, heartbeat freshness, active config, canonical DB path, latest receipt/event rows, current rejection reasons |
| strategy or probability behavior | source path, active config, materialized posterior/receipt fields, authority doc if changing law |
| settlement/source correctness | `SettlementSemantics`, current source/data evidence, city/date/source contract, market text or resolver evidence |
| DB truth | canonical SQLite file, table ownership manifest, write path, transaction boundary |
| position/execution truth | Chain/CLOB facts first, then chronicler/event log, then portfolio/local cache |
| docs and packet state | `docs/operations/current_state.md`, `docs/operations/AGENTS.md`, active package manifest, receipt path |
| review/impact | runtime-risk tier, invariants, changed paths, tests or receipts for reviewed slice |

If a proof surface is stale, say it is stale and stop using it as current fact. Do not bridge freshness gaps with memory, old logs, summaries, or archive bodies.

## 2. Trading Machine Invariants

Runtime entry points:

| Surface | File |
|---|---|
| live daemon | `src/main.py` |
| cycle orchestration | `src/engine/cycle_runner.py` |
| candidate-to-decision pipeline | `src/engine/evaluator.py` |
| live order placement | `src/execution/executor.py` |
| monitoring / exits | `src/engine/monitor_refresh.py`, `src/execution/exit_lifecycle.py` |
| settlement / learning follow-through | `src/execution/harvester.py` |

Truth path: `chain/CLOB facts -> canonical DB/events -> projections/status -> derived reports`. `state/status_summary.json` is an operator projection and may have stale PID/status after respawn.

Canonical DBs:

| DB | Class | Owns |
|---|---|---|
| `state/zeus-world.db` | `WORLD_CLASS` | markets and world/provenance records; trade-owned position tables here are legacy ghost shells |
| `state/zeus-forecasts.db` | `FORECAST_CLASS` | observations, settlements, calibration pairs, ensemble snapshots, source runs, market events |
| `state/zeus_trades.db` | trade execution | `position_current`, `position_events`, lifecycle projection, order state, venue commands, and execution records |

Table ownership is machine-checked by `architecture/db_table_ownership.yaml` and loaded through `src/state/table_registry.py`. No write transaction may span DBs through independent connections. Sanctioned cross-DB write paths are `get_forecasts_connection_with_world()` and `trade_connection_with_world_flocked()`.

Dual track: HIGH and LOW share local-calendar-day geometry but not physical quantity, observation field, Day0 causality, calibration family, replay identity, Platt fitting, settlement rebuild identity, or attribution slices.

Settlement: Polymarket weather markets settle on integer temperatures reported by Weather Underground. Settlement is discrete; real temperature may pass through sensor reading, METAR/WU rounding, and display before resolving. Every settlement DB write must pass `SettlementSemantics.assert_settlement_value()` in `src/contracts/settlement_semantics.py`.

| Bin type | Example | Cardinality |
|---|---|---|
| `point` | `10C` resolves on `{10}` | 1 |
| `finite_range` | `50-51F` resolves on `{50, 51}` | finite |
| `open_shoulder` | `75F+` | unbounded |

Shoulder bins are not symmetric bounded ranges. Do not infer bin semantics from label punctuation or continuous-interval intuition.

Settlement discovery and canonical DB write live in `src/execution/harvester.py`. Post-2026-02-21 weather settlement uses the internal automatic resolver documented by `architecture/settlement_dual_source_truth_2026_05_07.yaml`; harvester reads settled events via Gamma API for that era.

Risk levels change behavior; advisory-only risk is forbidden by INV-05.

| Level | Behavior |
|---|---|
| GREEN | normal operation |
| YELLOW | no new entries; continue monitoring |
| ORANGE | no new entries; exit at favorable prices |
| RED | cancel pending; sweep active positions |

Overall risk is max(individual levels). Genuine computation error -> RED fail-closed. Missing/stale truth input -> DATA_DEGRADED, YELLOW-equivalent: block new entries, preserve held positions, alert. Only RED sweeps active positions. Key file: `src/riskguard/risk_level.py`.

Lifecycle is enum-governed in `src/state/lifecycle_manager.py`: `pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled`; terminals are `voided`, `settled`, `admin_closed`; `unknown` is transient/recovery only. `quarantined` is retired from the enum entirely (T5, `docs/rebuild/quarantine_excision_2026-07-11.md`) — a confirmed-fill/chain-absence dispute keeps its TRUE phase (`active`/`pending_exit`) and the dispute lives in a typed `ReviewWorkItem` (`src/contracts/review_work_item.py`), never a lifecycle phase. Chain-only unknown assets never enter the Position lifecycle — they are typed `ChainOnlyFact` records, not a phase. Exit intent is not closure; settlement is not exit; no code may invent lifecycle strings.

Chain reconciliation order: `Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache)`. Local+chain match -> synced. Local exists, not on chain -> void local hallucination. Chain exists, not local -> materialize a scoped `ChainOnlyFact` (entry block limited to its own condition_id/market family + worst-case exposure counted into risk caps) and evaluate forced exit. Key file: `src/state/chain_reconciliation.py`.

`strategy_key` is the governance identity for attribution, risk policy, and performance slicing.

| Strategy | Edge source | Alpha decay |
|---|---|---|
| Settlement Capture | observed fact post-peak | very slow |
| Shoulder Bin Sell | retail cognitive bias | moderate |
| Center Bin Buy | model accuracy vs market | fast |
| Opening Inertia | new market mispricing | fastest |

Durable trading rules:

- Canonical DB/event truth outranks derived JSON, CSV, reports, notebooks.
- Every live venue BUY or SELL, including entry, reduce-only exit, single-order,
  and batch paths, must have a finite unit price inside inclusive `[0.05, 0.95]`.
  Anything below `0.05` or above `0.95` is rejected at command persistence,
  the submission envelope, and an independent final SDK boundary. Current
  tick/range, minimum size, identity, tradeability, fees, depth, robust
  delta-log-wealth/EV, and Kelly remain cumulative requirements; none may waive
  this absolute band. There are no strategy, side, lifecycle, or exit exceptions.
- A weather family is the exhaustive mutually-exclusive partition keyed by
  `(city, target_date, temperature_metric)`. Once that family has an open
  position or unresolved entry obligation, a live ENTRY may add only to the same
  canonical position / outcome token; a BUY for any sibling bin is rejected at
  global selection and again at venue-command persistence. SELL, CANCEL,
  monitoring, and exit remain available, and a typed close-before-open rebalance
  must close the old exposure before a sibling entry can be admitted. A legacy
  family already holding multiple outcome tokens admits no further BUY.
- Live may act; backtest may evaluate — and only against verified settlement joins, never mixed regimes. Shadow modes are extirpated (operator directive 2026-06-12); do not reintroduce one as a staging tier.
- Settlement values flow through `SettlementSemantics`.
- DB commits precede derived JSON/report exports.
- Authority loss degrades monitor/exit lanes to read-only; it does not kill the cycle.
- Price, probability, sizing, fill, lifecycle, and settlement evidence are separate facts.
- Current config affects present behavior but does not belong in root AGENTS unless it becomes durable law.

For derivations and worked examples, read `docs/reference/zeus_domain_model.md` and the targeted reference named by the task route. Term definitions live in `docs/reference/glossary.md`; math and physics index in `docs/reference/theory_map.md`.

## 3. Routing And Gates

Default route:

1. Read root `AGENTS.md`.
2. Read scoped `AGENTS.md` for any subtree you will touch.
3. Use CodeGraph for structural questions: symbols, callers, callees, traces, file impact, and "how does X work?"
4. Use topology only as optional route/context orientation; do not treat it as a runtime permission surface.
5. Read reference docs only after the route says which domain reference matters.

Do not grep first for symbol definitions or flow when CodeGraph is available. Do not use CodeGraph as settlement/source/current-fact authority.

Topology checks are advisory orientation. They must not add paperwork,
deny runtime-directed work, or turn broad/cross-zone edits into a refusal path.

If repo-wide docs checks fail from unrelated pre-existing registry drift, report the root changed-surface status separately from repo-wide drift. Do not repair unrelated docs drift just to make a narrow AGENTS change look globally clean.

`topology_doctor.py --navigation` is legacy substring routing: a route-card hint only, not a step and not a replacement for CodeGraph or live evidence.

Semantic boot inputs for settlement/source/observation/Day0/calibration tasks:

- `docs/operations/current_source_validity.md`
- `docs/operations/current_data_state.md`
- `architecture/task_boot_profiles.yaml`
- `architecture/fatal_misreads.yaml`

`architecture/city_truth_contract.yaml` defines the source-role schema, not current per-city truth. Keep the fatal antibody loaded: Code Review Graph can answer where to inspect and likely blast radius; it cannot decide what settles, which source is valid, what runtime is doing, or which authority rank wins a conflict.

Stop and plan before touching `architecture/**`, `docs/authority/**`, `.github/workflows/**`, `src/state/**` schema/truth/projection/lifecycle write paths, `src/control/**`, `src/supervisor_api/**`, cross-zone changes, more than four changed files, or anything described as canonical truth, lifecycle, governance, control, schema, DB authority, live execution, or settlement semantics.

Do not create root-level coordination files, scratch research, or ad hoc handoff files unless the user explicitly asks for them.

Task-specific supplemental reads:

| Task | Read after route/admission |
|---|---|
| pipeline-impacting work | `docs/reference/zeus_domain_model.md` plus targeted reference/module book |
| settlement/bin/source | `docs/reference/zeus_market_settlement_reference.md`, current source/data state, scoped source AGENTS |
| calibration/replay/probability | `docs/reference/zeus_math_spec.md`, `docs/reference/zeus_data_and_replay_reference.md` |
| execution/lifecycle | `docs/reference/zeus_execution_lifecycle_reference.md` |
| risk/sizing/strategy | `docs/reference/zeus_risk_strategy_reference.md` |
| source edits | scoped `src/**/AGENTS.md`, `architecture/module_manifest.yaml` |
| K0/K1 truth or lifecycle | `docs/authority/zeus_current_architecture.md`, `architecture/kernel_manifest.yaml`, `architecture/self_check/zero_context_entry.md`, `architecture/self_check/authority_index.md` |
| delivery/governance | `docs/authority/zeus_current_delivery.md`, `docs/operations/current_state.md`, active packet docs |
| historical failure | matched `architecture/history_lore.yaml` cards; full file only for failure-pattern investigation |
| adversarial debate / 5+ teammates / contamination remediation | `docs/methodology/adversarial_debate_for_project_evaluation.md` or matching repo-local skill |

## 4. Docs, Packets, And Mesh

Layering: `docs/authority/**` carries durable law; `architecture/**` carries machine-checkable law/registries/topology/invariants; `docs/reference/**` explains durable domain/module knowledge; `docs/operations/**` points at active work/current facts/packets/evidence; `docs/archive_registry.md` is the visible archive interface. Archive bodies, reports, generated evidence, raw captures, and scratch are evidence only until promoted through the correct authority/registry path.

Current-fact docs must be summary-only, receipt/evidence-backed, expiry-bound, and fail-closed when stale. Do not update them from memory.

Packet-local names (`evidence.md`, `findings.md`, `work_log.md`, `receipt.json`) are used only when an active packet, closeout gate, audit/review task, or future handoff consumes them. Direct T0/T1 work should not create packet evidence for appearance.

At the end of complete work, summarize what was promoted to durable surfaces, what was left local/scratch, and concrete topology friction or `none_observed`. Do not create standalone feedback capsules, root coordination files, ad hoc handoffs, or backlog entries unless the user explicitly asks.

When adding, renaming, deleting, or reclassifying files: update the owning manifest/registry when one exists; update scoped `AGENTS.md` only if local routes or file registries changed; update `workspace_map.md` only when directory-level structure or visibility classes changed.

Common registry routes:

| Surface | Registry |
|---|---|
| `src/**` | `architecture/source_rationale.yaml` |
| `scripts/*` | `architecture/script_manifest.yaml` |
| `tests/test_*.py` | `architecture/test_topology.yaml` |
| `docs/reference/zeus_*.md` | `docs/reference/AGENTS.md`, `architecture/reference_replacement.yaml`, `architecture/docs_registry.yaml` |
| `docs/reference/modules/*.md` | module router, docs registry, `architecture/module_manifest.yaml` |
| `docs/authority/*.md` | `docs/authority/AGENTS.md` |
| `docs/operations/task_*` | `docs/operations/AGENTS.md` |
| DB table ownership | `architecture/db_table_ownership.yaml` |

Unregistered files are invisible to future agents. Treat a missing registry row as a coverage gap, not a green light.

## 5. Change Control

### Live branch (main = live)

`main` is the **live** branch: the exact tree the running engine trades from continuously — a 24/7 mesh of scheduled jobs and event-woken agents, not a staging or integration branch. A commit on `main` is a commit the live daemons will act on within one reload.

- **Never commit to `main` directly; never mutate the live checkout's git state.** The daemons run from that checkout; switching or force-moving it out from under them is a live incident. Enforced by `maintree_git_state_guard` — deliberate operator moves prefix `MAINTREE_GIT_BYPASS=1`.
- **All work happens in a linked worktree/branch, is proven there, then lands on live promptly** so the running code reloads and stays coherent. Two landing lanes, chosen by urgency × blast-radius:
  - **Hot-fix** — a live defect degrading the money path (wrong settlement, dropped exit, fail-open admission, an unbounded hang). The smallest correct change plus a minimal antibody, `git cherry-pick`ed onto live as soon as it proves out. The bar is "restores correct live operation without new risk"; speed is the point.
  - **PR** — functional / milestone work (a feature, a new invariant + antibody tests, a schema migration with coverage, a gate). Open a PR into `main`, pass the required gates and review, then merge. Paid review fires once — batch related work (≥300 self-authored LOC).
- **Freshness and fail-closed gates are never weakened to land faster.** The §0 alpha-clock and failure-isolation invariants bind every money-path change, both lanes.
- **Multi-agent live-repair:** many agents are woken concurrently (improvement loop, failing gate, review, monitor, operator) to inspect and repair live. The **main thread is integrator + landing authority**; agents own bounded slices. Discipline: **one owner per file/slice**; parallel editors isolated in separate worktrees; each agent **verifies the defect is real, ships a behavioral antibody, and proves zero new regressions** before its commit counts; remaining findings are **adversarially dispositioned** (fix/refute/defer-with-rationale) before landing; integrate by **disjoint cherry-pick onto the current live tip** with a base-vs-integrated diff; land small and often. Never clobber concurrent live-ops — rebase onto the current tip, and a dirty live checkout is a coordination point, not a force. Lowest model tier that fits the slice; the top tier only for outcome-deciding money-path logic. Full protocol: `docs/operations/current/plans/live_branch_workflow_2026-07-20.md`.

Full workflow of record until fully absorbed here: `docs/operations/current/plans/live_branch_workflow_2026-07-20.md`.

Change classes: Math stays inside existing semantic contracts. Architecture changes canonical read/write paths, lifecycle grammar, truth ownership, schema, point-in-time semantics, or zone boundaries. Governance changes manifests, AGENTS, packets, constitutions, routing, or control surfaces.

Merge protocol: inspect conflict surface first (`git merge-tree`, `git merge --no-commit`, or equivalent); merge clean surfaces normally; resolve narrow mechanical conflicts directly and run affected checks; escalate to critic evidence only for broad, cross-zone, high-risk, schema, lifecycle, DB/control/live, or semantically ambiguous conflicts. Mechanism: `.agents/skills/zeus-ai-handoff/SKILL.md` and `architecture/worktree_merge_protocol.yaml`.

Commits use `type(scope): subject`. Add body only when why/tested scope/residual risk is non-obvious. Use `[skip-invariant]` only for governance/docs-only commits that intentionally bypass invariant baseline. In the main worktree, avoid broad staging unless explicitly permitted. Preserve unrelated dirty and untracked work.

Open PRs only for milestone-level changes: complete feature, new invariant plus antibody tests, security gate, schema migration with coverage, or equivalent. Single-function fixes, partial implementations, incremental docs, and local packet iterations stay in the worktree branch. Every PR consumes paid automated review once; batch related work before opening. Template: `.github/pull_request_template.md`.

Never run destructive git commands (`reset --hard`, `checkout .`, `clean -f`, force-push to main) or overwrite unrelated dirty work. Preserve runtime artifacts, untracked inputs, other packets, and user edits unless the active packet explicitly governs them.

## 6. Review Tasks

For code review, PR review, `/review`, automated review, ultrareview, or manual Claude/Codex/GitHub Copilot review: read `REVIEW.md` first; for deeper context read `docs/review/code_review.md` and `docs/review/review_scope_map.md`; review by runtime-risk surface, not GitHub file order; exhaust Tier 0 live-money/runtime safety before Tier 1 data/probability/persistence; review Tier 3 docs and agent-instruction surfaces only if budget remains.

Default-skip the canonical skip-list in `docs/review/review_scope_map.md` unless a skipped path demonstrably changes runtime. Cite `architecture/invariants.yaml` invariant IDs for invariant-protected behavior. For large PRs, state coverage limits explicitly; empty findings plus partial coverage is not a clean pass. Trust the PR template's "AI Review Scope" before traversing alphabetically. Severity-model drift between `REVIEW.md`, `docs/review/code_review.md`, `.github/copilot-instructions.md`, and `.github/instructions/*.instructions.md` is an Important Tier 3 finding. The review doctrine surface is owned by `docs/review/AGENTS.md`.

## 7. Code And Comment Discipline

Code and comments earn their length: precise beats clever. Trim variable names to the shortest form still unambiguous in scope. First principles over precedent, repo-wide: an entity earns existence only when the problem demands it (Occam's razor). Runtime mechanism: precision and simplicity are one axis — fewest moving parts, exact guaranteed behavior. Where syntax and semantics diverge, fix the shape, not a comment around the gap. The limits of language are the limits of the world (Wittgenstein). Minimum tokens, maximum precision.
