

# SECTION 1 — Executive implementation verdict

EDLI v1：event-sourced opportunity discovery、ForecastSnapshotReadyTrigger、Day0 absorbing boundary、native CLOB executable TradeScore、NoTradeRegretLedger、MarketChannel shadow ingest。**


```text
VERIFIED_FROM_REPO:
  root/scoped AGENTS money path
  K1 three-DB split
  table ownership registry
  schema module pattern
  forecast executable reader
  Day0 observation context fields
  SettlementSemantics
  ExecutableMarketSnapshotV2
  ExecutionPrice/Kelly
  MarketAnalysis native NO handling
  MarketPriorDistribution separation
  cycle_runtime no_trade_events writer
  execution/venue boundary
  money-path CI gate

VERIFIED_FROM_EXTERNAL_DOC:
  Polymarket market channel
  Polymarket user channel
  Polymarket orderbook
  Polymarket order types / FOK / FAK / GTC / GTD / post-only
  Polymarket fee / tick / min-order / negRisk
  ECMWF Open Data release / rolling archive / cycle step reality

INFERRED:
  EDLI event/no-trade regret should live in world DB as decision/evidence truth.
  Reactor should initially run shadow-only and use an adapter into existing evaluator/finalizer path.

UNKNOWN / REVIEW_REQUIRED:
  exact Day0 observation writer hook
  exact single-event evaluator/finalizer seam
  exact user-channel reconciliation mapping for partial/cancel/timeout
  exact RiskGuard API call shape for reactor final gate
  live taker FOK/FAK authorization under current execution law
```

The root repo authority defines Zeus as a live quantitative trading engine with the money path `contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`, and also defines the three canonical DB files: world, forecasts, and trades. It further states no write transaction may span DBs via independent connections. ([GitHub][1])

The biggest implementation correction is this: **EDLI event/evidence tables must not be blindly created in trade DB, and must not use SQLite cross-DB FK.** Current repo has machine-checked DB ownership via `architecture/db_table_ownership.yaml`; `no_trade_events` are world-owned, while active `book_hash_transitions` moved to trade DB and a world ghost remains legacy. ([GitHub][2]) ([GitHub][2])

---

# SECTION 2 — Final delta against the original package

| Area                    | Original / earlier package                    | Final correction                                                                                                                                                                                   |
| ----------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DB ownership            | Put `opportunity_events` / regret in trade DB | Put EDLI event/evidence/no-trade regret in **world DB**; no cross-DB FKs; executable snapshots/book hash stay trade DB                                                                             |
| Append-only event store | `processing_status` inside event row          | Split immutable `opportunity_events` from mutable `opportunity_event_processing`                                                                                                                   |
| Forecast completeness   | Custom row/member heuristic                   | Use existing executable forecast bundle/evidence wherever possible; classify using `source_run`, `source_run_coverage`, `required_steps`, `observed_steps`, `expected_members`, `observed_members` |
| ECMWF steps             | Same step expectation for all cycles          | Cycle-specific: ECMWF says 00z/12z steps extend 0–144 by 3 then 150–360 by 6, while 06z/18z are 0–144 by 3 after Cycle 50r1 update. ([ECMWF][3])                                                   |
| Day0 availability       | Could use observation timestamp               | Must use `Day0ObservationContext.observation_available_at`; observation time is not availability                                                                                                   |
| Day0 rounding           | Local helper allowed                          | Must delegate to `SettlementSemantics.round_single()` / `assert_settlement_value()`                                                                                                                |
| Market channel          | Could be opportunity trigger                  | Shadow-only; can update quote cache/evidence, cannot prove fill                                                                                                                                    |
| User channel            | Under-specified                               | Only authenticated user channel/order reconciliation proves my order/trade updates                                                                                                                 |
| Execution               | Taker FOK/FAK might live                      | Current execution AGENTS says **limit orders only** and Zeus provides liquidity on entry; live taker FOK/FAK is REVIEW_REQUIRED architecture change                                                |
| FDR                     | Duplicate event risk noted                    | Must use canonical `make_hypothesis_family_id()` and full-family logging; event idempotency cannot shrink BH denominator                                                                           |
| No-trade                | New ledger only                               | Also write existing `no_trade_events` compatibility row when natural key exists; EDLI regret ledger is richer evidence                                                                             |

The current execution scope says “Limit orders ONLY” and “Zeus always provides liquidity on entry.” Therefore EDLI v1 may compute taker ask/book-walk as a conservative executable-cost bound, and may collect FOK/FAK shadow fillability evidence, but **must not route live taker market orders unless the execution law is explicitly changed and tested**. ([GitHub][4])

---

# SECTION 3 — Authority map and evidence table

| Claim / object                                                 |                     Status | Repo / external evidence                                                                                                                                                                                                                                                 | Implementation consequence                                                                  | Unknowns                                         |
| -------------------------------------------------------------- | -------------------------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Zeus is live-money weather CLOB engine                         |         VERIFIED_FROM_REPO | Root AGENTS defines the money path, runtime entrypoints, DB split, settlement mechanics, and risk behavior. ([GitHub][1])                                                                                                                                                | EDLI can trigger evaluation; it cannot bypass settlement/source/execution/risk/lifecycle.   | None                                             |
| K1 three-DB split exists                                       |         VERIFIED_FROM_REPO | world/forecasts/trades DB split and ATTACH-only sanctioned cross-DB write paths are explicit. ([GitHub][1])                                                                                                                                                              | EDLI schema must declare DB ownership and open correct connection.                          | Need schema bump execution.                      |
| Table ownership is registry-checked                            |         VERIFIED_FROM_REPO | Root AGENTS routes DB table ownership to `architecture/db_table_ownership.yaml`, enforced by table registry/tests. ([GitHub][1])                                                                                                                                         | New tables must be added to registry and money-path CI.                                     | Exact yaml shape confirmed by implementation.    |
| `no_trade_events` already world-owned                          |         VERIFIED_FROM_REPO | Ownership registry says `no_trade_events` is world DB, written by `src/state/no_trade_events.py`. ([GitHub][2])                                                                                                                                                          | EDLI regret belongs world/evidence surface.                                                 | Add EDLI enum reasons or controlled fallback.    |
| Existing `write_no_trade_event` writer exists                  |         VERIFIED_FROM_REPO | Cycle runtime opens `get_world_connection` and writes rejected decisions to `no_trade_events`. ([GitHub][5])                                                                                                                                                             | Reactor should use same coarse logging when market natural key exists.                      | Event-only cases may lack natural key.           |
| Active book hash ledger is trade DB                            |         VERIFIED_FROM_REPO | Ownership registry states active `book_hash_transitions` moved to trade DB; world entry is legacy ghost. ([GitHub][2])                                                                                                                                                   | Market-channel event store must not duplicate active book truth.                            | Cache/snapshot update seam REVIEW_REQUIRED.      |
| Forecast reader has no-leakage gate                            |         VERIFIED_FROM_REPO | Reader blocks missing source linkage, non-VERIFIED authority, causal status not OK, and `available_at > now_utc`. ([GitHub][6]) ([GitHub][6])                                                                                                                            | Reactor must reuse reader/bundle instead of raw snapshot query for live.                    | Exact `read_executable_forecast` API call shape. |
| Forecast evidence includes required/observed steps and members |         VERIFIED_FROM_REPO | `ExecutableForecastEvidence` carries `required_steps`, `observed_steps`, `expected_members`, `observed_members`, `source_run_completeness_status`, `coverage_completeness_status`. ([GitHub][6])                                                                         | ForecastSnapshotReadyPayload should mirror evidence, not recompute blindly.                 | Cycle-specific completeness classification.      |
| ECMWF release/cycle reality changed in 2026                    | VERIFIED_FROM_EXTERNAL_DOC | ECMWF states Cycle 50r1 changed 06z/18z stream availability, and 00/12 vs 06/18 step horizons differ. ([ECMWF][3])                                                                                                                                                       | Required steps must be cycle-specific.                                                      | Exact Zeus target-window step set per city/date. |
| Day0 context has availability field                            |         VERIFIED_FROM_REPO | `Day0ObservationContext` includes `observation_available_at`, provider time, station_id, sample_count, coverage_status. ([GitHub][7])                                                                                                                                    | Day0 event `available_at_utc` must use observation availability, not observation timestamp. | Exact writer hook.                               |
| Day0 source fallback must be explicit                          |         VERIFIED_FROM_REPO | Observation client says executable Day0 observations are settlement-source-bound and diagnostic fallbacks must be explicit. ([GitHub][7])                                                                                                                                | Open-Meteo/IEM fallback cannot silently become live source truth.                           | City source validity check.                      |
| Settlement rounding authority exists                           |         VERIFIED_FROM_REPO | `SettlementSemantics` implements `round_single()` and `assert_settlement_value()`, and WMO half-up differs from Python/Decimal rounding. ([GitHub][8]) ([GitHub][8])                                                                                                     | Absorbing boundary must call SettlementSemantics.                                           | Bin attr names in current `Bin`.                 |
| Native NO support exists                                       |         VERIFIED_FROM_REPO | `MarketAnalysis` supports `p_market_no`, `buy_no_quote_available`, and explicitly says `1 - YES` is diagnostic, not executable. ([GitHub][9])                                                                                                                            | `buy_no` must use native NO ask/depth.                                                      | Token map completeness.                          |
| Market prior is not executable quote                           |         VERIFIED_FROM_REPO | `MarketPriorDistribution` is a named epistemic prior; raw quote/VWMP is legacy-only. ([GitHub][10])                                                                                                                                                                      | Orderbook affects cost/fill, not q.                                                         | Live validation evidence.                        |
| FDR full-family scope exists                                   |         VERIFIED_FROM_REPO | Strategy AGENTS says full tested hypothesis family is FDR budget basis; `selection_family.py` defines canonical family IDs. ([GitHub][11]) ([GitHub][12])                                                                                                                | Event duplicate/idempotency must not alter BH denominator.                                  | Reactor full-family adapter.                     |
| Polymarket market channel is public L2 data                    | VERIFIED_FROM_EXTERNAL_DOC | Market channel is public, subscribes by asset IDs, emits book/price/tick/trade/BBA/new/resolved events. ([Polymarket Documentation][13])                                                                                                                                 | Market channel is data/evidence only, not fill truth.                                       | Reconnect/backfill implementation.               |
| User channel is authenticated fill/order truth                 | VERIFIED_FROM_EXTERNAL_DOC | User channel is authenticated order/trade updates filtered by API key. ([Polymarket Documentation][14])                                                                                                                                                                  | Fill lifecycle comes from user channel/reconcile, not public book.                          | Exact adapter mapping.                           |
| BUY ask / SELL bid / midpoint display                          | VERIFIED_FROM_EXTERNAL_DOC | Orderbook docs say BUY price is best ask, SELL price is best bid; midpoint is display implied probability, and wide spread displays last trade. ([Polymarket Documentation][15]) ([Polymarket Documentation][15])                                                        | Cost path forbids midpoint/last trade/display price.                                        | None                                             |
| FOK/FAK immediate semantics                                    | VERIFIED_FROM_EXTERNAL_DOC | Polymarket docs define GTC/GTD as resting limit types, FOK/FAK as immediate market order types. ([Polymarket Documentation][16])                                                                                                                                         | FOK/FAK shadow evidence only unless execution law changes.                                  | Live taker review.                               |
| Fee/tick/min-order/negRisk are venue facts                     | VERIFIED_FROM_EXTERNAL_DOC | Orderbook contains min_order_size/tick_size/neg_risk/hash; orders require tickSize/negRisk; fees are taker-only and price-dependent. ([Polymarket Documentation][15]) ([Polymarket Documentation][16]) ([Polymarket Documentation][16]) ([Polymarket Documentation][17]) | TradeScore must gate fee/tick/min-order/negRisk.                                            | Market fee fetch freshness.                      |

