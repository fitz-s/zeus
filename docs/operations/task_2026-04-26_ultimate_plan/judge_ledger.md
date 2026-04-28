# Judge Ledger — Zeus Ultimate Plan Debate

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)

## Active state (judge keeps minimal in-context)

- current_phase: 0 (solo context boot, all 6 teammates)
- current_region: parallel (all 3)
- current_round: pre-R1
- pending_acks: 6 (proponent/opponent × Up/Mid/Down)

## Debate progress

| Region | Phase | Round | Layer | Status | Slice cards minted |
|---|---|---|---|---|---|
| Up    | 2 R2L2 | dispatched | data-provenance | active (sequential lead) | 5 minted; may grow |
| Mid   | 1 R1L1 | CLOSED | architecture | converged K=5 | mid-01..06 minted; PAUSED |
| Down  | 1 R1L1 | CLOSED | architecture | even-smaller reframe (5 BUSTED-HIGH + 8 R2 vectors) | pending mint; PAUSED |
| X1    | — | — | — | not_started | — |
| X2    | — | — | — | not_started | — |
| X3    | — | — | — | not_started | — |
| X4    | — | — | — | not_started | — |

## Routing yaml summary (heuristic — teammates re-verifying in boot)

- Total findings: 31 (F-001..F-012 + 17 §8.3 transitions + schema_event_v1 + provenance_audit)
- Existing-slice routes: 8 (A1×2, A3×2, C1×1, D1×1, D2×2)
- NET_NEW: 23 → 3 proposed umbrella slices: EXECUTION_ORDER_COMMAND_JOURNAL / EXECUTION_STATE_MACHINE / EXECUTION_RECONCILIATION_LOOP
- Plan coverage: 25.8%
- Structural insight: V2 plan is transport-only; Apr26 review is execution-correctness gaps. Mostly orthogonal.
- Caveat: routing executor flagged 0 shipped_pre_HEAD which is likely wrong (INV-30 should discharge F-001) — opponent-mid will reverify.

## Forbid-rerun list (do NOT re-debate)

- INV-23..INV-32, NC-16..NC-19 — landed law on `main` HEAD `874e00cc`.
- PR18 P0 + P1 — discharged by merges into `main` up to `874e00c`.
- Wave 1 closed: G6 `task_2026-04-26_g6_live_safe_strategies/`, G10-scaffold `task_2026-04-26_g10_ingest_scaffold/`, G10-helper-extraction `task_2026-04-26_g10_helper_extraction/`, B4-physical-bounds `task_2026-04-26_b4_physical_bounds/`, B5-DST `task_2026-04-26_b5_dst_antibody/`, U1 `task_2026-04-26_u1_hk_floor_antibody/`.
- PR #19 phases 1-5 — see `docs/operations/task_2026-04-26_full_data_midstream_fix_plan/` (heavy integration C1 still open in §11 deferred).

## Slice card index (maintained as cards land; judge does NOT read card bodies)

Up (5 minted):
- up-01..05 minted on disk. Final K=5 (K=10 retracted by opponent-up).

Mid (6 minted):
- mid-01 A1 RED→durable-cmd
- mid-02 PAYLOAD_BIND (depends_on: up-04 per X-UM-1 routing; column scope reduced to 3 signing hashes)
- mid-03 STATE_GRAMMAR_AMEND (A3 folded in)
- mid-04 PARTIAL/RESTING (A3 folded in)
- mid-05 EXCH_RECON
- mid-06 §8.3 compat + C1.5
Sub-sequence: mid-02 BEFORE mid-01.

Down: pending mint after R1L1 close.

## Key signals from boot phase

