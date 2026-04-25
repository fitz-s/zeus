# docs/reference/modules AGENTS

Dense module-reference layer for Zeus.

This directory exists for module books that make zero-context work possible
without promoting those books into authority. Module books explain one module's
purpose, hazards, truth surfaces, tests, and change routes. They do not replace
authority docs, machine manifests, current-fact surfaces, tests, or source.

## Read order

1. root `AGENTS.md`
2. `workspace_map.md`
3. the scoped `AGENTS.md` for the touched module or system surface
4. `architecture/module_manifest.yaml`
5. the one routed module book in this directory
6. current-fact/test surfaces named by that module book

## File registry

| File | Purpose |
|------|---------|
| `state.md` | Dense module book for canonical runtime truth, lifecycle legality, and projection discipline |
| `engine.md` | Dense module book for runtime orchestration, evaluator flow, replay parity, and monitor sequencing |
| `data.md` | Dense module book for source-role routing, ingest discipline, and data-version boundaries |
| `contracts.md` | Dense module book for frozen semantic contracts and typed cross-layer boundaries |
| `execution.md` | Dense module book for live-money order placement, exit mechanics, and settlement harvest |
| `riskguard.md` | Dense module book for protective enforcement and behavior-changing risk levels |
| `control.md` | Dense module book for the external control plane and gate provenance |
| `supervisor_api.md` | Dense module book for Zeus/Venus typed boundary contracts |
| `strategy.md` | Dense module book for edge selection, FDR, Kelly, and posterior fusion |
| `signal.md` | Dense module book for P_raw, Day0, and diurnal signal generation |
| `calibration.md` | Dense module book for Platt calibration, maturity gates, and shadow metrics |
| `observability.md` | Dense module book for derived operator read models and health views |
| `types.md` | Dense module book for unit safety, market types, and observation atoms |
| `analysis.md` | Dense module book for placeholder/derived analysis utilities |
| `scripts.md` | Dense module book for top-level script families and safety boundaries |
| `tests.md` | Dense module book for law gates, relationship tests, and diagnostic/advisory families |
| `topology_system.md` | Dense module book for machine routing, topology doctor, and manifest law |
| `docs_system.md` | Dense module book for the tracked docs mesh and trust-layer routing |
| `code_review_graph.md` | Dense module book for derived structural context and graph boundaries |
| `topology_doctor_system.md` | Dense system book for topology-doctor lanes, issue models, and CLI/closeout seams |
| `manifests_system.md` | Dense system book for manifest ownership, fact-type boundaries, and repair routing |
| `closeout_and_receipts_system.md` | Dense system book for scoped closeout, receipts, work records, and deferral evidence |

## Rules

- One file per module or system surface.
- Follow the Module Authority Book Standard described in the active packet and
  later durable references.
- Do not store packet status, dated audits, row counts, live source health, or
  archive bodies here.
- If a claim is time-bound, point to `docs/operations/**` instead of embedding
  it here.
- If a graph appendix is useful, keep it derived-only and subordinate to the
  module book.
