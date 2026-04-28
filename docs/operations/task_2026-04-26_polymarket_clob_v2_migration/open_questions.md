# Polymarket V2 Migration — Open Questions

Created: 2026-04-26
Last reused/audited: 2026-04-26

This file is the live tracker for operator decisions and external-dependency answers that gate phase advancement. The plan's phase-entry conditions reference these question IDs.

Update this file as evidence arrives. Do not delete answered questions — mark them resolved with date + evidence path.

---

## Status legend

- **OPEN** — no answer; gating phase advancement
- **PARTIAL** — partial answer; needs operator decision
- **RESOLVED** — definitive answer with evidence file
- **DEFERRED** — explicitly deferred by operator decision

---

## Question registry

### Q1 — Is `clob-v2.polymarket.com` reachable from Zeus's egress?

- Status: **OPEN**
- Phase gate: Phase 0 → Phase 1 (mandatory)
- Acceptance: HTTP 200 + JSON body with protocol identifier, captured from the Zeus daemon egress environment with funder-address context redacted or present as appropriate
- Owner: operator
- Evidence target: `evidence/q1_zeus_egress_2026-04-26.txt`
- Resolution path: Phase 0.A
- If blocked: document blocker (geofence / TLS / DNS), escalate via Q5/Q6 inquiry channel

### Q2 — What is the exact V2 OrderArgs schema and method signature diff?

- Status: **OPEN**
- Phase gate: Phase 0 → Phase 1 (mandatory; affects 1.B clob_protocol field set)
- Acceptance: documented per-method signature comparison covering every method in `zeus_touchpoint_inventory.md` §1-2
- Owner: operator
- Evidence target: `evidence/q2_sdk_api_diff_2026-04-26.md`
- Resolution path: Phase 0.B
- Source: `py-clob-client-v2` GitHub README + source

### Q3 — Does V2 SDK provide `getClobMarketInfo(conditionID)` returning fee_rate + tick_size + neg_risk in a single call?

- Status: **OPEN**
- Phase gate: Phase 0 → Phase 1 (gates Phase 2 slice 2.F design choice)
- Acceptance: yes/no + sample response shape
- Owner: operator
- Evidence target: `evidence/q3_getclobmarketinfo_capability_2026-04-26.md`
- Resolution path: Phase 0.C
- Affects: deletability of `polymarket_client.get_fee_rate` direct httpx call (`zeus_touchpoint_inventory.md` §1, line 116)

### Q-NEW-1 — Is pUSD a distinct on-chain ERC-20 or a USDC marketing label?

- Status: **RESOLVED-DIVERGENT**
- Phase gate: informs Z4/R1 collateral and redemption semantics
- Acceptance: direct Polygon `eth_call` proves ERC-20 `symbol()` and `name()`
- Owner: completed by R3L3 evidence agent
- Evidence: `docs/operations/task_2026-04-26_ultimate_plan/evidence/down/q_new_1_polygon_rpc_eth_call_2026-04-26_R3L3.md`
- Resolution: pUSD is distinct on-chain identity for Zeus implementation (`symbol() = "pUSD"`, `name() = "Polymarket USD"`). Earlier "pUSD is merely a marketing label" framing is overturned.

### Q-HB — What heartbeat cadence does Polymarket require or recommend for V2 resting orders?

- Status: **OPEN**
- Phase gate: Z3 heartbeat tuning; does not block implementing a fail-closed HeartbeatSupervisor with conservative defaults
- Acceptance: official docs/source/support evidence for cadence or explicit statement that no fixed cadence is mandated
- Owner: operator
- Evidence target: `evidence/q_hb_cadence_2026-04-26.md`
- Resolution path: Polymarket support / Discord / official docs inquiry
- Affects: Z3 default cadence, logging caveats, and G1 heartbeat readiness gate

### Q4 — What V1 fee rates do current Zeus weather tokens actually return?

- Status: **OPEN**
- Phase gate: optional — provides Phase 3 cutover validation baseline
- Acceptance: ≥3 weather token observations
- Owner: operator
- Evidence target: `evidence/q4_live_v1_fee_snapshot_2026-04-26.json`
- Resolution path: Phase 0.E
- Affects: A1 cross-protocol antibody acceptance threshold

### Q5 — What is the USDC.e → pUSD bridge path for existing Gnosis Safe holders?

