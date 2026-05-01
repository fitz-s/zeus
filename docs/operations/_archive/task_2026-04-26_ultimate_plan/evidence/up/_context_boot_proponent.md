# Region-Up Solo Context Boot — proponent-up

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Region: Up (boundary + provenance + raw payloads)
Author: proponent-up
Status: pre-R1, awaiting greenlight before A2A with opponent-up

---

## 1. Files read (path:line ranges)

| File | Range | Purpose |
|---|---|---|
| `src/types/observation_atom.py` | 1-130 | Reference shape: weather first-class provenance atom. |
| `src/data/polymarket_client.py` | 1-200 | Trading boundary client; raw-response NOT persisted. |
| `src/contracts/settlement_semantics.py` | 1-183 | Per-market resolution rules (rounding/precision); NOT a boundary id seam. |
| `src/state/chain_reconciliation.py` | 1-200 | ChainPosition (`token_id`, `condition_id`); reconcile loop (chain > portfolio). |
| `src/state/venue_command_repo.py` | 140-240 | venue_commands repo: `market_id` + `token_id` as bare strings, no signed_order / raw_response payload column. |
| `src/state/schema/v2_schema.py` | 1-540 | v2 schema; NO trading-side raw-payload tables (gamma/clob/order_command/order_event/trade_fill/exchange_position_snapshot/reconciliation_run/settlement_source_snapshot). |
| `architecture/invariants.yaml` | 178-310 | INV-23..INV-32 — INV-28 + INV-30 already discharge F-001 (durable command before side effect). |
| `docs/to-do-list/zeus_full_data_midstream_review_2026-04-26.md` | 1-125 | Apr26 review (full text — only 125 lines, NOT the line count assumed by routing yaml). |
| `docs/operations/task_2026-04-26_ultimate_plan/evidence/apr26_findings_routing.yaml` | 1-500 | Routing yaml; HEURISTIC overlay. |
| `docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md` | 1-80 | PR #19 plan: 10 findings → 3 structural decisions; A1-A4 + B1 + governance C-track. |
| **Supplementary (after path correction 2026-04-26):** | | |
| `src/state/db.py` | 160-790 | DB-level `authority TEXT CHECK(...)` constraints on observations/decisions/calibration_pairs/rescue_events (lines 166,225,303,330,368); `source TEXT NOT NULL` columns (189,235,587,604,618,649,713); `data_source_version TEXT` (223,724); `raw_response TEXT` on observation_instants (634-635). |
| `src/data/daily_obs_append.py` | 471-985 | Generic ingest path serving HKO + WU + Ogimet — explicit `source=`, `data_source_version=`, `authority="VERIFIED"` per atom write. Confirms HKO is generic-path, NOT a standalone module. Each city's source-tag is computed via `target.source_tag` (~line 1013-1023). |
| `architecture/city_truth_contract.yaml` | 1-80 | FORMALIZED source-role contract schema: 4 source roles (`settlement_daily_source` / `day0_live_monitor_source` / `historical_hourly_source` / `forecast_skill_source`) each with required `evidence_refs` + `freshness_class`. Schema-level provenance for the WEATHER side. No trading-side equivalent. |

## 2. External URLs fetched

None this round (Layer 1 architecture — no external fact-check needed yet; will fetch CLOB SDK docs at Layer 3 if needed).

## 3. Key findings (≤10 bullets)

1. **Weather provenance IS first-class.** `ObservationAtom` (`src/types/observation_atom.py:44-96`) is a `@dataclass(frozen=True)` with mandatory `source`, `api_endpoint`, `station_id`, `fetch_utc`, `rebuild_run_id`, `data_source_version`, `authority: Literal["VERIFIED","UNVERIFIED","QUARANTINED"]`. `__post_init__` (lines 98-116) raises `IngestionRejected` if `validation_pass=False` or authority/validation mismatched — invalid atoms are UNCONSTRUCTABLE. This is the canonical antibody shape.

2. **Weather raw response IS persisted.** `state/db.py:634`, `state/schema/v2_schema.py:288`, `data/hourly_instants_append.py:233-249`, `data/observation_instants_v2_writer.py:125,154,357,456` all carry a `raw_response TEXT` column.

3. **Trading raw response is NOT persisted.** Grep for `raw_response | raw_payload | signed_order | post_order_resp | orderbook_snapshot | gamma_snapshot` across `src/execution/`, `src/data/polymarket_client.py`, `src/state/venue_command_repo.py` returns EMPTY. F-011 is real.

4. **Trading boundary identifiers leak across modules.** `market_id`, `token_id`, `condition_id` appear in `src/contracts/tick_size.py:91-92`, `src/contracts/execution_intent.py:32-33`, `src/state/ledger.py:43-44,83-84`, `src/state/venue_command_repo.py:160-216`, `src/state/chain_reconciliation.py:130,134,169-176,424,442-468`. There is no `MarketIdentity` value-object that owns translation. F-007 is structural, not hygiene.

