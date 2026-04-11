# Zeus Current Architecture Law

Status: Active architecture authority
Scope: Present-tense semantics, truth ownership, lifecycle law, risk behavior, and zone boundaries

---

## 1. What this file is for

Read this file when you need the **current architectural law** for Zeus.

Use this file for:
- canonical truth ownership
- lifecycle semantics
- governance keys
- risk behavior
- zone boundaries
- current read/write authority

Do **not** use historical design packets or target-state specs to answer those questions first.

Current runtime work is routed by:
- `docs/operations/current_state.md` — current packet / branch truth
- `docs/known_gaps.md` — present-tense blockers and runtime gaps

---

## 2. Canonical truth model

### 2.1 Core truth rule

Zeus is a **position-managed trading runtime**. The canonical inner truth surface is the repo-owned state layer, not exports, chat summaries, or outer-host memory.

### 2.2 Truth hierarchy

```
Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache)
```

Reconciliation law:
1. Local + chain match → `SYNCED`
2. Local exists, not on chain → `VOID` immediately
3. Chain exists, not local → `QUARANTINE` and forced investigation / exit evaluation

### 2.3 DB authority

- The DB is the canonical repo truth surface.
- JSON / CSV outputs are derived exports only.
- Derived exports may support operators, audits, or compatibility paths, but they may not be promoted back into authority.
- If runtime compatibility code falls back from DB-backed truth to a legacy file surface, that is a **degraded state**, not equal authority.

### 2.4 Sole state authority

- Lifecycle state may be changed only through the lifecycle authority path.
- No math, execution, reporting, or helper surface may invent lifecycle state strings or bypass legal transitions.

---

## 3. Governance identity

### 3.1 Sole governance key

`strategy_key` is the sole governance key.

It is the key used for:
- attribution that affects behavior
- risk policy resolution
- learning / performance slicing
- operator controls that target a strategy

`edge_source`, `discovery_mode`, `entry_method`, scheduler mode, and similar fields are metadata, not governance centers.

### 3.2 No fallback governance

If exact attribution is missing, the system must fail, quarantine, or mark the record degraded. It must never invent a fallback governance bucket.

---

## 4. Lifecycle law

### 4.1 Lifecycle phases

The bounded lifecycle grammar is:

`pending_entry → active → day0_window → pending_exit → economically_closed → settled`

Terminal phases:
- `voided`
- `quarantined`
- `admin_closed`

Only enum-backed lifecycle phases are legal.

### 4.2 Critical distinctions

- Exit intent is not closure.
- Settlement is not exit.
- Quarantine is not a normal holding state.
- `day0_window` is a lifecycle phase with special monitoring semantics, not a discovery-mode synonym.

### 4.3 Event / projection contract

Canonical lifecycle truth is append-first:
- append domain event
- fold into deterministic current projection
- preserve both in the same DB authority model

Event append + current projection update must share one transaction boundary when that write path is used.

### 4.4 Missing-data truth

Data absence is not a silent skip. Missing, stale, unavailable, or rate-limited upstream inputs are first-class truth that must appear in diagnostics and learning surfaces.

---

## 5. Settlement law

Polymarket weather markets settle on integer Weather Underground-style daily highs.

Current settlement law:
- settlement semantics are typed contracts
- every settlement write must respect city/unit-specific semantics
- discrete settlement support outranks continuous intuition

Mandatory settlement concepts:
- `bin_contract_kind`
- `bin_settlement_cardinality`
- `settlement_support_geometry`

For the full domain rules, including Fahrenheit / Celsius / shoulder semantics, read `docs/reference/zeus_domain_model.md` §2.

---

## 6. Risk law

Risk levels must change behavior:

| Level | Required behavior |
|------|-------------------|
| `GREEN` | Normal operation |
| `YELLOW` | No new entries |
| `ORANGE` | No new entries; exit only at favorable prices |
| `RED` | Cancel all pending; exit all immediately |

Advisory-only risk is forbidden.

Overall level is fail-closed:
- max of all active levels wins
- computation error or broken truth input must not silently downgrade risk

---

## 7. Current read / write law

### 7.1 Write law

Current write authority belongs to repo-owned runtime surfaces only:
- state DB
- lifecycle authority path
- control-plane ingress that changes behavior through typed repo surfaces

Outer tools, docs, or supervisors may not directly mutate repo truth.

### 7.2 Read law

Allowed derived consumers include:
- operator status views
- audit scripts
- Venus-typed contract consumers
- reports and research artifacts

Those consumers may read derived surfaces, but may not upgrade themselves into authority.

---

## 8. Zone law

Zeus is partitioned into five zones:

| Zone | Meaning | Key dirs |
|------|---------|----------|
| `K0` | Kernel truth / lifecycle / contracts | `src/state/`, `src/contracts/` |
| `K1` | Protective behavior / control | `src/riskguard/`, `src/control/` |
| `K2` | Execution / supervisor contracts | `src/execution/`, `src/supervisor_api/` |
| `K3` | Math / data / strategy / engine | `src/signal/`, `src/calibration/`, `src/strategy/`, `src/engine/`, `src/data/` |
| `K4` | Observability / extension | `src/observability/`, `src/analysis/` |

Import rule: downward only. Lower-numbered zones may not import upward.

Special rule:
- K3 math code may not redefine K0/K1 lifecycle or governance semantics.

Machine-checkable source of truth:
- `architecture/zones.yaml`
- `architecture/kernel_manifest.yaml`

---

## 9. Hard architectural invariants

Current invariants enforced across the repo include:

- lifecycle is the only state authority
- `strategy_key` is the sole governance key
- DB is canonical truth
- point-in-time truth beats hindsight truth
- risk must change behavior
- settlement semantics are typed contracts
- lifecycle grammar is bounded
- no zone-boundary violations
- no ad hoc state strings
- no exact-attribution fallback guessing

Machine-checkable source of truth:
- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`

---

## 10. External boundary

- Zeus owns inner repo truth and architecture law.
- Venus may read typed contracts and derived status.
- OpenClaw may host workspace/runtime concerns and notifications.
- Neither Venus nor OpenClaw may become Zeus's truth writer or architectural authority.

See `docs/authority/zeus_openclaw_venus_delivery_boundary.md` for the narrow boundary law.

---

## 11. Required companion files

Read these when their domain applies:

- `docs/authority/zeus_packet_discipline.md` — packet closure, evidence, waivers, market-math requirements
- `docs/authority/zeus_autonomy_gates.md` — human gates, team restrictions, one-packet-at-a-time rule
- `docs/authority/zeus_openclaw_venus_delivery_boundary.md` — Zeus / Venus / OpenClaw boundary
- `docs/reference/zeus_domain_model.md` — probability chain and settlement semantics
- `docs/operations/current_state.md` — current branch / packet truth
- `docs/known_gaps.md` — present-tense runtime blockers
