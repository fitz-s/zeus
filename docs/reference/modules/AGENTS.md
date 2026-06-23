# docs/reference/modules AGENTS

Dense module-reference layer for Zeus. Module books explain package behavior, hazards, truth surfaces, tests, and change routes. They do not replace active authority docs, machine manifests, current-fact surfaces, tests, or source.

---

## Read Order

1. root `AGENTS.md`;
2. `workspace_map.md`;
3. scoped `AGENTS.md` for the touched module/system;
4. `docs/reference/zeus_prediction_market_quant_reference.md` for money-path context;
5. `architecture/module_manifest.yaml` when module routing/dependencies matter;
6. exactly the routed module book(s);
7. current-fact/test/evidence surfaces named by the module book only when needed.

Do not default-read all module books. Do not route through packet evidence, active-packet standards, or dated module audits.

---

## File Registry

| File | Purpose |
|---|---|
| `state.md` | Runtime truth, lifecycle legality, projection discipline |
| `engine.md` | Runtime orchestration, event reactor, q-kernel bridge, replay/monitor sequencing |
| `data.md` | Source-role routing, replacement forecast materialization, data-version boundaries |
| `contracts.md` | Frozen semantic contracts and typed cross-layer boundaries |
| `execution.md` | Live-money order placement, command persistence, exit mechanics, settlement harvest |
| `venue.md` | Polymarket adapter boundaries and submission provenance |
| `ingest.md` | Split ingest daemons and event-stream/fill bridge facts |
| `riskguard.md` | Protective enforcement and behavior-changing risk levels |
| `control.md` | External control plane and gate provenance |
| `supervisor_api.md` | Zeus/Venus typed boundary contracts |
| `strategy.md` | q-kernel edge selection, direction law, payoff vectors, utility/risk strategy |
| `signal.md` | Signal generation; legacy paths must be explicitly marked diagnostic if not live |
| `calibration.md` | Calibration/fusion/q band support; distinguish legacy Platt from current q authority |
| `observability.md` | Derived operator read models and health views |
| `types.md` | Unit safety, market types, observation atoms |
| `analysis.md` | Derived analysis utilities only |
| `scripts.md` | Script families and safety boundaries |
| `tests.md` | Law gates, relationship tests, diagnostic/advisory test families |
| `topology_system.md` | Machine routing, topology doctor, manifest law |
| `docs_system.md` | Docs mesh and trust-layer routing |
| `code_review_graph.md` | Derived structural context and graph boundaries |
| `topology_doctor_system.md` | Topology-doctor lanes, issue models, CLI/closeout seams |
| `manifests_system.md` | Manifest ownership, fact-type boundaries, repair routing |
| `closeout_and_receipts_system.md` | Scoped closeout, receipts, work records, deferral evidence |

---

## Rules

- One file per module or system surface.
- No packet status, dated audits, row counts, live source health, active position state, current rejection counts, or archive bodies.
- Time-bound claims must point to `docs/operations/current*.md` with freshness/expiry.
- Legacy module behavior must be clearly labeled diagnostic/history/rollback unless current code path proves active behavior.
- Graph appendices are derived-only and subordinate to source/manifests/tests.
