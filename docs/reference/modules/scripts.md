# Scripts System Authority Book

**Recommended repo path:** `docs/reference/modules/scripts.md`
**Current code path:** `scripts`
**Authority status:** Dense system reference for top-level scripts as tools, not hidden authority surfaces.

## 1. Module purpose
Explain the lifecycle, classes, and risk boundaries of the top-level scripts layer, which now spans topology enforcement, diagnostics, repairs, migrations, ETL writers, audits, and operator support.

## 2. What this module is not
- Not a second codebase with its own unstated law.
- Not a safe place for one-off repairs to persist forever.
- Not a substitute for runtime modules or tests.

## 3. Domain model
- Enforcement scripts (topology doctor and related checks).
- Diagnostic and diagnostic-report writers.
- Runtime support wrappers.
- ETL/backfill writers.
- Repair/config/deprecated-fail-closed tools.

## 4. Runtime role
Scripts either enforce repo discipline, perform bounded operational actions, or execute packet-approved data/schema work.

## 5. Authority role
Scripts may enforce or manipulate truth, but they are never themselves a constitutional authority center. Their legitimacy comes from manifests, law, packet evidence, and tests.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `scripts/AGENTS.md` and `architecture/script_manifest.yaml`
- `docs/authority/zeus_current_delivery.md` on packet doctrine and script classes
- `scripts/topology_doctor.py` family and associated tests for enforcement behavior

### Non-authority surfaces
- Unknown historical scripts retained only because they were never classified
- Outputs of diagnostic_report_writer scripts
- Packet-local scratch scripts after packet close

## 7. Public interfaces
- CLI entrypoints for topology doctor and durable long-lived scripts
- Dry-run/apply interfaces for ETL/repair scripts

## 8. Internal seams
- Long-lived vs packet-ephemeral scripts
- Diagnostic stdout-only tools vs writers
- Repair/apply tools vs topology enforcement

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `topology_doctor.py and helpers` | Repo integrity, routing, map maintenance, context, graph protocol support. |
| `backfill / audit / migration scripts` | Operational ETL and repair family; must be manifest-classified. |
| `runtime support scripts` | Heartbeat, daemon, and operational wrappers. |
| `code_review_graph_mcp_readonly.py` | Zeus-safe graph facade. |

## 10. Relevant tests
- tests/test_topology_doctor.py
- tests/test_backfill_openmeteo_previous_runs.py
- tests/test_etl_forecasts_v2_from_legacy.py
- tests/test_backfill_scripts_match_live_config.py

## 11. Invariants
- Every durable top-level script must be manifest-registered.
- Writers/repairs must declare targets, dry-run/apply semantics, and lifecycle.
- Scripts are not allowed to become hidden authority centers.

## 12. Negative constraints
- Do not leave packet-ephemeral scripts unclassified after packet close.
- Do not let diagnostic scripts write canonical DB truth.
- Do not let stale scripts linger in active runbooks/manifests.

## 13. Known failure modes
- One-off scripts become shadow production tools.
- Repair scripts outlive their packet and silently mutate truth later.
- Manifest too thin to prevent unsafe script discovery/execution.

## 14. Historical failures and lessons
- [Archive evidence] The archive contains many one-off packets, migrations, and scratch tools; without lifecycle discipline, the scripts layer becomes a shadow architecture.

## 15. Code graph high-impact nodes
- `scripts/topology_doctor.py` and companions are current script hubs.
- Backfill/migration families are high-risk write surfaces even when rarely invoked.

## 16. Likely modification routes
- New long-lived script: register in manifest, source rationale, tests, and docs in same packet.
- Retiring a script: mark deprecated/fail-closed and remove active references before archive.

## 17. Planning-lock triggers
- Any change to topology doctor, long-lived repair/ETL writers, or script lifecycle classification.
- Any packet that adds more than one new top-level script or changes write targets.

## 18. Common false assumptions
- A script is harmless because it is not imported by runtime modules.
- Old scripts are safe evidence because they once worked.
- Diagnostic output can stand in for canonical truth.

## 19. Do-not-change-without-checking list
- topology_doctor behavior without matching tests
- Dry-run/apply semantics of repair/ETL writers without manifest update

## 20. Verification commands
```bash
pytest -q tests/test_topology_doctor.py tests/test_backfill_scripts_match_live_config.py
python -m py_compile scripts/*.py
python scripts/topology_doctor.py --scripts --json
```

## 21. Rollback strategy
Rollback script packets together with manifest/test updates. For write scripts, also rollback or quarantine any side effects.

## 22. Open questions
- How many current top-level scripts are still underclassified because `script_manifest.yaml` is too compressed?

## 23. Future expansion notes
- Expand `architecture/script_manifest.yaml` into a real machine catalog with owner module, write targets, and retirement policy.

## 24. Rehydration judgement
This book is the dense reference layer for scripts. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
