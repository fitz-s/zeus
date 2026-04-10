# ZEUS_AUTHORITY

> Status: Root top-law file for Zeus.
> Role: the highest-order root statement of Zeus's foundation, method, and system law.
> Exact precedence, machine-checkable enforcement, and change permission still live in `architecture/self_check/authority_index.md`, the architecture manifests, and the governance constitutions.

---

## Reading instructions

Read this file first to understand what Zeus is, what kind of system it is allowed to become, and what kinds of changes are invalid even when they look locally reasonable.

Then read, in order:

1. `architecture/self_check/authority_index.md`
2. `docs/architecture/zeus_durable_architecture_spec.md`
3. `docs/zeus_FINAL_spec.md`
4. `architecture/kernel_manifest.yaml`
5. `architecture/invariants.yaml`
6. `architecture/negative_constraints.yaml`
7. `docs/governance/zeus_change_control_constitution.md`
8. `docs/governance/zeus_autonomous_delivery_constitution.md`
9. `AGENTS.md`

This file is intentionally large enough to carry real bearing capacity. It is not a thin guide. It is also not allowed to compete with the exact rule surfaces it summarizes.

---

## §0 Executive frame

Zeus is a durable, position-governed weather-arbitrage runtime built to trade against weather prediction markets without collapsing into multi-truth patchwork, monitor-theater, or prompt-shaped pseudo-architecture.

Its center is not a model, a dashboard, a scheduler, or an LLM session. Its center is a **single authority-bearing trading system** with:

- one canonical lifecycle truth path,
- one canonical governance key,
- one point-in-time learning chain,
- one executable protective spine,
- one finite lifecycle grammar,
- one operator-facing derived surface,
- one archive-aware control surface discipline,
- and one coding method that treats local patches as subordinate to structural law.

Zeus exists to bear reality, not to simulate coherence. The system is only valid when the same law survives translation across:

- specification,
- schema,
- runtime write paths,
- derived read models,
- tests,
- and agent execution behavior.

If the same law only exists in prose, Zeus is not hardened yet.

---

## §1 Source basis

This root law file is grounded in three major authority families plus the machine-checkable manifests:

1. **Present-tense architecture authority** — `docs/architecture/zeus_durable_architecture_spec.md`
   Owns current system shape, lifecycle grammar, canonical truth model, migration order, zone doctrine, and what must be true before any large change is valid.

2. **Terminal target-state / endgame authority** — `docs/zeus_FINAL_spec.md`
   Owns terminal framing, P9–P11 completion law, and the endgame clause that determines whether Zeus continues or is archived.

3. **Historical system philosophy reference** — `docs/architecture/zeus_design_philosophy.md`
   No longer principal authority, but still the clearest statement of the translation-loss problem, immune-system methodology, and the relation-first way Zeus must be built.

4. **Machine-checkable semantic authority**
   - `architecture/kernel_manifest.yaml`
   - `architecture/invariants.yaml`
   - `architecture/zones.yaml`
   - `architecture/negative_constraints.yaml`
   - `architecture/maturity_model.yaml`

5. **Governance / change-control authority**
   - `docs/governance/zeus_change_control_constitution.md`
   - `docs/governance/zeus_autonomous_delivery_constitution.md`

This file does not replace those sources. It compresses their shared law into one root-level statement with enough depth to orient future humans and coding agents without reducing the system to slogans.

---

## §2 What Zeus is trying to protect

Zeus is designed to protect a specific class of truth and a specific class of edge.

### 2.1 The truth Zeus protects

Zeus protects the integrity of these boundaries:

- **decision-time truth** versus hindsight truth,
- **lifecycle truth** versus local convenience state,
- **governance truth** versus metadata drift,
- **operator truth** versus derived storytelling,
- **archive/history** versus live law,
- **code correctness** versus data semantics,
- **alerting** versus executable protection.

### 2.2 The edge Zeus protects

Zeus's technical edge comes from treating weather-market trading as a problem of:

- physical forecast uncertainty,
- station/settlement semantics,
- temporal decay,
- market microstructure,
- and explicit governance over where truth enters, moves, and hardens.

The architecture exists so the edge can survive translation from world data to decision to execution to settlement. The system is invalid if the architecture becomes another layer of semantic loss.

---

## §3 Architectural intent

Zeus is not evolving into a generalized workflow platform or a bag of helpful scripts. It is evolving into a **hard, narrow, explicit runtime**.

