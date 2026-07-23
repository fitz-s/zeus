# Zeus Theory Map

Navigation index for all theory and reference documentation in the Zeus repository.

- **For a system overview and live chain description:** [`README.md`](../../README.md)
- **For operating law, authority hierarchy, and routing rules:** [`AGENTS.md`](../../AGENTS.md)
- **For the reference directory router (conditional load guidance):** [`docs/reference/AGENTS.md`](AGENTS.md)

This map surfaces every durable theory, reference, and architecture design doc. It does
**not** include operations packets, reports, runbooks, or evidence archives — those live
under `docs/operations/`, `docs/runbooks/`, and the archive. Authority docs (law, not
explanation) are listed separately in §6.

A `[ref]` tag means the doc is durable explanatory reference; an `[authority]` tag means
it carries binding law status (executable source and manifests take precedence over both
on disagreement).

---

## 1 · Forecast & Fusion

| Doc | Description | Status |
|-----|-------------|--------|
| [`docs/reference/zeus_math_spec.md`](zeus_math_spec.md) | Complete mathematical and statistical machinery: unit handling, empirical-Bayes de-bias, Ledoit–Wolf covariance shrinkage, precision-weighted Bayesian fusion, Monte Carlo P_raw (now offline evidence baseline), Extended Platt calibration (offline evidence), settlement-exact preimage integration, bootstrap confidence intervals. §0.1 authority notice identifies which sections describe the live path vs the offline evidence baseline. | `[ref]` durable |
| [`docs/reference/zeus_market_settlement_reference.md`](zeus_market_settlement_reference.md) | Market and settlement reference: event/market/bin hierarchy, token swap guard, VWMP, bin topology (exact/ceiling/floor shapes and width normalization), settlement rounding rules and `for_city` routing, sensor physics (ASOS σ, per-city overrides), Monte Carlo P_raw, probability chain (Platt, α, bootstrap CI). | `[ref]` durable |
| [`docs/reference/zeus_data_and_replay_reference.md`](zeus_data_and_replay_reference.md) | Data topology: three-DB split, core table schemas, hourly ingest flow, coverage tracking, IngestionGuard, provenance and authority contracts, dual-track MetricIdentity type safety, replay offline evidence status. | `[ref]` durable |
| [`docs/reference/zeus_calibration_weighting_authority.md`](zeus_calibration_weighting_authority.md) | Calibration weight semantics: the theorem that calibration weight must be a continuous function of precision dimensions (no binary discard), LOW-track binary→continuous transition, per-city eligibility (coastal/monsoon physics), ΔT-magnitude weighting forbidden in production. Empirical basis: PoC v4+v5 on 1.7M pairs. | `[ref]` durable |
| [`docs/reference/zeus_oracle_density_discount_reference.md`](zeus_oracle_density_discount_reference.md) | Oracle penalty and Data Density Discount (DDD): DDD as outage detector, Platt regime absorption, two-rail trigger, continuous linear curve, p05 hardened floor. Canonical spec for `src/strategy/oracle_penalty.py`; any change to that file must cite §6. | `[ref]` durable |
| [`docs/authority/replacement_final_form_2026_06_09.md`](../authority/replacement_final_form_2026_06_09.md) | **Live probability chain law.** Defines the canonical `replacement_forecast` path: `bayes_precision_fusion.fuse_bayes_precision_posterior` → `emos.bin_probability_settlement`. Supersedes all prior probability chain docs. | `[authority]` |
| [`docs/authority/regime_unification_2026-06-12.md`](../authority/regime_unification_2026-06-12.md) | Single-q regime law: eliminates era-layered fallback/parallel observe-only/active/off vocabulary; consolidates to one live probability regime with no legacy multi-regime fallback. | `[authority]` |
| [`architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md`](../../architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md) | Design resolution for math issues 2.3 (oracle LOW track), 2.4 (bootstrap transfer uncertainty via `transfer_logit_sigma`), and 3.1; records specific fixes and byte-identical default contracts. | `[ref]` dated design |
| [`docs/authority/statistical_calibration_authority_2026-06-12_README.md`](../authority/statistical_calibration_authority_2026-06-12_README.md) | Clean-room math consult on statistical calibration law: Wilson lower bound derivation, coverage requirements, walk-forward discipline. | `[authority]` |

---

## 2 · Calibration & Settlement

