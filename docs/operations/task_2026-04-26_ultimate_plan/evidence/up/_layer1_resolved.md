# Region Up — R1L1 Resolved (Layer 1: Architecture)

Date: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Authors: opponent-up + proponent-up (joint)
Status: closed pending judge accept; cards minted on disk

## Canonical convergence

```
consensus: F-007/8/9/11 = K=5 decisions: truth-contract yaml + for_market dispatcher + ExecutableMarketSnapshot+gate + venue_commands EXTEND + SignedExecutionEnvelope (post-final-D-phase per X4)
open: REORGED, X-UM-1 condition_id Up↔Mid, Wave-2 B7/B8
slice_card_ids: [up-01..05]
```

## Slice cards on disk

`docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-NN.yaml`

| Card | Title | Authority basis |
|---|---|---|
| up-01 | `architecture/polymarket_truth_contract.yaml` (Layer 0 — caution_flags + forbidden_inferences + evidence_classes mirror of `architecture/city_truth_contract.yaml:1-118`) | F-007 boundary structural decision; Apr26 review §10 |
| up-02 | `src/contracts/order_semantics.py::OrderSemantics.for_market()` dispatcher (mirror of `src/contracts/settlement_semantics.py:147-180`) | F-007 + F-009 + signature_type=2 hardcoding at `polymarket_client.py:76` |
| up-03 | `ExecutableMarketSnapshot` table + pre-trade gate | Apr26 review line 454: "create `ExecutableMarketSnapshot` from CLOB only; no trading decision can pass without it" |
| up-04 | EXTEND `venue_commands` ALTER TABLE preserve 16 cols (db.py:813-836) + add condition_id, outcome, clobTokenIds_raw, invariant_hash, signed_order_blob, post_order_response_blob, authority_tier, chain_anchor_* | F-001 cluster + F-008 token-identity at command level; INV-28 + INV-30 |
| up-05 | `SignedExecutionEnvelope` frozen dataclass + `ChainAnchorState` lifecycle (DRAFT\|SIGNED\|SUBMITTED\|MINED\|CONFIRMED\|REORGED), sequenced after final D-phase per X4 | F-011 + §9 raw-payload-storage; chain-shaped not SDK-shaped |

## How we got here (5 turns)

Boot phase: opponent-up wrote `_context_boot_opponent.md` (12 files, 7 attack vectors A1-A7 grep-verified).

Turn 1: opponent-up sent 3-fracture attack (F-007 authority tiers, F-011 sequencing collapse, §9 observational-vs-performative). Crossed in flight with proponent-up's opening; resent in turn 2.

Turn 2: opponent-up consolidated 6-attack salvo (A1-A7 + 5-card counter-proposal + 10 explicit asks). Demanded grep-verified counter-evidence for every claim, refused proponent-up's pre-baked weak-attack bait.

Turn 3 (proponent-up): full concede on Attacks 1+2+3:
- F-007: refactored `MarketIdentity` flat dataclass → `MarketRef[GAMMA|DATA|CHAIN]` tagged union with failing-promote operator (turning "GAMMA leaked into executor" into a mypy-detectable type error).
- F-011: conceded sequencing dilemma; lifecycle (chain-shaped) lands before D2.D, payload-blob (SDK-shaped) lands after.
- §9: full concede on observational-vs-performative category split. `authority="VERIFIED"` at construction = category error on never-mined order. Lifecycle-anchored, not authority-anchored.

Turn 3 (opponent-up): accepted refactor, raised 3 amendments:
1. up-05 truth-contract underscoped — missing caution_flags + forbidden_inferences + evidence_classes mirror.
2. `OrderSemantics.for_market()` dispatcher missing — separate from `MarketRef`.
3. up-02b ALTER vs CREATE not explicit on venue_commands EXTEND.

Turn 4 (proponent-up): partial concede + counter-proposal — 8-card list retaining MarketRef alongside ExecutableMarketSnapshot as orthogonal layered defenses (compile-time identity-tier vs runtime freshness/authority-tier). Then re-conceded MarketRef in turn 5 then re-restored it after opponent-up's final push.

Turn 5: full ACK + work split (proponent-up mints cards, opponent-up writes evidence trail + sends judge convergence). Cards minted at slice_cards/up-01..up-05.yaml. Convergence sent.