Its intended form is:

- one canonical lifecycle authority,
- one canonical governance key (`strategy_key`),
- one point-in-time learning spine,
- one executable protective substrate,
- one bounded lifecycle grammar,
- one operator-facing derived surface,
- one external audit / self-awareness boundary,
- one archive policy that keeps history but prevents historical drift from re-entering live law,
- and one packetized operating system for human and agent modification.

Zeus must become harder before it becomes larger. When there is a choice between:

- adding a surface,
- keeping a compatibility layer,
- writing another explanatory file,
- or removing ambiguity,

the law of Zeus prefers **harder / narrower / more explicit**.

### 3.1 Explicit non-goals

The following are not valid directions for Zeus unless top law is explicitly revised:

- generalized event-bus sprawl,
- asynchronous projector fabric for its own sake,
- new strategy taxonomies outside `strategy_key`,
- new discovery modes as governance centers,
- parallel truth surfaces without deletion or demotion plan,
- operational reliance on JSON exports as authority,
- policy theater that does not alter runtime behavior,
- archive growth that leaves the live repo surface visually indistinguishable from history,
- endless architecture recursion instead of reality contact.

---

## §4 Methodological law

Zeus is governed not only by structural artifacts, but by a specific method for turning problems into durable fixes.

### 4.1 Translation-loss law

The deepest failure mode in Zeus is not a single bug. It is **translation loss**:

- a relationship understood in natural language,
- partially translated into code,
- then weakened into local behavior,
- then forgotten at the module boundary where it mattered.

Functions survive translation better than relationships. Single-module logic survives better than cross-module constraints. Therefore Zeus must encode important relationships in:

- types,
- schema constraints,
- explicit write paths,
- relation-level tests,
- and machine-checkable manifests.

If a law is important enough to matter but not important enough to become executable, it is not yet hardened.

### 4.2 Structural-decision law

When Zeus presents N visible defects, the correct task is not to patch N symptoms. The correct task is to discover the smaller set of structural decisions, with **K << N**, that explains the entire failure family.

A valid Zeus repair therefore prefers:

- changing the category,
- removing the alternate path,
- making the wrong move unrepresentable,
- or demoting the shadow surface,

instead of adding local defensive code around each visible symptom.

### 4.3 Immune-system law

Venus-style reasoning is load-bearing because Zeus must produce **antibodies**, not just alerts.

An alert says: “this went wrong again.”
An antibody says: “this category can no longer survive in the system.”

Therefore any confirmed important gap should aim to close as one of:

- a test,
- a type constraint,
- a schema constraint,
- a linter / manifest rule,
- a write-path repair,
- or a deletion / demotion of the shadow surface that made the gap possible.

### 4.4 Data-semantics law

Code may be locally correct while the system is still semantically wrong if inherited data fields, time semantics, units, rounding behavior, or settlement assumptions are misunderstood.

So Zeus law requires checking not only whether code runs, but whether the **assumptions inside the code match the actual semantics of the data**.

### 4.5 Relation-test law

A module-local unit test is not enough when the true risk lives at the seam. Important boundaries require tests that assert what must stay true when one module hands meaning to another.

If a relationship cannot be expressed as an assertion, the design is still underspecified.

---

## §5 Constitutional laws of Zeus

These are the constitutional commitments that give Zeus its shape.

1. **One authority path**
   Lifecycle truth must converge toward one canonical event path plus one deterministic projection path.

2. **One governance center**
   `strategy_key` is the only governance key. Metadata may decorate behavior but may not become a rival law surface.

3. **One learning spine**
   Decision-time truth must remain recoverable and must outrank hindsight at learning seams.

4. **One protective substrate**
   Risk is only real if it can change runtime behavior.

5. **One bounded lifecycle grammar**
   States only exist when they change governance, execution, or reconciliation semantics.

6. **One derived operator surface**
   Operator files are derived from authority; they do not become authority by being convenient or nearby.

7. **One archive boundary**
   Historical materials are preserved for provenance, but they do not compete with live law.

8. **One packetized change method**
   Zeus changes by bounded, evidence-bearing packets, not by unscoped repo drift.

---

## §6 The 10 live invariants

The live invariant register is still `architecture/invariants.yaml`. The 10 invariant IDs below are summarized here because a highest-order law file must state them directly, not merely link to them.

