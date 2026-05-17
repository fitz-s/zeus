# docs/reference AGENTS

Canonical reference material for Zeus. Reference docs explain durable concepts
and orientation; they are not authority. Authority lives in `docs/authority/**`,
machine manifests, tests, and executable source.

## Default vs conditional read path

**Default reads** (when a digest requests reference context):
- `zeus_domain_model.md`

**Conditional reads** (load only when the task directly requires them):
- `zeus_architecture_reference.md` for architecture orientation
- `zeus_execution_lifecycle_reference.md` for execution, lifecycle, chain
  reconciliation, exit triggers, and settlement harvest
- `zeus_risk_strategy_reference.md` for risk levels, strategy taxonomy,
  Kelly sizing dynamics, and edge decay monitoring
- `zeus_market_settlement_reference.md` for settlement/market concepts
- `zeus_data_and_replay_reference.md` for data/replay concepts
- `zeus_failure_modes_reference.md` for failure-class reviews
- `zeus_math_spec.md` when math fact/spec context matters
- `zeus_calibration_weighting_authority.md` when working on calibration weight
  schemes, Platt fit pipelines, or LOW-track training eligibility (binds the
  binary→continuous training_allowed→precision_weight transition; per-city
  opt-out for coastal/monsoon physics; forbids ΔT-magnitude weighting in
  production)
- `zeus_oracle_density_discount_reference.md` when working on oracle penalty,
  Data Density Discount, source-thinness handling, or the
  `Mismatch ↔ DDD ↔ Platt-regime-absorption` relationship; encodes the
  three-stage refinement (track-aware coverage → Platt regime absorption
  verified empirically → hardened-baseline + σ-band + small-sample multiplier);
  any change to `oracle_penalty.py`, DDD, or `bridge_oracle_to_calibration.py`
  MUST cite §6 of that doc as authority basis
- `zeus_vendor_change_response_registry.md` when touching anything that may
  invalidate on a PM source switch (T1), WU silent change (T2), Lagos-class
  discovery (T3), Shenzhen-class onboarding (T4), or vendor outage (T5).
  Encodes the 14-layer dependency surface and per-trigger response playbook;
  any cutover work MUST cite the relevant §3 layer and §4 playbook
- `zeus_kelly_asymmetric_loss_handoff.md` when working on Kelly sizing,
  per-city asymmetric loss preferences, or DDD ↔ Kelly composition; encodes
  the authority that asymmetric loss must be expressed as per-city Kelly
  multipliers (NOT as DDD floor overrides); LANDED 2026-05-03 in
  `src/strategy/kelly.py`; wiring at evaluator.py is operator-owned
- `modules/AGENTS.md` when the task is module-sensitive and needs a dense module
  book route
- `modules/state.md`, `modules/engine.md`, `modules/data.md`,
  `modules/venue.md`/`modules/ingest.md`, `modules/execution.md`, and
  `modules/riskguard.md` when routed by the active phase/module manifest

Current data/source facts live under operations current-fact surfaces, not in
this directory. Dated analytical/support snapshots live under the reports
subroot.

Replacement/deletion eligibility is now governed by
`architecture/docs_registry.yaml` and packet evidence; do not recreate the old
support-reference layer.

Dense module books live under `docs/reference/modules/`. They are reference
surfaces that explain module behavior, hazards, and tests deeply enough for
zero-context work. They do not outrank authority docs, machine manifests,
current-fact surfaces, tests, or executable source.

The first-wave books (`state`, `engine`, `data`) and the remaining module books
now route through `modules/AGENTS.md`; use that router instead of guessing
which module reference matters.

## File registry