## Attacks landed (with disposition)

| # | Attack | Disposition |
|---|---|---|
| A1 | `MarketIdentity` invented; Apr26 line 454 cites `ExecutableMarketSnapshot` table+gate | Conceded; up-03 |
| A2 | Five leak-site evidence proves missing order semantics, not value-object | Conceded; up-02 dispatcher |
| A3 | Observational-vs-performative is a category split | Conceded; up-05 lifecycle-anchored |
| A4 | F-011 atom-before-D2.D against unknown v3 shape is YAGNI | Refactored; lifecycle before, payload-blob after |
| A5 | Routing yaml `shipped_pre_HEAD: []` grep-verifiably FALSE | Conceded; up-04 EXTEND not CREATE |
| A6 | Per-market dispatcher (mirror `for_city`) is the transferable antibody | Accepted as up-02 |
| A7 | Truth-contract yaml mirror with caution_flags + forbidden_inferences | Accepted as up-01 |

## Cross-region implications

- **X-UD-1** (F-011 sequencing on D-phase collapse) → folded into X4 by judge.
- **X-UM-1** (condition_id Up↔Mid coord on F-008) → logged by proponent-up at cross_region_questions.md:19.
- **Wave-2 B7** (legacy venue_commands quarantine pass for rows missing authority_tier) → deferred Layer 2/3.
- **Wave-2 B8** (weather→trading source-binding: condition_id ↔ city for settlement) → deferred Layer 2/3.

## Citation correction (turn 5)

Proponent-up acknowledged citation error: had been reading `docs/to-do-list/zeus_full_data_midstream_review_2026-04-26.md` (125 lines, the PR #19 workbook) rather than the canonical `~/Downloads/Zeus_Apr26_review.md` (1544 lines). Opponent-up's Apr26 line 454/483/525 citations verified correct via `wc -l` + sed grep. Routing yaml citations (apr26_findings_routing.yaml:109/111) also legitimate.

## What survived from weather kernel (Layer 2-territory preview)

Per turn-3 synthesis (opponent-up's diagnosis, proponent-up adopted):
- 1 of 3 weather-kernel layers transfers cleanly: **Layer 2 SQLite CHECK constraint pattern** — `state TEXT CHECK(state IN ('DRAFT','SIGNED','SUBMITTED','MINED','CONFIRMED','REORGED'))` is same antibody shape, constraining lifecycle enum instead of authority enum.
- 1 of 3 transfers with refactor: **Layer 3 manifest** — `architecture/trading_provenance_contract.yaml` (= up-01) lists 4 upstream roles (Gamma, Data-API, CLOB, Chain) with authority TIERS + freshness/finality classes. Encodes opponent-up's authority-tier insight as a contract.
- 1 of 3 does NOT transfer: **Layer 1 Python construction-time `authority="VERIFIED"`** is observational-only. Trading is performative; verification is a lifecycle event, not a construction property.

## Layer 2 attack-surface preview (opponent-up)

When proponent-up opens L2, expect their opener on:
- authority_tier propagation up-03 (ExecutableMarketSnapshot row) → up-04 (venue_commands.authority_tier) — same enum, single source of truth in up-01 yaml.
- `SettlementSemantics.for_city()` ↔ `OrderSemantics.for_market()` relationship — market→city resolution table?

Opponent-up L2 counter-attack vectors prepped (R1-R5):
- R1 authority_tier drift detection antibody between up-03 → up-04
- R2 market→city resolution: one-many (one settlement city) or many-many (synthetic markets)?
- R3 SignedExecutionEnvelope.chain_anchor_at >= ExecutableMarketSnapshot.chain_resolved_at must hold (no signing against future snapshot)
- R4 venue_commands.condition_id (up-04) ↔ Mid F-008 condition_id freeze (X-UM-1) — same column or two tables?
- R5 forbidden_inferences yaml entries → Python decorator/runtime check or yaml-only doc-layer?

## Closure conditions met

- 5 turns each direction (≥2 required) ✓
- Joint convergence text ≤200 chars sent to judge ✓ (proponent-up + opponent-up both fired matching K=5 — coordinate at judge end)
- Slice cards minted on disk ✓
- Evidence trail this file ✓
- Cross-region questions logged ✓

L1 closed pending judge accept. Standing by for L2 dispatch.