### INV-01 — Exit is not local close
Monitor intent and terminal lifecycle completion are different facts. Any design that lets an orchestration convenience imply economic closure is invalid.

### INV-02 — Settlement is not exit
Economic exit and final market settlement are distinct events in both meaning and audit obligation.

### INV-03 — Canonical authority is append-first and projection-backed
Lifecycle truth must be represented as canonical append history plus deterministic current projection. This is the core anti-fragmentation law.

### INV-04 — `strategy_key` is the sole governance key
No metadata field, scheduler mode, or fallback bucket is allowed to rival the governance role of `strategy_key`.

### INV-05 — Risk must change behavior
Advisory-only risk is theater. If risk cannot alter evaluation, sizing, execution, or durable commands, it is not law.

### INV-06 — Point-in-time truth beats hindsight truth
The system must preserve what was knowable at decision time. Silent upgrades to later state are epistemic corruption.

### INV-07 — Lifecycle grammar is finite and authoritative
Ad hoc state words are forbidden because they create semantic drift faster than code review can contain it.

### INV-08 — Canonical write path has one transaction boundary
Event append and projection update succeed or fail together. Anything less recreates split-brain truth.

### INV-09 — Missing data is first-class truth
Unavailable, stale, or degraded inputs must be represented explicitly. Missingness is not log noise.

### INV-10 — LLM output is never authority
Generated code, explanations, and summaries are proposals only. Authority lives in law, manifests, tests, evidence, and runtime truth.

---

## §7 The 10 live negative constraints

The live negative-constraint register is still `architecture/negative_constraints.yaml`. These are the hard “do not do this” rules that prevent Zeus from regressing into soft, scattered architecture.

### NC-01 — No broad prompt may edit K0 and K3 together without explicit packet justification
This prevents mathematically convenient edits from silently redefining kernel law.

### NC-02 — JSON exports may not be promoted back to authority
Derived exports remain derived, no matter how easy they are to read.

### NC-03 — No downstream strategy fallback or re-inference when `strategy_key` is available
Governance truth may not be re-guessed after it is already known.

### NC-04 — No direct lifecycle terminalization from orchestration code
Orchestration is not allowed to manufacture completion semantics for convenience.

### NC-05 — No silent fallback from missing decision snapshot to latest snapshot for learning truth
When decision-time truth is unavailable, the system must degrade explicitly, not hallucinate equivalence.

### NC-06 — No memory-only durable governance
If a durable decision matters, it must land in repo-governed surfaces, not session memory.

### NC-07 — No raw phase/state string assignment outside lifecycle kernel
Lifecycle vocabulary is kernel law, not app-layer improvisation.

### NC-08 — No bare implicit unit assumptions in semantic code paths
Unit semantics must be explicit wherever system meaning depends on them.

### NC-09 — No ad hoc probability complements across architecture boundaries when semantic contracts exist
Crossing a probability seam with a naked transform is forbidden when typed semantics already exist.

### NC-10 — No new shadow persistence surface without deletion or demotion plan
Every new durable surface increases truth fragmentation unless it comes with an explicit retirement or consolidation path.

---

## §8 The 5 boundary rules

These boundary rules are the shortest possible summary of where Zeus most often breaks.

### 8.1 Authority boundary
Canonical truth lives in append-first lifecycle events plus deterministic projection. Derived JSON, comments, archives, and LLM explanations never outrank that path.

**Primary sources:** `INV-03`, `INV-10`, `NC-02`

### 8.2 Lifecycle boundary
Exit intent, economic exit, settlement, and terminal closure are separate facts. The system must not collapse them at the orchestration seam.

**Primary sources:** `INV-01`, `INV-02`, `INV-07`, `NC-04`, `NC-07`

### 8.3 Governance boundary
`strategy_key` is the only governance key. No secondary label, metadata field, or fallback inference may become a second center.

**Primary sources:** `INV-04`, `NC-03`

### 8.4 Temporal-truth boundary
Decision-time truth outranks hindsight. Missing decision-time material must degrade explicitly rather than silently substituting a later surface.

**Primary sources:** `INV-06`, `INV-09`, `NC-05`

### 8.5 Durability boundary
Durable control and truth must live in explicit repo-governed surfaces. Hidden memory, shadow persistence, and undocumented archive drift are invalid.

**Primary sources:** `INV-09`, `INV-10`, `NC-06`, `NC-10`

---

