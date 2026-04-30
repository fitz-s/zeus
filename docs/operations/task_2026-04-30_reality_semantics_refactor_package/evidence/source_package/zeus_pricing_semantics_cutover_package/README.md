# Zeus Pricing Semantics Authority Cutover Package

Date: 2026-04-30
Branch evaluated: `fitz-s/zeus@plan-pre5`
Status: implementation strategy package, not a live-deploy authorization

## Non-negotiable conclusion

This package does **not** claim an absolute-best architecture. In live quantitative trading, an architecture is only as good as the facts, fill behavior, market microstructure, model validation, source truth, risk controls, and settlement reconciliation it keeps proving. The defensible claim is narrower and stronger:

> Under the currently reviewed Zeus repo surfaces, uploaded split spec, and Polymarket CLOB documentation, the correct first-principles architecture is a physically isolated three-layer system: Epistemic beliefs, Microstructure facts, and Execution/Risk economics. Any design that lets a raw price-like scalar cross those boundaries as trading authority is unsafe.

## Package contents

- `01_authority_truth_surfaces.md` — authority order and truth surfaces.
- `02_three_layer_architecture.md` — physical isolation contract for Epistemic, Microstructure, Execution/Risk.
- `03_hidden_branch_register.md` — all identified hidden branches, known truth, unresolved uncertainty, second-order consequences, tests.
- `04_multiphase_execution_plan.md` — final multi-phase step-by-step plan.
- `05_codex_execution_strategy.md` — concrete Codex work strategy, stop conditions, topology commands, prompt protocol.
- `06_patch_blueprint.md` — repo patch blueprint and likely files/modules.
- `07_verification_matrix.md` — tests, gates, verification loop, invariants.
- `08_blast_radius_rollback_monitor.md` — irreversible decisions, blast radius, rollback and monitors.
- `09_not_now_list.md` — explicit scope exclusions.
- `10_source_map.md` — evidence map and source references.
- `codex_prompts/*.md` — phase-specific prompts to hand to Codex.
- `templates/*.py` — illustrative contract/function skeletons for implementation guidance.
- `checklists/*.md` — preflight and closeout checklists.

## Intended use

Use this package as the implementation packet plan. It is not a patch and it is not authority by itself. In Zeus terms, source edits still require topology admission, scoped AGENTS reads, planning-lock evidence where required, focused tests, and closeout evidence. The uploaded split spec makes the same restriction explicit.

## One-sentence implementation target

Zeus must stop trading probability-like scalars and instead trade only executable hypotheses:

```text
ExecutableTradeHypothesis =
  event/bin/direction
  + selected token_id
  + payoff probability from posterior belief
  + executable market snapshot
  + depth/fee/tick/min-order-aware cost basis
  + order policy
  + FDR identity
  + immutable final intent
```