5. **F-001 / F-003 are NOT NET_NEW — already structurally discharged.** INV-28 (`venue_commands + venue_command_events SUBMIT_REQUESTED before side effect`, enforced by `tests/test_venue_command_repo.py`) + INV-30 (`venue_commands persisted with state=SUBMITTING within same process invocation`, enforced by `tests/test_executor_command_split.py`) already implement durable-command-before-submit. The routing yaml's `shipped_pre_HEAD: []` is WRONG. The residual F-001/F-003 gap is the raw signed-order + post_order response PAYLOAD columns, which is F-011, not a separate decision.

6. **Settlement semantics is per-market resolution rules, not a boundary id seam.** `SettlementSemantics` (`src/contracts/settlement_semantics.py:50-183`) owns `precision`, `rounding_rule`, `measurement_unit`, `finalization_time`. It is NOT a candidate for housing the polymarket-id translation — that needs a separate `MarketIdentity` value-object.

7. **`venue_commands` schema HAS `market_id` + `token_id` columns BUT no `condition_id`, no `clobTokenIds_raw`, no `signed_order_payload`, no `post_order_response`.** Adding these is the F-008 + F-011 raw-payload extension — natural successor to INV-28/INV-30, NOT a transport (D2.D) concern.

8. **§9 missing-tables claim is REAL even though its citation is fabricated.** Grep of `v2_schema.py:48-540` shows NO `gamma_market_snapshot`, NO `clob_market_snapshot`, NO `order_command`, NO `order_event`, NO `trade_fill`, NO `exchange_position_snapshot`, NO `reconciliation_run`, NO `settlement_source_snapshot`. Existing tables are weather/calibration/lifecycle only.

9. **`ExecutionIntent` is the boundary type missing both ID-seam discipline and provenance.** `src/contracts/execution_intent.py:32-33` has `market_id: str` + `token_id: str` as bare strings — no validation that they refer to the same market, no source/fetch_utc, no provenance. This is the trading equivalent of "passing temperature_value as a bare float without unit". Same class of failure as Fitz Constraint #4.

10. **PR #19 phases 1-5 closed weather-side data trust; trading-side has NO equivalent campaign.** `task_2026-04-26_full_data_midstream_fix_plan/plan.md` collapses 10 findings into 3 decisions (A=metric identity, B=lifecycle predicate ownership, C=governance). The trading region needs its own equivalent decomposition: K trading structural decisions, not N trading patches.

## 4. Premise mismatches with routing yaml

Routing yaml `apr26_findings_routing.yaml` is HEURISTIC and contains specific fabrications:

| Yaml claim | Actual |
|---|---|
| `F-007 file_line_in_review: 441` | Review is 125 lines; line 441 does not exist. F-007 keyword "Gamma/Data/CLOB" not in review. |
| `F-009 file_line_in_review: 483` | Same — does not exist. |
| `F-011 file_line_in_review: 525` | Same — does not exist. "raw payload" string not in review. |
| `provenance_audit file_line_in_review: 737` | Same — "§9" not in review; review has no §9. |
| `shipped_pre_HEAD: []` | INV-28 + INV-30 + `venue_commands` schema + `tests/test_venue_command_repo.py` + `tests/test_executor_command_split.py` already discharge the F-001/F-003 STRUCTURAL kernel. The remaining gap is payload columns, not the durable-command pattern. |
| `F-001/F-003 = PROPOSED_EXECUTION_ORDER_COMMAND_JOURNAL (NET_NEW)` | The journal exists (venue_commands + venue_command_events). What's missing is the raw-payload JSON columns, which is F-011 territory. |
| `F-009 = D1 (V2 SDK preflight)` | F-009 is tick/min/negRisk discipline at boundary; INV-25 (V2 preflight) is reachability-only — does NOT cover tick/min/negRisk. The mapping is plausible but incomplete. |

The routing yaml is useful as a TOPIC INDEX (which Apr26 themes exist) but its line citations are fabricated and its "shipped_pre_HEAD" gating is wrong. Re-route on theme, not on line.

