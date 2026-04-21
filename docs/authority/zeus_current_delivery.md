# Zeus Current Delivery Law

Status: Active delivery authority
Scope: Present-tense change control, packet discipline routing, autonomy limits, and completion evidence

---

## 1. What this file is for

Read this file when you need to know **how work is allowed to happen now**.

This file is the current delivery entrypoint. It replaces historical constitutions and decision logs in the default read path.

---

## 2. Authority precedence for delivery

When deciding what may land, use this order:

1. **Code, tests, and runtime behavior** — what the system actually does is the highest authority. If code and docs disagree, trust code, question docs.
2. **Machine-checkable manifests** (`architecture/*.yaml`, kernel manifest, invariants, zones, negative constraints)
3. **Current authority docs** in `docs/authority/`
4. **Operations pointer** (`docs/operations/current_state.md`)
5. **Current blockers** (`docs/operations/known_gaps.md`)
6. **Historical archives** for rationale only — never for current truth

Chat memory, hook behavior, outer-host memory, and convenience summaries never outrank repo law.

### 2.1 Current-phase rule

Zeus is live-only. Paper mode was decommissioned in Phase 1. Any code, test, doc, or field that presupposes paper as a peer mode is a violation, not technical debt. The live/backtest/shadow boundary is defined in `docs/authority/zeus_live_backtest_shadow_boundary.md`.

---

## 3. Change classes

Every task must be classified before editing:

- **Math** — formulas, thresholds, calibration, signal, feature logic inside existing semantics
- **Architecture** — lifecycle grammar, truth ownership, transaction boundaries, canonical read/write paths, zone boundaries
- **Governance** — AGENTS surfaces, constitutions, decision registers, authority docs, demotion rules, evidence burden
- **Schema / truth-contract** — migrations, DB truth contracts, supervisor contracts, control-plane semantics

If a task touches lifecycle truth, `strategy_key`, DB authority, settlement semantics, control-plane behavior, or cross-zone boundaries, treat it as architecture/governance even if code changes look small.

---

## 4. Planning lock

Planning lock is mandatory before work that:

- touches `K0` or `K1`
- touches `architecture/**`
- changes schema / migration / replay / parity behavior
- changes control-plane or supervisor contracts
- edits AGENTS, constitutions, decision docs, or authority docs
- crosses zones
- edits more than 4 files
- claims to change what surface is authoritative

No broad autopilot, no packet-less architecture editing, no authority rewrite by momentum.

---

## 5. Packet doctrine

### 5.1 Atomic unit

A packet is the atomic authority-bearing unit of execution.

- program > packet > execution slice
- a completed slice is not a completed packet
- continue autonomously only while staying inside the same approved packet and risk boundary

### 5.2 Must stop when

- packet is complete
- next step widens scope
- next step changes packet / phase / zone risk
- contradiction appears
- evidence burden cannot be met

### 5.3 Packet closeout standard

Do not claim done unless all three are true:
1. targeted evidence passed
2. broader affected-surface checks passed
3. bottom-layer runtime semantics actually converge with the claim

If later repo truth contradicts a closeout claim, reopen explicitly.

Detailed rules: `docs/authority/zeus_packet_discipline.md`

---

## 6. Evidence burden

Every verified batch of work must provide:

- changed files list
- tests / gates run, or explicit recorded waiver reason
- invariant / authority basis when applicable
- rollback note
- unresolved uncertainty stated plainly

Additional rules:
- capability-present and capability-absent paths both need evidence when relevant
- waived gates are acceptable only if staged/advisory by law or externally unavailable for a recorded reason
- convenience is never a valid waiver reason

### 6.1 Anti-vibe checklist (patch review)

Every patch review must answer these questions. If any answer is unfavorable, the patch is rejected or re-scoped.

1. Did this patch shrink or expand truth surfaces?
2. Did this patch add real actuation or only another report/view?
3. Did this patch preserve point-in-time truth?
4. Did this patch reduce or increase attribution ambiguity?
5. Did this patch respect the live/backtest/shadow boundary?
6. Did this patch create any new implicit state transition?
7. Did this patch encode missing-data truth or silently skip it?
8. Did this patch introduce a new shadow persistence surface?
9. Did this patch introduce any unregistered numeric constant that composes with other constants in a sizing/edge/probability cascade?

### 6.2 Natural-language landing layer

Human intent names *what must be true*. Coding models act on *what is easiest to change*. Every spec section must be translated into three layers before code:

1. **Truth-layer statement** — what becomes authoritative?
2. **Control-layer statement** — who can change behavior because of it?
3. **Evidence-layer statement** — how will we know this is true in runtime?

Until that translation exists, the task is not ready for coding.

---

## 7. Autonomy limits

### 7.1 Human-gated actions

The following always require explicit human approval:

- live cutover timing
- irreversible migration / cutover switch
- archive / delete / demotion actions that change the active law stack
- permanent risk re-enable after a safety-triggered pause

### 7.2 Team mode

Team mode is allowed only when:
- there is an approved packet
- work is parallelizable
- one owner remains accountable
- the team is not redefining authority

Do not teamize:
- `architecture/**`
- `docs/authority/**`
- migration cutover decisions
- supervisor / control-plane semantics
- packet-less exploratory rewrites

Exactly one frozen packet at a time. One file, one writer at a time.

Detailed rules: `docs/authority/zeus_autonomy_gates.md`

---

## 8. External boundary for delivery

- Zeus owns repo law and inner truth
- Venus may read derived status and typed contracts, and may issue narrow ingress commands
- OpenClaw may host workspace/runtime support, notifications, and memory injection
- outer hosts may not silently mutate Zeus authority or DB truth

Boundary details: `docs/authority/zeus_openclaw_venus_delivery_boundary.md`

---

## 9. Completion protocol

Before calling work complete:

1. verify code / docs changes persisted
2. run the relevant tests, diagnostics, or checks
3. ensure read paths and file registries are updated if files moved or were added/deleted
4. ensure no archived file is still referenced as active authority
5. ensure current-state docs still point to current truth, not historical design

---

## 10. Low-context read path

A new agent should normally read in this order:

1. `AGENTS.md`
2. `workspace_map.md`
3. scoped directory `AGENTS.md`
4. `docs/operations/current_state.md`
5. `docs/operations/known_gaps.md` if runtime blockers matter
6. `docs/authority/zeus_current_architecture.md` if architecture semantics matter
7. `docs/authority/zeus_packet_discipline.md` / `zeus_autonomy_gates.md` / boundary law only when their domain applies

Historical design files are **not** part of the default path.

---

## 11. Current companion files

- `docs/authority/zeus_current_architecture.md`
- `docs/authority/zeus_packet_discipline.md`
- `docs/authority/zeus_autonomy_gates.md`
- `docs/authority/zeus_openclaw_venus_delivery_boundary.md`
- `docs/authority/zeus_live_backtest_shadow_boundary.md`
- `docs/authority/zeus_change_control_constitution.md` (deep governance; load only when needed)