---

# SECTION 4 — Current repo money path reconstruction

EDLI v1 must attach to the current money path, not replace it.

```text
contract semantics
→ source truth
→ forecast ingest / executable forecast reader
→ calibration / Platt
→ market fusion
→ full-family edge scan / BH/FDR
→ typed ExecutionPrice / Kelly
→ executable snapshot / final intent / executor
→ venue command journal / user-channel reconciliation
→ monitoring / lifecycle / settlement / learning
```

| Step               | Current authority                                            | Verified behavior                                                                                                                                                                         | EDLI v1 touch                                                                            |
| ------------------ | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Contract semantics | `SettlementSemantics`, math/reference docs                   | Settlement values flow through typed rounding/precision; WMO half-up is not Python round. ([GitHub][8])                                                                                   | Day0 boundary calls `SettlementSemantics.round_single()` or `assert_settlement_value()`. |
| Source truth       | `src/data/AGENTS.md`, `observation_client.py`, evaluator     | Feed roles are non-fungible; Day0 monitoring source is settlement-source-bound. ([GitHub][18]) ([GitHub][7])                                                                              | Day0 trigger must carry source/station/local-date/DST/availability.                      |
| Forecast           | `ecmwf_open_data.py`, `executable_forecast_reader.py`        | Open Data commits snapshots/source_run; reader blocks non-linked/non-verified/future rows. ([GitHub][19]) ([GitHub][6])                                                                   | Forecast trigger emits after commit; reactor revalidates with reader/bundle.             |
| Calibration        | calibration/Platt path                                       | Existing path already owns `P_raw -> P_cal`; EDLI must not refit Platt.                                                                                                                   | Live layer consumes approved `p_cal`.                                                    |
| Market fusion      | `market_fusion.py`                                           | Corrected modes require `MarketPriorDistribution`; raw quote prior only in legacy mode. ([GitHub][10])                                                                                    | Capped market-prior factor only if validated; orderbook never changes q.                 |
| Edge scan/FDR      | strategy AGENTS, `selection_family.py`                       | FDR budget is full tested family; canonical family ids encode metric/snapshot/source/spread. ([GitHub][11]) ([GitHub][12])                                                                | Reactor must log sibling family once per event idempotency key.                          |
| Kelly              | `ExecutionPrice`, `kelly.py`                                 | Bare floats at Kelly seam are violations; Kelly calls `assert_kelly_safe()`. ([GitHub][20]) ([GitHub][21])                                                                                | TradeScore must produce typed fee-adjusted cost.                                         |
| Execution          | `cycle_runtime.py`, `execution/AGENTS.md`, `venue/AGENTS.md` | Runtime builds final intent, reprices from executable snapshot, and calls executor only after final-intent contract; venue adapter is only SDK/API boundary. ([GitHub][5]) ([GitHub][22]) | Reactor must call reusable finalizer, never venue adapter.                               |
| No-trade           | `cycle_runtime.py`, `no_trade_events.py`                     | Runtime writes rejected decisions to world DB no_trade_events. ([GitHub][5])                                                                                                              | EDLI writes existing coarse log + new regret ledger.                                     |
| Lifecycle          | `src/state/AGENTS.md`, execution lifecycle ref               | Append-first discipline and legal lifecycle transitions are canonical truth. ([GitHub][23]) ([GitHub][24])                                                                                | EDLI cannot invent order/fill/lifecycle truth.                                           |
| CI                 | `money-path-required.yml`                                    | Unknown money-path objects fail closed; object and CI mapping updates are required. ([GitHub][25])                                                                                        | Every PR adds manifest/tests.                                                            |

---

# SECTION 5 — Final target topology

## New directories

```text
src/events/
  AGENTS.md
  __init__.py
  opportunity_event.py
  idempotency.py
  event_store.py
  event_writer.py
  event_coalescer.py
  dead_letter.py
  replay.py
  triggers/
    __init__.py
    forecast_snapshot_ready.py
    day0_extreme_updated.py
    market_channel_shadow.py
    new_market_discovered.py
  reactor.py

src/strategy/live_inference/
  AGENTS.md
  __init__.py
  state.py
  absorbing_boundary.py
  markov_smoothing.py
  bayesian_factors.py
  executable_cost.py
  trade_score.py
  no_trade_regret.py
  promotion_ledger.py

src/state/schema/
  opportunity_events_schema.py
  opportunity_event_processing_schema.py
  event_dead_letters_schema.py
  no_trade_regret_events_schema.py
  shadow_execution_evidence_schema.py

src/engine/
  event_reactor_adapter.py
  decision_finalizer.py              # optional but preferred refactor target

src/analysis/
  event_opportunity_report.py
  day0_boundary_report.py
  forecast_release_reaction_report.py
  orderbook_shadow_fill_report.py
```

## Existing files to modify

```text
src/state/db.py
architecture/db_table_ownership.yaml
architecture/money_path_objects.yaml
architecture/money_path_ci.yaml
architecture/source_rationale.yaml
architecture/module_manifest.yaml
workspace_map.md
config/settings.json
src/data/ecmwf_open_data.py             # post-commit callback or event sink only
src/engine/cycle_runtime.py             # only if extracting decision_finalizer
src/main.py                             # reactor shadow/catch-up boot, flags off by default
src/contracts/no_trade_reason.py        # optional EDLI reason enum additions
```

Root AGENTS explicitly requires new files/directories to update source rationale, workspace map, and DB table ownership registry; unregistered files are invisible to future agents. ([GitHub][1])

---

# SECTION 6 — Module authority boundaries

| Module                            | Owns                                                                          | Must not own                                            |
| --------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------- |
| `src/events/opportunity_event.py` | immutable semantic event contract, timestamp triad, payload hash, idempotency | source truth, q model, execution                        |
| `event_store.py`                  | world DB insert/fetch/status/dead-letter                                      | raw market packet parsing, candidate eval, order submit |
| `event_writer.py`                 | single world-DB writer queue/backpressure                                     | SQLite writes from WS callbacks                         |
| `event_coalescer.py`              | packet-to-semantic-event reduction                                            | live alpha or fill truth                                |
| `forecast_snapshot_ready.py`      | post-commit forecast event emission                                           | forecast ingest correctness, Platt                      |
| `day0_extreme_updated.py`         | Day0 semantic event construction                                              | rounding implementation, station/source authority       |
| `market_channel_shadow.py`        | public market WS parsing, shadow cache/evidence                               | authenticated user order/fill status                    |
| `reactor.py`                      | event processing loop, hydration, no-bypass gates                             | venue side effects                                      |
| `live_inference/*`                | low-parameter belief update and robust scoring                                | DB truth, execution side effects, training from regret  |
| `no_trade_regret.py`              | regret ledger write/read for reports                                          | live model training                                     |
| `promotion_ledger.py`             | wrapper over existing Phase 6 evidence tiers                                  | parallel promotion system                               |

---

# SECTION 7 — Database schema package

## 7.1 Ownership

Final ownership:

```text
WORLD DB:
  opportunity_events
  opportunity_event_processing
  event_dead_letters
  no_trade_regret_events
  shadow_execution_evidence

TRADE DB:
  executable_market_snapshots
  active book_hash_transitions
  venue_commands
  order / position / execution truth

FORECASTS DB:
  ensemble_snapshots_v2
  source_run
  source_run_coverage
  readiness_state
```

Reason: EDLI events and regrets are decision/evidence/no-trade surfaces; existing `no_trade_events` and Phase 6 evidence tables are world DB surfaces. Phase 6 evidence schema is world-owned and caller-supplied connection, not auto-opened. ([GitHub][26])

## 7.2 DDL samples

```sql
CREATE TABLE IF NOT EXISTS opportunity_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'FORECAST_SNAPSHOT_READY',
        'DAY0_EXTREME_UPDATED',
        'BOOK_SNAPSHOT',
        'BEST_BID_ASK_CHANGED',
        'NEW_MARKET_DISCOVERED'
    )),
    entity_key TEXT NOT NULL,
    source TEXT NOT NULL,

    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    received_at TEXT NOT NULL,

    causal_snapshot_id TEXT,
    payload_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,

    priority INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_opportunity_events_available
ON opportunity_events(available_at, priority, received_at, event_id);

CREATE INDEX IF NOT EXISTS idx_opportunity_events_entity
ON opportunity_events(entity_key, event_type, available_at);

CREATE INDEX IF NOT EXISTS idx_opportunity_events_causal
ON opportunity_events(causal_snapshot_id);
```

```sql
CREATE TABLE IF NOT EXISTS opportunity_event_processing (
    consumer_name TEXT NOT NULL,
    event_id TEXT NOT NULL,
    processing_status TEXT NOT NULL CHECK (processing_status IN (
        'pending',
        'processing',
        'processed',
        'failed',
        'dead_letter',
        'expired',
        'ignored'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    processed_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (consumer_name, event_id)
);

CREATE INDEX IF NOT EXISTS idx_opportunity_event_processing_pending
ON opportunity_event_processing(consumer_name, processing_status, updated_at, event_id);
```

