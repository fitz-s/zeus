# Region-Down R1L1 Converged Result

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Round: R1L1 (Layer 1 architecture / authority order)
Status: SIGNED — both teammates concur

---

## Consensus

V2 packet (`docs/operations/task_2026-04-26_polymarket_clob_v2_migration/`) is structurally OVER-SCOPED relative to actual V2 SDK reality at commit `a2ec069f` (py-clob-client-v2 v1.0.0, 2026-04-17). The plan models a 4-phase migration (Phase 0 → Phase 4) around three "paradigm shifts" (transport, collateral, state machine), most of which are falsified by SDK-source evidence.

V2 collapses to:

- **D0 (operator, ≤4h)**: 3 questions only — Q1-zeus-egress (host probe FROM ZEUS DAEMON MACHINE, with `funder_address` headers), Q-NEW-1 (Polygonscan `name()`/`symbol()` on `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` to disambiguate pUSD vs USDC label dispute), Q-HB (Polymarket support / Discord inquiry on whether mandated heartbeat cadence exists).
- **D1 (single small slice, replaces Phases 1+2 combined)**: drop-in unified-SDK swap. Replace `from py_clob_client.client import ClobClient` with `from py_clob_client_v2.client import ClobClient` in `src/data/polymarket_client.py`. Add `py-clob-client-v2>=1.0.0` to `requirements.txt`. Replace direct httpx `/fee-rate` call with `clob.get_fee_rate_bps(token_id)`. **PRESERVE** the existing two-step `signed = self._clob_client.create_order(args); result = self._clob_client.post_order(signed)` pattern at `src/data/polymarket_client.py:160-161`; do NOT migrate to V2's convenience `create_and_post_order` wrapper. Seam preservation is required by X1 ruling so that Mid's mid-02 (signed_order_hash + SIGNED_ORDER_PERSISTED) can intercept at `signer.sign(order)` for both V1 and V2-shape orders. Run Zeus regression. SDK's `_resolve_version()` does per-token routing internally.
- **D2 (CONDITIONAL on D0 answers, may be empty)**: D2.B apscheduler-job-4 V2-heartbeat (only if Q-HB confirms cadence; tombstone-before-os._exit ordering encoded; parallel `_clob_v2_heartbeat_fails` counter, fail-soft via tombstone, no `os._exit`). D2.C collateral-branch (only if Q-NEW-1 reveals on-chain `symbol()` ≠ USDC variant; lazy branch in `get_balance` + `harvester` redemption). Otherwise SKIP both.
- **D3 (cutover runbook)**: env-flip + monitoring window + rollback. NO pUSD bridge step.
- **D4 (cleanup)**: drop V1 SDK dual pin. Same as plan §7.

Plan provisions DROPPED outright:
- `clob_protocol` typed atom (`src/contracts/clob_protocol.py` create) — SDK auto-resolves version per-token via `_is_v2_order()` + `__resolve_version()` cache; Zeus duplicating that state is structurally redundant.
- Phase 2.A (M2 `delayed`-status fill_tracker branch) — zero `delayed|delaying` hits in V2 SDK source per opponent-down's grep over `client.py + clob_types.py + endpoints.py`. Replaced by V2-INDEPENDENT UNKNOWN_STATUS branch in `_normalize_status` — but that work routes to **Region-Mid as K2 error-typing** per opponent-mid's K=6 reduction, not Down.
- Phase 2.C (pUSD redemption swap) — **STAYS IN-SCOPE per Q-NEW-1 resolution 2026-04-26 11:12**. opponent-down dispatched direct Polygon RPC `eth_call` via `polygon.drpc.org` against `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`: `symbol()="pUSD"` and `name()="Polymarket USD"`. pUSD IS a distinct ERC-20 — the contract was repurposed from historical USDC.e. Earlier team-lead "marketing label" ruling overturned by on-chain ground truth. Phase 2.C work is REAL: `balanceOf()` rewire in `polymarket_client.get_balance` + pUSD redemption path in `harvester.py:1244-1264`. F1 (FX accounting between USDC.e legacy positions and pUSD trading collateral) re-opens.
- Phase 2.F (`getClobMarketInfo` fee_rate cache) — V2 SDK already caches `MarketDetails` per-token internally via `__ensure_market_info_cached`. Zeus's planned cache layer is a parallel state machine (relationship-violation per memory L23).

