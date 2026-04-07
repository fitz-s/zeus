# PRD — Reality-contract mainline merge

## Requirements Summary
- The current mainline has reached a truthful autonomous stop boundary after `P7R7`, with no live packet and no further lawful non-destructive `P7` freeze justified (`architects_state_index.md:14-28`, `architects_task.md:20-27`, `architects_progress.md:33-41`).
- The principal architecture spec still orders work as `P0` through `P8`, and that completed foundation content must be retained rather than rewritten away, with `P7` reserved for migration/cutover/delete and `P8` for packetized implementation discipline (`docs/architecture/zeus_durable_architecture_spec.md:118-130`, `:934-983`, `:987-1174`).
- The crisis document identifies a distinct class of failure: implicit external assumptions drifting out of sync with live market reality, and proposes a Reality Contract Layer instead of one-off bug fixing (`docs/TOP_PRIORITY_zeus_reality_crisis_response.md:15-46`, `:102-140`, `:475-606`).
- Because `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` is later than the current foundation spec and records newer external-reality findings, it must act as a **descriptive override input** wherever older foundation text is contradicted on present-tense reality; the foundation/spec stack still remains the **normative landing surface** for formal change authorization.
- Zeus already has partial precursor surfaces for this work: typed assumption decay in `src/contracts/expiring_assumption.py:7-40`, a runtime assumption manifest in `state/assumptions.json:1-38`, validation glue in `scripts/validate_assumptions.py:23-98`, and per-city settlement semantics in `src/contracts/settlement_semantics.py:7-70`.
- A newer audit also surfaces a deeper K0 beneath the reality-contract problem: **epistemic fragmentation**. Repo evidence supports that diagnosis: Zeus already has partial semantic/provenance scaffolding in `src/contracts/semantic_types.py`, `src/contracts/edge_context.py`, and `src/contracts/execution_intent.py`, but key strategy/execution seams still collapse semantics back to bare floats and ad-hoc multipliers (for example `src/strategy/kelly.py` takes `p_posterior: float`, while `src/engine/evaluator.py` threads float probabilities and threshold multipliers directly into sizing and decision paths).
- The OpenClaw/Venus boundary is explicit: Venus may read derived status and consume typed contracts, but may not become repo authority or write DB truth directly (`docs/governance/zeus_autonomous_delivery_constitution.md:345-355`, `:426-441`). Venus integration remains an implementation extension of the foundation pack, not a replacement for foundation law.
- The two Venus architecture documents add required content that must not be lost in the merged mainline: Zeus emits stable truth contracts, Venus owns audit orchestration, heartbeat remains a guardrail rather than a research lab, daily/weekly audits absorb heavy interpretation, and the three-layer model (RiskGuard reflex / Zeus execution / Venus reasoning) remains the operating philosophy (`docs/architecture/venus_zeus_audit_integration_plan.md:1-176`, `docs/architecture/venus_operator_architecture.md:1-203`).
- Those same documents also contribute concrete extension surfaces that must be preserved in packetized form rather than dropped: read-order discipline, zero-build Venus infrastructure inventory, assumption-manifest/world-model concepts, audit cron layering, and the antibody framing for converting detected drift into durable fixes (`docs/architecture/venus_operator_architecture.md:35-171`, `docs/architecture/venus_zeus_audit_integration_plan.md:20-176`).

## RALPLAN-DR Summary
### Principles
1. Preserve the installed `P0`–`P8` mainline as historical substrate; do not rewrite achieved work into “not done.”
2. Treat `TOP_PRIORITY_zeus_reality_crisis_response.md` as the higher-priority descriptive correction for external reality, then extract its durable law into repo authority instead of leaving a parallel emergency doctrine alive.
3. Keep Venus external but present: Zeus must expose typed contracts and derived status, while Venus integration lands as a foundation-extension implementation lane rather than being erased or promoted into repo-internal authority.
4. Preserve the three-layer consciousness model and audit-lane split from the Venus docs: RiskGuard reflex, Zeus runtime execution, Venus reasoning; heartbeat fast/safety-only, daily/weekly audits heavy/interpretive.
5. Before external-reality contracts can be fully trusted, Zeus must stop collapsing epistemic meaning into bare floats across signal → strategy → execution handoffs.
6. Convert detected drift into antibodies (tests/contracts/code changes), not just alerts or side-notes.
7. Packetize external-reality hardening the same way internal-truth hardening was packetized: narrow, evidence-first, capability-present/capability-absent.
8. Do not advance to destructive `P7 M4` retirement/delete until reality-alignment gates exist and are proven.