## §9 Authority doctrine and conflict resolution

The exact authority order is maintained in `architecture/self_check/authority_index.md`. The doctrine beneath that order is:

1. machine-checkable semantic authority wins when a rule is explicitly encoded there,
2. present-tense architecture authority governs current implementation shape,
3. terminal target-state authority governs endgame framing,
4. governance constitutions govern how change is allowed,
5. the repo operating brief governs execution behavior,
6. active control surfaces coordinate live work,
7. archives and explanations remain historical context only.

A root top-law file like this one is valid only if it strengthens that order without competing with it.

### 9.1 What this file must never become

This file must not become:

- a second packet tracker,
- a second authority index,
- a second AGENTS file,
- a shadow copy of the manifests,
- or a narrative excuse for violating machine-checkable law.

Its function is constitutional compression, not operational duplication.

---

## §10 Archive, control, and live-surface doctrine

Zeus now distinguishes three classes of documentation clearly.

### 10.1 Live top law
- `ZEUS_AUTHORITY.md`
- `AGENTS.md`
- `architecture/self_check/authority_index.md`
- `docs/architecture/zeus_durable_architecture_spec.md`
- `docs/zeus_FINAL_spec.md`
- manifests and governance docs

### 10.2 Live control / orientation
- `docs/control/current_state.md`
- `docs/reference/repo_overview.md`
- `docs/reference/workspace_map.md`
- `docs/work_packets/<current>.md`
- `docs/known_gaps.md`

### 10.3 Historical / archived
- `docs/archives/**`

The law is simple: history is preserved, but history must not visually dominate the live system surface.

---

## §11 Operator and change method

Zeus changes by evidence-bearing packets, not by momentum.

### 11.1 Packet law
A valid change must answer:

- what authority it relies on,
- what truth surface it touches,
- what zone it enters,
- what invariants or negative constraints matter,
- what evidence proves it,
- what rollback exists,
- and what must remain forbidden.

### 11.2 Evidence law
A claim is not complete because it sounds coherent. A claim is complete when:

- the code or docs changed,
- the required gates ran,
- the evidence is visible,
- critic/verifier review happened when required,
- and the result did not create a new shadow surface.

### 11.3 Zero-context law
A new agent entering Zeus must orient through authority before editing. Retrieval similarity, recent conversation memory, and plausible prose are not enough.

### 11.4 Archive law
If a surface is no longer live authority, live control, or live runtime orientation, it should be archived rather than left to drift in place.

---

## §12 Remaining unfinished law before live

The FINAL spec still defines the unfinished high-order work that stands between Zeus and a valid live gate.

### 12.1 P9 — Epistemic contract and provenance enforcement
Zeus still requires stronger protection against cross-layer semantic collapse, provenance drift, and unregistered constants in multiplicative decision chains.

### 12.2 P10 — External reality contract layer
Zeus still requires explicit, typed representation of external market assumptions so reality drift becomes detectable law instead of hidden configuration folklore.

### 12.3 P11 — External audit boundary and retirement readiness
Zeus still requires a clean consumer-side audit boundary and a real retirement/continuation readiness doctrine.

### 12.4 Endgame clause
The FINAL spec's endgame clause remains active: Zeus does not get infinite architecture recursion. At some point the system faces a binary reality test.

---

## §13 Methodology attribution

The root methodological lineage behind Zeus includes:

- structural decisions over symptom patching,
- translation-loss awareness,
- immune-system style antibody generation,
- cross-module relation testing,
- provenance over narrative comfort,
- and the refusal to let convenience exports masquerade as truth.

This file carries that lineage forward because a true top-law file must state not only what Zeus forbids, but **how Zeus thinks about making error categories impossible**.

---

## §14 Zero-context usage

If you enter Zeus cold:

1. read this file,
2. read `architecture/self_check/authority_index.md`,
3. read the durable architecture spec,
4. read the FINAL spec,
5. read the manifests,
6. read governance,
7. only then read live control surfaces and packet docs,
8. only then change code.

If a future session reduces Zeus to a set of local edits without first reconstructing this hierarchy, it is already off the law path.

---

## Coda

Zeus should prefer executable law over eloquent explanation.

But a system that forgets its own constitutional method also forgets how to know when it is drifting. This file exists so that the root of Zeus is visible in one place with enough scale to survive first contact, while still handing exact enforcement back to the files that can actually stop the mistake.
