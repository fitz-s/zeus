# opponent-up — Solo Context Boot (Region Up)

Date: 2026-04-26
HEAD: 874e00cc0244135f49708682cab434b4d151d25d (main)
Independent boot — NO coordination with proponent-up.

## Files read (with line ranges)

- `/Users/leofitz/.openclaw/workspace-venus/zeus/src/types/observation_atom.py` (1–164, full file) — canonical atom shape
- `/Users/leofitz/.openclaw/workspace-venus/zeus/src/contracts/execution_intent.py` (1–53, full file) — current trading intent contract
- `/Users/leofitz/.openclaw/workspace-venus/zeus/src/data/polymarket_client.py` (1–367, full file) — CLOB/Data-API/Gamma I/O surface
- `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/chain_reconciliation.py` (1–80, then grep for identifiers across full file 762 lines)
- `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` (lines 813–895 — venue_commands, venue_command_events) + grep for trading raw-payload tables
- `/Users/leofitz/Downloads/Zeus_Apr26_review.md` lines 435–560 (F-007, F-008, F-009, F-010, F-011, F-012) and lines 730–860 (§9, §10)
- `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-04-26_ultimate_plan/evidence/apr26_findings_routing.yaml` (full)

## URLs fetched

- None yet. WebFetch reserved for Layer 2 if proponent-up disputes Polymarket API authority semantics.

## Grep evidence (re-verified within this boot window)

1. **`MarketIdentity` / `MarketRef` / `TradingPayloadAtom` / `SignedExecutionEnvelope` do NOT exist anywhere in repo.** Proponent-up's value-objects are inventions, not citations.
2. **`ObservationAtom` exists** at `src/types/observation_atom.py:45–164` with all the fields proponent quoted: `source` (65), `api_endpoint` (67), `station_id` (66), `fetch_utc` (70), `rebuild_run_id` (88), `data_source_version` (89), `authority: Literal["VERIFIED","UNVERIFIED","QUARANTINED"]` (92). `__post_init__` (98–163) raises `IngestionRejected`. Proponent's §9 grep claim VERIFIED.
3. **`ExecutionIntent`** at `src/contracts/execution_intent.py:11–52` has `market_id: str` (32) and `token_id: str` (33) as bare strings. NO source/authority/fetch_utc fields. Proponent's claim VERIFIED.
4. **`venue_commands` table** at `src/state/db.py:813–836` ALREADY has `command_id PRIMARY KEY`, `idempotency_key UNIQUE`, `market_id`, `token_id`, `state`, `last_event_id`, `review_required_reason`. **`venue_command_events` table** at 845–854 already has `payload_json TEXT` and append-only invariant (NC-18). The "PROPOSED_EXECUTION_ORDER_COMMAND_JOURNAL" slice the routing yaml proposes overlaps materially with existing schema — routing yaml understates what's already shipped.
5. **No `gamma_market_snapshot`, `clob_market_snapshot`, `order_command` (separate from venue_commands), `order_event`, `trade_fill`, `exchange_position_snapshot`, `reconciliation_run`, `settlement_source_snapshot`, `raw_payload`** table exists in `src/state/db.py`. F-011 / §9 raw-payload-storage IS genuinely NET_NEW.
6. **`chain_reconciliation.py` mixes three identifiers** (`token_id`, `condition_id`, `market_id`) at lines 130, 134, 670, 679, 688, 697 — verifies F-007 leakage but ALSO shows that `market_id` is used as a degraded/quarantine sentinel (line 670 comment: "market_id can collide with degraded-but-live positions"). That semantics is not captured by a "value-object translation" framing.
7. **`polymarket_client.py` mixes CLOB + Data-API + Gamma surfaces in one client** — `CLOB_BASE` + `DATA_API_BASE` constants at lines 17–18, `get_orderbook` (CLOB), `get_positions_from_api` (Data-API), no Gamma in this file but scanner code is separate. Apr26 §10 recommendation (line 833+) is to split into `polymarket_gateway.py` / `order_journal.py` / `reconciler.py` — proponent-up's `MarketIdentity` value-object does NOT solve this; only a wrapper-structure refactor does.
8. **`signature_type=2` is hardcoded** at `polymarket_client.py:76` — Apr26 §10 calls this "unacceptable for general live-money readiness". This is in scope for Region Up F-007/F-009 territory and proponent did not mention it.

