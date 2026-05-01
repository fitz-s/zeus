# Zeus Ultimate Plan R3 — Live-money execution + dominance

Created: 2026-04-26 (post multi-review + V2.1 merger + user directive on TIGGE/training operator gates)
HEAD anchor: `874e00cc0244135f49708682cab434b4d151d25d` (`main`)
Decomposition: lifecycle phases (Z foundation → U snapshot/provenance → M execution → R settlement → T parity → F forecast → A strategy/risk → G gates)

This document is the implementation contract. A cold-start agent (compacted, zero-context, fresh session) reads:
1. `R3_README.md` — packet navigation + memory cross-references.
2. THIS file — phase-by-phase synthesis + DAG.
3. `slice_cards/<phase_id>.yaml` — implementation contract per phase.
4. `operator_decisions/INDEX.md` — gates blocking phases.
5. `dependency_graph_r3.mmd` + `slice_summary_r3.md` — critical path + per-phase budget.

---

## §0 What "live + dominate" means

Per original prompt: **after R3 implementation, Zeus must be able to (i) trade live real money on Polymarket V2 with no silent S0 losses and (ii) dominate the live market — alpha + execution + risk wired so edge can compound.** Per user directive 2026-04-26: **data ingest + training (e.g., TIGGE) is operator-decision; wiring must be ready.**

R3 satisfies all three:
- Z + U + M + R + T phases close the safety bar (Wave A+B+C+D+E equivalent of R2 with V2.1 hardening).
- A1 + A2 close the dominance bar (no strategy goes live without StrategyBenchmarkSuite PROMOTE; capital deployment bounded by RiskAllocator).
- F1 + F2 + F3 wire the data + training plumbing — TIGGE ingest path exists but is gated by operator decision artifact + env flag (NC-NEW-J).
- G1 is the final 17-gate CI matrix that a single command can verify.

---

## §1 Phase DAG (top-level)

```
                   ┌─→ Z3 heartbeat ─────────┐
Z0 plan-lock ─→ Z1 cutover-guard ─→ Z2 V2-adapter ─┤                         ├─→ U1 snapshot-v2 ─→ U2 5-projection-schema
                                                   └─→ Z4 collateral-ledger ─┘                                  │
                                                                                                                ↓
                                                                                                    M1 lifecycle-grammar
                                                                                                                ↓
                                                                                                    M2 unknown-side-effect
                                                                                                                ↓
                                                                                                    M3 user-channel-ws
                                                                                                                ↓
                                                                                                    M4 cancel-replace
                                                                                                                ↓
                                                                                                    M5 reconcile-sweep
                                                                                                       ↓                   ↓
                                                                                                R1 settlement      T1 fake-venue
                                                                                                                                  ↓
                                                                F1 forecast-source-registry ────→ F2 calibration-retrain ←─ A1 benchmark-suite
                                                                                ↓
                                                                F3 TIGGE-ingest-stub
                                                                                ↓                                                  ↓
                                                                                                                       A2 risk-allocator
                                                                                                                                  ↓
                                                                                                            G1 live-readiness-gates
```

Mermaid version: `dependency_graph_r3.mmd`. Critical path: **256h** along
`Z0 → Z1 → Z2 → Z4 → U1 → U2 → M1 → M2 → M3 → M4 → M5 → T1 → A1 → A2 → G1`.

Total budget: **312h** across 20 phase cards. Realistic high-end: 380-440h with 20-40% buffer.

---

## §2 Phase summaries (one paragraph per card; full detail in slice_cards/)

### Z phase — Foundation (5 cards, 68h)

