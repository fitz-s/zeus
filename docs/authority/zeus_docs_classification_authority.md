# Zeus Docs Classification Authority

Status: active durable authority law  
Scope: docs planes, default-read eligibility, authority/reference/current/evidence separation, and runtime artifact placement  
Machine authority: `architecture/docs_registry.yaml`, `architecture/docs_plane_manifest.yaml`, `architecture/reference_replacement.yaml`  
Freshness model: durable classification law. Individual current facts expire in their own surfaces.

---

## 1. Three-Plane Law

Every tracked or generated cognition artifact must map to exactly one top-level plane:

| Plane | Authority? | Default-read? | Contents |
|---|---:|---:|---|
| Authority | yes | task-required | durable law and machine-checkable law |
| Operations | no durable authority | pointer-only/current-task | runtime artifacts, current facts, active work, receipts, live evidence |
| Persistent special | no | routed only | references, runbooks, reports, evidence, archives, rebuild history, module books |

Detailed labels such as reference, runbook, report, evidence, archive, and module book still exist for indexing, but they roll up to these three planes.

---

## 2. Authority Plane

Authority is only:

- active files under `docs/authority/**` listed in `docs/authority/AGENTS.md` and `architecture/docs_registry.yaml`;
- machine authority manifests under `architecture/**`.

Reference is not authority. A reference can be excellent, complete, and required for cognition, but it explains law; it does not create law.

---

## 3. Operations Plane

Runtime artifacts belong in operations.

Examples:

- launchd/process inspection output;
- live bankroll or collateral snapshot;
- PID or loaded SHA;
- current open orders or positions;
- current rejection counts;
- DB row counts, WAL size, lock-holder observations;
- source health probe output;
- smoke-test receipts;
- active packet work logs and current evidence.

Allowed durable pointers:

- `docs/operations/current_state.md`
- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`

Active package bodies under `docs/operations/current/**` are operations plane but not default boot.

---

## 4. Persistent Special Plane

Persistent special files may be preserved but do not authorize runtime:

- `docs/reference/**`
- `docs/runbooks/**`
- `docs/evidence/**`
- `docs/reports/**`
- `docs/archive/**`
- `docs/rebuild/**`
- `docs/archive_registry.md`

They are search/discovery/explanation/procedure/history surfaces. If a durable rule is extracted from them, promote the rewritten rule to authority and demote the source.

---

## 5. Runtime Artifact Rule

All future runtime outputs produced by agents, scripts, daemons, smoke tests, DB probes, source checks, or live inspection must go to operations or runtime state, never to authority/reference.

Preferred tracked home for human-readable runtime receipts:

```text
docs/operations/current/evidence/
docs/operations/current/reports/
```

If a runtime artifact must be retained after closeout, move it to evidence/report/archive and register it. Do not leave it in default boot.

---

## 6. Promotion Rule

To promote material out of operations or persistent special:

1. extract the durable rule;
2. prove it against current code/manifests/tests/runtime evidence;
3. rewrite it into active authority or canonical reference;
4. update registry and routers;
5. demote or archive the source.

Never promote a raw packet, raw consult, raw report, current runtime snapshot, or dated audit wholesale.
