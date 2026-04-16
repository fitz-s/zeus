# Topology Enforcement Hardening

Date: 2026-04-15
Branch: data-improve
Program: Zeus Topology Compiler and Recurrence-Proof Program

## Goal

Close the remaining intent-to-enforcement gaps:

1. eliminate topology-dark report write targets
2. compile closeout into one machine-readable lane
3. require route receipts for high-risk changes
4. add evidence-budget telemetry for workbook/report surfaces

## Phases

### Phase 1 — Reports Surface Normalization

- Formalize `docs/reports/` as an explicit active evidence-report subroot
- Route diagnostic report writers through a topology-known docs surface
- Add docs/map/artifact lifecycle ownership for report outputs

### Phase 2 — Closeout Compiler

- Add `topology_doctor closeout`
- Compile planning-lock, work-record, map-maintenance, artifact-lifecycle,
  docs, and changed-file-sensitive source/tests/scripts/data-rebuild checks into
  one machine-readable result

### Phase 3 — Change Receipt Contract

- Add `architecture/change_receipt_schema.yaml`
- Add change receipt checker and `--receipt-path`
- Require receipts for high-risk surfaces

### Phase 4 — Evidence Budget And Compiler Telemetry

- Add advisory budgets for docs evidence/report surfaces
- Expose dark-target, route-health, and evidence-growth telemetry in compiled
  topology and closeout outputs

## Constraints

- Keep `topology_doctor.py` the only runnable topology compiler entrypoint
- Helper modules remain `DO_NOT_RUN`
- Preserve existing CLI behavior outside the new explicit closeout/receipt lanes
- Avoid touching unrelated runtime artifacts in `state/`

## Required closeout

1. `python scripts/topology_doctor.py closeout --changed-files <files> --plan-evidence docs/operations/task_2026-04-15_topology_enforcement_hardening/plan.md --work-record-path docs/operations/task_2026-04-15_topology_enforcement_hardening/work_log.md --receipt-path docs/operations/task_2026-04-15_topology_enforcement_hardening/receipt.json --summary-only`
2. `pytest -q tests/test_topology_doctor.py`
3. third-party critic + verifier review before packet close
