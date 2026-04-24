# Closeout and Receipts System Expanded Reference Draft

Status: Proposed expansion. Reference-only cognition.

## 1. Module purpose

The closeout and receipts system ensures packet work closes with relevant evidence, scoped gates, companion updates, and durable current-state/work-record visibility.

## 2. What this module is not

- Not a full strict repo-health audit.
- Not permission to ignore global drift.
- Not runtime behavior proof.
- Not a substitute for reviewer judgment.

## 3. Domain model

- changed files,
- required companions,
- selected lanes,
- always-on closeout lanes,
- planning lock evidence,
- work record,
- change receipt,
- current_state binding,
- deferrals,
- global health sidecar.

## 4. Runtime role

No runtime mutation. It governs packet closure.

## 5. Authority role

Closeout enforces route/work evidence requirements. It does not create semantic truth.

## 6. Read/write surfaces and canonical truth

| Surface | Role |
|---|---|
| `scripts/topology_doctor_closeout.py` | Closeout compilation |
| `architecture/map_maintenance.yaml` | Companion rules |
| `architecture/artifact_lifecycle.yaml` | Work record contract |
| `architecture/change_receipt_schema.yaml` | Receipt schema |
| `docs/operations/current_state.md` | Live control pointer |
| `docs/operations/task_*/work_log.md` | Preferred work record |
| `docs/operations/task_*/receipt.json` | Packet receipt |
| `docs/authority/zeus_current_delivery.md` | Packet/closeout doctrine |
| `tests/test_topology_doctor.py` | Regression tests |

## 7. Public interfaces

```bash
python scripts/topology_doctor.py closeout --changed-files <files> --summary-only
python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <plan> --json
python scripts/topology_doctor.py --change-receipts --json
python scripts/topology_doctor.py --work-record --json
```

## 8. Internal seams

### Changed-file gate vs global health

Closeout should block only packet-relevant issues. Global health should be reported separately.

### Companion rules vs broad docs rewrite

Companions are explicit manifest/registry moves, not an invitation to rewrite broad docs.

### Deferral vs hiding

A deferral must be explicit in receipt/work record and must not relabel a real blocker as clean.

## 9. Source files and roles

See section 6.

## 10. Relevant tests

- changed-file lane selection,
- companion missing behavior,
- unrelated drift not blocking,
- current_state receipt binding,
- work-record required fields,
- planning-lock triggers,
- graph stale advisory behavior.

## 11. Invariants

- Changed source requires source rationale companion.
- Changed test requires test topology companion.
- Changed script requires script manifest companion.
- Changed module book requires docs registry and module manifest companion.
- Architecture changes require planning lock.
- Current_state changes require receipt binding.
- Global health remains visible.

## 12. Negative constraints

- Do not close with hidden companion drift.
- Do not block packet closeout on unrelated global drift.
- Do not use reports/artifacts as authority proof.
- Do not mutate runtime/source behavior during topology closeout.

## 13. Known failure modes

- Overblocking from global health.
- Underblocking because companion not expanded.
- Receipt missing changed current_state.
- Active packet invisible from current_state.
- Work log absent or too thin.

## 14. Historical lessons

Packet work becomes unsafe when active context lives only in local scratch or unregistered docs. The closeout system makes current work visible without making packet plans durable law.

## 15. Graph high-impact nodes

Graph can suggest impacted tests/review order during closeout, but cannot waive required gates.

## 16. Likely modification routes

- Change closeout policy: topology_doctor_closeout + tests + topology_doctor_system book.
- Change receipt schema: architecture schema + current_delivery + tests.
- Change current_state binding: docs_system + closeout book + docs checks.

## 17. Planning-lock triggers

- Any change to closeout blocking policy.
- Any change to receipt schema/current_state role.
- Any architecture manifest change.

## 18. Common false assumptions

- “Closeout pass means strict health pass.” False.
- “A warning can be ignored silently.” False; deferrals must be explicit.
- “Receipt evidence is authority.” No, it is packet evidence.
- “Graph stale blocks closeout.” Only if the packet explicitly requires graph evidence.

## 19. Do-not-change-without-checking list

- change receipt schema,
- current_state labels,
- map maintenance companions,
- planning lock rules,
- work-record minimum fields.

## 20. Verification commands

```bash
python scripts/topology_doctor.py closeout --changed-files <files> --summary-only
python scripts/topology_doctor.py --planning-lock --changed-files <files> --json
python scripts/topology_doctor.py --change-receipts --json
python scripts/topology_doctor.py --work-record --json
pytest -q tests/test_topology_doctor.py -k "closeout or receipt or planning"
```

## 21. Rollback strategy

Rollback closeout policy and tests together. Do not leave receipts/current_state rules inconsistent.

## 22. Open questions

- How should explicit deferrals be represented in receipt JSON?
- Should closeout show top global health warnings by default?

## 23. Future expansion notes

Add deferral examples and typed issue closeout policy examples after P0/P1.

## 24. Rehydration judgment

This book should make packet closure obligations visible without turning closeout into global strict health.
