# Zeus Current Delivery Law

Status: active delivery authority
Scope: authority order, boot order, planning lock, packet doctrine, autonomy limits, demotion/promotion hygiene, completion evidence

---

## 1. Purpose

This file defines how work is allowed to happen in Zeus:

- who outranks whom
- how agents boot into the right mental model
- when planning lock applies
- how packets are closed or reopened
- what autonomy may and may not do
- how authority surfaces stay clean

It is the single default entrypoint for delivery/change-control law.

---

## 2. Authority Order

Use this order when deciding what may land:

1. executable source, tests, DB/event/projection truth
2. machine manifests under `architecture/**`
3. `docs/authority/zeus_current_architecture.md`
4. this file
5. `docs/authority/zeus_change_control_constitution.md` for deep governance
6. `docs/operations/current_state.md`
7. task-profile-required current-fact surfaces
8. durable reference
9. derived context
10. reports, artifacts, packets, archive/history evidence

Chat memory, hook behavior, outer-host memory, generated plans, graph output,
reports, and archives never outrank repo law.

---

## 3. Default Boot Order

For every non-trivial task:

1. `AGENTS.md`
2. `workspace_map.md`
3. `docs/authority/zeus_current_architecture.md`
4. classify task class
5. `architecture/task_boot_profiles.yaml`
6. `architecture/fatal_misreads.yaml`
7. `architecture/city_truth_contract.yaml` if city/source/date/settlement/
   hourly/Day0/calibration truth is involved
8. `docs/operations/current_state.md`
9. profile-required current-fact surfaces, if fresh enough
10. scoped `AGENTS.md`
11. targeted code/tests
12. graph/topology Stage 2

For governance or docs-authority work, also read:

- this file
- `architecture/docs_registry.yaml`
- `architecture/map_maintenance.yaml`

---

## 4. Change Classes

Classify before editing:

- **Math**: formulas, thresholds, calibration, signal, feature logic inside
  existing semantics.
- **Architecture**: lifecycle grammar, truth ownership, transaction boundaries,
  canonical read/write paths, zone boundaries, source/settlement semantics.
- **Governance**: AGENTS surfaces, authority docs, constitutions, demotion,
  promotion, docs registry, evidence burden.
- **Schema / truth-contract**: migrations, DB truth contracts, supervisor
  contracts, control-plane semantics.

If a task touches lifecycle truth, `strategy_key`, DB authority, settlement
semantics, control-plane behavior, authority order, or cross-zone boundaries,
treat it as architecture/governance even if the diff is small.

---

## 5. Planning Lock

Plan before touching:

- `docs/authority/**`
- `architecture/**`
- `.github/workflows/**`
- `src/state/**` truth ownership, schema, projection, lifecycle write paths
- `src/control/**`
- `src/supervisor_api/**`
- cross-zone work
- more than 4 files
- any authority order / doc-class / demotion / promotion change
- any current truth / lifecycle / governance / control / schema boundary

Machine check:

`python scripts/topology_doctor.py --planning-lock --changed-files <files...> --plan-evidence <plan file>`

No broad autopilot, no packet-less architecture editing, and no authority
rewrite by momentum.

---

## 6. Packet Doctrine

### 6.1 Atomic Unit

A packet is the atomic authority-bearing unit of execution:

- program > packet > execution slice
- a completed slice is not a completed packet
- a commit is not closeout

Continue autonomously only while:

- the same packet remains open
- the next slice is clear
- no new authority/risk boundary is crossed
- no contradiction appears

Stop when the packet is complete, scope widens, a new packet/phase would start,
or evidence burden cannot be met.

### 6.2 Closure And Reopen

Packet acceptance is defeasible by later repo-truth contradiction.

Do not claim done unless all are true:

1. targeted evidence passed
2. broader affected-surface checks passed
3. runtime semantics converge with the claim

If later repo truth contradicts a closeout claim, reopen explicitly.

### 6.3 Evidence Visibility

Every required evidence item must appear in the packet, work log, receipt, or a
clearly referenced evidence surface. Chat memory and implied knowledge are not
evidence.

### 6.4 Capability Present / Absent

When behavior depends on a capability being present, acceptance must prove:

- capability-present behavior
- capability-absent behavior

If absent behavior is advisory skip, fail-loud, or staged no-op, say so and
test or record it directly.

### 6.5 Waivers

A gate may be waived only when:

- it is explicitly staged/advisory by current law, or
- it is externally unavailable for a recorded reason

Convenience is never a waiver reason.

---

## 7. Review And Closeout Gates

Before phase or packet closeout:

1. Run targeted tests/gates.
2. Run broader affected-surface checks.
3. Run adversarial Critic review.
4. Run Verifier review.
5. Record changed files, evidence, residual risks, and rollback/disposition.

After a packet is accepted/pushed:

1. Run one additional third-party Critic review.
2. Run one additional Verifier pass.
3. If either finds contradiction, stale control state, or evidence gap, reopen
   or repair before advancing.

Treat post-close success as separate advancement permission.

---

## 8. Autonomy Limits

### 8.1 Always Human-Gated

- live cutover timing
- irreversible migration/cutover switches
- archive/delete/demotion actions that change the active law stack
- permanent risk re-enable after a safety-triggered pause
- expansion of command vocabulary that affects live control

### 8.2 Team Mode

Team mode is allowed only when:

- there is an approved packet
- work is parallelizable
- one owner remains accountable
- team members are not redefining authority

Do not teamize:

- `architecture/**`
- `docs/authority/**`
- migration cutover decisions
- supervisor/control-plane semantics
- packet-less exploratory rewrites

One frozen packet at a time. Serialize writes to the same file.

---

## 9. Authority Hygiene

### 9.1 `docs/authority/` Is Durable Law Only

Do not leave these in authority:

- `task_YYYY-MM-DD_*`
- `*_adr.md`
- fix-pack notes
- packet-scoped rollback doctrine
- dated boundary supplements
- one-off constitutions

If useful, move them to operations packet evidence, reports history, or archive
interfaces.

### 9.2 Current Facts Are Not Law

`current_state.md`, `current_data_state.md`, and
`current_source_validity.md` are time-bound planning surfaces. They must be:

- receipt-backed
- expiry-bound
- summary-only
- fail-closed when stale

### 9.3 Reference Is Not Current Truth

`docs/reference/` carries durable descriptive understanding. Current row
counts, provider status, packet status, and dated audits belong elsewhere.

### 9.4 Derived Context Does Not Rise

Graph, topology, digests, context packs, and source rationale help routing and
review. They do not replace authority, current facts, receipts, manifests, or
tests.

---

## 10. Demotion And Promotion

Promote a rule to durable authority only if it is:

- no longer packet-scoped
- no longer date-scoped
- applicable to future similar work
- backed by executable truth, manifests, tests, or operator reality

Demote a file when it:

- only applied to a past packet
- has been absorbed into core law or manifests
- is ADR/fix-pack/rollback residue
- is better kept as historical evidence

Demotion is not deletion. It preserves history while cleaning the active law
surface.

---

## 11. Market-Math And Settlement Packets

Any packet touching market math or settlement semantics must state:

1. domain assumptions
2. authority source for each assumption
3. invalidation condition if false

Review must verify:

- bin contract kind
- settlement cardinality
- shoulder semantics
- discrete support semantics
- source/date/unit/track proof

---

## 12. External Boundary

Zeus owns repo law and inner truth.

Venus may read derived status and typed contracts and may issue narrow ingress
commands. OpenClaw may host workspace/runtime support, memory injection,
notifications, and gateway routing.

Outer hosts may not silently mutate Zeus authority, schema, DB truth, or
control semantics.

---

## 13. Completion Protocol

Before calling work complete:

1. verify persistence of code/docs changes
2. run relevant checks
3. update maps/registries for moved/added/deleted files
4. confirm no moved historical file remains active authority
5. confirm current_state points at the active packet and receipt
6. confirm no new parallel law surface was created
7. record remaining risks honestly

### 13.1 Script Disposal

If a packet adds, modifies, runs, deprecates, or moves a top-level script, its
closeout must name the script disposition:

- deleted
- promoted to long-lived script
- promotion candidate with owner and deadline
- packet-ephemeral with delete-by date
- deprecated fail-closed with canonical `DO_NOT_RUN`

Do not close a packet with anonymous one-off scripts left in `scripts/`.

---

## 14. Relationship To Other Files

- `docs/authority/zeus_current_architecture.md`: how Zeus operates.
- `docs/authority/zeus_change_control_constitution.md`: deep anti-entropy
  rationale, not default boot.
- `docs/operations/current_state.md`: current active packet pointer.
- `architecture/docs_registry.yaml`: machine-readable docs classification.
