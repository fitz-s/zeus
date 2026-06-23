# Zeus Change-Control Constitution

Version: 2026-06-23  
Status: durable deep-governance constitution  
Default-read: no  
Use when: authority rewrite, kernel-boundary change, anti-entropy review, packet design, long-horizon safety doctrine

This constitution explains why Zeus is governed this way. It does not define the trading strategy and it does not replace `docs/authority/zeus_current_architecture.md` or `docs/authority/zeus_current_delivery.md`.

---

## 0. Purpose / 目的

Zeus is a live-money prediction-market trading system. Its primary long-run failure is not lack of ideas; it is semantic drift: a future agent reads packet lore, dated consult material, or a local code seam as if it were durable law and then ships a locally plausible but systemically wrong patch.

This constitution exists to make that failure hard. It requires stable kernels, machine-checkable invariants, bounded packets, explicit truth ownership, and documentation isolation so that an imperfect agent cannot silently break the architecture.

---

## 1. Kernel Zones

Every change must classify the strongest zone it touches.

### K0 — Frozen Semantic Kernel

K0 includes:

- settlement contract semantics;
- family/bin/native-side identity;
- canonical lifecycle grammar;
- append-only event + deterministic projection truth;
- DB/table ownership;
- typed unit/probability/price/value boundaries;
- point-in-time truth and no-hindsight learning;
- `strategy_key` governance identity;
- command/idempotency and no-duplicate-submit boundaries.

K0 may evolve only with explicit authority/governance review, tests, and manifest updates.

### K1 — Governance And Protection

K1 includes RiskGuard, risk allocator, control-plane commands, posture, strategy gates, reconciliation semantics, and operator-control durability. K1 may change behavior, but it must not redefine K0.

### K2 — Runtime Product Layer

K2 includes orchestration, daemon wiring, monitor/exit scheduling, status summaries, ingest processes, and operational runbooks. K2 may move faster, but it must respect K0/K1 truth boundaries.

### K3 — Strategy/Signal Extension Layer

K3 includes forecast, calibration, probability, q-kernel, selection, feature, and analytics modules. K3 may innovate, but it cannot create new authority planes or bypass contract/execution/lifecycle law.

### K4 — Disposable/Experimental Layer

K4 includes notebooks, one-off scripts, raw reports, consult notes, PR reviews, and packet diaries. K4 is never default authority.

---

## 2. Constitutional Invariants

CONST-01: Exit intent is not economic close; economic close is not settlement.  
CONST-02: Canonical lifecycle truth is append-only event plus deterministic projection.  
CONST-03: `strategy_key` is the sole governance key.  
CONST-04: Point-in-time truth outranks hindsight snapshots.  
CONST-05: Missing, stale, partial, rate-limited, or degraded data is a first-class fact.  
CONST-06: Unit, price, probability, q, q_lcb, and native-side semantics must be protected by types/contracts/tests, not reviewer memory.  
CONST-07: Lifecycle phases can only come from the declared grammar.  
CONST-08: Risk/control must change evaluator, sizing, execution, cancellation, reduce-only, or exit behavior; logging-only risk is theater.  
CONST-09: Shadow persistence needs a deletion/demotion plan before it is added.  
CONST-10: LLM output, chat memory, raw consults, and packet closeouts are not authority.  
CONST-11: Docs authority and reference planes must not contain packet evidence or live current facts.  
CONST-12: No live-money candidate exists until contract truth, q authority, executable cost, side semantics, sizing, and risk gates agree.

---

## 3. Translation Discipline

Before coding, translate every proposal through three layers:

1. **Truth layer** — What becomes a stronger truth source? Which DB/table/manifest/code object owns it?
2. **Control layer** — What behavior changes at runtime? Entry, size, risk, submit, cancel, exit, settlement, or learning?
3. **Evidence layer** — How will tests, runtime receipts, manifests, or settlement-graded evidence prove it?

A requirement that cannot name these three layers is not ready for implementation.

---

## 4. Packet Discipline

A packet is the atomic execution unit, not durable authority. It must have one primary goal, bounded file scope, explicit invariants, required evidence, rollback/demotion rules, and closure criteria.

A packet is not complete merely because a commit exists. Completion requires:

- targeted tests/checks;
- affected-surface checks;
- evidence receipt;
- router/registry cleanup;
- explicit disposition for any scripts/docs/artifacts created;
- no stale packet route left in default boot.

After closeout, surviving durable law must be promoted to authority/reference. The packet itself becomes evidence/history and must not remain in default AGENTS/README/registry route.

---

## 5. Documentation Constitution

`docs/authority/**` is durable law.  
`docs/reference/**` is durable system/reference knowledge.  
`docs/operations/current*.md` is current fact with freshness/expiry.  
`docs/runbooks/**` is procedure.  
`docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, and `docs/rebuild/**` are evidence/history.

The default-read plane must be small, current, and safe for zero-context agents. Dated strategy-of-record documents, consult transcripts, PR reviews, raw debate logs, evidence packets, and active work diaries must never be authority merely because they contain important words.

If a historical source contains a correct durable rule, promote the rule and demote the source.

---

## 6. Review Discipline

Review must ask:

- Does this change cross K0/K1/K2/K3 boundaries?
- Did it add a second authority plane?
- Did it reintroduce legacy doctrine under a new name?
- Did it treat NO as a shortcut complement rather than native side?
- Did it use continuous weather intuition where settlement-bin semantics are required?
- Did it treat stale source, stale q, stale book, stale chain, or stale current facts as live truth?
- Did it leave evidence/packet/history in default routes?
- Did it update machine manifests and docs registry when routes changed?

A review that only checks local code style is insufficient for Zeus.

---

## 7. Machine-Checkable Enforcement

Machine manifests and tests are not decorative. The following classes must remain machine-checkable where possible:

- DB table ownership and split: `architecture/db_table_ownership.yaml`;
- invariants: `architecture/invariants.yaml`;
- negative constraints: `architecture/negative_constraints.yaml`;
- fatal misreads: `architecture/fatal_misreads.yaml`;
- runtime modes and posture: `architecture/runtime_modes.yaml`, `architecture/runtime_posture.yaml`;
- money-path object/state-machine registry: `architecture/money_path_objects.yaml`;
- docs classification and default-read routes: `architecture/docs_registry.yaml`;
- test topology: `architecture/test_topology.yaml`.

When prose and machine checks disagree, update the stale surface rather than ignoring the check.

---

## 8. Prohibited Illusions

A long prompt is not authority.  
A passed local test is not architecture proof.  
A packet closeout is not durable law.  
A report with strong wording is not current state.  
A default route to an evidence folder is not harmless.  
A backtest is not live authorization.  
A market quote is not settlement truth.  
A Polymarket NO token is not a casual `1 - YES` shortcut.  
A JSON export is not canonical DB truth.  
A graph/topology result is not semantic proof.

---

## 9. Relationship To Active Law

This constitution is retained because its anti-entropy doctrine is durable. It is not the first file to read for daily work. For current system semantics, use `docs/authority/zeus_current_architecture.md`. For current change-control and docs isolation rules, use `docs/authority/zeus_current_delivery.md`.
