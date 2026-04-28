# Polymarket CLOB V2 — Corrected Capability and System Impact Report

Created: 2026-04-26
Last reused/audited: 2026-04-27
Authority basis: R3 Z0 plan-lock; `docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/V2_1_STRUCTURAL_DIFF.md`; `docs/operations/task_2026-04-26_ultimate_plan/evidence/down/converged_R1L1.md`; direct on-chain Q-NEW-1 evidence `docs/operations/task_2026-04-26_ultimate_plan/evidence/down/q_new_1_polygon_rpc_eth_call_2026-04-26_R3L3.md`; prior packet grep/source evidence.
Receipt-bound source: this file

This report supersedes the earlier 2026-04-26 impact report for active implementation planning. The earlier report mixed real V2 risk with several falsified or under-evidenced premises. R3 Z0 keeps the live-money risk framing but corrects the factual substrate before any CLOB V2 execution work proceeds.

---

## 1. Executive verdict

**CLOB V2 is a live-money P0 venue-adapter migration for Zeus.** It is not a strategy rewrite and not a trivial drop-in. It changes the venue boundary that can place, cancel, reconcile, and account for real-money orders.

**Implementation authority now routes through R3**, not the original Phase 1-4 plan body:

- R3 entry: `docs/operations/task_2026-04-26_ultimate_plan/r3/R3_README.md`
- R3 phase plan: `docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md`
- First executable phase: `Z0`, then `Z1`, `Z2`, `Z3`, `Z4`, `U1`, `U2`, ... through `G1`.

### Falsified-premise disclaimer set

The active plan must carry these corrections forward:

1. **pUSD as marketing label disclaimer** — any earlier claim that pUSD is merely a marketing label for USDC is false for Zeus implementation. The on-chain ERC-20 at `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` returns `symbol() = "pUSD"` and `name() = "Polymarket USD"` in the Q-NEW-1 direct `eth_call` evidence. Docs/product wording may describe pUSD as the trading collateral label, but Zeus must treat the on-chain identity as distinct collateral authority.
2. **V1 release date 2026-02-19** — `py-clob-client v0.34.6` is not evidence of V1 being patched two days after V2 GA. The corrected V1 release date is 2026-02-19, two months before V2 GA; this supports the R3 framing that V1/V2 coexistence and cutover timing require operator gates rather than panic migration.
3. **Mandatory 10s heartbeat unsourced** — the exact "10s mandatory heartbeat cancels all orders" claim is not accepted as active law without fresh official/source evidence. R3 still makes HeartbeatSupervisor mandatory for Zeus risk correctness, but the cadence remains Q-HB/operator-evidence governed.
4. **EIP-712 v1→v2 binary switch wrong** — active implementation must not hard-code a deploy-time domain-version binary switch. Versioning/signing behavior is SDK/token resolved and must be verified at the adapter/envelope layer.
5. **`fee_rate_bps` removed is partial-truth** — fee fields may disappear from the submitted order shape, but Zeus's local EV/fee formula remains load-bearing. Fee discovery moves behind venue market-info/provenance, not into deletion of internal EV math.
6. **`delayed` status unsourced** — `delayed` / `ORDER_DELAYED` / `DELAYING_ORDER_ERROR` strings were not sufficiently evidenced in the V2 SDK source checked by the debate packet. Unknown venue statuses still require typed fail-closed handling, but no active implementation may rely on those exact strings without fresh source citation.
7. **`post_only` existed in V1 v0.34.2** — `post_only` must not be treated as exclusively V2-native without version-pinned source evidence. It is still an order-type constraint requiring explicit tests.
8. **heartbeat existed in V1 v0.34.2** — heartbeat-related SDK surface is not by itself proof of a V2-only mandatory cadence. Zeus's runtime requirement is risk-driven: live resting orders need a supervised health mechanism; the venue cadence remains evidence-gated.

---

## 2. Corrected implementation stance

### 2.1 What is real and structural

