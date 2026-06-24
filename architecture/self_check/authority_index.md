# Authority Index

Role: zero-context authority read index for high-risk Zeus work.

This index names the durable authority spine. It is not a substitute for source, tests, manifests, or scoped AGENTS files.

---

## Canonical Order

1. `AGENTS.md`
2. `workspace_map.md`
3. scoped `AGENTS.md` for touched files
4. `docs/authority/zeus_current_architecture.md`
5. `docs/authority/zeus_current_delivery.md`
6. `architecture/kernel_manifest.yaml`
7. `architecture/invariants.yaml`
8. `architecture/negative_constraints.yaml`
9. `architecture/db_table_ownership.yaml` when DB/table/source/runtime truth is involved
10. `architecture/money_path_objects.yaml` when economic objects, states, commands, or side effects are involved
11. `architecture/docs_registry.yaml` when docs/default-read/routing is involved
12. `docs/authority/ARCHIVAL_RULES.md` when demotion/archive/evidence isolation is involved
13. `docs/authority/zeus_change_control_constitution.md` when deep governance rationale applies
14. `docs/reference/zeus_prediction_market_quant_reference.md` for broad money-path reference
15. focused canonical reference routed by `docs/reference/AGENTS.md`

---

## Non-Authority Surfaces

These are not present-tense law:

- `docs/evidence/**`
- `docs/reports/**`
- `docs/archive/**`
- `docs/rebuild/**`
- closed `docs/operations/task_*`
- active package bodies under `docs/operations/current/**` unless explicitly current-routed
- graph/topology output
- generated plans

---

## Rule

Reference docs explain; manifests and code constrain. Reports and archives preserve evidence. Current-fact files expire. A packet can guide a task, but once closed it is evidence, not law.

---

## Current Topology Tools

Use route/topology tools as bounded context, not semantic proof. Minimum docs/authority validation commands remain:

```bash
python3 scripts/topology_doctor.py --strict
python3 scripts/topology_doctor.py --source
python3 scripts/topology_doctor.py --tests
python3 scripts/topology_doctor.py --fatal-misreads
```