## Where the routing yaml is wrong/weak (attack vectors)

- **`shipped_pre_HEAD_874e00c: []` is FALSE for F-001 cluster.** `venue_commands` + `venue_command_events` already implement most of the durable command journal proponent treats as NET_NEW. Yaml says 0 shipped; reality is partial-shipped. Judge ledger line 32 already flags this caveat for opponent-mid; in Region Up, F-008 (token identity at command level) similarly may be partly satisfied by existing `idempotency_key + market_id + token_id` schema.
- **F-007 routing target `PROPOSED_DISCOVERY_CLOB_SNAPSHOT` is NOT a value-object seam.** Apr26 review fix design (line 454) explicitly says "create `ExecutableMarketSnapshot` from CLOB only; no trading decision can pass without it." That is a SNAPSHOT TABLE + GATE, not a translation seam. Proponent-up's `MarketIdentity` framing departs from the cited Apr26 design.
- **F-011 routing target `PROPOSED_EXECUTION_RAW_PAYLOAD_STORAGE` is correctly NET_NEW** but routing yaml does not address the sequencing question vs D2.D (CLOB SDK swap). Authority order says raw payloads upstream of transport — routing yaml is silent.
- **§9 "weather is first-class" needs a stronger claim.** Weather has authority field but `__post_init__` ALLOWS `UNVERIFIED` post-hoc tagging (lines 105–111 — not unconstructable, just rejected for `validation_pass=True`). Proponent's "UNCONSTRUCTABLE" framing is slightly overstated; the real antibody is "validated entry, post-hoc demotion only".

## Top 5 attack vectors for A2A (in priority order)

### Attack-A1 (F-007 architectural framing): proponent-up invented `MarketIdentity` — Apr26 cites `ExecutableMarketSnapshot` (TABLE + GATE), not value-object
The structural decision is not "one type owns translation" — it's "no command flows without a CLOB executable snapshot row written to a new table, with timestamp/hash/tick/min/negRisk/freshness". A value-object cannot enforce gate semantics; only a state-machine + table + invariant can. Proponent's framing is YAGNI on translation while missing the actual gate.

### Attack-A2 (F-011 sequencing collapse): D2.D SDK swap WILL mutate the payload envelope shape
Building `TradingPayloadAtom` BEFORE D2.D forces the atom to be defined against v2-shaped envelopes (`OrderArgs` / `post_order` response shape). v3 SDK changes those. Either (i) atom lands AFTER D2.D against stable v3 shape, or (ii) atom is envelope-agnostic — which means defining the contract before the contract is observable, and is the WORSE form of YAGNI. Proponent must pick.

### Attack-A3 (§9 false equivalence): observational vs performative is a category split
`ObservationAtom` disambiguates competing observations of one ground truth (multiple sensors, one temperature). `ExecutionIntent`/order is performative — the payload IS the truth, signed and chain-anchored, no "ground truth this is approximating". `authority: VERIFIED` on a draft order is verified-against-what? The chain hasn't seen it. Mirror-shape collapses two distinct semantic categories. The right antibody is `SignedExecutionEnvelope` with chain-anchor lifecycle states (`DRAFT|SIGNED|SUBMITTED|MINED|CONFIRMED|REORGED`) — which is a different shape from ObservationAtom.

### Attack-A4 (routing yaml understates pre-shipped): venue_commands already partial-implements F-001/F-003/F-008
Proponent treats the durable command journal as fully NET_NEW. `db.py:813` already has `command_id PRIMARY KEY + idempotency_key UNIQUE + market_id + token_id + state + last_event_id`. The Region-Up plan must EXTEND not REPLACE. Proponent must cite which fields are actually missing (e.g. `condition_id`, `outcome`, `clobTokenIds_raw`, `invariant_hash`) rather than minting a new umbrella slice.

