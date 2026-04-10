# Test spec — Reality-contract mainline merge

## Planning-stage proof
- `architects_state_index.md:14-28` / `architects_task.md:20-27` / `architects_progress.md:33-41` are cited to prove the current stop boundary and no-live-packet state.
- `docs/architecture/zeus_durable_architecture_spec.md:118-130`, `:934-983`, `:987-1174` are cited to prove the current mainline ordering, migration structure, and packet discipline.
- `docs/TOP_PRIORITY_zeus_reality_crisis_response.md:15-46`, `:102-140`, `:299-394`, `:475-606` are cited to prove the crisis diagnosis, RCL proposal, Venus integration proposal, and implementation intent, and to justify descriptive override of older foundation text on present-tense external-reality facts.
- `docs/architecture/venus_zeus_audit_integration_plan.md:1-176` and `docs/architecture/venus_operator_architecture.md:1-203` are cited to prove the read-order contract, heartbeat/daily/weekly split, three-layer model, zero-build Venus infra inventory, and world-model/antibody extension requirements.
- Existing precursor surfaces are cited from `src/contracts/semantic_types.py`, `src/contracts/edge_context.py`, `src/contracts/execution_intent.py`, `src/contracts/expiring_assumption.py:7-40`, `state/assumptions.json:1-38`, `scripts/validate_assumptions.py:23-98`, and `src/contracts/settlement_semantics.py:7-70`.

## Global checks for future execution packets
- `python3 scripts/check_work_packets.py`
- `python3 scripts/check_kernel_manifests.py`
- changed-file diagnostics on touched code/docs

## Packet-family test shape
### SPEC-RCL-00-PRINCIPAL-MERGE
- principal-spec coherence review
- the merge explicitly states that the crisis doc has higher descriptive priority on newer external-reality facts, while principal authority remains the normative landing surface
- packet grammar stays valid

### SPEC-RCL-01-MACHINE-LAW
- invariant/manifest tests proving new reality-integrity law is machine-visible
- negative-constraint coverage for no uncategorized external-value hardcoding on claimed seams

### SPEC-RCL-02-BOUNDARY-AND-CHANGE-CONTROL
- explicit review that Venus/OpenClaw remain read/consume/raise only
- no direct DB/code authority granted outward
- foundation-pack Venus integration content is preserved as implementation scope rather than silently dropped
- read-order contract and audit-lane split are written down as boundary-safe implementation law

### P9.1-EPISTEMIC-CONTRACT-KERNEL
- typed probability / edge / sizing contract parsing and validation tests
- provenance fields and confidence/bounds survive claimed handoff paths

### P9.2-PROVENANCE-REGISTRY-AND-MAGIC-NUMBER-TAX
- documented constant registry tests
- unregistered adjustment behavior proves fail-loud or neutralization on the claimed seam

### P9.3-EPISTEMIC-ACTUATION-AT-SIZING-SEAMS
- Kelly / fusion / execution-intent paths reject or wrap bare semantic floats on the claimed path
- provenance mismatch is surfaced explicitly rather than silently multiplied through

### P10.1-REALITY-CONTRACT-KERNEL
- contract schema parsing / category / TTL / criticality tests
- compatibility-present and compatibility-absent behavior for any reused assumption substrate

### P10.2-REALITY-CONTRACT-STATUS-VERIFIER
- verifier present-path proof
- verifier unavailable/stale-path proof
- explicit blocking/degraded/advisory status emission proof

### P10.3-REALITY-CONTRACT-ACTUATION
- failing blocking contract prevents the claimed trade/runtime action
- degraded contract changes behavior or status explicitly instead of logging only

### P10.4-ECONOMIC-REALITY-CONTRACTS
- fee/rebate contract verification
- edge/Kelly/exit behavior changes on contract drift

### P10.5-EXECUTION-REALITY-CONTRACTS
- tick size / min order size / rounding proofs on the touched order path
- capability-absent behavior when live orderbook metadata is unavailable

### P10.6-DATA-PROTOCOL-REALITY-CONTRACTS
- settlement-source and protocol-freshness verification
- explicit degraded/blocking behavior when source/freshness contracts fail

### P11.1-VENUS-TYPED-BOUNDARY
- typed export/read contract proof
- no direct Venus/OpenClaw write path into DB truth surfaces
- explicit proof that Venus integration remains extension implementation, not repo-internal authority

### P11.2-VENUS-AUDIT-ORCHESTRATION
- heartbeat only runs fast/safety-relevant checks
- daily/weekly audits own heavy interpretation
- authoritative read-order is explicit and tested
- world-model/antibody surfaces are classified and do not become canonical truth by accident

### P11.3-RETIREMENT-READINESS-UNDER-RCL
- explicit readiness report proving late retirement/delete work is reality-aligned enough to even freeze
- no destructive cutover claim without human gate

## Closeout discipline
- pre-close critic + verifier before acceptance
- post-close critic + verifier before freezing next packet
- explicit reopen if runtime truth disproves an acceptance claim later