### Decision Drivers
1. The current autonomous stop boundary is real; the next mainline must be explicitly justified, not invented from momentum (`architects_task.md:27-28`, `:64-68`, `:81-85`).
2. The crisis document contains newer external-reality findings than the older foundation text, so any contradiction on present-tense market/protocol facts must be reconciled in favor of the crisis document’s descriptive evidence.
3. Boundary law forbids turning Venus/OpenClaw into repo-internal authority or direct DB writers, but does not forbid keeping Venus integration as a foundation-extension implementation lane (`docs/governance/zeus_autonomous_delivery_constitution.md:428-441`).
4. The Venus docs contribute concrete operational content that the current plan must preserve: authoritative read order, heartbeat/daily/weekly audit separation, existing infra activation, and world-model/antibody concepts, even though some older tracker-authority wording must be reinterpreted under current repo truth.
5. The latest Gemini audit identifies a deeper root cause than individual external-drift gaps: epistemic contract loss across signal, strategy, and execution layers; current repo code shows partial typed semantics but no strict provenance enforcement at key sizing/fusion seams.

### Viable Options
#### Option A — Append a new reality-alignment phase family onto the existing mainline (chosen)
- **Approach:** Keep `P0`–`P8` intact, add a new principal-spec extension (`P9/P10` or equivalent) for external reality contracts, drift verification, and typed Venus boundary work.
- **Pros:** Preserves truthful history, reuses packet discipline, and cleanly gates later retirement work.
- **Cons:** Requires careful authority extraction from the crisis doc and several new packet families.

#### Option B — Treat the crisis doc as a separate emergency side-program
- **Approach:** Leave the durable spec unchanged and drive RCL from `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` plus ad hoc packets.
- **Pros:** Faster initial motion.
- **Cons:** Creates parallel authority, weakens precedence, and conflicts with the repo’s anti-drift governance model.

#### Option C — Rewrite the principal spec around the crisis doc
- **Approach:** Recast the entire mainline around external reality sensing as the new center.
- **Pros:** Maximally emphasizes the crisis.
- **Cons:** Erases completed mainline progress, overstates what is still missing, and risks reopening already-installed internal hardening for no gain.

## ADR
- **Decision:** Extend the durable mainline in two layers: first install an epistemic/provenance enforcement family that prevents semantic collapse across signal→strategy→execution handoffs, then install the external-reality contract family, treat the crisis document as the descriptive override for newer external-reality facts, preserve the completed internal-hardening phases, and make late `P7 M4` retirement contingent on the new reality-alignment gates.
- **Drivers:** truthful stop boundary after `P7R7`; crisis doc’s explicit external-assumption drift diagnosis and newer factual observations; Gemini’s deeper K0 diagnosis of epistemic fragmentation; existing precursor semantic/provenance surfaces; Venus boundary law.
- **Alternatives considered:** separate side-program (`Option B`), full spec rewrite (`Option C`).
- **Why chosen:** it preserves authority compression, prevents a second doctrine from forming, and gives the crisis response a lawful place in the existing spec/program structure.
- **Consequences:** the next mainline begins with authority extraction/spec merge work before code; `P7 M4` destructive retirement is deferred behind reality-alignment evidence; some current crisis proposals must be reshaped to satisfy repo law.
- **Follow-ups:** freeze the authority packet first, then machine-checkable law, then kernel/verifier/actuation packets.

## Scope And Non-Goals
### In scope
- Define a new spec-shaped mainline that combines the completed internal hardening with (a) an epistemic/provenance contract layer and (b) a durable external-reality contract layer.
- Identify the minimum authority, invariant, substrate, and runtime packets needed to encode both layers.
- Specify how the current crisis document is extracted into repo authority without becoming a competing law surface.
- Carry forward the Venus architecture package as concrete extension content: boundary-safe audit orchestration, layer model, heartbeat/daily/weekly lanes, zero-build activation inventory, world-model handling, and antibody generation.

### Out of scope
- Direct implementation of fee/tick-size/websocket fixes in the planning packet.
- Any destructive `P7 M4` retirement/delete/cutover work.
- Moving Venus logic into repo-internal `src/venus/**` surfaces.
- Treating `state/assumptions.json` as canonical truth without an explicit classification/demotion plan.