### Attack-A5 (F-009 ownership): proponent did not raise hardcoded `signature_type=2`
`polymarket_client.py:76` hardcodes `signature_type=2`. Apr26 §10 explicitly calls this unacceptable. F-009 is officially routed to D1, but auth correctness blurs into precision/tick territory because both fail at the wrapper layer. Proponent's three-card scope (MarketIdentity, TradingPayloadAtom, ExecutableSnapshot) does not include this — and B7/B8 (Wave-2 hygiene) may try to absorb it. Region Up scope must explicitly own or explicitly punt this.

## Convergence preview (what I'm willing to settle on)

- F-007: **NET_NEW slice = `ExecutableMarketSnapshot` table + pre-trade gate** (per Apr26 line 454). NOT `MarketIdentity` value-object. The translation-seam concern collapses into "no command without a snapshot row" — gate enforces freshness automatically.
- F-011: **NET_NEW upstream packet, but sequenced AFTER D2.D, not before.** Atom shape must be defined against the stable v3 envelope. D2.D builds the transport; atom packet immediately follows and locks the envelope into a typed shape.
- §9 provenance audit: **NET_NEW packet with the full table set** (`gamma_market_snapshot`, `clob_market_snapshot`, `order_event`, `trade_fill`, `exchange_position_snapshot`, `reconciliation_run`, `settlement_source_snapshot`) — but NOT mirror-ObservationAtom semantically; the trading atoms have lifecycle + chain-anchor, not multi-source-observation, semantics.
- Pre-shipped credit: **routing yaml must restate** that `venue_commands` + `venue_command_events` ARE already a partial command journal. Region-Up scope = field extensions + new tables, not "all NET_NEW".

## Boot complete summary
8 files read across `src/types`, `src/contracts`, `src/data`, `src/state`, `docs/operations/...`. Routing yaml grep-verified for F-007/F-011/§9 — proponent's invented type names (`MarketIdentity`, `TradingPayloadAtom`, `SignedExecutionEnvelope`, `MarketRef`) do NOT exist. Apr26 review's fix design language ("ExecutableMarketSnapshot from CLOB only") is the canonical authority — proponent's framing departs from it. Pre-shipped venue_commands / venue_command_events partly cover the F-001 cluster which routing yaml mis-states as 0% shipped.

## Boot extension (post team-lead path correction)

Files added: `src/contracts/settlement_semantics.py:1-180` (esp. lines 51, 71-78, 147-180), `architecture/city_truth_contract.yaml:1-142`, `docs/operations/current_source_validity.md:1-50`, `docs/operations/task_2026-04-26_u1_hk_floor_antibody/plan.md`. Wave-1 ingest files (wu_hourly_client 354 lines, observation_client 481, observation_instants_v2_writer 638, hourly_instants_append 519) line-counted but not deep-read; if Layer 2 needs ingest detail I'll spot-read.

### Key findings from extension

1. **Weather has per-entity dispatcher pattern**: `SettlementSemantics.for_city()` at `src/contracts/settlement_semantics.py:147-180` returns `SettlementSemantics(rounding_rule, measurement_unit, resolution_source)` per city. HK alone gets `oracle_truncate` (line 171), all others `wmo_half_up` (lines 127, 142, 180). This is the canonical per-entity semantic carve-out antibody pattern — cross-module relationship enforced by dispatch shape, not by ad-hoc if-checks.

2. **Trading has NO equivalent `OrderSemantics.for_market()` dispatcher.** Polymarket per-market values that should dispatch the same way: `tick_size`, `min_order_size`, `neg_risk`, `signature_type`. Currently delegated to SDK at `polymarket_client.py:73-77` (signature_type=2 hardcoded — applies blanket to all wallets) and `:184-191` (`OrderArgs` constructed without per-market preflight). F-009 routing to "D1 SDK contract antibody" understates this — D1 catches ONE call site; the missing structure is a `for_market()` dispatcher mirror of `for_city()`.

