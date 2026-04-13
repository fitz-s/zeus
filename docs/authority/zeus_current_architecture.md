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

The canonical file-level zone map lives in `architecture/zones.yaml`. This section is a human reminder only and must not be used as a competing ownership table.

Practical reading guidance:
- treat `src/state` as a mixed navigation cluster when reading docs
- consult `architecture/zones.yaml` for the file-level split before editing any state-adjacent file
- use package `AGENTS.md` files as local navigation, not authority

Import rule: follow the canonical directionality encoded in `architecture/zones.yaml`.

Zone boundary enforcement:
- K3 math code may not redefine K0/K1 lifecycle or governance semantics (INV-07 / FM-01)
- No broad prompt may edit K0 and K3 in the same patch without packet justification (FM-01)
- K4 code must not be imported into K0/K1/K2 without an explicit promotion packet
- Cross-zone edits trigger planning lock regardless of zone sensitivity

Machine-checkable source of truth:
- `architecture/zones.yaml`
- `architecture/kernel_manifest.yaml`

---

## 9. Hard architectural invariants

Every invariant exists because violating it has already caused bugs, data corruption, or P&L loss — or creates conditions under which those become undetectable.

| ID | Rule | WHY |
|----|------|-----|
| INV-01 | Exit is not local close | Monitor decisions must produce `EXIT_INTENT`, not directly terminalize positions. Direct close from orchestration conflates intent with execution, making exit audit and retry impossible. |
| INV-02 | Settlement is not exit | Exit execution and final market settlement are distinct lifecycle facts. Conflating them creates false P&L and breaks the reconciliation chain. |
| INV-03 | Canonical authority is append-first | Durable audit plus fast current-state reads require one canonical event stream (`position_events`) and one deterministic projection (`position_current`). |
| INV-04 | `strategy_key` is the sole governance key | `edge_source`, `discovery_mode`, `entry_method`, and scheduler mode are metadata, not governance. Multiple governance keys create attribution drift that compounds across learning, risk, and analytics. |
| INV-05 | Risk must change behavior | Advisory-only risk outputs are theater. If a risk command cannot alter evaluator/sizing/execution outcome, it provides false safety assurance. |
| INV-06 | Point-in-time truth beats hindsight truth | Learning must preserve what was knowable at decision time. Silent upgrade to later snapshots creates lookahead bias that makes backtests unreplicable. |
| INV-07 | Lifecycle grammar is finite | Arbitrary state strings create semantic drift. States exist only if they change governance, execution, or reconciliation semantics. |
| INV-08 | Every write path has one transaction boundary | Event append and projection update must succeed or fail together. Split writes create ghost states where the event log and projection disagree. |
| INV-09 | Missing data is first-class truth | Opportunity loss and degraded reliability must be explicit facts, not log noise. Learning derived only from available cases overestimates system coherence. |
| INV-10 | LLM output is never authority | Spec, invariants, tests, and evidence are authority. Generated code is only valid after packet, gates, and evidence bundle. |

### Aspirational invariants (not yet enforced in code)

These were identified as necessary for live trading but are not yet implemented. They are architectural law in intent, pending code realization.

| ID | Rule | WHY |
|----|------|-----|
| INV-11 | External assumptions are explicit and verified | Every assumption about external market reality (fees, tick size, settlement source, protocol timing, resolution rules) must be a typed contract with TTL, verification method, and criticality. Silent assumption drift is forbidden — hardcoded numeric values mirroring an external fact are INV-11 violations unless wrapped in a contract. |
| INV-12 | Cross-layer probability/edge carries provenance | Floats crossing signal→strategy, strategy→execution, or execution→settlement boundaries must carry a typed wrapper declaring: optimization target (accuracy vs EV), source (model vs market vs blended), confidence bound, and whether external costs (fee/slippage/vig) have been deducted. A bare `float` at a cross-layer seam is an INV-12 violation. |
| INV-13 | Unregistered numeric constants in Kelly-class multiplicative cascades are forbidden | Any adjustment to α, Kelly multiplier, edge threshold, or sizing fraction must be registered with: declared optimization target, data basis, replacement criteria. An unregistered constant in a cascade product is neutralized (weight=1.0) until registered. |

Machine-checkable source of truth:
- `architecture/invariants.yaml` — INV-01 through INV-10 (enforced)
- `architecture/negative_constraints.yaml` — NC-01 through NC-10 (enforced)

---

## 10. Forbidden moves (negative constraints)

Every FM maps to at least one enforcement mechanism: schema constraint, AST/semgrep rule, import-boundary check, invariant test, or packet-review rejection.

| ID | Forbidden pattern | Enforcement |
|----|-------------------|-------------|
| FM-01 | No broad prompt may edit K0 and K3 in the same patch without explicit packet justification | Packet review (NC-01) |
| FM-02 | No JSON surface may be promoted back to authority (`positions.json`, `status_summary.json`, `strategy_tracker.json`) | semgrep `zeus-no-json-authority-write` (NC-02) |
| FM-03 | No downstream strategy fallback or re-inference when `strategy_key` is already available | semgrep `zeus-no-strategy-default-fallback` (NC-03) |
| FM-04 | No direct lifecycle terminalization from orchestration code (`close_position()`, `void_position()` from engine) | semgrep `zeus-no-direct-close-from-engine` (NC-04) |
| FM-05 | No silent fallback from missing decision snapshot to latest snapshot for learning truth | Test + forbidden_patterns.md (NC-05) |
| FM-06 | No memory-only runtime control state representing durable policy | semgrep `zeus-no-memory-only-control-state` (NC-06) |
| FM-07 | No raw phase/state string assignment outside lifecycle fold/manager/projection | semgrep `zeus-no-direct-phase-assignment` (NC-07) |
| FM-08 | No bare implicit unit assumptions (`F` default, `C` default) in semantic code paths | semgrep + test (NC-08) |
| FM-09 | No ad hoc probability complements across architecture boundaries when semantic contracts exist | semgrep `zeus-no-ad-hoc-probability-complement` (NC-09) |
| FM-10 | No new shadow persistence surface without explicit deletion or demotion plan | Packet review (NC-10) |

Machine-checkable source of truth: `architecture/negative_constraints.yaml`
Human-readable map: `architecture/ast_rules/forbidden_patterns.md`

---

## 11. External boundary

- Zeus owns inner repo truth and architecture law.
- Venus may read typed contracts and derived status.
- OpenClaw may host workspace/runtime concerns and notifications.
- Neither Venus nor OpenClaw may become Zeus's truth writer or architectural authority.

See `docs/authority/zeus_openclaw_venus_delivery_boundary.md` for the narrow boundary law.

---

## 12. Required companion files

Read these when their domain applies:

- `docs/authority/zeus_packet_discipline.md` — packet closure, evidence, waivers, market-math requirements
- `docs/authority/zeus_autonomy_gates.md` — human gates, team restrictions, one-packet-at-a-time rule
- `docs/authority/zeus_openclaw_venus_delivery_boundary.md` — Zeus / Venus / OpenClaw boundary
- `docs/reference/zeus_domain_model.md` — probability chain and settlement semantics
- `docs/operations/current_state.md` — current branch / packet truth
- `docs/known_gaps.md` — present-tense runtime blockers