| Doc | Description | Status |
|-----|-------------|--------|
| [`docs/reference/zeus_market_settlement_reference.md`](zeus_market_settlement_reference.md) | See §1 — canonical settlement semantics, rounding rules, and bin topology. | `[ref]` durable |
| [`docs/authority/zeus_current_architecture.md`](../authority/zeus_current_architecture.md) | Architecture law including settlement invariants, SettlementSemantics as a semantic atom, truth planes, and DB ownership rules. | `[authority]` |
| [`docs/reference/zeus_strategy_spec.md`](zeus_strategy_spec.md) | Strategy mathematics and proof taxonomy: Wilson lower bound mechanics, adverse-selection defense, offline evidence baseline description. **Superseded for the live q chain** (see §0 authority notice), but retained as reference for the offline evidence baseline, strategy grading, and proof taxonomy. | `[ref]` partial supersession |
| [`docs/authority/statistical_calibration_authority_2026-06-12_README.md`](../authority/statistical_calibration_authority_2026-06-12_README.md) | Calibration statistical law. See §1. | `[authority]` |

---

## 3 · Uncertainty & Selection

| Doc | Description | Status |
|-----|-------------|--------|
| [`docs/reference/zeus_math_spec.md`](zeus_math_spec.md) | Uncertainty width composition, σ floor, representativeness variance, renormalize-then-quantile lower bound discipline. See §1. | `[ref]` durable |
| [`docs/reference/zeus_risk_strategy_reference.md`](zeus_risk_strategy_reference.md) | RiskLevel enum (5 levels including DATA_DEGRADED), 6 risk inputs to `tick()`, trailing loss computation, strategy gate emission, Kelly sizing (dynamic_kelly_mult thresholds), RiskGuard process architecture (dual-DB, alert emission), strategy taxonomy. | `[ref]` durable |
| [`docs/reference/zeus_calibration_weighting_authority.md`](zeus_calibration_weighting_authority.md) | Calibration weight math and eligibility. See §1. | `[ref]` durable |
| [`docs/reference/zeus_oracle_density_discount_reference.md`](zeus_oracle_density_discount_reference.md) | DDD and oracle penalty design. See §1. | `[ref]` durable |
| [`docs/authority/exit_portfolio_execution_authority_2026-06-13.md`](../authority/exit_portfolio_execution_authority_2026-06-13.md) | Clean-room consult authority on exit formulas, horse-race Kelly, stop-loss proof, mean-variance/robust sizing math, dynamic execution. High confidence for Q1/Q2; medium for Q3 microstructure. | `[authority]` |
| [`architecture/market_cost_seam_executable_uncertainty_2026_05_27.md`](../../architecture/market_cost_seam_executable_uncertainty_2026_05_27.md) | Architecture upgrade for market-cost seam and executable-uncertainty propagation: 22 chain-safety mechanisms, 5 structural decisions, typed probability seams (INV-12, INV-21). | `[ref]` dated design |

---

## 4 · Sizing & Risk

| Doc | Description | Status |
|-----|-------------|--------|
| [`docs/reference/zeus_kelly_asymmetric_loss_reference.md`](zeus_kelly_asymmetric_loss_reference.md) | Fractional Kelly sizing and per-city asymmetric loss: the ruling that asymmetric loss preferences must be expressed as per-city Kelly multipliers (NOT DDD floor overrides). Lists affected cities with recommended multipliers. Landed 2026-05-03 in `src/strategy/kelly.py`; evaluator wiring is operator-owned. | `[ref]` durable |
| [`docs/reference/zeus_risk_strategy_reference.md`](zeus_risk_strategy_reference.md) | Risk levels, strategy taxonomy, Kelly dynamics. See §3. | `[ref]` durable |
| [`docs/authority/exit_portfolio_execution_authority_2026-06-13.md`](../authority/exit_portfolio_execution_authority_2026-06-13.md) | Sizing and exit authority. See §3. | `[authority]` |
| [`docs/reference/zeus_math_spec.md`](zeus_math_spec.md) | Kelly sizing math and fail-closed discipline. See §1. | `[ref]` durable |

---

## 5 · Execution & Lifecycle

| Doc | Description | Status |
|-----|-------------|--------|
| [`docs/reference/zeus_execution_lifecycle_reference.md`](zeus_execution_lifecycle_reference.md) | Lifecycle state machine (10 phases, fold table), chain reconciliation (3-state classifier, 3 rules), order execution (share quantization, mode timeouts), exit triggers (8-layer evaluation), monitor refresh (2 signal paths), settlement harvest (3-layer dedup, P&L, redemption). | `[ref]` durable |
| [`architecture/lifecycle_grammar.md`](../../architecture/lifecycle_grammar.md) | Canonical status strings for the lifecycle state machine: maps all strings in use to their canonical meaning and settling migration intent. Ground-truth complement to the lifecycle reference. | `[ref]` durable |
| [`docs/reference/zeus_failure_modes_reference.md`](zeus_failure_modes_reference.md) | Code-grounded failure modes with invariant anchors: settlement/rounding, probability chain, lifecycle/state, data ingestion, execution — each with exact failure mechanism, preventing contract, and code anchor. | `[ref]` durable |
| [`architecture/exit_strategy_audit_2026_05_27.md`](../../architecture/exit_strategy_audit_2026_05_27.md) | Provenance audit of exit strategy code (2026-05-27 base); per-file verdicts. | `[ref]` dated audit |
| [`architecture/exit_strategy_integration_plan_2026_05_27.md`](../../architecture/exit_strategy_integration_plan_2026_05_27.md) | Integration plan following the exit strategy audit; wiring and sequencing decisions. | `[ref]` dated design |
| [`architecture/world_mutex_io_offmutex_refactor_2026_06_04.md`](../../architecture/world_mutex_io_offmutex_refactor_2026_06_04.md) | Design for removing blocking I/O from under the world write mutex (WAL starvation fix); lists all sites and fix status. | `[ref]` dated design |
| [`docs/reference/zeus_vendor_change_response_registry.md`](zeus_vendor_change_response_registry.md) | 14-layer vendor dependency surface map; T1–T5 response playbooks (PM source switch, WU silent mutation, Lagos-class failure, Shenzhen-class onboarding, vendor outage). Required reading before any source cutover. | `[ref]` durable |