- Routing yaml's "0% shipped" is grep-verifiably FALSE for F-001 cluster (INV-30 + venue_commands implement most of it).
- §8.3 17 transitions reduce to 3-4 K-decisions (Fitz #1 confirmed in Mid).
- Apr26 review's F-007 design language is `ExecutableMarketSnapshot` (table+gate), NOT a value-object — proponent-up's `MarketIdentity` is a departure (opponent-up Attack-A1 wins this).
- Layer 1 architecture is converging on: 2-3 NET_NEW upstream packets (snapshot table, raw-payload schema, exchange-side reconciliation loop) + extensions to existing venue_commands.
- **Down WebFetch audit: 5 BUSTED-HIGH plan premises** (commit a2ec069f, py-clob-client-v2 v1.0.0, 2026-04-17):
  - pUSD is FICTIONAL (SDK uses USDC) — Phase 2.C / Q5 / F1 / F2 = dead scope
  - EIP-712 v1→v2 binary switch is wrong — SDK does per-token resolution
  - Mandatory 10s heartbeat is unsourced — drop heartbeat_supervisor unless Polymarket cite found
  - fee_rate_bps "removed" is false — slice 2.F over-scoped
  - "delayed" status set unsourced — M2/2.A capital-leak narrative needs cite
  - **V2 SDK is unified V1+V2 client** — if adopted as unified, plan collapses 4 phases → 1 phase
  - clob-v2.polymarket.com/version → 200 OK (Q1 partial answer; operator must still probe from Zeus egress)
- **Region-Down's actual scope is SMALLER, not larger, than the plan as written.** Major X-cut implication.
- **F-001 ROW-state CLOSED by INV-30 (state=SUBMITTING pre-submit). F-001 PAYLOAD-bytes RESIDUAL** (no signed_order_hash column at venue_command_repo.py:194-242). opponent-mid AV-1 wins — judge ledger amended.
- §8.3 17 transitions reduce: 4 already exist (REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED, RECONCILED_BY_POSITION) → 11 missing → opponent-mid K=6 (K1 payload-bind, K2 error-typing, K3 book-resting grammar, K4 cancel-failure terminal, K5 exchange-recon, K6 settlement-redeem).
- 8 BUSTED-HIGH plan premises in Down (5 from R1 + 3 from R2): V1 release date 2026-02-19 not 2026-04-19 (impact-report transcription error); heartbeat existed in V1 v0.34.2 (2026-01-06); post_only existed in V1 v0.34.2; PyPI installable (slice 1.G CLOSED).

## Cross-region pending questions

- **X-UD-1** (Up↔Down F-011 sequencing on D-phase collapse) → ROUTED to X4
- **X-MD-1** (Mid↔Down signing-surface seam) → ROUTED to X1
- **X-UM-1** (Up↔Mid `condition_id` coordinated migration) → ROUTED to X3 (up-04 single ALTER TABLE; mid depends on up-04)
- **Q-NEW-1** (operator) — query symbol()/name() on `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` via Polygonscan to resolve pUSD vs USDC label dispute. Polygonscan + raw GitHub config.py both blocked from judge; docs.polymarket.com confirms "pUSD" verbatim without defining separate ERC-20. **RESOLVED-DIVERGENT 2026-04-26 11:12**: opponent-down dispatched direct Polygon RPC eth_call (polygon.drpc.org) → symbol()="pUSD" name()="Polymarket USD". pUSD IS distinct ERC-20, contract repurposed from historical USDC.e. **Judge earlier "marketing label" ruling was WRONG.** Phase 2.C / Q5 / F1 / F2 NOT dead scope; down-07 activates with real balanceOf() rewire + redemption path; F1 (FX classification) re-opens.

## Process adjustments

### Sequential mode (2026-04-26 ~11:08)

Operator directive: parallel 3-region debate creating chaos with cross-region message crossings. After R1L1 closes across all regions, switch to SEQUENTIAL: drive Up through L2+L3 alone, then Mid, then Down, then cross-cuts.

### One-pair mode (2026-04-26 ~11:13)

Operator directive: token consumption exponential. Consolidate from 6 teammates → 2. Up pair (proponent-up + opponent-up) is the SOLE remaining pair. After Up R2L2 + R3L3 close, the Up pair re-briefs on Mid + Down by reading on-disk evidence (`evidence/<region>/_context_boot_*.md` + `converged_R1L1.md` + `slice_cards/<region>-NN.yaml`) and runs L2/L3 for those regions. Then cross-cuts X1-X4.

Mid teammates: shutdown_request sent 11:13. Down teammates: shutdown after Q-NEW-1 amendment + 7 down-NN cards mint completes.

Token discipline mandated for the Up pair:
- ≤500 chars per A2A turn
- ≤200 char converged HARD limit
- file:line + grep > discussion
- reuse on-disk evidence; never re-cite

## Side-channel fixes

(empty — see side_channel_fixes.md)
