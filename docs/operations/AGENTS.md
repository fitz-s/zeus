# docs/operations AGENTS

Operations is the current-pointer and active-work surface for Zeus. It is not a second authority plane.

Authority still lives in source, tests, machine manifests, DB/runtime receipts, and `docs/authority/**`. Operations files may describe current facts only while fresh and evidence-backed.

---

## 1. Default-Readable Operations Files

Only these operations files may be read by default when a task needs current facts:

| File | Class | Purpose |
|---|---|---|
| `current_state.md` | current pointer | active program/packet pointer, required evidence, next action |
| `current_data_state.md` | current fact pointer | audited data/DB/source posture summary |
| `current_source_validity.md` | current fact pointer | audited settlement/source-validity posture summary |

They must stay thin and must carry evidence, freshness, and expiry semantics. If stale or unverifiable, treat the relevant fact as unknown.

---

## 2. Non-Default Surfaces

Do not recursively default-read:

- `current/**` package bodies;
- `task_*/**` packet folders;
- packet-local `evidence.md`, `findings.md`, `work_log.md`, or `receipt.json`;
- top-level historical reports;
- closed or superseded package inputs;
- monitoring/evidence folders unless the task explicitly routes there.

These files are evidence/work context only. They do not authorize architecture or present-tense runtime behavior.

---

## 3. Current-Fact Requirements

A current-fact file must state:

- Status;
- Last audited / checked_at;
- evidence packet or receipt path;
- max staleness or expiry rule;
- stale do-not-use policy;
- refresh trigger;
- owner/source of truth.

Do not update current facts from memory or old logs. Do not copy runtime state into authority/reference.

---

## 4. Active Work Home

All planning, goals, and ongoing-operation files must live under `docs/operations/current/`. Nothing goes in repo root or local scratch directories as durable work evidence.

| Artifact | Canonical location |
|---|---|
| active session goal | `docs/operations/current/GOAL.md` |
| active task ledger | `docs/operations/current/task.md` |
| package manifest | `docs/operations/current/package.yaml` |
| plans | `docs/operations/current/plans/<name>.md` |
| scope sidecars | `docs/operations/current/plans/<name>/scope.yaml` |
| evidence/reports | `docs/operations/current/evidence/`, `docs/operations/current/reports/` |

The directory is an active work home, not a default boot corpus.

---

## 5. Packet Closeout And Demotion

Closed/superseded packet material must be moved to archive/report/evidence and indexed. Do not leave completed packet folders in the default operations route.

Closeout requires:

1. promote surviving durable law into authority/reference;
2. move or demote packet bodies;
3. update `docs/archive_registry.md`;
4. remove active pointers;
5. update `architecture/docs_registry.yaml` and affected routers;
6. record unresolved work in a current pointer or known-gap surface, not in a closed packet diary.

Operator-only closeout is required only where a packet itself says it is waiting for operator action or carries an active runtime-gating artifact.

---

## 6. File Registry

| File/path | Class |
|---|---|
| `AGENTS.md` | operations router |
| `current_state.md` | current pointer |
| `current_data_state.md` | current fact pointer |
| `current_source_validity.md` | current fact pointer |
| `current/` | active work home; non-default body |
| `packet_scope_protocol.md` | procedural support |
| `known_gaps.md` | compatibility pointer to known-gap/worklist surfaces |
| `INDEX.md`, `POLICY.md`, run-state handoffs | operational support; not authority |
| `task_*` | packet evidence unless explicitly current-active |
| monitoring/evidence subdirs | evidence/monitoring, non-default |

Archived packet evidence is listed through `docs/archive_registry.md` or archive indexes. Do not re-list closed packet bodies here as active work.

---

## 7. Rules

- Operations docs do not authorize architecture.
- Current facts expire and must stop being used when stale.
- Do not leave completed or superseded packet material in active routes.
- Do not create packet evidence just to make a direct task look formal.
- For direct work, keep feedback/lessons in the final response unless an active packet requires a work record.
- Separate changed-surface validation failures from repo-wide pre-existing drift.
