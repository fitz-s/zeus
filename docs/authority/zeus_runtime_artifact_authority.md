# Zeus Runtime Artifact Authority

Status: active durable authority law  
Scope: placement and lifecycle of runtime artifacts, live receipts, probes, generated reports, and temporary operational outputs  
Machine authority: `architecture/docs_plane_manifest.yaml`, `architecture/docs_registry.yaml`  
Freshness model: durable placement law. Runtime artifact contents are current facts and expire.

---

## 1. Core Law

All runtime artifacts belong to the Operations plane unless explicitly promoted through authority change control.

Runtime artifacts include:

- live daemon status, PID, loaded SHA, process command line;
- launchd inspection output;
- bankroll, collateral, allowance, position, order, fill, or venue snapshot;
- DB row counts, WAL size, lock state, or DB probe output;
- current source availability or provider health probes;
- current rejection/no-trade counts;
- smoke-test receipts;
- active packet logs and current evidence;
- generated diagnostics from live runtime.

None of these may live in `docs/authority/**` or `docs/reference/**` as present-tense facts.

---

## 2. Placement

Preferred tracked locations:

```text
docs/operations/current/evidence/
docs/operations/current/reports/
docs/operations/current/plans/
```

Runtime DBs, logs, and state files may remain under runtime state paths such as `state/**` or `logs/**`, but they are still Operations plane facts, not docs authority.

---

## 3. Current Pointer Rule

Only three operations files may summarize current facts for default boot:

- `docs/operations/current_state.md`
- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`

Each summary must name evidence, checked_at/observed_at, freshness/expiry, and stale behavior.

---

## 4. Promotion Rule

A runtime artifact can produce durable law only after:

1. the durable lesson is extracted;
2. current code/manifests/tests support it;
3. the lesson is rewritten into authority or reference;
4. the raw artifact remains operations/evidence/history;
5. registry and routers are updated.

Do not copy live snapshots into authority/reference.

---

## 5. Closeout Rule

When an operation closes:

- active runtime artifacts either remain under operations as current evidence until expiry, or move to evidence/report/archive;
- closed artifacts must not remain default-readable;
- any residual live work must be summarized in a current pointer or a new active operation, not a closed packet diary.
