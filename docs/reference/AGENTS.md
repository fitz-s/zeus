# docs/reference AGENTS

Canonical reference material for Zeus. References explain durable concepts and module behavior; they are not authority. Active authority lives in `docs/authority/**`, machine manifests, tests, and executable source.

Current facts do not live here. Dated audits, consults, PR reviews, packet evidence, rebuild notes, and operational snapshots do not live here.

---

## Default Reference Route

When a task asks for broad Zeus orientation or can change live-money behavior, read:

1. `docs/reference/zeus_prediction_market_quant_reference.md`;
2. the focused reference matching the task;
3. relevant `docs/reference/modules/**` only when routed by module scope or `architecture/module_manifest.yaml`.

Do not start from old replacement papers, support-reference snapshots, rebuild specs, consult reviews, or packet plans.

---

## Focused References

| Task | Read |
|---|---|
| full deploy money path | `zeus_prediction_market_quant_reference.md` |
| domain/family/bin/native side | `zeus_domain_model.md` |
| q/q_lcb/math/probability | `zeus_math_spec.md` |
| forecast source/product/regional models | `zeus_forecast_source_and_regional_model_reference.md` |
| strategy/admission/selection | `zeus_strategy_spec.md` |
| settlement/source/market topology | `zeus_market_settlement_reference.md` |
| execution/commands/lifecycle/exit/settlement | `zeus_execution_lifecycle_reference.md` |
| sizing/risk/degraded data | `zeus_risk_strategy_reference.md` |
| DB/replay/backtest/current-fact boundaries | `zeus_data_and_replay_reference.md` |
| known failure classes | `zeus_failure_modes_reference.md` |
| module-specific implementation context | `modules/AGENTS.md`, then the routed module book |

Other legacy or specialized references are non-default unless the task explicitly names their subject and current code/manifests still support their claims.

---

## Active File Registry

| File | Class | Purpose |
|---|---|---|
| `zeus_prediction_market_quant_reference.md` | canonical durable reference | Complete current deploy money-path reference from contract truth through settlement/learning |
| `zeus_domain_model.md` | canonical durable reference | Domain model: family, Ω, bins, native sides, high/low identity, truth hierarchy |
| `zeus_math_spec.md` | canonical durable reference | Current probability/q/q_lcb/payoff/utility math, with executable-vs-reference-vs-target labels |
| `zeus_forecast_source_and_regional_model_reference.md` | canonical durable reference | Forecast source/product identity, regional model inclusion, residual discipline, source-role separation |
| `zeus_strategy_spec.md` | canonical durable reference | Direction law, admission, candidate selection, no-trade reasons, q-kernel strategy path |
| `zeus_market_settlement_reference.md` | canonical durable reference | Market/settlement/source/bin topology and settlement semantics |
| `zeus_execution_lifecycle_reference.md` | canonical durable reference | Entry, command, fill, monitor, exit, lifecycle, chain reconciliation, settlement/redeem |
| `zeus_risk_strategy_reference.md` | canonical durable reference | Sizing/risk levels/portfolio exposure/DATA_DEGRADED behavior |
| `zeus_data_and_replay_reference.md` | canonical durable reference | DB topology, table ownership, forecast/observation/settlement provenance, replay boundaries |
| `zeus_failure_modes_reference.md` | canonical durable reference | Live-money failure modes and antibodies |
| `modules/AGENTS.md` | module router | Routes dense module books without making them authority |
| `modules/*.md` | module reference | Dense implementation context for specific packages only when routed |

---

## Rules

- Do not add present-tense runtime facts, live bankroll, PID, loaded SHA, active positions, active rejection counts, or packet diaries to reference docs.
- Do not add stale support, dated audit, packet-evidence, workbook, consult, PR-review, or current operational fact files here.
- Do not route canonical references to demoted history for present-tense facts.
- Use `docs/operations/current_data_state.md`, `docs/operations/current_source_validity.md`, and `docs/operations/current_state.md` for current audited facts, with freshness/expiry.
- Module books are orientation only. They do not outrank authority docs, machine manifests, tests, current-fact surfaces, or executable source.
- If a historical file contains surviving durable reference material, merge the content into an active reference and demote the source.