- Status: **OPEN**
- Phase gate: Phase 1 → Phase 2 (mandatory; gates 2.C pUSD redemption swap)
- Acceptance: documented step-by-step bridge procedure (or confirmation that Polymarket auto-bridges)
- Owner: operator
- Evidence target: `evidence/q5_q6_q7_polymarket_support_inquiry_2026-04-26.md`
- Resolution path: Phase 0.D inquiry to Polymarket support / Discord
- Affects: entire Phase 2.C scope; pUSD funding ops; FX accounting decision

### Q6 — Is V1 EOL announced or planned?

- Status: **OPEN**
- Phase gate: not strictly mandatory but determines packet urgency
- Acceptance: official date or "no plan to deprecate" confirmation
- Owner: operator
- Evidence target: `evidence/q5_q6_q7_polymarket_support_inquiry_2026-04-26.md`
- Resolution path: Phase 0.D
- Affects: phase pacing; if EOL within 30 days, accelerate Phase 2

### Q7 — Does Zeus need to register a builder code for V2 fee-share program?

- Status: **OPEN**
- Phase gate: Phase 2 (gates 2.H builder code field)
- Acceptance: yes/no + registration steps if yes
- Owner: operator
- Evidence target: `evidence/q5_q6_q7_polymarket_support_inquiry_2026-04-26.md`
- Resolution path: Phase 0.D
- Affects: 2.H slice scope (skip if not required)

---

## Strategy-level questions (Phase 2 strategic slices, not migration-blocking)

### S1 — Should Zeus adopt WebSocket book / price_change subscriptions?

- Status: **DEFERRED**
- Rationale: WS adoption changes monitor_refresh from periodic to reactive, which is a strategy-layer decision separate from V2 migration
- Resolution path: independent critic-opus review after Phase 2 closes; not gated by this packet
- Affects: strategy semantics; day0 boundary-ambiguous evaluation surface

### S2 — Should Zeus use post-only flag to avoid taker fees?

- Status: **DEFERRED**
- Rationale: weather market liquidity may be insufficient for maker-only strategy
- Resolution path: requires live V2 fill-rate data from Phase 3 dual-run

### S3 — Should Zeus adopt batch-order submission for day0 multi-market scans?

- Status: **DEFERRED**
- Rationale: partial-failure rollback semantics complex; not on critical path
- Resolution path: post-Phase 4 feature work

---

## FX / accounting questions (Phase 3 prerequisite)

### Q-FX-1 — How is USDC.e ↔ pUSD conversion classified in Zeus PnL?

- Status: **OPEN**
- Phase gate: Z4/R1 redemption/accounting path; must be resolved before any pUSD redemption path can affect PnL reporting
- Acceptance: explicit operator classification: trading PnL inflow, FX line item, carry cost, or another named accounting bucket
- Owner: operator
- Evidence target: `evidence/q_fx_1_classification_decision_2026-04-26.md`
- Runtime default: pUSD redemption path raises classification-pending gate until evidence and env flag are present
- Affects: CollateralLedger, settlement/redeem command ledger, reports, and G1 readiness gate

### F1 — How is USDC.e ↔ pUSD conversion FX classified in Zeus PnL?

- Status: **OPEN**
- Phase gate: Phase 3 cutover runbook must answer this before flip
- Acceptance: explicit classification in 3.C runbook (trading PnL vs carry cost vs separate FX line)
- Owner: operator
- Resolution path: Phase 3.C runbook authoring; informed by Q5 evidence

### F2 — Is pUSD recognized as USDC-equivalent by external accounting / tax / audit consumers?

- Status: **OPEN**
- Phase gate: Phase 3 (operator-side compliance)
- Acceptance: confirmation from Zeus's external accounting context
- Owner: operator
- Resolution path: outside Zeus codebase; may require legal / tax advisor

---

## Resolution log

(Append entries as questions resolve. Format: `YYYY-MM-DD — Q# RESOLVED — evidence path — one-line summary`)

2026-04-27 — Q-NEW-1 RESOLVED-DIVERGENT — `docs/operations/task_2026-04-26_ultimate_plan/evidence/down/q_new_1_polygon_rpc_eth_call_2026-04-26_R3L3.md` — direct Polygon `eth_call` proves `symbol()="pUSD"`, `name()="Polymarket USD"`; pUSD is distinct collateral identity for Zeus implementation.