| File | Purpose |
|------|---------|
| `zeus_domain_model.md` | Short domain model and first conceptual reference |
| `zeus_architecture_reference.md` | Durable descriptive architecture reference |
| `zeus_execution_lifecycle_reference.md` | Lifecycle state machine (10 phases, fold table), chain reconciliation (3-state classifier, 3 rules), order execution (share quantization, mode timeouts), exit triggers (8-layer evaluation), monitor refresh (2 signal paths), settlement harvest (3-layer dedup, P&L, redemption) |
| `zeus_risk_strategy_reference.md` | RiskLevel enum (5 levels incl DATA_DEGRADED), 6 risk inputs to tick(), trailing loss computation, strategy gate emission, Kelly sizing (dynamic_kelly_mult thresholds), RiskGuard process architecture (dual-DB, alert emission) |
| `zeus_market_settlement_reference.md` | Market structure (event/market/bin hierarchy, token swap guard, VWMP), bin topology (3 types, width normalization), settlement semantics (rounding rules, for_city routing), sensor physics (ASOS σ, per-city overrides), Monte Carlo P_raw, probability chain (Platt, alpha, bootstrap CI) |
| `zeus_data_and_replay_reference.md` | Database topology (3-DB split), core table schemas, data ingestion (hourly instants, coverage tracking, IngestionGuard layers), provenance/authority contracts, dual-track identity (MetricIdentity type safety), replay diagnostic status |
| `zeus_failure_modes_reference.md` | Code-grounded failure modes with invariant anchors: settlement/rounding, probability chain, lifecycle/state, data ingestion, execution — each with exact failure mechanism, preventing contract, and code anchor |
| `zeus_math_spec.md` | Deep math/specification reference; executable law and authority manifests win on disagreement |
| `zeus_calibration_weighting_authority.md` | Mathematical authority for calibration weight semantics (LOW track binary→continuous, per-city eligibility, ΔT-magnitude forbidden in production); empirical basis PoC v4+v5 on 1.7M pairs |
| `zeus_kelly_asymmetric_loss_handoff.md` | Per-city asymmetric loss preferences via Kelly multipliers (NOT DDD floor); LANDED 2026-05-03 in `src/strategy/kelly.py`; open wiring at evaluator.py is operator-owned deliberate two-stage rollout |
| `zeus_vendor_change_response_registry.md` | 14-layer vendor dependency surface map + T1-T5 response playbooks for PM source switch, WU silent mutation, Lagos-class failure, Shenzhen-class onboarding, vendor outage |
| `modules/AGENTS.md` | Router for dense module books under `docs/reference/modules/` |
| `modules/state.md` | Dense state truth / lifecycle / projection module book |
| `modules/engine.md` | Dense engine orchestration / replay / sequencing module book |
| `modules/data.md` | Dense source-routing / ingest / data-versioning module book |
| `modules/venue.md` | Dense Polymarket V2 adapter / submission provenance module book |
| `modules/ingest.md` | Dense user-channel ingest / U2 fact append bridge / forecast-live producer module book |
| `modules/execution.md` | Dense live execution / command / exit / settlement and pre-submit gate module book |
| `modules/riskguard.md` | Dense riskguard and R3 A2 risk-allocator/governor module book |
| `modules/topology_system.md` | Dense topology-system reference for machine routing and manifest law |
| `modules/docs_system.md` | Dense docs-system reference for trust layers and docs mesh maintenance |
| `modules/code_review_graph.md` | Dense derived-context reference for graph boundaries |
| `modules/topology_doctor_system.md` | Dense topology-doctor reference for lanes, issue models, and closeout seams |
| `modules/manifests_system.md` | Dense manifest-system reference for fact ownership and repair routing |
| `modules/closeout_and_receipts_system.md` | Dense closeout/receipts reference for scoped gates and evidence |
| `zeus_oracle_density_discount_reference.md` | Oracle penalty, Data Density Discount, source-thinness handling, and Mismatch↔DDD↔Platt-regime-absorption relationship reference |

## Rules

- Do not add stale support, dated audit, packet-evidence, workbook, or current
  operational fact files here.
- Do not route canonical references to demoted legacy-reference reports for
  present-tense facts.
- Use `docs/operations/current_data_state.md` and
  `docs/operations/current_source_validity.md` for current audited facts.
- Do not treat module books as authority or current-fact surfaces; use them as
  dense orientation after the scoped router or `architecture/module_manifest.yaml`
  tells you which module matters. R3 Z1 registers `docs/reference/modules/control.md`
  for the CutoverGuard control route; it remains reference-only, not authority.
- Do not recreate a frozen support layer.