```sql
CREATE TABLE IF NOT EXISTS event_dead_letters (
    dead_letter_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    consumer_name TEXT NOT NULL,
    failure_stage TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_detail TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

```sql
CREATE TABLE IF NOT EXISTS no_trade_regret_events (
    regret_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,

    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('high','low')),
    family_id TEXT NOT NULL,
    bin_label TEXT,
    direction TEXT CHECK (direction IN ('buy_yes','buy_no','sell_yes','sell_no')),

    q_live REAL,
    q_lcb_5pct REAL,
    c_fee_adjusted REAL,
    c_cost_95pct REAL,
    p_fill_lcb REAL,
    trade_score REAL,

    no_trade_reason TEXT NOT NULL,
    rejection_stage TEXT NOT NULL,
    native_quote_available INTEGER CHECK (native_quote_available IN (0,1) OR native_quote_available IS NULL),
    source_status TEXT,
    family_complete INTEGER CHECK (family_complete IN (0,1) OR family_complete IS NULL),

    hypothetical_order_type TEXT,
    hypothetical_fill_status TEXT,
    hypothetical_fill_price REAL,

    later_outcome TEXT,
    would_have_won INTEGER CHECK (would_have_won IN (0,1) OR would_have_won IS NULL),
    would_have_filled INTEGER CHECK (would_have_filled IN (0,1) OR would_have_filled IS NULL),
    regret_bucket TEXT,

    causal_snapshot_id TEXT,
    executable_snapshot_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_no_trade_regret_event
ON no_trade_regret_events(event_id);

CREATE INDEX IF NOT EXISTS idx_no_trade_regret_family_time
ON no_trade_regret_events(family_id, decision_time);
```

```sql
CREATE TABLE IF NOT EXISTS shadow_execution_evidence (
    evidence_id TEXT PRIMARY KEY,
    event_id TEXT,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_label TEXT CHECK (outcome_label IN ('YES','NO')),
    direction TEXT NOT NULL,

    quote_seen_at TEXT NOT NULL,
    book_hash_before TEXT,
    best_bid_before REAL,
    best_ask_before REAL,
    depth_before_json TEXT,

    order_intent_time TEXT,
    submit_time TEXT,
    accepted_or_rejected TEXT CHECK (
        accepted_or_rejected IN (
            'NOT_SUBMITTED_SHADOW',
            'ACCEPTED',
            'REJECTED',
            'TIMEOUT_UNKNOWN',
            'ERROR_UNKNOWN'
        )
    ),
    venue_order_id TEXT,

    fok_full_fill INTEGER CHECK (fok_full_fill IN (0,1) OR fok_full_fill IS NULL),
    fak_partial_fill INTEGER CHECK (fak_partial_fill IN (0,1) OR fak_partial_fill IS NULL),
    filled_shares REAL,
    fill_price REAL,

    cancel_remainder_status TEXT,
    book_hash_after TEXT,
    latency_ms INTEGER,
    maker_cancel_before_submit INTEGER CHECK (maker_cancel_before_submit IN (0,1) OR maker_cancel_before_submit IS NULL),
    would_have_edge_after_fee INTEGER CHECK (would_have_edge_after_fee IN (0,1) OR would_have_edge_after_fee IS NULL),

    created_at TEXT NOT NULL
);
```

## 7.3 Schema module actions

```text
Open src/state/schema/opportunity_events_schema.py
  add CREATE_* SQL
  add ensure_tables(conn)
  caller supplies world conn

Open src/state/db.py
  import and call ensure_tables(conn) in world init_schema path
  bump SCHEMA_VERSION according to current repo pattern
  do not create EDLI world tables in trade init path