Packet status: ACTIVE → **dormant-tracker**. Plan stretches indefinitely without urgency: V1 SDK has been DORMANT since v0.34.6 release on 2026-02-19 (2 months PRE-V2-GA, NOT 2 days post per impact_report's transcription error). No EOL announced. Plan re-activates only when (i) Polymarket announces V1 EOL, OR (ii) operator runs Q1+Q-NEW-1+Q-HB and decides to advance.

Impact_report (`v2_system_impact_report.md`) requires REWRITE before Phase 0.F critic verdict: 6+ premises falsified by SDK-source evidence (mandatory 10s heartbeat unsourced; "delayed" status set unsourced; EIP-712 v1→v2 binary switch wrong; pUSD as separate ERC-20 wrong; fee_rate_bps "removed" partial-truth; V1 release date transcription error). Marketing-label disclaimer required at lines 21, 84, §4.5.

## Open

- Q1-zeus-egress (operator probe `clob-v2.polymarket.com/version` + `/v1/heartbeats` + `/ok` from Zeus daemon machine with funder_address present)
- Q-HB (operator inquiry to Polymarket support / Discord on whether a mandated heartbeat cadence exists)
- Q-FX-1 (NEW, post-Q-NEW-1 resolution): how does Zeus classify USDC.e ↔ pUSD conversion in PnL accounting? Trading PnL vs carry cost vs separate FX line. Operator decision required before pUSD redemption path lands.

## Resolved during R1L1

- **Q-NEW-1**: RESOLVED-DIVERGENT 2026-04-26 11:12. opponent-down direct Polygon RPC `eth_call` via `polygon.drpc.org` against `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` returned `symbol()="pUSD"`, `name()="Polymarket USD"`. pUSD IS a distinct ERC-20; contract repurposed from historical USDC.e. Earlier judge ruling ("contract is bridged USDC; pUSD is marketing label only") overturned. Phase 2.C / Q5 work is REAL. F1 (FX accounting) re-opens as Q-FX-1.

## Slice card IDs

Active (5):
- `down-01` D1 unified-SDK drop-in swap (preserve `create_order → post_order` two-step seam at `polymarket_client.py:160-161`)
- `down-02` D0 question-trio (Q1-zeus-egress, Q-NEW-1 on-chain symbol() probe, Q-HB cadence inquiry; encodes absorption rule "F-### absorbs iff lands on V2-touched file, else stays in execution stream")
- `down-03` unified-client antibody (asserts `OrderArgsV1`/`OrderArgsV2`/`_resolve_version`/`_is_v2_order`/`post_heartbeat`/`get_fee_rate_bps`/`get_tick_size`/`get_neg_risk` exist; skip-on-import-error; pattern from `tests/test_neg_risk_passthrough.py:66-83`)
- `down-04` Q1 acceptance amendment ("from Zeus daemon machine, with funder_address present in headers"; amend `open_questions.md` and `plan.md`)
- `down-05` packet status amendment (flip `plan.md` packet status to dormant-tracker; impact_report rewrite gate at Phase 0.F with marketing-label disclaimer)

D2-gated heartbeat (1, may never activate):
- `down-06` apscheduler-job-4 V2-heartbeat (only if Q-HB confirms cadence; tombstone-before-os._exit ordering encoded; parallel `_clob_v2_heartbeat_fails` counter; fail-soft via tombstone; no `os._exit` in V2-heartbeat job; cycle entry pre-check on tombstone)

ACTIVATED post-Q-NEW-1 resolution (1, was D2-gated, now real work):
- `down-07` pUSD-collateral-branch — Q-NEW-1 resolved 2026-04-26 11:12 via on-chain RPC: pUSD is distinct ERC-20 (`symbol()="pUSD"`, `name()="Polymarket USD"` on `0xC011a7…2DFB`). Branch `polymarket_client.get_balance` + `harvester.py:1244-1264` redemption path; F1 FX classification decision required. **No longer conditional.**

## Cross-region routing summary (Down's stake)

- **F-003** (no exchange-proven idempotency) → Mid (execution-journal A1.5). V2 SDK does NOT change F-003's fix shape: no `client_order_id` on `OrderArgsV1`/`OrderArgsV2`, no `Idempotency-Key` HTTP header in `endpoints.py`, no documented server-side dedup-by-prior-hash. Apr26 review fix (durable command journal + reconcile-first retry) is execution-journal work, not transport.
- **F-009** (tick/min/negRisk SDK discipline) → Down (down-03 antibody covers).
- **F-011** (raw-payload persistence) → Up (`clob_market_snapshot` + ~30 LOC executor.py instrumentation per X-UM-1 → X3 ruling). Down's down-01 PRESERVES the `signer.sign(order)` interception seam Up needs to anchor on.
- **UNKNOWN_STATUS** branch in `fill_tracker._normalize_status` → Mid (K2 error-typing, judge ledger line 74). Was tentatively in Down's slice list as down-02; rerouted under reframe because there is no V2 forcing function (the "delayed" capital-leak narrative is unsourced).
- **Wave 2 B2 / B4 / B5** (settlement backfill scripts / obs_v2 physical-bounds / DST flag writer) → Up. All touch DATA authority files (`source_rationale.yaml`, `test_topology.yaml`, `observation_instants_v2_writer.py`); not Down's region. Flagged here only for completeness.

## Cross-cut rulings respected

- **X1** (X-MD-1 fold, signing-surface seam): A1 + mid-02 + UNKNOWN_STATUS are PARALLELIZABLE iff `signer.sign(order)` seam preserved. **down-01 EXPLICITLY preserves** the existing two-step `create_order → post_order` pattern at `polymarket_client.py:160-161`. NO migration to V2's `create_and_post_order` convenience wrapper allowed. mid-02 (signed_order_hash + SIGNED_ORDER_PERSISTED) intercepts ONCE at this seam and the hash binds to BOTH V1-shape and V2-shape orders.
- **X4** (X-UD-1 fold, F-011 anchor): F-011 sequences AFTER Down's collapsed D-phase. Region-Up's `clob_market_snapshot` table + raw-order-side capture instrumentation (Up-03 / Up-04 cards) anchor on D1 (the drop-in swap module boundary), not on the no-longer-existing D2.D phase. Up owns SCHEMA additions; Down's down-01 wires the signer.sign seam Up needs.
- **X3** (X-UM-1 fold, condition_id schema): Up-04 owns the SINGLE coordinated `ALTER TABLE` on `venue_commands`; Mid's mid-02 depends on up-04. Down has no objection.

## Signed-by

- proponent-down (a56f348a989bc1411) at 2026-04-26
- opponent-down (af73a54f38af5cb17) at 2026-04-26 — confirming signature. All consensus points concur. Specifically affirming:
  - 7-of-7 attack concessions stand with SDK-source citations from commit a2ec069f.
  - Q-NEW-1 RESOLVED-DIVERGENT via my direct polygon.drpc.org eth_call — symbol="pUSD", name="Polymarket USD" — overturns earlier "marketing label" ruling; Phase 2.C / Q5 / F1 / F2 are real work; down-07 promotes from D2-gated tracker to ACTIVE.
  - down-01 explicit seam-preservation at polymarket_client.py:160-161 (NOT create_and_post_order) is binding for X1 parallelizability.
  - down-06 race-spec (a)-(e): tombstone-write BEFORE its own os._exit; reuse main.py:367 pattern; worst-case covered by INV-25 V2-preflight + cleanup_orphan_open_orders at cycle_runner.py:287.
  - F-003→Mid, F-009→Down, F-011→Up, UNKNOWN_STATUS→Mid, B2/B4/B5→Up routings concur.
  - Packet status → dormant-tracker concurs.

## Next phase

Region-Down PAUSED per operator's sequential-debate directive (memory rule 33). Awaiting judge dispatch of Down R2L2 turn after Up + Mid complete L2/L3.

When Down R2L2 dispatches:
- Defend D1 seam-preservation guarantee (`polymarket_client.py:160-161` two-step pattern) when Up's mid-02 / SignedExecutionEnvelope work intersects via X1 ruling.
- Validate D0 question wording with Phase 0.B addendum on V2 idempotency surface deep-source check.
- Sequence down-04 (Q1 amendment) and down-05 (packet status amendment) as text-edit slices that can ship pre-Q probe-results as low-risk closure work.