---

## 6 · Data & Architecture

| Doc | Description | Status |
|-----|-------------|--------|
| [`docs/reference/zeus_domain_model.md`](zeus_domain_model.md) | Five-minute domain overview: trading machine scope, key concepts, module relationships. Default read for any new context. | `[ref]` durable |
| [`docs/reference/zeus_architecture_reference.md`](zeus_architecture_reference.md) | Compact descriptive architecture reference: module boundaries, data flows, component responsibilities. | `[ref]` durable |
| [`docs/authority/zeus_current_architecture.md`](../authority/zeus_current_architecture.md) | **Current architecture law**: semantic atoms (SettlementSemantics, MetricIdentity), truth planes, DB ownership, lifecycle grammar, trading machine invariants. The law version; reference docs above are explanatory. | `[authority]` |
| [`docs/authority/zeus_current_delivery.md`](../authority/zeus_current_delivery.md) | Delivery law: phases, ownership, delivery gates, commit discipline. | `[authority]` |
| [`docs/reference/zeus_data_and_replay_reference.md`](zeus_data_and_replay_reference.md) | Data topology and replay. See §1. | `[ref]` durable |
| [`docs/reference/schema_cheatsheet.md`](schema_cheatsheet.md) | Generated live-DB schema quick reference: table names, column types, key relationships across the three databases. Useful for query authoring; not maintained as primary authority. | `[ref]` generated |
| [`architecture/self_check/authority_index.md`](../../architecture/self_check/authority_index.md) | Zero-context authority read order: the canonical spine for high-risk work with no prior session context. | `[ref]` durable |
| [`architecture/self_check/zero_context_entry.md`](../../architecture/self_check/zero_context_entry.md) | Entry protocol for zero-context sessions: when to use the authority index and how to refuse unsafe starts. | `[ref]` durable |
| [`docs/authority/zeus_change_control_constitution.md`](../authority/zeus_change_control_constitution.md) | Change-control law: merge protocol, proof gates, commit constraints. | `[authority]` |
| [`architecture/agent_pr_discipline_2026_05_09.md`](../../architecture/agent_pr_discipline_2026_05_09.md) | Four first-principles of agent PR workflow quality (operator directive 2026-05-09); hook backstops and bypass envs. | `[ref]` durable |

---

## 7 · Module Books

Dense per-module references live under `docs/reference/modules/`. Route through
[`docs/reference/modules/AGENTS.md`](modules/AGENTS.md) rather than loading module
books directly — the router identifies which book applies to the active task.

Key books: `state.md`, `engine.md`, `data.md`, `execution.md`, `venue.md`, `ingest.md`,
`riskguard.md`, `strategy.md`, `calibration.md`, `signal.md`, `contracts.md`,
`control.md`, `types.md`, `topology_system.md`, `docs_system.md`,
`closeout_and_receipts_system.md`, `manifests_system.md`.

---

## Reading paths

**New to Zeus?** `zeus_domain_model.md` → `README.md` §Methodology → `zeus_math_spec.md` §0–2

**Reviewing a calibration change?** `zeus_calibration_weighting_authority.md` → `zeus_math_spec.md` §5–6 → `statistical_calibration_authority_2026-06-12_README.md`

**Reviewing execution or lifecycle?** `zeus_execution_lifecycle_reference.md` → `lifecycle_grammar.md` → `zeus_failure_modes_reference.md`

**Working on Kelly sizing?** `zeus_kelly_asymmetric_loss_reference.md` → `zeus_risk_strategy_reference.md` → `exit_portfolio_execution_authority_2026-06-13.md`

**Reviewing settlement math?** `zeus_market_settlement_reference.md` → `zeus_math_spec.md` §3 → `zeus_current_architecture.md` §SettlementSemantics

**Debugging the live probability chain?** `replacement_final_form_2026_06_09.md` → `regime_unification_2026-06-12.md` → `zeus_math_spec.md` §0.1