## Key Merge Adjustments Required By Current Law
1. **Descriptive override, then normative merge:** the crisis document is currently untracked/out-of-scope (`architects_state_index.md:32-43`), but its newer external-reality findings must override older foundation descriptions where they conflict; then that corrected reality must be extracted into the principal architecture spec / machine-checkable authority before execution.
2. **Keep foundation Venus content, but boundary-safe:** crisis proposals such as `src/venus/reality_verifier.py` must be reframed into Zeus-owned typed contract/verifier surfaces plus external Venus consumption, while preserving Venus integration as a foundation-extension implementation concern rather than dropping it (`docs/TOP_PRIORITY_zeus_reality_crisis_response.md:520-548`, `docs/governance/zeus_autonomous_delivery_constitution.md:428-441`).
2b. **Preserve audit-orchestration content:** heartbeat/daily/weekly separation, authoritative read order, and zero-build Venus infrastructure from the two Venus docs should become explicit packetized extension work, not informal background notes.
2c. **Reinterpret stale details under current repo truth:** older claims such as tracker-as-attribution-authority survive only as historical inputs and must be updated to current compatibility-only truth during the merge.
3. **No new shadow authority by convenience:** `state/assumptions.json` already exists, but `NC-10` forbids new shadow persistence without deletion/demotion planning (`architecture/negative_constraints.yaml:68-73`). Any retained assumptions surface must be classified explicitly as config, derived export, or transitional compatibility.
4. **No gap-by-gap whack-a-mole before kernel:** domain fixes (fee, tick size, settlement source, protocol freshness) follow only after the epistemic contract kernel plus the reality-contract verifier/actuation path exist.
5. **No semantic collapse by convenience:** current typed semantic helpers in `src/contracts/**` should be extended, not bypassed; new work must not reintroduce float-only probability/edge handoffs on claimed seams.

## Proposed New Mainline Structure
### Mainline verdict
- Keep `P0`–`P8` as installed substrate; do not lose foundation-pack content that already landed.
- Add a new phase family after the current `P7` pre-retirement stop boundary and before any destructive `P7 M4` claims.
- Carry forward the foundation’s Venus-integration intent, but only as boundary-safe implementation work under the new reality-contract phases.
- Recommended labels inside the principal spec:
  1. `P9 — Epistemic contract and provenance enforcement`
  2. `P10 — External reality contract layer`
  3. `P11 — External audit boundary and retirement readiness`

### Phase intent
#### P9 — Epistemic contract and provenance enforcement
- Stop reducing cross-layer meaning to bare floats and undocumented multipliers.
- Make probability/edge/sizing semantics carry optimization target, provenance, and confidence/bounds across signal → strategy → execution handoffs.
- Force undocumented or unregistered numerical adjustments to become explicit, typed, and auditable.

#### P10 — External reality contract layer
- Convert implicit external assumptions into typed, explicit, TTL-bound contracts.
- Install verifier semantics for `blocking`, `degraded`, and `advisory` contracts.
- Wire runtime behavior so contract invalidity changes Zeus behavior instead of merely creating logs.

#### P11 — External audit boundary, audit orchestration, and retirement readiness
- Expose typed/derived contract status for Venus to consume.
- Keep Zeus as authority owner and Venus as external auditor/orchestrator.
- Preserve the existing foundation-pack Venus integration intent as an implementation extension, not a rival law stack.
- Install the audit-lane contract: heartbeat as guardrail, daily/weekly audits as heavy interpretation lanes, and explicit read-order discipline.
- Carry forward world-model / antibody concepts as extension surfaces that consume typed Zeus outputs rather than bypassing them.
- Gate late migration retirement/delete work on reality-aligned evidence, not just internal parity.

## Packet Families
### Family A — Authority extraction / spec merge
1. **`SPEC-RCL-00-PRINCIPAL-MERGE`**
   - **Objective:** reconcile the principal architecture authority with the crisis document by treating the crisis file as the descriptive override for newer reality facts, preserving completed foundation content, and extracting the merged law back into principal authority.
   - **Preferred files:** `docs/architecture/zeus_durable_architecture_spec.md`; one companion architecture doc if needed (for example `docs/architecture/zeus_reality_contract_layer_spec.md`).
   - **Why now:** current repo state has no lawful live packet and needs a new principal direction before any implementation.