The **actual Apr26 review's CLOB-relevant content** is:
- Line 28-38 ("Part 2 — Midstream data to Polymarket orders" table — narrative, not findings).
- Finding 8 (line 70-72: legacy position metric fallback, authority='UNVERIFIED' — already addressed by PR #19 Slice A4).
- P3 line 112 ("Extend typed execution-price / tick-size / slippage contracts through the CLOB-send and realized-fill boundary") — F-009 hook.

Everything else in the routing yaml's F-### IDs is a SYNTHESIS overlay (presumably a separate Apr26 review document not committed under `docs/to-do-list/`). The yaml's findings are real architectural observations — but they are NOT directly anchored in the line-numbered review file the yaml claims.

## 5. Working hypothesis for Layer 1 (architecture / authority-order)

**Q1 (F-007 boundary leakage):** STRUCTURAL boundary problem. Three Polymarket id name-spaces (Gamma `market_id`, Data-API `token_id`, on-chain `condition_id`) leak across `contracts/`, `state/ledger.py`, `state/venue_command_repo.py`, `state/chain_reconciliation.py`. No single seam owns translation. Fix shape: a `MarketIdentity` value-object — frozen dataclass with `(condition_id, market_id, yes_token_id, no_token_id)` — plus a single `resolve(market_id)` seam. Hygiene-only consolidation regresses on the next caller. This is the same class of failure ObservationAtom solved on the weather side.

**Q2 (F-011 raw-payload first-class):** NET_NEW upstream packet, NOT a D2.D add-on. Fitz Constraint #4: provenance is its own dimension. D2.D is a transport swap (`py-clob-client` v0.34 → newer SDK). Bolting provenance into a transport-replacement PR conflates concerns and forfeits the antibody. The structural decision is: introduce a `TradingPayloadAtom` (mirror of `ObservationAtom`) BEFORE D2.D so the SDK swap lands on a typed boundary. Schema add: `raw_signed_order_jsonb`, `raw_post_order_response_jsonb`, `raw_orderbook_snapshot_jsonb`, `raw_gamma_snapshot_jsonb`, `fetch_utc`, `sdk_version`, `signature_type`, `authority`. Bolted onto venue_commands + new orderbook/gamma snapshot tables. Note: F-001/F-003 STRUCTURE is already shipped (INV-28/INV-30); only PAYLOAD is residual — collapses cleanly into F-011.

**Q3 (§9 weather/trading provenance asymmetry):** GREP-VERIFIED. Weather IS first-class. Trading is NOT. The same shape IS achievable architecturally because we have a reference implementation (`ObservationAtom` + `IngestionGuard.validate()` + `raw_response TEXT` column pattern) to mirror. This makes F-011 STRUCTURAL upstream, achievable without major surgery — small new module + new schema rows + opt-in writes — not a multi-month architecture change.

**Convergence anchor:** The Up region's two structural decisions are:
- **U-DEC-1:** Polymarket identifier translation seam (`MarketIdentity` value-object) — discharges F-007 + F-008 (token-identity not frozen at command-level).
- **U-DEC-2:** Trading payload first-class (`TradingPayloadAtom` + raw-payload schema add) — discharges F-011 + provenance_audit + the residual payload columns from F-001/F-003 + the `sdk_version`/`signature_type` from §8.3 transitions + Apr26 §9 as a whole.

Wave-2 B7 (B4-legacy-quarantine, ~1h) and B8 (B4-source-binding, gated) sit DOWNSTREAM of U-DEC-2: B7 = quarantine inherited rows whose provenance fails authority check; B8 = bind weather-side source tags to trading-side `TradingPayloadAtom`. Both depend on U-DEC-2 landing first.

Cross-cut X4 (raw-payload persistence) is the IMPLEMENTATION half of U-DEC-2 — recommend the slice card pattern: U-DEC-2 has child slice cards `up-NN` for each payload site (signed_order, post_order_response, orderbook, gamma). Not a separate cross-cut.

## 6. Layer-1 attack surface I expect from opponent-up

- (a) "MarketIdentity is over-engineering — three callers can share a `_resolve_token_id_for_market(market_id)` helper without a value-object."
- (b) "TradingPayloadAtom is YAGNI — D2.D will introduce SDK v3 which natively returns provenance objects."
- (c) "Weather had multi-source ambiguity (WU/HKO/IEM/ECMWF); trading is single-source (Polymarket only) — the same antibody isn't needed."
- (d) "F-001/F-003 is genuinely NET_NEW — `venue_commands` lacks `signed_order_hash`, so the journal isn't a true durable command record, just a state machine."
- (e) "raw_response column on weather-side is mostly NULL/empty in practice (`hourly_instants_append.py:240` writes only `{"rebuild_run_id": ...}`) — it's not load-bearing, so mirroring is cargo-culting."

I'll counter (a) by citing the four leak sites in finding 4; counter (b) by citing Fitz Constraint #2 (encode insight into structure that works without being understood — SDK v3 is a future dependency, atom is local); counter (c) by noting trading IS multi-source: Gamma snapshot ≠ CLOB orderbook ≠ on-chain Position — that's the F-007 finding itself; counter (d) by reading INV-28+INV-30 enforced_by tests; counter (e) by checking weather raw_response usage in observation_instants_v2_writer.py more carefully.

---

## 7. Supplementary findings (post path-correction 2026-04-26)

11. **DB-level `authority` is enforced by CHECK constraints, not just by Python.** `src/state/db.py:166,225,303,330,368` show `authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED'))` on observations / decisions / calibration_pairs / rescue_events. SQLite refuses to write a non-enum authority value. This is a SECOND-LAYER antibody beyond the Python frozen-dataclass — provenance is enforced at the storage seam, not just at the construction seam. Trading-side `venue_commands` schema (`state/db.py:init_schema`) has NO authority column at all.

12. **`source TEXT NOT NULL` is mandatory on every weather table.** `src/state/db.py:189,235,587,604,618,649,713` confirm. Including `forecasts` (587), `historical_forecasts` (604), `observation_instants` (618), `observation_revisions` (649), `historical_forecasts_v2` (713). Trading-side tables have no `source` column; venue is implicit (always Polymarket) but Gamma vs CLOB vs Data-API vs Chain is NOT distinguished — collapsing four upstreams into "source=POLYMARKET" forfeits the F-007 boundary.

13. **HKO is generic-path, not a standalone module.** `src/data/daily_obs_append.py:471-499,929-985` confirms HKO ingestion uses the same `_write_atom_with_coverage()` helper as WU + Ogimet, with `source=HKO_REALTIME_SOURCE` / `HKO_SOURCE` / `HKO_OPENDATA_SOURCE`, `data_source_version="hko_rhrread_accumulated_v1"` / `"hko_opendata_v1_2026"`, `authority="VERIFIED"`. The pattern is: source-tag + version + authority, on EVERY atom write — codified by `_write_atom_with_coverage` (`daily_obs_append.py:543-580`). Trading-side has no equivalent funnel.

14. **`architecture/city_truth_contract.yaml` formalizes source-roles for the weather side at the SCHEMA level.** Four roles: `settlement_daily_source`, `day0_live_monitor_source`, `historical_hourly_source`, `forecast_skill_source`. Each requires `source_family`, `station_or_product`, `evidence_refs`, `freshness_class`. The contract is enforced by the `freshness_class` + `evidence_refs` fields and the `volatile_assertion_policy` block (lines 30-40). Trading-side has no manifest of upstream roles. Per Fitz Constraint #2, the manifest is the place where intent survives translation loss — its absence on the trading side is exactly the architectural asymmetry F-011 calls out.

## 8. Updated working hypothesis

The supplementary evidence STRENGTHENS U-DEC-2. The weather provenance kernel is THREE LAYERS:

1. **Construction-time** — `ObservationAtom.__post_init__` raises `IngestionRejected` (Python).
2. **Storage-time** — `authority TEXT CHECK(...)` constraint at SQLite schema level.
3. **Manifest-time** — `city_truth_contract.yaml` declares the source-role schema with mandatory `evidence_refs`.

To MIRROR this on the trading side, U-DEC-2 must include all three layers:
- `TradingPayloadAtom` frozen dataclass with `__post_init__` validation.
- `authority` + `source` columns on `venue_commands` + new raw-payload tables, enforced by `CHECK(...)`.
- `architecture/trading_provenance_contract.yaml` declaring upstream roles (Gamma, CLOB, Data-API, Chain) with `evidence_refs`.

This is a multi-slice but bounded packet — modeled on PR #19 which closed the corresponding three layers on the weather side.

## 9. Slice card preview (to mint after greenlight)

- `up-01` U-DEC-1.A: `MarketIdentity` value-object (frozen dataclass, single `resolve()` seam). Discharges F-007 + F-008.
- `up-02` U-DEC-1.B: Migrate the four leak sites (`contracts/`, `state/ledger.py`, `state/venue_command_repo.py`, `state/chain_reconciliation.py`) to consume `MarketIdentity`. depends_on: up-01.
- `up-03` U-DEC-2.A: `TradingPayloadAtom` frozen dataclass with `__post_init__`. Discharges F-011 construction-layer.
- `up-04` U-DEC-2.B: Schema add — `authority`, `source`, `raw_signed_order`, `raw_post_order_response`, `raw_orderbook_snapshot`, `raw_gamma_snapshot`, `fetch_utc`, `sdk_version`, `signature_type` columns + CHECK constraints. depends_on: up-03.
- `up-05` U-DEC-2.C: `architecture/trading_provenance_contract.yaml` — manifest of trading upstream roles. depends_on: up-03.
- `up-06` U-DEC-2.D: Wire executor + venue_command_repo to write atoms. depends_on: up-04.
- `up-07` Wave-2 B7: legacy-row quarantine pass for venue_commands without authority. depends_on: up-04.
- `up-08` Wave-2 B8: weather→trading source-binding (link condition_id ↔ city for settlement). depends_on: up-04, up-05.

---

End boot. ACK to judge after greenlight.