3. **Weather has caution_flags catalog** at `architecture/city_truth_contract.yaml:101-111` (`hong_kong_explicit_caution`, `source_changed_by_date`, `website_api_product_divergence`, `airport_station_not_settlement_station`, `freshness_audit_required`). Each caution flag is REIFIED — a discrete enum-tagged carve-out documented per evidence class. **Trading has NO `polymarket_truth_contract.yaml`.** Equivalent flags that SHOULD exist: `signature_type_per_wallet_required`, `neg_risk_market_only_specific_pricing`, `tick_size_per_market_varies`, `clob_endpoint_drift_after_v3`, `gamma_active_clob_closed_divergence`. None captured.

4. **Weather has authority columns on 5+ tables** (`db.py:166, 225, 303, 330, 368`). Trading has authority column on 0 trading tables (`venue_commands` / `venue_command_events` at db.py:813,845 have NO authority column). The asymmetry is wider than proponent stated — this is per-row, not just per-payload.

5. **city_truth_contract.yaml has `forbidden_inferences` section** (lines 113-118) — explicit list of inferences agents must NOT make. Trading has no analogue. Equivalent forbidden inferences: "Do not infer Gamma open == CLOB tradable", "Do not infer Data-API position == order/trade reconciliation", "Do not infer SDK signature_type=2 covers all wallet types", "Do not infer get_orderbook freshness from successful HTTP 200".

### New attack vector (Attack-A6): the transferable antibody is `for_market()`, NOT `MarketIdentity`

Proponent-up framed F-007 as identifier translation. The actual transferable antibody from weather is a **per-market dispatcher** (`OrderSemantics.for_market()`) that returns `(tick_size, min_order_size, neg_risk, signature_type, freshness_window, clob_endpoint_id)` — same shape as `SettlementSemantics.for_city()`. This subsumes F-009 (precision/tick), F-007 (boundary leak via forced gate-on-dispatch), and Apr26 §10's signature_type concern. Value-object framing is the WRONG abstraction; dispatcher framing is the antibody that already exists in weather and can be replicated.

### New attack vector (Attack-A7): caution_flags catalog is missing

Trading needs an `architecture/polymarket_truth_contract.yaml` analogue with `caution_flags` + `forbidden_inferences` + `evidence_classes` mirroring `city_truth_contract.yaml`. Without this, every per-market exception (negRisk, GTD, FOK, signature_type) lives in human memory. Proponent's plan does not mention this layer. This is the structural decision that makes F-007 + F-008 + F-009 + F-010 all fall out as enforced consequences (Fitz Constraint #1: K=1 dominates K=4).

### Sharper convergence preview (replaces section above)

| Region-Up packet | proponent framing | opponent counter |
|---|---|---|
| F-007 | `MarketIdentity` value-object | NEW slice = `polymarket_truth_contract.yaml` + `OrderSemantics.for_market()` dispatcher + `ExecutableMarketSnapshot` table. Three artifacts, one structural decision. |
| F-011 | `TradingPayloadAtom` BEFORE D2.D | NEW slice AFTER D2.D — atom locks against stable v3 envelope; sequencing reverses. |
| §9 | "mirror ObservationAtom semantics" | NOT mirror — observation is multi-source/one-truth; trading is performative/chain-anchored. New atom = `SignedExecutionEnvelope` with `DRAFT|SIGNED|SUBMITTED|MINED|CONFIRMED|REORGED` lifecycle, NOT `authority: VERIFIED/UNVERIFIED/QUARANTINED`. |
| F-009 | D1 SDK contract antibody | EXTEND: per-market preflight via `for_market()` dispatcher (mirror of `for_city()`). Same packet as F-007 truth-contract slice. |
| pre-shipped credit | not addressed | venue_commands at db.py:813 partial-implements F-001/F-003/F-008; routing yaml `shipped_pre_HEAD: []` needs correction. |
