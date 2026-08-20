# src/analysis AGENTS

Module book: `docs/reference/modules/analysis.md`
Machine registry: `architecture/module_manifest.yaml`

Zone: K4 — Extension (analysis utilities)

## What this code does (and WHY)

Derived analysis over settled truth — reporting and post-hoc attribution that
must never feed a decision. No longer a placeholder: eleven modules live here,
including `settlement_skill_attribution.py` (the six-class post-settlement
grader whose output the calibration report and skill claims rest on),
`regret_decomposer.py`, `exit_timing_attribution.py`, `evidence_report.py`, and
the per-surface reports (`day0_boundary`, `event_opportunity`,
`forecast_release_reaction`, `orderbook_execution_feasibility`,
`settlement_guard`).

The zone rule below is what keeps this safe: everything here reads truth and
writes only reports. A grader that could write back into the surface it grades
would let an outcome label rewrite the evidence it was derived from.

## Domain rules

- K4 zone — no planning lock required
- Analysis code is DERIVED — it may read truth surfaces but never write to them
- May import from K3 and below, never from K0/K1/K2 internals

## References
- Root rules: `../../AGENTS.md`