Open architecture/db_table_ownership.yaml
  add all EDLI tables as db: world
  writer: src/events/event_store.py or src/strategy/live_inference/no_trade_regret.py
  report readers: src/analysis/*

Add tests:
  test_world_conn_has_edli_tables_after_init
  test_trade_conn_does_not_silently_write_world_event_tables
  test_db_table_ownership_registers_edli_tables
  test_schema_version_check_accepts_edli_bump
```

The Wave2 critic documented a real wrong-DB live failure where a hook wrote/query-routed through a trade-shaped connection while the target tables lived in world DB; EDLI must ship a wrong-DB regression test, not just a happy-path in-memory world-shaped test. ([GitHub][27])

---

# SECTION 8 — Core event object implementation

```python
# src/events/opportunity_event.py
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Mapping

EventType = Literal[
    "FORECAST_SNAPSHOT_READY",
    "DAY0_EXTREME_UPDATED",
    "BOOK_SNAPSHOT",
    "BEST_BID_ASK_CHANGED",
    "NEW_MARKET_DISCOVERED",
]
Metric = Literal["high", "low"]

def utc(dt: datetime, name: str) -> datetime:
    if not isinstance(dt, datetime):
        raise TypeError(f"{name} must be datetime")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return dt.astimezone(timezone.utc)

def _default(x: Any) -> Any:
    if isinstance(x, datetime):
        return utc(x, "datetime").isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, Decimal):
        return str(x)
    raise TypeError(type(x).__name__)

def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        default=_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

def sha256_json(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

def stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps(parts, default=_default, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"

@dataclass(frozen=True)
class OpportunityEvent:
    event_id: str
    event_type: EventType
    entity_key: str
    source: str

    observed_at: datetime
    available_at: datetime
    received_at: datetime

    causal_snapshot_id: str | None
    payload_hash: str
    idempotency_key: str

    priority: int
    expires_at: datetime | None

    payload_json: dict[str, Any]
    schema_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "available_at", utc(self.available_at, "available_at"))
        object.__setattr__(self, "received_at", utc(self.received_at, "received_at"))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", utc(self.expires_at, "expires_at"))
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")
        if self.payload_hash != sha256_json(self.payload_json):
            raise ValueError("payload_hash mismatch")
        if not self.idempotency_key:
            raise ValueError("idempotency_key required")

def make_event(
    *,
    event_type: EventType,
    entity_key: str,
    source: str,
    observed_at: datetime,
    available_at: datetime,
    received_at: datetime,
    causal_snapshot_id: str | None,
    payload: Mapping[str, Any],
    idempotency_parts: tuple[object, ...],
    priority: int = 0,
    expires_at: datetime | None = None,
) -> OpportunityEvent:
    body = dict(payload)
    ph = sha256_json(body)
    idem = stable_id("opp-idem", event_type, entity_key, source, *idempotency_parts, ph)
    return OpportunityEvent(
        event_id=stable_id("opp-event", idem, available_at),
        event_type=event_type,
        entity_key=entity_key,
        source=source,
        observed_at=observed_at,
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=causal_snapshot_id,
        payload_hash=ph,
        idempotency_key=idem,
        priority=priority,
        expires_at=expires_at,
        payload_json=body,
        schema_version=1,
    )

def assert_in_filtration(event: OpportunityEvent, decision_time: datetime) -> None:
    if event.available_at > utc(decision_time, "decision_time"):
        raise ValueError(
            "EVENT_AVAILABLE_AFTER_DECISION_TIME:"
            f"{event.event_id}:{event.available_at.isoformat()}"
        )
```

Payload contracts:

```python
@dataclass(frozen=True)
class ForecastSnapshotReadyPayload:
    city: str
    target_date: date
    metric: Metric
    source_id: str
    source_run_id: str
    cycle: Literal["00z", "06z", "12z", "18z"]
    track: str
    snapshot_id: str
    snapshot_hash: str
    captured_at: datetime
    available_at: datetime
    required_fields_present: bool
    required_steps_present: bool
    member_count: int
    min_members_floor: int
    completeness_status: Literal["COMPLETE", "PARTIAL_ALLOWED", "PARTIAL_BLOCKED"]

    # Added from repo executable forecast evidence:
    required_steps: tuple[int, ...] = ()
    observed_steps: tuple[int, ...] = ()
    expected_members: int = 51
    source_run_status: str = ""
    source_run_completeness_status: str = ""
    coverage_completeness_status: str = ""
    coverage_readiness_status: str | None = None
```

```python
@dataclass(frozen=True)
class Day0ExtremeUpdatedPayload:
    city: str
    local_date: date
    metric: Metric
    settlement_source_type: str
    observation_source: str
    station_id: str | None
    observation_time_utc: datetime
    available_at_utc: datetime
    current_temp: float | None
    high_so_far: float | None
    low_so_far: float | None
    rounding_rule: str
    unit: Literal["F", "C"]
    source_match_status: Literal["MATCH", "MISMATCH", "UNKNOWN"]
    local_date_status: Literal["MATCH", "DST_REVIEW", "MISMATCH"]

    # Added from Day0ObservationContext:
    sample_count: int = 0
    first_sample_time_utc: str | None = None
    last_sample_time_utc: str | None = None
    coverage_status: str = "UNKNOWN"
    provider_reported_time_utc: str | None = None
```

```python
@dataclass(frozen=True)
class MarketBookEventPayload:
    condition_id: str
    token_id: str
    outcome_label: Literal["YES", "NO"]
    event_kind: Literal["BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"]
    book_timestamp: datetime
    received_at: datetime
    best_bid: Decimal | None
    best_ask: Decimal | None
    spread: Decimal | None
    depth_at_best_bid: Decimal | None
    depth_at_best_ask: Decimal | None
    depth_json: str | None
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool
    orderbook_hash: str
```

Required tests:

```text
test_payload_hash_deterministic
test_idempotency_key_changes_when_payload_changes
test_observed_available_received_do_not_alias
test_available_at_future_rejected
test_causal_snapshot_id_required_for_live_forecast_decision
test_day0_available_at_uses_observation_available_at_not_observation_time
```

---

# SECTION 9 — EventStore / Writer / Coalescer

## 9.1 EventStore

```python
# src/events/event_store.py
from __future__ import annotations

import json
from datetime import datetime, timezone

from src.events.opportunity_event import OpportunityEvent, canonical_json

class EventStore:
    def __init__(self, conn, consumer_name: str = "opportunity_reactor_v1"):
        self.conn = conn
        self.consumer_name = consumer_name

    def insert_or_ignore(self, event: OpportunityEvent) -> bool:
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO opportunity_events (
              event_id, event_type, entity_key, source,
              observed_at, available_at, received_at,
              causal_snapshot_id, payload_hash, idempotency_key,
              priority, expires_at, payload_json, schema_version, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.entity_key,
                event.source,
                event.observed_at.isoformat(),
                event.available_at.isoformat(),
                event.received_at.isoformat(),
                event.causal_snapshot_id,
                event.payload_hash,
                event.idempotency_key,
                int(event.priority),
                event.expires_at.isoformat() if event.expires_at else None,
                canonical_json(event.payload_json),
                int(event.schema_version),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        inserted = cur.rowcount == 1
        if inserted:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_event_processing
                  (consumer_name, event_id, processing_status, attempt_count, updated_at)
                VALUES (?, ?, 'pending', 0, ?)
                """,
                (self.consumer_name, event.event_id, datetime.now(timezone.utc).isoformat()),
            )
        return inserted

    def fetch_pending(self, *, decision_time: datetime, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT e.*
            FROM opportunity_events e
            JOIN opportunity_event_processing p
              ON p.event_id = e.event_id
             AND p.consumer_name = ?
            WHERE p.processing_status IN ('pending', 'failed')
              AND datetime(e.available_at) <= datetime(?)
              AND (e.expires_at IS NULL OR datetime(e.expires_at) > datetime(?))
            ORDER BY e.priority DESC,
                     datetime(e.available_at) ASC,
                     datetime(e.received_at) ASC,
                     e.event_id ASC
            LIMIT ?
            """,
            (self.consumer_name, decision_time.isoformat(), decision_time.isoformat(), int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def claim(self, event_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            """
            UPDATE opportunity_event_processing
               SET processing_status='processing',
                   attempt_count=attempt_count+1,
                   claimed_at=?,
                   updated_at=?
             WHERE consumer_name=?
               AND event_id=?
               AND processing_status IN ('pending','failed')
            """,
            (now, now, self.consumer_name, event_id),
        )
        return cur.rowcount == 1
```

## 9.2 Writer rules

```text
ForecastSnapshotReady / Day0ExtremeUpdated:
  lossless queue, blocking allowed, also catch-up rebuildable.

Market channel book/BBA:
  lossy shadow queue under backpressure, coalesced before DB write.

All event writes:
  world DB connection only.
  caller supplies conn where repo law requires caller-supplied conn.
  no writes from raw WS callback.
```

## 9.3 Market coalescer

```text
BOOK_SNAPSHOT:
  write first snapshot per token
  write material orderbook_hash change
  do not write every price level update

BEST_BID_ASK_CHANGED:
  coalesce 100–250 ms
  emit only best bid/ask/spread material change
  emit only if active info-event window exists, or cache refresh is needed

TICK_SIZE_CHANGE:
  update cache and invalidate executable snapshot
  do not create live opportunity
```

Tests:

```text
test_insert_or_ignore_duplicate
test_processing_state_separate_from_event_row
test_world_conn_required_for_event_tables
test_trade_conn_wrong_db_fails_loud
test_replay_order_deterministic
test_market_coalescer_db_write_budget
test_forecast_day0_not_dropped_on_backpressure
test_market_shadow_dropped_counter_on_backpressure
```

---

# SECTION 10 — ForecastSnapshotReadyTrigger

## 10.1 Hook

Use `src/data/ecmwf_open_data.py::collect_open_ens_cycle()` **after `conn.commit()`** and before returning summary. Repo code logs commit start/end and returns `source_run_id`, `release_calendar_key`, `forecast_track`, `source_id`, and snapshot counts after commit. ([GitHub][19])

Preferred implementation is **callback/event sink injection** to avoid data-ingest layer hard-importing event infrastructure:

```python
# src/data/ecmwf_open_data.py
def collect_open_ens_cycle(..., event_sink=None):
    ...
    conn.commit()
    ...
    if event_sink is not None and status == "ok":
        event_sink.emit_forecast_snapshot_ready(
            source_run_id=source_run_id,
            source_id=SOURCE_ID,
            track=forecast_track,
            committed_at=authority_computed_at,
        )
```

If callback is too invasive, implement a **catch-up emitter** in `src/events/triggers/forecast_snapshot_ready.py` that scans committed `source_run` / `source_run_coverage` and emits missing events idempotently.

## 10.2 Completeness logic

Do not blindly use `member_count == 51` as the sole completeness test. Use existing executable forecast evidence fields:

```text
required_steps
observed_steps
expected_members
observed_members
source_run_status
source_run_completeness_status
coverage_completeness_status
coverage_readiness_status
```

Classification:

```text
COMPLETE:
  source_run_status success/ready
  source_run_completeness_status complete
  coverage_completeness_status complete
  required_steps ⊆ observed_steps
  observed_members >= expected_members
  read_executable_forecast(...) returns LIVE_ELIGIBLE

PARTIAL_ALLOWED:
  required_steps ⊆ observed_steps
  observed_members >= settings.ensemble.min_members_floor
  source_run/coverage not complete but not blocked
  shadow only

PARTIAL_BLOCKED:
  missing required fields
  missing target local-day steps
  observed_members < min_members_floor
  reader blocks authority/causality/source linkage/available_at
```

The settings currently use `min_members_floor=40` with rationale that strict 51-only caused systematic partial-dissemination drops; EDLI live path should still require COMPLETE for live, while PARTIAL_ALLOWED is shadow evidence only. ([GitHub][28])

## 10.3 Event payload construction

```python
def make_forecast_snapshot_ready_event(bundle, *, received_at):
    ev = bundle.evidence
    snap = bundle.snapshot

    status = classify_completeness_from_evidence(ev)
    payload = ForecastSnapshotReadyPayload(
        city=snap.city,
        target_date=snap.target_local_date,
        metric=snap.temperature_metric,
        source_id=ev.forecast_source_id,
        source_run_id=ev.source_run_id,
        cycle=cycle_label(ev.source_cycle_time),
        track=track_for_metric(snap.temperature_metric),
        snapshot_id=str(snap.snapshot_id),
        snapshot_hash=snap.manifest_hash or ev.raw_payload_hash or "",
        captured_at=parse_utc(ev.captured_at),
        available_at=parse_utc(ev.source_available_at),
        required_fields_present=True,
        required_steps_present=set(ev.required_steps).issubset(set(ev.observed_steps)),
        member_count=int(ev.observed_members),
        min_members_floor=settings["ensemble"]["min_members_floor"],
        completeness_status=status,
        required_steps=tuple(ev.required_steps),
        observed_steps=tuple(ev.observed_steps),
        expected_members=int(ev.expected_members),
        source_run_status=ev.source_run_status,
        source_run_completeness_status=ev.source_run_completeness_status,
        coverage_completeness_status=ev.coverage_completeness_status,
        coverage_readiness_status=ev.coverage_readiness_status,
    )
```

Tests:

```text
test_complete_snapshot_emits_once
test_rerun_idempotent_same_source_run_snapshot_hash
test_partial_40_members_shadow_only
test_missing_required_steps_partial_blocked
test_available_at_is_source_available_not_issue_time
test_read_executable_forecast_blocks_future_available_at
test_00z_12z_step_set_differs_from_06z_18z_after_cycle_50r1
test_forecast_emit_failure_does_not_rollback_ingest_commit
```

ECMWF official documentation says IFS data release is tied to the real-time dissemination schedule, Open Data keeps the most recent 12 runs, and 00/12 vs 06/18 step horizons differ. ([ECMWF][3])

---

# SECTION 11 — Day0ExtremeUpdatedTrigger + absorbing boundary

## 11.1 Hook

Verified object: `Day0ObservationContext` includes `current_temp`, `high_so_far`, `low_so_far`, `source`, `observation_time`, `unit`, `station_id`, `sample_count`, `first_sample_time`, `last_sample_time`, `coverage_status`, `observation_available_at`, and `provider_reported_time`. ([GitHub][7])

**REVIEW_REQUIRED:** exact observation writer/caller hook. The trigger should fire only after durable observation/candidate context exists, or via catch-up against latest source-matched Day0 context.

## 11.2 Emission rule

```text
Emit DAY0_EXTREME_UPDATED if:
  first source-matched Day0 observation for city/date/metric
  OR metric=high and high_so_far increased
  OR metric=low and low_so_far decreased

Do not live emit if:
  source mismatch
  station mismatch
  local date mismatch / DST_REVIEW
  rounding unknown
  metric mismatch
  observation_available_at > decision_time
```

## 11.3 Source authorization

Use existing evaluator policy:

```python
DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE = {
    "wu_icao": frozenset({"wu_api"}),
}
```

The evaluator already rejects unauthorized source/future/stale observation, so Day0 trigger must reuse this law instead of introducing a new source map. ([GitHub][29])

## 11.4 Absorbing boundary implementation

```python
# src/strategy/live_inference/absorbing_boundary.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

Metric = Literal["high", "low"]

@dataclass(frozen=True)
class BoundaryResult:
    mask: np.ndarray
    rounded_extreme: float | None
    fact_true_bin_label: str | None
    killed_bin_labels: tuple[str, ...]
    reason: str | None

def _bin_bounds(bin_obj) -> tuple[float | None, float | None, str]:
    # REVIEW_REQUIRED: adapt attr names to current src.types.Bin.
    return (
        getattr(bin_obj, "low", None),
        getattr(bin_obj, "high", None),
        str(getattr(bin_obj, "label", "")),
    )

def compute_absorbing_boundary_mask(
    *,
    metric: Metric,
    bins: Sequence[object],
    settlement_semantics,
    high_so_far: float | None,
    low_so_far: float | None,
    source_gate_passed: bool,
) -> BoundaryResult:
    if not source_gate_passed:
        return BoundaryResult(
            mask=np.ones(len(bins), dtype=float),
            rounded_extreme=None,
            fact_true_bin_label=None,
            killed_bin_labels=(),
            reason="DAY0_SOURCE_TRUTH_BLOCKED",
        )

    raw = high_so_far if metric == "high" else low_so_far
    if raw is None:
        return BoundaryResult(
            mask=np.ones(len(bins), dtype=float),
            rounded_extreme=None,
            fact_true_bin_label=None,
            killed_bin_labels=(),
            reason="DAY0_EXTREME_UNAVAILABLE",
        )

    rounded = settlement_semantics.round_single(float(raw))
    mask = np.ones(len(bins), dtype=float)
    killed: list[str] = []
    fact_true: str | None = None

    for i, b in enumerate(bins):
        lo, hi, label = _bin_bounds(b)

        if metric == "high":
            if lo is not None and hi is not None and rounded > hi:
                mask[i] = 0.0
                killed.append(label)
            elif hi is None and lo is not None and rounded >= lo:
                fact_true = label

        if metric == "low":
            if lo is not None and hi is not None and rounded < lo:
                mask[i] = 0.0
                killed.append(label)
            elif lo is None and hi is not None and rounded <= hi:
                fact_true = label

    if fact_true is not None:
        mask[:] = 0.0
        for i, b in enumerate(bins):
            _, _, label = _bin_bounds(b)
            if label == fact_true:
                mask[i] = 1.0

    if float(mask.sum()) <= 0.0:
        return BoundaryResult(mask, rounded, fact_true, tuple(killed), "DAY0_BOUNDARY_ZERO_MASS")

    return BoundaryResult(mask, rounded, fact_true, tuple(killed), None)
```

Tests:

```text
test_high_finite_bin_killed_when_rounded_high_exceeds_upper
test_low_finite_bin_killed_when_rounded_low_below_lower
test_upper_high_shoulder_fact_true
test_lower_low_shoulder_fact_true
test_source_mismatch_blocks_fact_true
test_station_mismatch_blocks
test_metric_swap_blocks
test_observation_available_at_future_blocks
test_dst_ambiguous_local_date_blocks
test_settlement_semantics_used_not_python_round
test_openmeteo_diagnostic_fallback_never_live_source_truth
```

---

# SECTION 12 — MarketChannelShadowIngestor

## 12.1 External contract

Polymarket market channel is public L2 market data; subscribe by `assets_ids` and `type: "market"`, with `custom_feature_enabled: true` for `best_bid_ask`, `new_market`, and `market_resolved`. Events include `book`, `price_change`, `tick_size_change`, `last_trade_price`, `best_bid_ask`, `new_market`, and `market_resolved`. ([Polymarket Documentation][13])

Polymarket user channel is authenticated and filtered by API key; it emits order and trade updates. ([Polymarket Documentation][14])

## 12.2 Scope

```text
Subscribe:
  active Zeus weather YES token ids
  active Zeus weather NO token ids
  only markets already mapped by scanner/topology/executable snapshot

Do not subscribe:
  all Polymarket markets
  stale/resolved markets for alpha
  new_market until Zeus topology parser validates it
```

## 12.3 Parser skeleton

```python
# src/events/triggers/market_channel_shadow.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from src.events.opportunity_event import MarketBookEventPayload

def ms_to_utc(ms: object) -> datetime:
    return datetime.fromtimestamp(int(str(ms)) / 1000.0, tz=timezone.utc)

def dec(x: object) -> Decimal | None:
    if x is None or x == "":
        return None
    return Decimal(str(x))

def parse_book(msg: dict, *, token_meta: dict[str, dict], received_at: datetime) -> MarketBookEventPayload:
    token_id = str(msg["asset_id"])
    bids = msg.get("bids") or []
    asks = msg.get("asks") or []
    best_bid = dec(bids[0]["price"]) if bids else None
    best_ask = dec(asks[0]["price"]) if asks else None

    return MarketBookEventPayload(
        condition_id=str(msg["market"]),
        token_id=token_id,
        outcome_label=token_meta[token_id]["outcome_label"],
        event_kind="BOOK_SNAPSHOT",
        book_timestamp=ms_to_utc(msg["timestamp"]),
        received_at=received_at,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=None if best_bid is None or best_ask is None else best_ask - best_bid,
        depth_at_best_bid=dec(bids[0]["size"]) if bids else None,
        depth_at_best_ask=dec(asks[0]["size"]) if asks else None,
        depth_json=json.dumps({"bids": bids, "asks": asks}, sort_keys=True, separators=(",", ":")),
        tick_size=Decimal(str(token_meta[token_id]["tick_size"])),
        min_order_size=Decimal(str(token_meta[token_id]["min_order_size"])),
        neg_risk=bool(token_meta[token_id]["neg_risk"]),
        orderbook_hash=str(msg["hash"]),
    )
```

## 12.4 Shadow-only rules

```python
def market_channel_event_can_live_trade(event_type: str) -> bool:
    return False

def stale_book_strategy_live_allowed(settings: dict) -> bool:
    return False
```

Market channel can update in-memory quote cache and `shadow_execution_evidence`; it cannot update q, cannot create live stale-book orders, and cannot prove my accepted/fill/partial/cancel/timeout state.

## 12.5 Reconnect/backfill behavior

```text
on connect:
  REST-fetch latest orderbook for every subscribed token
  seed cache
  emit first BOOK_SNAPSHOT shadow event

on disconnect:
  record gap_start
  reconnect with bounded exponential backoff
  resubscribe same active token set
  REST-fetch latest orderbooks
  if orderbook hash changed during gap, emit gap-marked BOOK_SNAPSHOT
  stale-book live remains disabled

on tick_size_change:
  invalidate quote cache and executable snapshot freshness
  force market_scanner/snapshot refresh
  no live trade until fresh ExecutableMarketSnapshotV2 confirms tick
```

Tests:

```text
test_book_buy_uses_best_ask
test_book_sell_uses_best_bid
test_midpoint_forbidden
test_last_trade_forbidden
test_tick_size_change_forces_snapshot_refresh
test_min_order_size_enforced
test_market_channel_cannot_write_fill_truth
test_user_channel_is_only_fill_authority
test_reconnect_gap_shadow_only
test_coalescing_prevents_db_spam
```

---

# SECTION 13 — LiveBinInferenceLayer v1

## 13.1 Mathematical law

Filtration:

```text
F_t = sigma(
  forecast events,
  observation events,
  orderbook events,
  market lifecycle events,
  source health events
)
```

Hard invariant:

```text
event.available_at <= decision_time
```

Live update:

```text
π_t_minus = T(Δt)^T π_previous

π_t_plus = Normalize(K_t ⊙ L_e_t ⊙ π_t_minus)
```

Where:

```text
T      = low-parameter Markov smoothing
K_t    = Day0 absorbing boundary mask / fact-true mask
L_e_t  = capped forecast innovation likelihood
π_live = p_live
```

Orderbook freshness **does not** affect q; it only affects executable cost, fill lower bound, and adverse-selection penalty.

## 13.2 State object

```python
@dataclass(frozen=True)
class LiveBinInferenceState:
    inference_id: str
    event_id: str
    decision_time: datetime

    city: str
    target_date: date
    metric: Literal["high", "low"]
    market_family_id: str

    bins: list

    p_cal: np.ndarray
    p_prior_previous: np.ndarray | None
    p_markov: np.ndarray
    p_after_boundary: np.ndarray
    p_after_event_likelihood: np.ndarray
    p_live: np.ndarray

    factor_contributions: list[dict]
    absorbing_boundary_report: dict
    forecast_completeness_status: str | None

    causal_snapshot_id: str
    executable_snapshot_id: str | None
```

## 13.3 Pure functions

```python
def normalize(p: np.ndarray, name: str) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    if p.ndim != 1 or p.size == 0:
        raise ValueError(f"{name} invalid shape")
    if np.any(p < 0) or not np.all(np.isfinite(p)):
        raise ValueError(f"{name} invalid values")
    s = float(p.sum())
    if s <= 0:
        raise ValueError(f"{name} zero mass")
    return p / s

def markov_smooth(pi_previous, p_cal, *, delta_hours: float, sigma_bins: float = 0.75):
    base = normalize(p_cal if pi_previous is None else pi_previous, "markov_base")
    n = len(base)
    idx = np.arange(n)
    sigma = sigma_bins * max(1.0, min(4.0, (delta_hours / 6.0) ** 0.5))
    T = np.exp(-((idx[:, None] - idx[None, :]) ** 2) / (2 * sigma * sigma))
    T = T / T.sum(axis=0, keepdims=True)
    return normalize(T.T @ base, "p_markov")

def capped_llr_update(p, llr, *, reliability: float, cap: float):
    p = normalize(p, "llr_input")
    llr = np.asarray(llr, dtype=float)
    if llr.shape != p.shape:
        raise ValueError("LLR shape mismatch")
    if not np.all(np.isfinite(llr)):
        raise ValueError("LLR invalid")
    if not 0 <= reliability <= 1:
        raise ValueError("reliability out of range")
    z = np.log(np.clip(p, 1e-9, 1 - 1e-9) / np.clip(1 - p, 1e-9, 1))
    z = z + reliability * np.clip(llr, -cap, cap)
    return normalize(1 / (1 + np.exp(-z)), "llr_output")
```

## 13.4 Live-capable factors

```text
Day0 hard fact:
  K_t mask or fact-true, source-gated.

Forecast innovation:
  capped LLR, low reliability, no PnL-trained weights.

Market prior consistency:
  allowed only if MarketPriorDistribution.validated_for_live is true.

Orderbook:
  q impact forbidden in v1.
```

Tests:

```text
test_p_live_normalizes
test_available_at_future_blocks
test_day0_mask_overrides_markov
test_llr_cap_enforced
test_orderbook_event_does_not_change_q
test_market_prior_requires_validated_for_live
test_partial_forecast_shadow_only
test_zero_mass_after_boundary_no_trade
```

---

# SECTION 14 — Native executable cost and Robust TradeScore

## 14.1 Venue reality

For Polymarket token orderbooks, BUY price is the best ask and SELL price is the best bid; midpoint is display implied probability and can be replaced by last traded price when spread is wider than $0.10. ([Polymarket Documentation][15]) ([Polymarket Documentation][15])

Fees are taker-only, price-dependent, and applied at match time:

```text
fee = C × feeRate × p × (1 - p)
```

([Polymarket Documentation][17])

## 14.2 Cost rules

```text
buy_yes:
  token_id = YES token
  cost = walk asks on YES token

buy_no:
  token_id = NO token
  cost = walk asks on NO token

sell_yes / sell_no:
  token_id = held token
  proceeds = walk bids on held token

forbidden:
  c_no = 1 - yes_price
  midpoint
  displayed probability
  last_trade_price
  p_market prior
  raw VWMP as executable cost
```

## 14.3 TradeScore object

```python
@dataclass(frozen=True)
class RobustExecutableTradeScore:
    bin_index: int
    bin_label: str
    direction: Literal["buy_yes", "buy_no"]

    q_posterior: float
    q_lcb_5pct: float

    token_id: str
    native_quote_available: bool

    c_best_ask: float | None
    c_vwap_to_size: float | None
    c_fee_adjusted: float | None
    c_cost_95pct: float | None
    c_stress: float | None

    p_fill_lcb: float

    lambda_source: float
    lambda_tail: float
    lambda_corr: float
    lambda_adverse: float
    lambda_stress: float

    trade_score: float
    score_components: dict
    no_trade_reason: str | None
```

## 14.4 Score formula

```text
TradeScore =
P_fill_LCB * min(
    q_5pct - c_95pct - λ,
    q_posterior - c_stress - λ_stress
)
```

```python
def robust_trade_score(
    *,
    q_posterior: float,
    q_lcb_5pct: float,
    c_cost_95pct: float,
    c_stress: float,
    p_fill_lcb: float,
    lambda_source: float,
    lambda_tail: float,
    lambda_corr: float,
    lambda_adverse: float,
    lambda_stress: float,
) -> float:
    if p_fill_lcb <= 0:
        return 0.0
    lam = lambda_source + lambda_tail + lambda_corr + lambda_adverse
    robust_edge = min(
        q_lcb_5pct - c_cost_95pct - lam,
        q_posterior - c_stress - lambda_stress,
    )
    return max(0.0, p_fill_lcb * robust_edge)
```

## 14.5 Execution policy correction

```text
EDLI v1 shadow:
  ask/book-walk cost is valid conservative executable bound.
  FOK/FAK evidence is shadow fillability evidence only.

EDLI v1 live:
  must use existing final execution intent / executor path.
  must not create market orders unless execution AGENTS law is changed.
  if existing executor posts post-only passive GTC/GTD, then ask cost is only a bound, not fill guarantee.
  P_fill_LCB must reflect passive fill uncertainty.
```

Polymarket docs define FOK/FAK as immediate market-order types and post-only as GTC/GTD only; current Zeus execution law says limit orders only and entry provides liquidity. ([Polymarket Documentation][16]) ([Polymarket Documentation][16]) ([GitHub][4])

Tests:

```text
test_buy_no_uses_native_no_ask_not_complement
test_midpoint_edge_positive_ask_edge_negative_no_trade
test_last_trade_forbidden_as_cost
test_fee_erases_edge
test_tick_size_mismatch_blocks
test_neg_risk_mismatch_blocks
test_min_order_blocks
test_missing_causal_snapshot_blocks
test_kelly_receives_execution_price_not_float
test_taker_fok_live_requires_execution_policy_review
test_passive_post_only_live_uses_passive_fill_gate
```

---

# SECTION 15 — Opportunity Reactor integration

## 15.1 Runtime position

`src/main.py` remains scheduler owner for heartbeat, source health, harvester, redeem/wrap, market discovery fallback, and liveness. EDLI reactor is an additional event-driven consumer with scheduler catch-up. Root AGENTS names `src/main.py`, `cycle_runner.py`, `evaluator.py`, and `executor.py` as the current runtime entrypoints. ([GitHub][1])

Add:

```text
src/events/reactor.py
src/engine/event_reactor_adapter.py
src/engine/decision_finalizer.py   # preferred refactor
```

## 15.2 Reactor flow

```python
for event in event_store.fetch_pending(decision_time=now):
    claim(event)
    assert_in_filtration(event, now)

    causal_state = adapter.hydrate_event(event, now)
    if causal_state.blocked:
        write_no_trade_regret(...)
        mark_processed(event)
        continue

    candidates = adapter.generate_full_family_candidates(event, causal_state)

    for candidate in candidates:
        inference = live_inference.evaluate(...)
        score = trade_score.evaluate(...)

        if score.trade_score <= 0:
            write_no_trade_regret(stage="TRADE_SCORE", ...)
            continue

        fdr = adapter.apply_existing_fdr(candidate.full_family)
        if not fdr.pass_:
            write_no_trade_regret(stage="FDR", ...)
            continue

        kelly = adapter.apply_existing_kelly(score.execution_price)
        if not kelly.pass_:
            write_no_trade_regret(stage="KELLY", ...)
            continue

        risk = adapter.apply_existing_riskguard(...)
        if not risk.pass_:
            write_no_trade_regret(stage="RISK_GUARD", ...)
            continue

        if mode == "shadow":
            write_no_trade_regret(stage="SHADOW_ONLY", reason="SHADOW_MODE_WOULD_TRADE", ...)
            continue

        decision_finalizer.submit_through_existing_executor_only(...)
```

## 15.3 Exact no-bypass gates

```text
G01 event.available_at <= decision_time
G02 event idempotency not processed for same consumer
G03 causal_snapshot_id exists for forecast-dependent live decision
G04 executable forecast reader/bundle says LIVE_ELIGIBLE
G05 Day0 source/station/local-date/DST/rounding/metric pass
G06 executable_snapshot_id exists
G07 ExecutableMarketSnapshotV2 fresh and tradeability executable
G08 native YES/NO quote exists for selected direction
G09 no midpoint/last-trade/displayed probability cost
G10 fee/tick/min-order/negRisk pass
G11 full family hypotheses logged
G12 BH/FDR pass
G13 typed ExecutionPrice Kelly pass
G14 RiskGuard/exposure/cluster/shoulder cap pass
G15 final execution intent contract pass
G16 executor, not venue adapter, creates side effect
G17 live flag + pilot cap pass
```

## 15.4 Feature flags

```json
{
  "feature_flags": {
    "EDLI_V1_ENABLED": false,
    "EDLI_V1_REACTOR_MODE": "off",
    "EDLI_V1_FORECAST_TRIGGER_SHADOW": true,
    "EDLI_V1_DAY0_TRIGGER_SHADOW": true,
    "EDLI_V1_MARKET_CHANNEL_SHADOW": false,

    "EDLI_V1_DAY0_HARD_FACT_LIVE": false,
    "EDLI_V1_TAKER_FOK_FAK_LIVE": false,
    "EDLI_V1_STALE_BOOK_LIVE": false,

    "EDLI_V1_TINY_LIVE_MAX_NOTIONAL_USD": 5.0,
    "EDLI_V1_TINY_LIVE_MAX_ORDERS_PER_DAY": 1
  }
}
```

## 15.5 Tests

```text
test_event_cannot_bypass_source_truth
test_event_cannot_bypass_executable_snapshot
test_event_cannot_bypass_fdr
test_event_cannot_bypass_kelly
test_event_cannot_bypass_riskguard
test_reactor_never_imports_venue_adapter
test_duplicate_event_not_double_counted
test_sibling_family_logged_once
test_available_at_future_dead_letter_or_no_trade
test_market_channel_event_shadow_only
test_live_day0_requires_tiny_cap
test_taker_fok_fak_live_flag_false_blocks
```

---

# SECTION 16 — NoTradeRegretLedger

## 16.1 Dual logging

EDLI writes two kinds of no-trade evidence:

```text
1. Existing no_trade_events:
   compatibility / coarse decision log
   only when candidate has market_slug, metric, target_date, observation_time natural key

2. New no_trade_regret_events:
   EDLI-specific q/c/fillability/outcome ledger
   always for event-triggered rejection
```

Existing writer uses `DecisionNaturalKey`, `NoTradeReason`, `strategy_key`, `event_source`, and `shadow_runtime`, and commits through a caller-supplied world connection. ([GitHub][30])

## 16.2 Record object

```python
@dataclass(frozen=True)
class NoTradeRegretRecord:
    event_id: str
    candidate_id: str
    decision_time: datetime

    city: str
    target_date: str
    metric: str
    family_id: str
    bin_label: str | None
    direction: str | None

    q_live: float | None
    q_lcb_5pct: float | None
    c_fee_adjusted: float | None
    c_cost_95pct: float | None
    p_fill_lcb: float | None
    trade_score: float | None

    no_trade_reason: str
    rejection_stage: str

    native_quote_available: bool | None
    source_status: str | None
    family_complete: bool | None

    hypothetical_order_type: str | None
    hypothetical_fill_status: str | None
    hypothetical_fill_price: float | None

    causal_snapshot_id: str | None
    executable_snapshot_id: str | None
```

## 16.3 Rejection stages

```text
EVENT_FILTER
CAUSAL_STATE
SOURCE_TRUTH
FORECAST_COMPLETENESS
FAMILY_TOPOLOGY
INFERENCE
EXECUTABLE_QUOTE
TRADE_SCORE
FDR
KELLY
RISK_GUARD
EXECUTOR_EXPRESSIBILITY
SHADOW_ONLY
LIVE_CAP
UNKNOWN_REVIEW_REQUIRED
```

## 16.4 Regret buckets

```text
MODEL_WRONG
SOURCE_WRONG
QUOTE_UNAVAILABLE
FEE_ERASED_EDGE
NO_DEPTH
STALE_UNFILLABLE
FDR_REJECTED
KELLY_TOO_SMALL
RISK_CAP
SHOULDER_TAIL_BLOCK
FAMILY_INCOMPLETE
WOULD_HAVE_WON_BUT_UNFILLABLE
WOULD_HAVE_WON_AND_FILLABLE
WOULD_HAVE_LOST
LEAKAGE_BLOCKED
UNKNOWN_REVIEW_REQUIRED
```

## 16.5 Hindsight protection

```text
later_outcome
would_have_won
would_have_filled
regret_bucket
```

These fields may be populated only after settlement/fillability evidence is available. Live inference code must not import report readers or read these columns.

Tests:

```text
test_insert_idempotent
test_existing_no_trade_event_compatibility_written_when_natural_key_exists
test_event_without_market_slug_still_writes_regret_ledger
test_later_outcome_join_after_settlement_only
test_live_reader_denies_outcome_columns
test_fillable_vs_unfillable_bucket
```

---

# SECTION 17 — Reports / observability

Add:

```text
src/analysis/event_opportunity_report.py
src/analysis/day0_boundary_report.py
src/analysis/forecast_release_reaction_report.py
src/analysis/orderbook_shadow_fill_report.py
```

Every report must answer:

```text
events by type/source
events processed/pending/dead_letter
candidate count
blocked count by rejection_stage/no_trade_reason
native quote availability
TradeScore positive count
FDR/Kelly/RiskGuard rejection count
fillable vs unfillable
later won/lost after settlement
available_at violations
midpoint/last-trade cost violations
NO complement cost violations
duplicate idempotency / duplicate FDR family count
```

Metrics:

```text
edli.events.inserted
edli.events.duplicates
edli.events.future_available_at_blocked
edli.forecast.complete
edli.forecast.partial_allowed_shadow
edli.forecast.partial_blocked
edli.day0.source_mismatch
edli.day0.fact_true
edli.day0.bin_killed
edli.market_channel.coalesced
edli.market_channel.reconnect_gap
edli.trade_score.positive_shadow
edli.trade_score.no_native_quote
edli.live_pilot.cap_blocked
```

Promotion blockers:

```text
available_at violations = 0
midpoint/last-trade cost uses = 0
NO complement cost uses = 0
market-channel stale-book live trades = 0
duplicate FDR family counts = 0
wrong-DB writes = 0
```

---

# SECTION 18 — Tests and CI package

## 18.1 New tests

```text
tests/events/test_opportunity_event.py
tests/events/test_event_store_idempotency.py
tests/events/test_event_writer_single_writer.py
tests/events/test_forecast_snapshot_ready.py
tests/events/test_day0_extreme_updated_trigger.py
tests/events/test_market_channel_shadow_ingestor.py
tests/events/test_reactor.py

tests/strategy/live_inference/test_day0_absorbing_boundary.py
tests/strategy/live_inference/test_markov_smoothing.py
tests/strategy/live_inference/test_bayesian_factors.py
tests/strategy/live_inference/test_live_bin_inference.py
tests/strategy/live_inference/test_executable_cost.py
tests/strategy/live_inference/test_trade_score.py
tests/strategy/live_inference/test_no_trade_regret.py

tests/analysis/test_event_opportunity_report.py
tests/engine/test_event_reactor_no_bypass.py
tests/money_path/test_edli_invariants.py
tests/state/test_edli_table_ownership.py
```

## 18.2 Fast PR-blocking commands

```bash
python scripts/check_schema_version.py
python scripts/ci/assert_test_quality.py

python -m pytest -q \
  tests/state/test_edli_table_ownership.py \
  tests/events/test_opportunity_event.py \
  tests/events/test_event_store_idempotency.py \
  tests/events/test_event_writer_single_writer.py \
  tests/events/test_forecast_snapshot_ready.py \
  tests/events/test_day0_extreme_updated_trigger.py \
  tests/events/test_market_channel_shadow_ingestor.py \
  tests/strategy/live_inference/test_day0_absorbing_boundary.py \
  tests/strategy/live_inference/test_live_bin_inference.py \
  tests/strategy/live_inference/test_executable_cost.py \
  tests/strategy/live_inference/test_trade_score.py \
  tests/strategy/live_inference/test_no_trade_regret.py \
  tests/events/test_reactor.py \
  tests/engine/test_event_reactor_no_bypass.py \
  --maxfail=8 --timeout=300
```

## 18.3 Money-path commands

```bash
python -m pytest -q tests/money_path --maxfail=5 --timeout=300
python3 scripts/replay_correctness_gate.py
```

Money-path CI already fails closed for unregistered economic objects, new state machines, providers, scheduler jobs, or side-effect surfaces; EDLI must update `architecture/money_path_objects.yaml` and `architecture/money_path_ci.yaml`. ([GitHub][25])

---

# SECTION 19 — PR-by-PR implementation package

## PR1 — Schema + event skeleton, no consumer

Files:

```text
src/events/AGENTS.md
src/events/__init__.py
src/events/opportunity_event.py
src/events/idempotency.py
src/events/event_store.py
src/state/schema/opportunity_events_schema.py
src/state/schema/opportunity_event_processing_schema.py
src/state/schema/event_dead_letters_schema.py
src/state/db.py
architecture/db_table_ownership.yaml
architecture/money_path_objects.yaml
architecture/money_path_ci.yaml
architecture/source_rationale.yaml
workspace_map.md
tests/events/test_opportunity_event.py
tests/events/test_event_store_idempotency.py
tests/state/test_edli_table_ownership.py
```

Acceptance:

```text
world DB owns EDLI event tables
event rows immutable
processing state separate
idempotency works
wrong-DB test passes
no consumer / no live path
```

Rollback:

```text
feature flags absent/off
tables inert
old scheduler unaffected
```

## PR2 — EventWriter + coalescer

Files:

```text
src/events/event_writer.py
src/events/event_coalescer.py
tests/events/test_event_writer_single_writer.py
tests/events/test_market_event_coalescer.py
```

Acceptance:

```text
single writer
market shadow backpressure lossy
forecast/day0 lossless
no raw WS SQLite writes
```

## PR3 — ForecastSnapshotReadyTrigger shadow

Files:

```text
src/events/triggers/forecast_snapshot_ready.py
src/data/ecmwf_open_data.py
tests/events/test_forecast_snapshot_ready.py
```

Acceptance:

```text
post-commit emit or catch-up rebuild
COMPLETE / PARTIAL_ALLOWED / PARTIAL_BLOCKED
cycle-specific required steps
reader revalidates no future available_at
old scheduler update_reaction remains
```

## PR4 — Day0 trigger + absorbing boundary

Files:

```text
src/events/triggers/day0_extreme_updated.py
src/strategy/live_inference/AGENTS.md
src/strategy/live_inference/absorbing_boundary.py
tests/events/test_day0_extreme_updated_trigger.py
tests/strategy/live_inference/test_day0_absorbing_boundary.py
```

Acceptance:

```text
SettlementSemantics used
source/station/local-date/DST/rounding/metric gates
high/low finite kill
shoulder fact-true
no probabilistic nowcast live
```

## PR5 — LiveBinInference pure functions

Files:

```text
src/strategy/live_inference/state.py
src/strategy/live_inference/markov_smoothing.py
src/strategy/live_inference/bayesian_factors.py
tests/strategy/live_inference/test_live_bin_inference.py
```

Acceptance:

```text
no DB
no executor
no orderbook q update
LLR caps
normalization
factor ledger
```

## PR6 — Native cost + TradeScore shadow

Files:

```text
src/strategy/live_inference/executable_cost.py
src/strategy/live_inference/trade_score.py
tests/strategy/live_inference/test_executable_cost.py
tests/strategy/live_inference/test_trade_score.py
```

Acceptance:

```text
YES ask / NO ask / sell bid
no complement cost
no midpoint
no last trade
fee/tick/min-order/negRisk checks
typed ExecutionPrice to Kelly
```

## PR7 — MarketChannelShadowIngestor

Files:

```text
src/events/triggers/market_channel_shadow.py
src/state/schema/shadow_execution_evidence_schema.py
tests/events/test_market_channel_shadow_ingestor.py
```

Acceptance:

```text
market channel parsed
user channel not confused
reconnect gap shadow-only
FOK/FAK cohort fields
no live stale-book path
```

## PR8 — Reactor shadow mode

Files:

```text
src/events/reactor.py
src/engine/event_reactor_adapter.py
src/main.py
tests/events/test_reactor.py
tests/engine/test_event_reactor_no_bypass.py
```

Acceptance:

```text
events hydrate candidates
no bypass of source/executable/FDR/Kelly/RiskGuard
all would-trades logged shadow
scheduler maintenance preserved
```

## PR9 — Regret ledger + reports

Files:

```text
src/state/schema/no_trade_regret_events_schema.py
src/strategy/live_inference/no_trade_regret.py
src/strategy/live_inference/promotion_ledger.py
src/analysis/event_opportunity_report.py
src/analysis/day0_boundary_report.py
src/analysis/forecast_release_reaction_report.py
src/analysis/orderbook_shadow_fill_report.py
tests/strategy/live_inference/test_no_trade_regret.py
tests/analysis/test_event_opportunity_report.py
```

Acceptance:

```text
every rejection logged
outcome join leakage-safe
reports answer cohort/fillability/win/loss questions
```

## PR10 — Tiny live pilot Day0 hard fact only

Files:

```text
config/settings.json
src/events/reactor.py
src/engine/decision_finalizer.py      # if not already extracted
tests/money_path/test_edli_day0_tiny_live_pilot.py
```

Acceptance:

```text
only DAY0_EXTREME_UPDATED hard fact
source/local-date/station/rounding/metric match
native quote exists
TradeScore positive
typed Kelly pass
RiskGuard pass
executor final-intent contract pass
tiny cap
live release gate local proof
```

Additional PR10 blocker:

```text
EDLI_V1_TAKER_FOK_FAK_LIVE remains false
unless execution AGENTS/law and final-intent tests explicitly admit it.
```

---

# SECTION 20 — Before / during / after implementation checklist

## Before start

```text
[ ] Branch from current main.
[ ] Snapshot world/forecasts/trades DBs.
[ ] Set all EDLI live flags false.
[ ] Run topology_doctor navigation for the exact changed files.
[ ] Read root AGENTS, src/data AGENTS, src/state AGENTS, src/strategy AGENTS, src/execution AGENTS, src/venue AGENTS.
[ ] Inspect architecture/db_table_ownership.yaml.
[ ] Inspect src/state/db.py schema init and current SCHEMA_VERSION.
[ ] Inspect exact Day0 observation writer/caller hook.
[ ] Inspect exact evaluator/finalizer seam.
[ ] Verify Polymarket docs current order/fee/tick semantics.
[ ] Verify ECMWF current Open Data cycle/step semantics.
[ ] Define rollback: flags off + no reactor + old scheduler path intact.
```

## During implementation

```text
[ ] Schema/registry first.
[ ] Shadow first.
[ ] No event code imports venue adapter.
[ ] No raw WS callback writes SQLite.
[ ] Deterministic ids.
[ ] Fail closed on missing causal_snapshot_id.
[ ] Fail closed on available_at > decision_time.
[ ] Reuse SettlementSemantics.
[ ] Reuse ExecutableMarketSnapshotV2.
[ ] Reuse ExecutionPrice/Kelly.
[ ] Update money-path object registry.
[ ] Add relationship tests with each PR.
[ ] Keep MarketChannel stale-book live disabled.
[ ] Keep FOK/FAK live disabled unless execution law changes.
```

## After implementation

```text
[ ] Run schema checks.
[ ] Run EDLI fast tests.
[ ] Run money_path tests.
[ ] Run replay correctness.
[ ] Run migration on DB copy.
[ ] Verify zero future available_at.
[ ] Verify wrong DB connection tests.
[ ] Verify no duplicate FDR count.
[ ] Verify no complement/midpoint/last-trade cost.
[ ] Verify market channel stale-book live impossible.
[ ] Verify rollback.
[ ] Only then consider PR10 tiny live pilot.
```

---

# SECTION 21 — Hidden branch register

| Hidden branch                 | Failure mechanism                         | Prevention                               | Test / signal                         | Severity |
| ----------------------------- | ----------------------------------------- | ---------------------------------------- | ------------------------------------- | -------- |
| Wrong DB write                | world table queried from trade conn       | world-owned EDLI tables + wrong-DB tests | `test_trade_conn_wrong_db_fails_loud` | P0       |
| Cross-DB FK illusion          | SQLite attached DB FK not valid authority | no cross-DB FK                           | schema smoke                          | P0       |
| Event spam / DB lock          | raw WS packet append storm                | coalescer + single writer                | burst write-count test                | P0       |
| Idempotency collision         | key omits source_run/hash/status          | include event/source/entity/payload hash | collision tests                       | P1       |
| Event ordering race           | replay order nondeterministic             | priority/available/received/event_id     | replay hash                           | P1       |
| Clock skew                    | received/available confusion              | timestamp triad audit                    | skew report                           | P1       |
| Forecast issue-time leakage   | issue_time used as availability           | use source_available_at/available_at     | future snapshot test                  | P0       |
| Partial ECMWF snapshot        | missing steps treated as signal           | completeness contract                    | partial tests                         | P0       |
| ECMWF cycle horizon drift     | 06/18 expected steps wrong                | cycle-specific step policy               | 06/18 test                            | P1       |
| Day0 provider lag             | observation_time used as availability     | use observation_available_at             | future obs test                       | P0       |
| DST/local date mismatch       | wrong local day                           | local_date_status gate                   | DST test                              | P0       |
| Station mismatch              | wrong station                             | station gate                             | station test                          | P0       |
| Metric swap                   | high fact applied to low                  | metric identity gate                     | metric swap test                      | P0       |
| Rounding ambiguity            | local helper drifts                       | SettlementSemantics only                 | rounding test                         | P0       |
| Token map stale               | old YES/NO token                          | fresh executable snapshot                | token mismatch test                   | P0       |
| Native quote missing          | complement fabricated                     | native quote required                    | no quote test                         | P0       |
| Tick/min-order change         | venue reject                              | snapshot assert                          | tick/min tests                        | P0       |
| Fee change                    | edge survives only pre-fee                | fee-adjusted ExecutionPrice              | fee erases edge                       | P0       |
| NegRisk mismatch              | wrong order option                        | snapshot negRisk assert                  | negRisk test                          | P0       |
| Book hash mismatch            | stale depth used                          | hash in snapshot/cache                   | hash test                             | P0       |
| Market/user channel confusion | public book treated as fill               | separate modules                         | fill authority test                   | P0       |
| Accepted vs filled            | post accepted treated as fill             | user-channel lifecycle states            | lifecycle test                        | P0       |
| Partial fill remainder        | FAK remainder ignored                     | explicit fields                          | FAK partial test                      | P0       |
| Timeout after submit          | timeout assumed reject/fill               | UNKNOWN_REVIEW_REQUIRED                  | timeout test                          | P0       |
| Maker cancel before submit    | quote disappears                          | P_fill_LCB conservative                  | cohort report                         | P1       |
| Stale quote adverse selection | fill event carries bad info               | stale-book shadow-only                   | promotion gate                        | P1       |
| FDR sibling undercount        | one edge only                             | full-family logging                      | sibling test                          | P0       |
| Kelly float regression        | TradeScore float as cost                  | typed ExecutionPrice                     | monkeypatch test                      | P0       |
| RiskGuard bypass              | event creates intent directly             | finalizer only                           | import/no-bypass test                 | P0       |
| No-trade hindsight leakage    | outcome read live                         | live reader denial                       | leakage test                          | P0       |
| Shadow evidence bias          | denominator missing                       | log all eligible cohorts                 | cohort completeness                   | P1       |
| Report cohort mixing          | shadow/live/partial mixed                 | cohort labels required                   | report tests                          | P1       |
| Feature flag misconfig        | stale-book live accidentally true         | flag matrix tests                        | boot flag report                      | P0       |
| Live pilot cap bypass         | multiple events live                      | per-day/event caps                       | cap test                              | P0       |
| Topology drift                | unregistered dirs                         | source_rationale/workspace updates       | topology doctor                       | P2       |
| CI blind spot                 | unregistered money object                 | money-path registry update               | money-path CI                         | P0       |

---

# SECTION 22 — Semantic risk register

## P0 live-money correctness

```text
RISK: Buy NO complement cost.
WHY REVIEW MISSES: binary math looks valid.
MITIGATION: native NO ask/depth only.
GATE: test_buy_no_uses_native_no_ask_not_complement.
PROMOTION: zero complement-cost violations.

RISK: midpoint/display/last-trade as cost.
WHY REVIEW MISSES: UI price resembles probability.
MITIGATION: ask/bid book-walk only.
GATE: midpoint/last-trade forbidden tests.
PROMOTION: zero violations.

RISK: source-mismatched Day0 hard fact.
WHY REVIEW MISSES: physical boundary math passes.
MITIGATION: source/station/local-date/rounding/metric gate.
GATE: source mismatch blocks positive score.
PROMOTION: source matched only.

RISK: taker live execution conflicts with current maker-entry law.
WHY REVIEW MISSES: TradeScore uses ask correctly, but execution policy differs.
MITIGATION: FOK/FAK shadow only unless architecture law changes.
GATE: EDLI_V1_TAKER_FOK_FAK_LIVE=false.
PROMOTION: explicit execution-law packet + final-intent tests.
```

## P1 leakage/statistical validity

```text
RISK: available_at leakage.
MITIGATION: event model + SQL fetch + reactor gate.
GATE: available_at violation report = 0.

RISK: partial forecast illusion.
MITIGATION: COMPLETE live, PARTIAL_ALLOWED shadow, PARTIAL_BLOCKED no-trade.
GATE: forecast_release_reaction_report.

RISK: no-trade regret training leak.
MITIGATION: v1 reports only; no Markov/Bayes training.
GATE: live reader cannot access outcome columns.
```

## P2 runtime/ops

```text
RISK: DB lock storm.
MITIGATION: coalescer, single writer, market shadow drops.
GATE: burst test.

RISK: WS reconnect gap.
MITIGATION: REST backfill + gap shadow-only.
GATE: reconnect test.

RISK: dead-letter backlog.
MITIGATION: DLQ report and fail-closed no live.
GATE: DLQ threshold alert.
```

## P3 maintainability

```text
RISK: topology undocumented.
MITIGATION: AGENTS/source_rationale/workspace/money-path registry.
GATE: topology doctor + money-path CI.

RISK: EDLI v1 expands into E-BOSS.
MITIGATION: not-now list enforced by flags/tests.
GATE: no Hawkes/POMDP/VOI imports in live path.
```

---

# SECTION 23 — Not-now list

| Item                              | Why tempting                 | Why unsafe now                           | Required shadow evidence later                    |
| --------------------------------- | ---------------------------- | ---------------------------------------- | ------------------------------------------------- |
| full Hawkes                       | models event bursts          | thin weather books, nonstationary makers | large out-of-time fill cohort + negative controls |
| POMDP/RL                          | optimizes timing/order type  | sparse reward, tail risk, phantom policy | sim-to-live validation + safety envelope          |
| complex change-point alpha        | detects regimes              | overfits source/market noise             | safety/no-trade use first                         |
| full VOI                          | optimizes subscriptions      | correctness first                        | latency/cost study                                |
| conformal live sizing             | robust intervals             | event-triggered coverage unproven        | out-of-time conformal audit                       |
| stale-book live sweep             | visible stale quote          | fill is endogenous/adverse               | FOK/FAK cohort + maker cancel stats               |
| forecast-release stale sweep live | release + stale quote        | market can react before submit           | timestamp/fillability cohort                      |
| shoulder live sizing expansion    | large tail payoff            | source/tail/correlation risk             | shoulder-specific shadow cohort                   |
| relative-value basket live        | negRisk seems arbitrage-like | conversion/fee/liquidity/FDR complex     | basket simulator + conversion proof               |
| training Markov/Bayes from regret | rich data                    | hindsight/fillability confounding        | locked offline dataset + preregistration          |

---

# SECTION 24 — Final executable acceptance contract

```text
A01. opportunity_events rows are immutable append-only.
A02. mutable processing state is in opportunity_event_processing.
A03. every event has deterministic event_id, payload_hash, idempotency_key.
A04. duplicate idempotency_key cannot double-count FDR family.
A05. observed_at, available_at, received_at are separate fields.
A06. no event enters inference if available_at > decision_time.
A07. live forecast decision requires causal_snapshot_id.
A08. forecast live eligibility reuses executable_forecast_reader / bundle evidence.
A09. COMPLETE forecast snapshot can be live eligible.
A10. PARTIAL_ALLOWED is shadow-only.
A11. PARTIAL_BLOCKED is no-trade.
A12. Day0 hard fact requires source/station/local-date/DST/rounding/metric match.
A13. Day0 source mismatch blocks positive TradeScore.
A14. absorbing boundary uses SettlementSemantics, not Python round.
A15. orderbook events cannot change q_live.
A16. market-channel stale-book strategy is shadow-only.
A17. public market channel cannot prove fill.
A18. user channel/reconcile is only fill-state authority.
A19. no midpoint/displayed probability/last_trade_price is executable cost.
A20. buy YES uses native YES ask/depth.
A21. buy NO uses native NO ask/depth.
A22. sell uses held token bid/depth.
A23. c_no = 1 - yes_price is forbidden.
A24. fee/tick/min-order/negRisk come from ExecutableMarketSnapshotV2.
A25. Kelly receives typed fee-adjusted ExecutionPrice only.
A26. accepted/resting/matched/partial/cancel remainder/timeout UNKNOWN are distinct.
A27. reactor never imports or calls venue adapter directly.
A28. FDR logs full sibling family once per event family.
A29. RiskGuard remains mandatory.
A30. every event-triggered rejection writes no_trade_regret_events.
A31. later outcome fields are unavailable to live inference.
A32. scheduler maintenance jobs remain intact.
A33. rollback is feature flags off + no reactor; old scheduler path intact.
A34. tiny live pilot is Day0 hard fact only.
A35. taker FOK/FAK live remains off unless execution-law packet approves it.
A36. all new tables are in db_table_ownership.yaml.
A37. all new money-path objects are in money_path_objects.yaml / money_path_ci.yaml.
A38. wrong-DB regression test must pass before merge.
```

**Final implementation verdict:** implement EDLI v1 with these corrections. The original strategic direction is right; the unsafe parts are DB ownership, append-only semantics, ECMWF completeness, Day0 availability/rounding/source authority, market/user channel separation, FDR idempotency, and live execution-policy mismatch. Those are now explicit gates, tests, and PR cuts.

[1]: https://raw.githubusercontent.com/fitz-s/zeus/main/AGENTS.md "raw.githubusercontent.com"
[2]: https://raw.githubusercontent.com/fitz-s/zeus/main/architecture/db_table_ownership.yaml "raw.githubusercontent.com"
[3]: https://www.ecmwf.int/en/forecasts/datasets/open-data "Open data | ECMWF"
[4]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/execution/AGENTS.md "raw.githubusercontent.com"
[5]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/engine/cycle_runtime.py "raw.githubusercontent.com"
[6]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/data/executable_forecast_reader.py "raw.githubusercontent.com"
[7]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/data/observation_client.py "raw.githubusercontent.com"
[8]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/contracts/settlement_semantics.py "raw.githubusercontent.com"
[9]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/strategy/market_analysis.py "raw.githubusercontent.com"
[10]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/strategy/market_fusion.py "raw.githubusercontent.com"
[11]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/strategy/AGENTS.md "raw.githubusercontent.com"
[12]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/strategy/selection_family.py "raw.githubusercontent.com"
[13]: https://docs.polymarket.com/developers/CLOB/websocket/market-channel "Market Channel - Polymarket Documentation"
[14]: https://docs.polymarket.com/developers/CLOB/websocket/user-channel "User Channel - Polymarket Documentation"
[15]: https://docs.polymarket.com/trading/orderbook "Orderbook - Polymarket Documentation"
[16]: https://docs.polymarket.com/trading/orders/create "Create Order - Polymarket Documentation"
[17]: https://docs.polymarket.com/trading/fees "Fees - Polymarket Documentation"
[18]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/data/AGENTS.md "raw.githubusercontent.com"
[19]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/data/ecmwf_open_data.py "raw.githubusercontent.com"
[20]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/contracts/execution_price.py "raw.githubusercontent.com"
[21]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/strategy/kelly.py "raw.githubusercontent.com"
[22]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/venue/AGENTS.md "raw.githubusercontent.com"
[23]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/state/AGENTS.md "raw.githubusercontent.com"
[24]: https://raw.githubusercontent.com/fitz-s/zeus/main/docs/reference/zeus_execution_lifecycle_reference.md "raw.githubusercontent.com"
[25]: https://raw.githubusercontent.com/fitz-s/zeus/main/.github/workflows/money-path-required.yml "raw.githubusercontent.com"
[26]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/state/schema/phase6_evidence_schema.py "raw.githubusercontent.com"
[27]: https://raw.githubusercontent.com/fitz-s/zeus/main/docs/operations/task_2026-05-21_mainline_completion_authority/WAVE2_CRITIC_VERDICT.md "raw.githubusercontent.com"
[28]: https://raw.githubusercontent.com/fitz-s/zeus/main/config/settings.json "raw.githubusercontent.com"
[29]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/engine/evaluator.py "raw.githubusercontent.com"
[30]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/state/no_trade_events.py "raw.githubusercontent.com"