- **Z0** plan-lock + source-of-truth rewrite (4h, low risk) — Doc-only slice. Replace stale inactive-tracker language with `V2_ACTIVE_P0`, rewrite `impact_report` v2 with falsified-premise disclaimers (8 R2 multi-review premises), add `polymarket_live_money_contract.md` listing the 8 V2 invariants. CI grep enforces no stale language.
- **Z1** CutoverGuard (12h, high risk, critic-opus) — Replace operator-runbook with code: state machine NORMAL → PRE_CUTOVER_FREEZE → CUTOVER_DOWNTIME → POST_CUTOVER_RECONCILE → LIVE_ENABLED. Operator-token-signed transitions only; runtime gate prevents live submit when state ≠ LIVE_ENABLED.
- **Z2** V2 strict adapter + VenueSubmissionEnvelope (18h, high risk, critic-opus) — `src/venue/polymarket_v2_adapter.py` is the ONLY live placement surface. Pin envelope (provenance), not seam (NC-NEW-G). Replaces R2 X1 seam-pinning. Removes `py-clob-client` from live deps.
- **Z3** HeartbeatSupervisor MANDATORY (12h, high risk, critic-opus) — Async coroutine + placement gate. GTC/GTD blocked when `health != HEALTHY`. Reuses existing apscheduler tombstone (NC-NEW-F single-tombstone preserved). Promoted from R2 down-06 D2-gated to required.
- **Z4** CollateralLedger (22h, high risk, critic-opus) — Replaces R2 down-07 `balanceOf` rewire with multi-asset ledger: pUSD balance + allowance + CTF token balance per outcome + reserved buy/sell sizes + wrap/unwrap durable commands + legacy USDC.e classification. NC-NEW-K: `sell_preflight` cannot substitute pUSD for token inventory.

### U phase — Snapshot + provenance (2 cards, 42h)

- **U1** ExecutableMarketSnapshotV2 (14h, medium risk, critic-opus) — Append-only table (NC-NEW-B preserved via SQLite triggers). Every venue_command MUST cite a fresh snapshot whose token id / tick / min size / fee / neg_risk match intent. Freshness gate at `venue_command_repo.insert_command` (Python single-insertion-point). Replaces R2 up-03 + up-07.
- **U2** Raw provenance schema — 5 distinct projections (28h, high risk, critic-opus) — Splits R2's compressed CommandState grammar into 5 tables: `venue_commands` (intent+submit) + `venue_order_facts` (RESTING/MATCHED/PARTIALLY_MATCHED/CANCEL_*) + `venue_trade_facts` (MATCHED/MINED/CONFIRMED/RETRYING/FAILED) + `position_lots` (OPTIMISTIC vs CONFIRMED exposure) + `venue_submission_envelopes`. NC-NEW-H: calibration training filters `WHERE state='CONFIRMED'`. NC-NEW-I: risk allocator separates OPTIMISTIC vs CONFIRMED.

### M phase — Execution lifecycle (5 cards, 70h)

