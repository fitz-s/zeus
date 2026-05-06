# Zeus review scope map

Path → review tier table. Used by AI reviewers to pre-classify a diff
before reading any file. Authoritative for Tier classification; if any
other doc disagrees, this file wins for the path-list and `code_review.md`
wins for everything else.

Tier definitions and severity model live in `code_review.md` and `REVIEW.md`.
This file is path-only.

---

## Tier 0 — Live money / runtime safety / kill switch

Review focus: live-money loss, fail-closed bypass, venue/CLOB identity,
settlement semantics, persistence corruption, kill-switch integrity.

```
src/execution/**
  executor.py                       — limit-order execution engine (live)
  exit_triggers.py                  — 8-layer churn defense
  exit_lifecycle.py                 — exit lifecycle management
  collateral.py                     — fail-closed sell-collateral facade
  settlement_commands.py            — durable settlement/redeem ledger
  wrap_unwrap_commands.py           — USDC.e ↔ pUSD command state
  fill_tracker.py                   — fill tracking + timeout
  harvester.py                      — settlement harvest
src/venue/**                        — Polymarket V2 adapter, CLOB submit, on-chain
src/main.py                         — live daemon entry
src/engine/cycle_runner.py          — cycle orchestration
src/engine/evaluator.py             — candidate → decision pipeline
src/engine/monitor_refresh.py       — monitoring lane
src/contracts/settlement_semantics.py     — INV-06 enforcement, integer rounding
src/contracts/execution_price.py          — typed price wrappers, side semantics
src/contracts/venue_submission_envelope.py — V2 submission provenance
src/contracts/fx_classification.py        — pUSD/USDC.e accounting enum
src/state/db.py                     — canonical DB write/query
src/state/lifecycle_manager.py      — sole lifecycle transition authority
src/state/ledger.py                 — append-only event spine
src/state/projection.py             — deterministic projection fold
src/state/chain_reconciliation.py   — chain-truth convergence (INV-18)
src/state/collateral_ledger.py      — pre-submit fail-closed truth
src/state/venue_command_repo.py     — venue command/event journal (INV-28..31)
src/state/readiness_repo.py         — readiness verdict store, fail-closed
src/riskguard/**                    — risk levels, RED fail-closed (INV-19)
src/control/**                      — control surface, runtime_posture (INV-26)
src/supervisor_api/**               — supervisor / kill switch API
migrations/**                       — schema migrations (review for data loss)
architecture/2026_04_02_architecture_kernel.sql   — schema authority
```

---

## Tier 1 — Data / probability / persistence correctness

Review focus: probability/economics layer separation, dual-track integrity,
forecast/calibration provenance, derived export discipline.

```
src/calibration/**                  — Platt fitting, manager, replay
src/signal/**                       — P_raw, ensemble, Monte Carlo, ASOS rounding
src/strategy/**                     — strategy_key grammar, market_phase, oracle wiring
src/data/**                         — forecast ingest, fields, dual-track
src/ingest/**                       — event ingest boundary
src/oracle/**                       — oracle interactions (resolution lookup, etc.)
src/observability/**                — metrics, telemetry, structured events
src/risk_allocator/**               — Kelly sizing, FDR, executable-cost economics
src/analysis/**                     — analytical/diagnostic surfaces
src/backtest/**                     — backtest evaluation (read-only relative to live)
src/runtime/**                      — runtime configuration / harness
src/types/**                        — semantic types (cross-zone identity)
src/contracts/calibration_bins.py        — canonical bin grid (training/inference law)
src/contracts/edge_context.py            — edge provenance (INV-12 territory)
src/contracts/epistemic_context.py       — cross-layer uncertainty
src/contracts/vig_treatment.py           — vig/fee treatment contracts
src/contracts/reality_contract.py        — external assumption (INV-11)
src/contracts/reality_contracts_loader.py — YAML loader
src/contracts/reality_verifier.py        — staleness/drift detection
src/contracts/provenance_registry.py     — INV-13 constant registration
src/contracts/execution_intent.py        — entry/exit intent typing
src/contracts/alpha_decision.py          — alpha target declaration
src/contracts/decision_evidence.py       — decision evidence bundles
src/contracts/semantic_types.py          — base semantic types
src/contracts/tail_treatment.py          — tail probability handling
src/contracts/expiring_assumption.py     — TTL-bound assumptions
src/contracts/hold_value.py              — hold-value computation
src/contracts/exceptions.py              — contract violation exceptions
src/state/portfolio.py              — runtime position read model
src/state/portfolio_loader_policy.py — DB-vs-fallback discipline
src/state/decision_chain.py         — point-in-time decision lineage
src/state/job_run_repo.py           — scheduler/missed-window provenance
src/state/source_run_repo.py        — source-run completeness truth
src/state/market_topology_repo.py   — venue/source contract freshness
src/ingest_main.py                  — ingest entry surface
src/config.py                       — runtime configuration loader
config/**                           — runtime config payloads (when changed)
```

---

## Tier 2 — Tests and validation

Review focus: regression coverage, relationship-test discipline, contract
test integrity, xfail/skip honesty.

```
tests/contracts/**                  — relationship and contract tests
tests/test_*invariant*.py           — invariant relationship tests
tests/test_architecture_contracts.py — INV enforcement test bed
tests/**                            — paired tests for Tier 0 / Tier 1 changes
```