2. **`SPEC-RCL-01-MACHINE-LAW`**
   - **Objective:** install machine-checkable law for reality integrity (e.g. `INV-11`) plus truth-surface classification updates.
   - **Preferred files:** `architecture/invariants.yaml`; `architecture/kernel_manifest.yaml` or `architecture/negative_constraints.yaml`.
   - **Why now:** without machine-checkable law, RCL remains prose.

3. **`SPEC-RCL-02-BOUNDARY-AND-CHANGE-CONTROL`**
   - **Objective:** encode the Venus/OpenClaw boundary and evidence burden for reality-contract packets.
   - **Preferred files:** `docs/governance/zeus_autonomous_delivery_constitution.md`; `docs/governance/zeus_change_control_constitution.md`.
   - **Why now:** crisis proposals currently blur repo-vs-Venus execution ownership.

### Family B — Zeus-owned epistemic / provenance kernel
4. **`P9.1-EPISTEMIC-CONTRACT-KERNEL`**
   - **Objective:** introduce a Zeus-owned epistemic contract surface under `src/contracts/**` so probability/edge/sizing values carry provenance, optimization target, and confidence/bounds instead of collapsing to bare floats.
   - **Likely files:** `src/contracts/epistemic_contract.py` (new or adjacent), `src/contracts/edge_context.py`, `src/contracts/semantic_types.py`, targeted tests.
   - **Precursor reuse:** extend existing semantic/provenance helpers rather than creating a parallel `src/base/` authority surface.

5. **`P9.2-PROVENANCE-REGISTRY-AND-MAGIC-NUMBER-TAX`**
   - **Objective:** make adjustment constants explicit and registered; undocumented strategy/execution scalars on claimed seams either fail loud or are neutralized by rule.
   - **Likely files:** provenance registry/config, bounded strategy/evaluator helpers, targeted tests.
   - **Constraint:** no repo-wide numeric purge in one packet.

6. **`P9.3-EPISTEMIC-ACTUATION-AT-SIZING-SEAMS`**
   - **Objective:** ensure Kelly/market-fusion/execution-intent seams consume typed epistemic values rather than bare floats on the claimed path.
   - **Likely files:** `src/strategy/kelly.py`, bounded evaluator seams, targeted tests.
   - **Constraint:** fix the contract path before fixing every downstream symptom.

### Family C — External reality contract kernel
7. **`P10.1-REALITY-CONTRACT-KERNEL`**
   - **Objective:** introduce typed external-reality contract definitions/registry semantics using Zeus-owned surfaces under `src/contracts/**` and config declarations under `config/reality_contracts/**`.
   - **Likely files:** `src/contracts/reality_contract.py` (new), `config/reality_contracts/*.yaml`, `tests/test_reality_contracts.py`.
   - **Precursor reuse:** fold or supersede `src/contracts/expiring_assumption.py` rather than duplicating it.

8. **`P10.2-REALITY-CONTRACT-STATUS-VERIFIER`**
   - **Objective:** add verification and freshness state surfaces plus explicit capability-absent behavior.
   - **Likely files:** verifier script/helper, status export/DB query seam, targeted tests.
   - **Constraint:** avoid making `state/assumptions.json` canonical by convenience.

9. **`P10.3-REALITY-CONTRACT-ACTUATION`**
   - **Objective:** ensure invalid `blocking`/`degraded` contracts change behavior in evaluator/execution/riskguard/control paths.
   - **Likely files:** bounded runtime readers/actuators only on the claimed seam.
   - **Constraint:** no multi-domain widening in one patch.

### Family D — Domain migration from implicit assumptions to explicit contracts
10. **`P10.4-ECONOMIC-REALITY-CONTRACTS`**
   - Fee/rebate assumptions, temporary FeeGuard if still needed, and edge/Kelly/exit actuation proofs.
11. **`P10.5-EXECUTION-REALITY-CONTRACTS`**
   - Tick size, min order size, rounding/price-depth execution semantics.
12. **`P10.6-DATA-PROTOCOL-REALITY-CONTRACTS`**
   - Settlement source, NOAA/WU distinctions, REST freshness, websocket requirement semantics, UMA/dispute timing.

### Family E — External audit boundary and late migration gate
13. **`P11.1-VENUS-TYPED-BOUNDARY`**
    - Export only derived status + typed contracts needed for Venus heartbeat/audit, with no direct DB write path.
14. **`P11.2-VENUS-AUDIT-ORCHESTRATION`**
    - Install the boundary-safe audit operating contract: heartbeat read order, daily/weekly audit classes, world-model update rules, and antibody handoff semantics.