- **M1** Lifecycle grammar (14h, high risk, critic-opus) — INV-29 amendment commit + planning-lock receipt required to merge. Extends CommandState with INTENT_CREATED / SNAPSHOT_BOUND / SIGNED_PERSISTED / POSTING / POST_ACKED / SUBMIT_UNKNOWN_SIDE_EFFECT etc. cycle_runner-as-proxy lock for RED→durable-cmd (NC-NEW-D function-scope antibody). RESTING NOT in CommandState (NC-NEW-E).
- **M2** Unknown-side-effect semantics (10h, high risk, critic-opus) — Replace `status='rejected'` for post-POST exceptions with `unknown_side_effect`. NC-19 idempotency_key dedup + economic-intent fingerprint. Reconciliation converts unknown → ACKED/FILLED/SAFE_REPLAY_PERMITTED.
- **M3** User WebSocket ingest + REST fallback (16h, high risk, critic-opus) — `src/ingest/polymarket_user_channel.py`. WS gap detection → forces M5 sweep before new submit. Closes Apr26 axis-45 + axis-24. Replaces R2 mid-07 decision-slice with WS-first explicit.
- **M4** Cancel/replace + exit safety (12h, high risk, critic-opus) — Mutex per (position, token) + typed CancelOutcome parser. Exit preflight uses Z4 token reservations, not pUSD. CANCEL_UNKNOWN blocks replacement.
- **M5** Exchange reconciliation sweep (18h, high risk, critic-opus) — Bulk diff exchange truth vs journal. `exchange_reconcile_findings` table records ghost-orders / orphans / unrecorded-trades / position-drift / heartbeat-suspected-cancel / cutover-wipe. Findings → operator review queue (closes R2 multi-review architect's "antibody-without-actuator" liability).

### R phase — Settlement (1 card, 12h)

- **R1** Settlement / redeem command ledger (12h, medium risk, critic-opus) — `settlement_commands` table with REDEEM_INTENT_CREATED → REDEEM_SUBMITTED → REDEEM_TX_HASHED → REDEEM_CONFIRMED. Crash-recoverable via tx_hash recovery. K6 deferral from R2 lands here.

### T phase — Paper/live parity (1 card, 22h)

- **T1** FakePolymarketVenue (22h, high risk, critic-opus) — Implements PolymarketV2Adapter Protocol exactly. Failure-injection knobs: TIMEOUT_AFTER_POST / NETWORK_JITTER / ORACLE_CONFLICT / RESTART_MID_CYCLE / HEARTBEAT_MISS / OPEN_ORDER_WIPE / CANCEL_NOT_CANCELED / etc. Closes Apr26 axis-31 + axis-49 + 5 unowned P0 tests. Paper and live use SAME adapter Protocol.

### F phase — Forecast pipeline plumbing (3 cards, 32h)

This is the layer that addresses the user directive: **wired but operator-gated**.

- **F1** Forecast source registry (12h, medium risk, critic-opus) — Typed source registry: existing primary sources (ECMWF open data, openmeteo, etc.) ungated; new sources (TIGGE, GFS) gated by operator-decision artifact + env flag. NC-NEW-J: gated source raises `SourceNotEnabled`. Extends `src/data/forecasts_append.py` to persist `source_id` + `raw_payload_hash` + `authority_tier` per row.
- **F2** Calibration retrain loop (14h, high risk, critic-opus) — Operator-armed retrain trigger (operator token + evidence file required). Frozen-replay harness asserts P_raw → Size is bit-identical pre/post on 3 fixture portfolios (R2 up-08 carries forward). Drift detection blocks promotion. New `calibration_params_versions` table holds versioned params.
- **F3** TIGGE ingest stub (6h, medium risk, critic-opus) — Registered in F1 registry with `tier='experimental'` + dual-gate (artifact + ZEUS_TIGGE_INGEST_ENABLED env flag). `TIGGEIngest.fetch()` raises `TIGGEIngestNotEnabled` until operator flips both gates, then reads only an operator-approved local JSON payload path; external TIGGE archive HTTP/GRIB remains a later packet. Code path lands; ingest dormant by default.

### A phase — Strategy + risk (2 cards, 52h)

Pulled in from R2 §4 deferred Dominance Roadmap per user mandate "live trade and dominate".

- **A1** StrategyBenchmarkSuite (30h, high risk, critic-opus) — Standardized metrics (EV after fees+slippage, realized spread capture, fill probability, adverse selection, time-to-resolution risk, drawdown, calibration error vs market-implied). Strategy promotion gate: replay → paper → live-shadow tests must all PASS. New `strategy_benchmark_runs` table.
- **A2** RiskAllocator + PortfolioGovernor (22h, high risk, critic-opus) — Caps per-market / per-event / per-resolution-window. Drawdown governor + kill switch. Maker/taker mode based on book depth + heartbeat health + resolution deadline. Reduce-only mode when degraded. NC-NEW-I: sizing distinguishes OPTIMISTIC vs CONFIRMED exposure.

### G phase — Live readiness gates (1 card, 14h)

- **G1** 16 active CI gates + staged-live-smoke (14h, medium risk, critic-opus + operator) — Single command (`scripts/live_readiness_check.py`) runs all active gates: V2 SDK / Heartbeat / pUSD / Sell-token / Snapshot / Provenance / Order-type / Unknown / Matched-not-final / User-channel / Cancel-replace / Cutover-wipe / Crash / Paper-live-parity / Strategy-benchmark / Agent-docs. Each gate maps to a specific R3 antibody. INV-NEW-S: LIVE deploy requires 16/16 active PASS + ≥1 staged-live-smoke environment passing the same.

---

## §3 Antibody system (NC-NEW-A..S)

| ID | Owner phase | Rule |
|---|---|---|
| NC-NEW-A | U2 | No `INSERT INTO venue_commands` outside `src/state/venue_command_repo.py` |
| NC-NEW-B | U1 | `executable_market_snapshots` APPEND-ONLY (SQLite triggers + semgrep + Python encapsulation) |
| NC-NEW-C | (R2 carry) | `ClobClient.create_order()` allowlist of 3 callers |
| NC-NEW-D | M1 | Function-scope: `cycle_runner._execute_force_exit_sweep` is SOLE caller of `insert_command(IntentKind.CANCEL, reason='red_force_exit_proxy', ...)` within cycle_runner.py |
| NC-NEW-E | M1 | RESTING is NOT a CommandState member; lives in `venue_order_facts.state` |
| NC-NEW-F | Z3 | Single-tombstone: `state/auto_pause_failclosed.tombstone` reused by HeartbeatSupervisor |
| NC-NEW-G | Z2 | Provenance pinned at `VenueSubmissionEnvelope` contract, NOT specific SDK call shape |
| NC-NEW-H | U2 | Calibration training paths filter `venue_trade_facts WHERE state='CONFIRMED'`; SELECTing MATCHED for training raises ValueError |
| NC-NEW-I | U2 | Risk allocator separates OPTIMISTIC_EXPOSURE from CONFIRMED_EXPOSURE in capacity check |
| NC-NEW-J | F3 | TIGGEIngest.fetch() raises TIGGEIngestNotEnabled when operator gate closed |
| NC-NEW-K | Z4 | `sell_preflight` ONLY consults CTF token balance + reservations; cannot substitute pUSD |
| INV-NEW-A | Z1 | No live submit when CutoverGuard.current_state() != LIVE_ENABLED |
| INV-NEW-B | Z2 | Every submit() call produces a VenueSubmissionEnvelope persisted via venue_command_repo BEFORE side effect |
| INV-NEW-C | Z3 | GTC/GTD orders MUST NOT be submitted when HeartbeatSupervisor.status().health != HEALTHY |
| INV-NEW-D | Z4 | Reserved tokens for an open sell command MUST be released atomically when command transitions to CANCELED/FILLED/EXPIRED |
| INV-NEW-E | U1 | Every venue_commands row MUST cite a `snapshot_id`; freshness gate enforced in `venue_command_repo.insert_command` |
| INV-NEW-F | U2 | Every fact in 5 projections has source + observed_at + local_sequence; events from REST and WS_USER reconcile by trade_id |
| INV-NEW-G | M2 | Network/timeout exceptions after POST request leaves SDK MUST create SUBMIT_UNKNOWN_SIDE_EFFECT, never SUBMIT_REJECTED |
| INV-NEW-H | M3 | WS gap detected → block new submit + force M5 sweep before unblocking |
| INV-NEW-I | M4 | Replacement sell BLOCKED until prior sell reaches CANCEL_CONFIRMED, FILLED+CONFIRMED, EXPIRED, or proven absent |
| INV-NEW-J | M4 | Exit mutex per (position_id, token_id) is single-holder |
| INV-NEW-K | M5 | M5 sweep is read-only against venue + journal; never INSERTs into venue_commands |
| INV-NEW-L | R1 | Settlement transitions are durable + crash-recoverable; REDEEM_TX_HASHED is recovery anchor |
| INV-NEW-M | T1 | Paper-mode runs go through SAME PolymarketV2Adapter Protocol; FakePolymarketVenue and live adapter produce schema-identical events |
| INV-NEW-N | F1 | Every forecast row carries `source_id` + `raw_payload_hash` + `authority_tier` |
| INV-NEW-O | F2 | Calibration retrain consumes ONLY `venue_trade_facts WHERE state='CONFIRMED'` |
| INV-NEW-P | F2 | Calibration param promotion to live REQUIRES frozen-replay PASS |
| INV-NEW-Q | A1 | No strategy promoted to live without StrategyBenchmarkSuite.promotion_decision() returning PROMOTE |
| INV-NEW-R | A2 | Kill switch trips on (reconcile_finding_count > N) OR (heartbeat_lost) OR (ws_gap_seconds > M) OR (unknown_side_effect_count > K) |
| INV-NEW-S | G1 | LIVE deployment requires 16/16 active G1 gate PASS + ≥1 staged-live-smoke environment passing same |

---

## §4 Operator decision register (8 gates)

Full register in `operator_decisions/INDEX.md`. Quick reference:

| Gate | Phase blocked | Default if absent |
|---|---|---|
| Q1-zeus-egress | Z2 cutover | engineering proceeds, cutover BLOCKED |
| Q-HB-cadence | Z3 tuning | default 5s used |
| Q-FX-1 | Z4 redemption + R1 | FXClassificationPending raised |
| INV-29 amendment | M1 merge | M1 PR fails CI |
| TIGGE-ingest go-live | F3 fetch | TIGGEIngestNotEnabled raised; open gate without local payload raises TIGGEIngestFetchNotConfigured |
| Calibration retrain | F2 promotion | engine reads frozen Platt params |
| CLOB v2 cutover | Z1 LIVE_ENABLED | CutoverGuard stays in PRE_CUTOVER_FREEZE |
| Impact-report rewrite | Z0 critic gate | Z0 not closed |

---

## §5 R2 → R3 mapping (R2 work survives)

The 23 R2 slice cards remain at `../slice_cards/{up,mid,down}-NN.yaml`. Each R3 card lists which R2 cards it absorbs in its `links.r2_cards` field. R2 file:line citations + multi-review hardening + NC-NEW-A..F antibodies all carry forward.

| R2 card | R3 phase | Note |
|---|---|---|
| up-01..03 | U1 | Truth contract + OrderSemantics + Snapshot table |
| up-04 | U2 | 15-col ALTER absorbed; new fields in U2 schema |
| up-05 | U2 | SignedExecutionEnvelope replaced by VenueSubmissionEnvelope (Z2) |
| up-06 | G1 | UNVERIFIED rejection matrix → live-readiness gate |
| up-07 | U1 | Snapshot freshness gate → part of U1 |
| up-08 | F2 | Frozen-replay harness → part of F2 + drift detection |
| mid-01 | M1 | RED→durable-cmd; cycle_runner-as-proxy ownership lock |
| mid-02 | Z2 + U2 | Signer interception → VenueSubmissionEnvelope contract |
| mid-03 | M1 + M2 | State grammar amendment + SUBMIT_UNKNOWN |
| mid-04 | M1 + U2 | PARTIAL → trade-facts MATCHED/MINED/CONFIRMED |
| mid-05 | M5 | Exchange reconciliation sweep |
| mid-06 | G1 | Relationship tests → readiness gates |
| mid-07 | M3 | WS-or-poll → user-channel-ws-first with REST fallback |
| mid-08 | T1 | Failure injection → fake venue parity |
| down-01 | Z2 | V2 SDK swap → strict adapter |
| down-02 | Z0 | D0 questions → operator-decision register |
| down-03 | Z2 | V2 SDK contract antibody |
| down-04 | operator_decisions/q1_zeus_egress.md | |
| down-05 | Z0 | Status amendment |
| down-06 | Z3 | HeartbeatSupervisor MANDATORY |
| down-07 | Z4 | CollateralLedger expanded scope |

---

## §6 Wave plan + deployment sequence

R3 ships in 6 waves. Each wave gates the next; G1 is the cutover gate.

**Wave A** — Foundation (~68h, Z phase): Z0 → Z1 → Z2 → Z3 + Z4 in parallel after Z2.
- Pre-conditions: HEAD = 874e00cc + multi-review re-grep (R2 carry) + impact_report rewrite operator gate.
- Outcomes: V2 adapter + heartbeat + collateral ledger + cutover guard. Engineering can author Wave B in parallel after Z2.

**Wave B** — Snapshot + provenance + lifecycle (~126h, U + M phase):
U1 → U2 → M1 (gated on INV-29 amendment) → M2 → M3 → M4 → M5.
Outcomes: full 5-projection state machine + WS ingest + reconciliation sweep.

**Wave C** — Settlement + parity (~34h, R + T phase): R1 + T1 (parallel).
Outcomes: settlement command ledger + paper/live parity fake venue.

**Wave D** — Forecast plumbing (~32h, F phase, parallelizable with Wave C): F1 → F2 + F3.
Outcomes: forecast source registry + calibration retrain wiring + local-payload TIGGE stub. Operator can flip local TIGGE / retrain switches without code change; external TIGGE archive HTTP/GRIB is a later data-source packet.

**Wave E** — Dominance (~52h, A phase): A1 → A2.
Outcomes: StrategyBenchmarkSuite + RiskAllocator + PortfolioGovernor.

**Wave F** — Live readiness + cutover (~14h + operator time, G phase): G1 → operator runs `scripts/live_readiness_check.py` → if 16/16 active gates PASS and staged smoke is green, operator dispatches CutoverGuard transition `PRE_CUTOVER_FREEZE → CUTOVER_DOWNTIME → POST_CUTOVER_RECONCILE → LIVE_ENABLED`.
Outcomes: Zeus is live.

**Total wall-clock estimate**: 312h engineering + operator gate time. With 2-3 engineers in parallel and operator-decision turnaround ~1-3 days per gate, **realistic delivery: 5-8 weeks**.

---

## §7 What "dominate live market" looks like post-G1

- Every order Zeus places is reconstructable from raw payload provenance (U2 + Z2 envelope).
- Every order Zeus places is heartbeat-protected (Z3).
- Every cancel has a typed outcome (M4 CANCELED / CANCEL_FAILED / CANCEL_UNKNOWN).
- Every fill is split MATCHED → MINED → CONFIRMED with calibration consuming CONFIRMED only (NC-NEW-H).
- Every redemption is durable command-journal entry (R1).
- Every collateral call distinguishes pUSD-buy from CTF-token-sell (Z4).
- Paper and live use SAME state machine via T1 fake venue.
- A1 benchmark gates strategy promotion.
- A2 risk allocator caps deployment per market / event / resolution-time / drawdown.
- F1+F2+F3 forecast plumbing is wired so operator can flip local TIGGE ingest + calibration retrain switches without code changes; external TIGGE archive HTTP/GRIB is not yet authorized.
- G1 17-gate CI matrix asserts the safety bar holds.

That is the minimum infrastructure for "Zeus dominates live market" per the original prompt. Once Wave F closes, edge can compound rather than being erased by execution-state pollution.

---

## §8 Multi-review verdict (R3 inheritance)

R2's multi-review (architect / critic / explore / scientist / verifier) verdict was APPROVE_WITH_CONDITIONS. R3 adopts V2.1's structural moves to close the remaining gaps:

| R2 multi-review concern | R3 closure |
|---|---|
| seam-pinning brittle to SDK refactor | NC-NEW-G envelope-pinning (Z2) |
| MATCHED ≠ CONFIRMED collapsed | 5-projection split (U2) + NC-NEW-H |
| OPTIMISTIC vs CONFIRMED exposure missing | position_lots state column (U2) + NC-NEW-I + A2 |
| Cutover as runbook | Z1 CutoverGuard runtime states |
| Heartbeat conditional | Z3 mandatory + INV-NEW-C |
| pUSD as balanceOf rewire | Z4 CollateralLedger full ledger + NC-NEW-K |
| 5 unowned Apr26 P0 tests | T1 FakePolymarketVenue P0 suite |
| 0 cards improve edge / forecast / learning untouched | F1 + F2 + F3 + A1 + A2 |
| EIP-712 determinism unverified | Z2 critic-opus gate; if non-deterministic, redesign per R2 mid-02 amendment |
| Citation drift compounds | Z0 plan-lock + 10-min re-grep before each contract lock |

R3 must itself be multi-reviewed before declaring DEBATE_CLOSED — see memory `feedback_multi_angle_review_at_packet_close`.

---

## §9 Cold-start agent quick-start

If you are an agent reading this for the first time:

1. Confirm HEAD is `874e00cc` or run `git log --oneline 874e00cc..HEAD` to map drift.
2. Pick a phase whose `depends_on:` is satisfied. Z0 is always available.
3. Open `slice_cards/<phase_id>.yaml` and read:
   - `preconditions.required_files` — verify they exist and contain expected content.
   - `preconditions.required_invariants` — confirm INV-23..INV-32, NC-16..NC-19 are intact.
   - `deliverables` — exactly what to write.
   - `acceptance_tests` — the contract.
   - `operator_decision_required` — STOP if any gate is `blocking: yes` and artifact missing.
4. Implement. Run pytest on the new tests. If `critic_gate: critic-opus`, dispatch reviewer.
5. After PR merges, register the slice card as COMPLETE in a sibling tracker file.
6. Re-run `python3 r3/scripts/aggregate_r3_cards.py` to update `dependency_graph_r3.mmd`.
7. Pick the next phase whose `depends_on:` is now satisfied.

If you are blocked: write `r3/_blocked_<phase_id>.md` describing the precondition failure + suggested resolution. Do NOT attempt to bypass operator gates.

---

## Appendix: R3 artifact locations

- `R3_README.md` — entry point
- `ULTIMATE_PLAN_R3.md` — this file
- `slice_cards/<phase_id>.yaml` — 20 phase cards (Z0..Z4, U1..U2, M1..M5, R1, T1, F1..F3, A1..A2, G1)
- `operator_decisions/INDEX.md` — 8-gate register
- `operator_decisions/<gate_id>.md` — per-gate dossier (TBD as gates land)
- `dependency_graph_r3.mmd` — Mermaid graph
- `slice_summary_r3.md` — auto-generated card summary + critical path
- `scripts/aggregate_r3_cards.py` — aggregator script
- `reference_excerpts/` — frozen excerpts of external docs (Polymarket V2 docs, py-clob-client-v2 SDK, TIGGE archive access) — operator captures so cold-start agent doesn't need network
- `../evidence/multi_review/` — R2 multi-review reports + V2.1 structural diff
- `../slice_cards/{up,mid,down}-NN.yaml` — R2 23 cards (detailed source for R3 phases)
- `../evidence/{up,mid,down}/converged_R<N>L<N>.md` — R2 debate evidence

End of plan. Write code.