Reviewer rule: a Critical or Important source change should have a paired
test change. Missing pair is at least Important.

---

## Tier 3 — Docs / instructions / agent surfaces

Review focus: authority direction, scope creep, staleness, reader contract,
path-routing freshness. Treat docs as authority surfaces, not prose.

```
AGENTS.md                           — root router
src/**/AGENTS.md                    — scoped module routers (16+ files)
docs/**/AGENTS.md                   — docs-area routers
tests/**/AGENTS.md                  — test-area routers
architecture/**/AGENTS.md           — architecture-zone routers
.agents/skills/**                   — skill definitions (zeus-ai-handoff, etc.)
.claude/CLAUDE.md                   — Claude Code session boot
.claude/skills/**                   — skill bodies
.claude/agents/**                   — agent definitions (critic-opus, safety-gate, verifier)
.claude/hooks/**                    — pre-commit / pre-merge hooks
.claude/settings.json               — settings (review only when changed)
.github/copilot-instructions.md     — Copilot review entry point
.github/instructions/**             — Copilot path-scoped instructions
.github/pull_request_template.md    — PR template
.github/workflows/**                — CI workflows
.github/workflows/AGENTS.md         — workflow router
architecture/invariants.yaml        — invariant catalog (INV-NN)
architecture/source_rationale.yaml  — file-level role/hazard/route table
architecture/module_manifest.yaml   — module-reference layer
architecture/test_topology.yaml     — test classification
architecture/script_manifest.yaml   — script registry
architecture/runtime_modes.yaml     — runtime mode manifest
architecture/zones.yaml             — zone grammar
architecture/task_boot_profiles.yaml — task-class boot vocabularies
architecture/fatal_misreads.yaml    — known fatal misread patterns
architecture/history_lore.yaml      — historical failure lessons
architecture/reality_contracts/**   — external reality contracts
architecture/code_review_graph_protocol.yaml  — DEPRECATED stub
architecture/improvement_backlog.yaml         — improvement queue
architecture/worktree_merge_protocol.yaml     — merge protocol
architecture/packet_templates/**    — packet templates
architecture/ast_rules/**           — AST rules
architecture/self_check/**          — self-check authority
architecture/2026_04_02_architecture_kernel.sql (Tier 0 — listed there)
docs/authority/**                   — authoritative surface docs
docs/operations/current_*.md        — current state surfaces (current_state, current_data_state, current_source_validity)
docs/reference/**                   — reference docs (domain model, math spec, etc.)
docs/reference/modules/**           — dense module books
docs/runbooks/**                    — operational runbooks
docs/methodology/**                 — operating methodology
docs/to-do-list/**                  — known gaps + active to-dos
docs/review/**                      — this set (REVIEW doctrine)
REVIEW.md                           — root review doctrine
workspace_map.md                    — directory-level structure / visibility
docs/archive_registry.md            — archive interface (visible)
config/AGENTS.md, config/reality_contracts/AGENTS.md  — config-area routers
scripts/AGENTS.md                   — scripts router
raw/AGENTS.md                       — raw data router
```

---

## Deprioritized — review only if change demonstrably alters runtime behavior

Burden of proof: PR's AI Review Scope must explicitly justify why a
deprioritized path warrants attention.

```
.claude/orchestrator/**             — orchestrator runtime state
.claude/worktrees/**                — worktree manifests
.code-review-graph/**               — derived graph database
.omc/**, .omx/**, .zeus/**          — runtime caches / scratch
.zeus-githooks/**, .zpkt-cache/**   — derived hook + packet cache
docs/archives/**                    — provenance / cold storage
docs/artifacts/**                   — past review snapshots / external evidence
docs/reports/**                     — reporting outputs
docs/operations/archive/**          — archived operations packets
docs/operations/task_*/**           — closed task packets (historical evidence)
logs/**                             — log streams
raw/**                              — raw ingest / fixture data
state/**                            — runtime DB / state files (dynamic)
evidence/**                         — collected evidence
*.lock                              — lock files
.DS_Store                           — Finder metadata
*.log                               — log artifacts
__pycache__/**, *.pyc               — generated bytecode
.gitleaks.toml                      — gitleaks config (review only when changed)
.importlinter                       — importlinter config (review only when changed)
.gitignore                          — gitignore (review only when changed)
SECURITY-FALSE-POSITIVES.md         — gitleaks false-positive registry (review only when changed)
LIVE_TRADING_LOCKED_2026-05-04.md   — lock state file (review only when changed)
station_migration_alerts.json       — runtime alert dump
.zeus/**                            — runtime cache
.venv/**                            — virtualenv (never tracked)
```

---

## Notes

- Path patterns use shell-glob style (`**` = recursive).
- A path matched by both Tier 0 and a deprioritized rule is **Tier 0**.
  Tier wins over skip.
- A path not listed here defaults to Tier 1 if under `src/`, Tier 2 if
  under `tests/`, Tier 3 if under `docs/` / `architecture/` / `.github/`
  / `.claude/`, otherwise deprioritized.
- New top-level directories under `src/` should be added here when
  introduced. Drift between this file and the actual repo is itself a
  Tier 3 finding.

Sunset: revisit whenever a new top-level package is added under `src/`,
a new module is added that warrants Tier 0 status, or
`architecture/source_rationale.yaml` reclassifies a file's zone.