15. **`P11.3-RETIREMENT-READINESS-UNDER-RCL`**
    - Prove that late `P7 M4` retirement/delete candidates are reality-aligned enough to even be considered.

## Acceptance Criteria
1. A new mainline plan exists that preserves `P0`–`P8` history and places reality-alignment work after the current `P7R7` stop boundary rather than pretending old phases were incomplete.
2. The spec merge path explicitly prevents `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` from remaining a competing authority surface.
3. The planned architecture keeps Venus external and forbids `src/venus/**` from becoming the implementation center.
4. The first implementation packet is an authority packet, not a fee/tick-size hotfix.
5. The revised plan explicitly recognizes the deeper K0 problem as epistemic fragmentation, not just external-reality drift.
6. The plan inserts an epistemic/provenance enforcement family before external-reality contract migration.
7. The plan explicitly preserves the Venus architecture package: three-layer model, read-order discipline, heartbeat/daily/weekly split, zero-build infra inventory, and antibody/world-model concepts.
8. Late `P7 M4` retirement/delete work is explicitly gated on the new reality-contract evidence path.
9. The plan references existing precursor surfaces (`semantic_types`, `EdgeContext`, `ExecutionIntent`, `ExpiringAssumption`, `state/assumptions.json`, `validate_assumptions.py`, `SettlementSemantics`) so the next mainline starts from repo truth, not a greenfield fantasy.

## Implementation Steps
1. **Freeze authority direction first**
   - Draft the new mainline sections and packet family in a spec-shaped artifact.
   - Keep the first live packet to at most two authority files.
2. **Install machine-checkable law**
   - Add the invariant and truth-surface rules needed to make external assumptions explicit and testable.
3. **Build a Zeus-owned epistemic contract kernel first**
   - Extend current semantic/provenance helpers so cross-layer handoffs stop collapsing to bare floats.
4. **Install provenance enforcement on sizing seams**
   - Make adjustment constants and semantic conversions explicit before fixing every downstream symptom.
5. **Then build the external reality-contract kernel**
   - Create typed reality contracts/config and unify or supersede the current expiring-assumption substrate.
6. **Add verifier + status semantics**
   - Define where contract freshness/state lives and how absent verification fails/degrades.
7. **Wire actuation**
   - Ensure failing epistemic/reality contracts change runtime behavior on the claimed seam.
8. **Install Venus-side extension orchestration**
   - Freeze typed boundary first, then heartbeat/daily/weekly audit packetization, world-model update rules, and antibody handoff semantics.
9. **Migrate domain gaps by family, not bug-by-bug**
   - Economic first if still the highest-risk blocker; then execution; then data/protocol.
10. **Only then revisit retirement/cutover**
   - Make late migration deletion packets contingent on RCL-backed readiness evidence.

## Risks And Mitigations
- **Risk:** creating a second doctrine by leaving the crisis doc as active authority.
  - **Mitigation:** first packet is principal-spec merge plus explicit demotion/extraction language.
- **Risk:** boundary violation by moving Venus logic into repo code.
  - **Mitigation:** keep verifier/contracts Zeus-owned and export typed/derived surfaces outward.
- **Risk:** introducing a new shadow persistence surface.
  - **Mitigation:** classify every new file/table/status surface as config, canonical, derived, or compatibility; reject uncategorized state.
- **Risk:** exploding one crisis into a giant “fix 17 things” patch.
  - **Mitigation:** require kernel → verifier → actuation → domain migration ordering.
- **Risk:** temporary emergency guards become permanent silent defaults.
  - **Mitigation:** any FeeGuard-style stopgap must carry explicit expiration, owner, and replacement packet.

## Verification Steps
- Planning artifact checks:
  - ensure this PRD and its paired test spec exist under `.omx/plans/`
  - ensure the proposed first packet obeys authority-file count limits
- During later execution packets:
  - `python3 scripts/check_work_packets.py`
  - `python3 scripts/check_kernel_manifests.py`
  - targeted `tests/test_architecture_contracts.py`
  - new `tests/test_reality_contracts.py`
  - packet-bounded critic + verifier pre-close, then post-close critic + verifier

## Recommended first packet
- **`SPEC-RCL-00-PRINCIPAL-MERGE`**
- Reason: the repo is currently at a truthful no-live-packet stop boundary, and the crisis response must become law before it becomes code.