- **Venue adapter boundary**: live placement must move behind a strict V2 adapter and `VenueSubmissionEnvelope` so every side effect is reconstructable from provenance.
- **Cutover state**: live submit must be blocked unless `CutoverGuard` allows it.
- **Heartbeat supervision**: GTC/GTD resting-order strategies must not submit when heartbeat health is unknown or unhealthy, even while cadence details remain operator-evidence gated.
- **Collateral semantics**: pUSD is BUY collateral; CTF outcome tokens are SELL inventory. A normal exit sell cannot be preflighted by pUSD balance alone.
- **Trade finality**: MATCHED is not CONFIRMED. Calibration and promotion paths must consume confirmed venue facts only.
- **Paper/live parity**: paper mode must exercise the same adapter protocol via a fake venue, not a parallel state machine.

### 2.2 What is not active law

- No code may assume V2 requires an exact 10-second heartbeat cadence until Q-HB evidence lands.
- No code may assume exact `delayed` status spelling without current SDK/API citation.
- No code may assume pUSD is USDC-equivalent for accounting; Q-FX-1 governs PnL classification.
- No code may use the original plan's Phase 1-4 sequence as implementation authority when it conflicts with R3 phase cards.

---

## 3. R3 phase mapping

| Concern | Active R3 owner | Notes |
|---|---|---|
| Plan/source-of-truth correction | Z0 | This report is the Z0 correction artifact. |
| Runtime cutover block | Z1 | `CutoverGuard` prevents live submits before operator/go-no-go. |
| V2 SDK seam and provenance | Z2 | `PolymarketV2Adapter` + `VenueSubmissionEnvelope`; provenance over SDK call shape. |
| Heartbeat health | Z3 | Mandatory health supervisor for live resting orders; cadence evidence remains Q-HB. |
| pUSD/CTF accounting | Z4 | `CollateralLedger` separates buy collateral from sell inventory and reservations. |
| Snapshot and payload lineage | U1/U2 | Fresh executable snapshots and five raw provenance projections. |
| Execution lifecycle | M1-M5 | Unknown side effects, WS/REST facts, cancel safety, reconciliation. |
| Settlement/redeem | R1 | Durable settlement command ledger. |
| Fake venue parity | T1 | Paper and live share one adapter protocol. |
| Dominance infrastructure | F1-F3/A1-A2 | Source/retrain/TIGGE wiring plus benchmark and risk allocator. |
| Final live gate | G1 | 17/17 gates plus staged live smoke and operator deploy command. |

---

## 4. Operator gates preserved

| Gate | Blocks | Default when absent |
|---|---|---|
| Q1-zeus-egress | Z2 production preflight/cutover confidence | live placement blocked / preflight failure |
| Q-HB-cadence | Z3 tuning | default conservative cadence with caveat; no unsourced venue claim |
| Q-FX-1 | Z4/R1 pUSD redemption/accounting | redemption path raises classification-pending gate |
| CLOB v2 cutover | Z1 `LIVE_ENABLED` transition | `PRE_CUTOVER_FREEZE` / no live submit |
| TIGGE ingest | F3 active ingest | dormant, fetch raises gate-not-enabled |
| Calibration retrain | F2 live parameter promotion | existing Platt parameters stay frozen |
| G1 live-money deploy | final production flip | no live-money deployment |

---

## 5. Live-money contract summary

The packet-local live-money contract is `polymarket_live_money_contract.md`. Its constraints are deliberately infrastructure-level and do not claim alpha dominance by themselves. R3 must make every venue side effect reconstructable, every live submit gateable, every fill finality state explicit, and every capital allocation bounded before live-money cutover.

---

## 6. Status

Status: **V2_ACTIVE_P0 under R3**.

This report is corrected enough for Z0 acceptance. It does not authorize Z1+ implementation by itself; subsequent phases still require their own drift checks, topology navigation, operator-gate checks, acceptance tests, critic/verifier review, and phase status updates.
