# Created: 2026-04-27
# Last reused/audited: 2026-05-21
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml
#                  + Phase 4 T2 (2026-05-21): CandidateDecision, CandidateContext, evaluate()
#                    per PHASE_4_PLAN.md §T2 base interface contract
"""Strategy candidate framework for the A1 benchmark harness.

Phase 4 T2 (2026-05-21) additions:
  - CandidateDecision: frozen dataclass representing the outcome of evaluate().
    Two discriminated variants: outcome="enter" (writes decision_events row via
    canonical writer) and outcome="no_trade" (writes no_trade_events row). Never
    returns None; never silent.
  - CandidateContext: bundle type wrapping MarketAnalysisVNext + the writer
    essentials (natural_key, observed_at). ADR: we use CandidateContext rather
    than a wide evaluate() signature because write_decision_event requires
    DecisionNaturalKey + DecisionSourceContext + EffectiveKellyContext which
    MarketAnalysisVNext alone does not carry; bundling keeps the abstract
    evaluate() signature clean and stable.
  - BaseStrategyCandidate.evaluate(): abstract method per plan base contract.
    Default implementation raises NotImplementedError so that T4-deferred stubs
    (cross_market_correlation_hedge, neg_risk_basket) remain instantiable without
    AbstractMethodError — they simply cannot be called at runtime yet.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Optional, Protocol, Tuple, Union

if TYPE_CHECKING:
    from src.analysis.market_analysis_vnext import MarketAnalysisVNext
    from src.contracts.decision_natural_key import DecisionNaturalKey
    from src.contracts.no_trade_reason import NoTradeReason


class StrategyProtocol(Protocol):
    strategy_key: str

    def describe(self) -> str: ...


@dataclass(frozen=True)
class CandidateMetadata:
    strategy_key: str
    family: str
    description: str
    executable_alpha: bool = False


@dataclass(frozen=True)
class CandidateContext:
    """Bundle type carrying everything evaluate() needs beyond MarketAnalysisVNext.

    Candidates receive context + conn + decision_time via evaluate(). The
    natural_key and observed_at fields are required for canonical writer calls;
    they are computed by the caller before dispatching evaluate().

    ADR: CandidateContext keeps the evaluate() signature stable regardless of
    how write_decision_event's required fields evolve. Candidates read fields
    they need; unused fields are zero-cost to pass.
    """

    # Per-call write essentials
    natural_key: "DecisionNaturalKey"
    observed_at: str  # ISO-8601 UTC

    # Market analysis
    analysis: "MarketAnalysisVNext"

    # Optional: caller-side timing stamps for live rows (may be empty for shadow)
    first_member_observed_time: Optional[str] = None
    run_complete_time: Optional[str] = None
    zeus_submit_intent_time: Optional[str] = None
    venue_ack_time: Optional[str] = None


@dataclass(frozen=True)
class CandidateDecision:
    """Discriminated result of BaseStrategyCandidate.evaluate().

    outcome="enter": candidate recommends entering a position.
      Required field (enforced): side.
      Optional fields for shadow candidates: target_price, target_size_usd, edge,
      p_posterior. Shadow candidates do not have live sizing; these may be None.
      Live-promoted candidates must populate all sizing fields before writing
      through the canonical write_decision_event path.

    outcome="no_trade": candidate recommends not trading.
      Required field (enforced): reason (NoTradeReason).
      Optional: reason_detail (human-readable context).

    Neither variant is None. A candidate that has no opinion emits no_trade
    with an appropriate reason rather than returning None or raising.
    """

    outcome: Literal["enter", "no_trade"]

    # enter fields
    side: Optional[str] = None
    target_price: Optional[Decimal] = None
    target_size_usd: Optional[Decimal] = None
    edge: Optional[Decimal] = None
    p_posterior: Optional[Decimal] = None

    # no_trade fields
    reason: Optional["NoTradeReason"] = None
    reason_detail: Optional[str] = None

    def __post_init__(self) -> None:
        if self.outcome == "enter":
            # side is the only required field — shadow candidates may omit
            # target_price / target_size_usd (no live sizing).
            if self.side is None:
                raise ValueError(
                    "CandidateDecision(outcome='enter') requires a non-None side."
                )
        elif self.outcome == "no_trade":
            if self.reason is None:
                raise ValueError(
                    "CandidateDecision(outcome='no_trade') requires a non-None reason."
                )
        else:
            raise ValueError(f"CandidateDecision.outcome must be 'enter' or 'no_trade'; got {self.outcome!r}")


@dataclass(frozen=True)
class PriceLevel:
    """A single price level in an order-book leg.

    price: probability price (0 < price < 1) for YES or NO.
    quantity: number of shares available at this level.
    """

    price: Decimal
    quantity: Decimal  # shares (notional units)


@dataclass(frozen=True)
class LegBook:
    """Order-book depth for one token (YES or NO) in a neg-risk family.

    yes_levels: list of PriceLevel sorted ascending by price (best ask first).
    no_levels: list of PriceLevel sorted ascending by price (best ask first).
    condition_id: Polymarket condition ID for this token.
    """

    condition_id: str
    yes_levels: Tuple[PriceLevel, ...]
    no_levels: Tuple[PriceLevel, ...]


@dataclass(frozen=True)
class FamilyOrderBookSnapshot:
    """Complete order-book snapshot for a neg-risk family.

    legs: one LegBook per outcome token in the family, ordered by outcome index.
    K: number of outcomes in the family (= len(legs)).
    neg_risk_market_id: Polymarket neg-risk market/group identifier.
    captured_at_iso: ISO-8601 timestamp when book was captured.
    """

    legs: Tuple[LegBook, ...]
    neg_risk_market_id: str
    captured_at_iso: str

    @property
    def K(self) -> int:  # noqa: N802 — matches math-spec variable
        return len(self.legs)


@dataclass(frozen=True)
class LegIntent:
    """Single-leg fill intent produced by a vector-edge strategy.

    side: "buy_yes" or "buy_no".
    condition_id: token being bought.
    quantity: shares to fill at q*.
    price_limit: maximum acceptable fill price (= best ask used in sweep).
    """

    side: Literal["buy_yes", "buy_no"]
    condition_id: str
    quantity: Decimal
    price_limit: Decimal


@dataclass(frozen=True)
class DeterministicEdgeDecision:
    """§19.2 deterministic single-leg decision — pathwise-certain payoff.

    Authority: zeus_strategy_spec.md §19.2.

    Fields are the §19 superset so single-leg deterministic strategies (D2/D3
    settlement capture, resolution window) can carry side/token_id/executable_price
    to the executor without casting to CandidateDecision.
    """

    outcome: Literal["enter"] = field(default="enter", init=False)
    strategy_key: str                   # e.g. "settlement_capture"
    proof_type: str                     # e.g. "physical_interval_subset"
    side: Literal["buy_yes", "buy_no"]  # token being bought
    token_id: str                       # Polymarket condition ID
    executable_price: Decimal           # best-ask fill price
    fee: Decimal                        # phi(q, price, rate) for this leg
    deterministic_payoff: Decimal       # gross payoff if executed ($)
    deterministic_profit: Decimal       # payoff - executable_price - fee
    proof_inputs_hash: str              # SHA-256 hex of proof inputs

    def __post_init__(self) -> None:
        if self.deterministic_profit <= Decimal(0):
            raise ValueError(
                "DeterministicEdgeDecision requires deterministic_profit > 0; "
                f"got {self.deterministic_profit}"
            )


@dataclass(frozen=True)
class VectorEdgeDecision:
    """§19.3 vector decision — multi-leg deterministic basket.

    Authority: zeus_strategy_spec.md §19.3.

    basket_execution_id: nullable UUID until §11.8 multi-leg execution lands;
      pass empty string "" for shadow rows.
    vector_cost: Σ sweep notional across all legs at q* (excluding fees).
    vector_fee: Σ phi(q*, price, rate) across all legs.
    vector_payoff: deterministic payoff (q* for YES basket; (K-1)*q* for NO).
    vector_profit: vector_payoff - vector_cost - vector_fee.
    """

    outcome: Literal["enter"] = field(default="enter", init=False)
    strategy_key: str                    # "neg_risk_basket"
    proof_type: str                      # "complete_family_basket"
    basket_execution_id: str             # "" until multi-leg execution ships
    legs: Tuple[LegIntent, ...]
    q_star: Decimal
    vector_cost: Decimal                 # Σ sweep notional (price × qty per level)
    vector_fee: Decimal                  # Σ phi across all legs
    vector_payoff: Decimal               # deterministic payoff
    vector_profit: Decimal               # = payoff - cost - fee
    proof_inputs_hash: str               # SHA-256 of (family legs, q_star, fee_rate) — §19.3

    def __post_init__(self) -> None:
        if self.vector_profit <= Decimal(0):
            raise ValueError(
                "VectorEdgeDecision requires vector_profit > 0; "
                f"got {self.vector_profit}"
            )


def _is_world_db_conn(conn: sqlite3.Connection) -> bool:
    """Return True when *conn* targets zeus-world.db (not an in-memory DB).

    Detected via PRAGMA database_list: the 'main' database file path ends with
    'zeus-world.db'. In-memory connections return an empty string for the path.
    """
    from src.state.db import ZEUS_WORLD_DB_PATH
    rows = conn.execute("PRAGMA database_list").fetchall()
    for _seq, _name, path in rows:
        if _name == "main" and path:
            from pathlib import Path
            return Path(path).resolve() == Path(ZEUS_WORLD_DB_PATH).resolve()
    return False


def _candidate_strategy_key_for_reason(reason: Optional["NoTradeReason"]) -> str:
    """Map shadow candidate no-trade reasons back to their candidate strategy.

    Candidate no-trade rows are written from the shared framework, after the
    concrete candidate has already returned a canonical reason. Preserve the
    strategy provenance here instead of relying on ad-hoc attributes on the
    analysis object.
    """
    if reason is None:
        return "unknown_candidate"

    from src.contracts.no_trade_reason import NoTradeReason

    strategy_by_reason = {
        NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE: "stale_quote_detector",
        NoTradeReason.RESOLUTION_DISPUTED: "resolution_window_maker",
        NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE: "resolution_window_maker",
        NoTradeReason.LIQPROV_HEARTBEAT_ABSENT: "liquidity_provision_with_heartbeat",
        NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED: "weather_event_arbitrage",
        NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE: "cross_market_correlation_hedge",
        NoTradeReason.NEGRISK_FAMILY_INCOMPLETE: "neg_risk_basket",
        NoTradeReason.NEGRISK_NO_PROFITABLE_BASKET: "neg_risk_basket",
        # shoulder_impossible_tail_capture data-gate and theorem failure
        NoTradeReason.PHYSICAL_ENVELOPE_UNWIRED: "shoulder_impossible_tail_capture",
        NoTradeReason.SHOULDER_PHYSICAL_BOUND_NOT_EXCLUDES_TAIL: "shoulder_impossible_tail_capture",
    }
    return strategy_by_reason.get(reason, "unknown_candidate")


def write_candidate_no_trade_row(
    conn: sqlite3.Connection,
    context: "CandidateContext",
    decision: "CandidateDecision",
) -> None:
    """Write a no_trade_events row for a candidate no-trade decision.

    On world-DB connections, acquires db_writer_lock(LIVE) around seq allocation
    + INSERT to prevent PRIMARY KEY races with other decision/no-trade writers.
    On in-memory (test) connections, inserts directly without the lock (the
    world-DB path assertion in write_no_trade_event would fail in-memory anyway).

    This is the canonical write path for candidate no_trade outcomes in shadow
    mode. Live promotion of any candidate must route through the canonical
    write_no_trade_event writer with a real world-DB connection.
    """
    from src.state.db import SCHEMA_VERSION
    from src.state.decision_events import allocate_decision_seq

    market_slug, temperature_metric, target_date, observation_time, _ = context.natural_key
    candidate_strategy_key = _candidate_strategy_key_for_reason(decision.reason)
    reason_detail = (
        f"shadow_runtime=true; candidate_strategy_key={candidate_strategy_key}; "
        f"{decision.reason_detail or ''}"
    )

    if _is_world_db_conn(conn):
        from src.state.no_trade_events import write_no_trade_event

        write_no_trade_event(
            context.natural_key,
            decision.reason,  # type: ignore[arg-type]
            reason_detail,
            context.observed_at,
            conn=conn,
            allow_schema_compatibility_downgrade=False,
            strategy_key=candidate_strategy_key,
            event_source="shadow_decision",
            shadow_runtime=True,
        )
        return

    def _do_insert(seq: int) -> None:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(no_trade_events)").fetchall()
        }
        if {"schema_compatibility", "strategy_key", "event_source", "shadow_runtime"} <= columns:
            conn.execute(
                """
                INSERT INTO no_trade_events (
                    market_slug, temperature_metric, target_date, observation_time, decision_seq,
                    reason, reason_detail, strategy_key, event_source, shadow_runtime,
                    observed_at, schema_version, schema_compatibility
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    market_slug, temperature_metric, target_date, observation_time, seq,
                    decision.reason.value if decision.reason is not None else "uncategorized",
                    reason_detail,
                    candidate_strategy_key,
                    "shadow_decision",
                    1,
                    context.observed_at,
                    SCHEMA_VERSION,
                    "current",
                ),
            )
        elif "schema_compatibility" in columns:
            conn.execute(
                """
                INSERT INTO no_trade_events (
                    market_slug, temperature_metric, target_date, observation_time, decision_seq,
                    reason, reason_detail, observed_at, schema_version, schema_compatibility
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    market_slug, temperature_metric, target_date, observation_time, seq,
                    decision.reason.value if decision.reason is not None else "uncategorized",
                    reason_detail,
                    context.observed_at,
                    SCHEMA_VERSION,
                    "current",
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO no_trade_events (
                    market_slug, temperature_metric, target_date, observation_time, decision_seq,
                    reason, reason_detail, observed_at, schema_version
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    market_slug, temperature_metric, target_date, observation_time, seq,
                    decision.reason.value if decision.reason is not None else "uncategorized",
                    reason_detail,
                    context.observed_at,
                    SCHEMA_VERSION,
                ),
            )
        conn.commit()

    seq = allocate_decision_seq(
        market_slug, temperature_metric, target_date, observation_time, conn=conn
    )
    _do_insert(seq)


@dataclass(frozen=True)
class BaseStrategyCandidate:
    metadata: CandidateMetadata

    @property
    def strategy_key(self) -> str:
        return self.metadata.strategy_key

    def describe(self) -> str:
        return self.metadata.description

    def evaluate(
        self,
        *,
        context: "CandidateContext",
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> "Union[CandidateDecision, DeterministicEdgeDecision, VectorEdgeDecision]":
        """Evaluate the candidate strategy against the given market context.

        Must return a CandidateDecision, DeterministicEdgeDecision, or
        VectorEdgeDecision — never None. Implementors should:
          - Return CandidateDecision(outcome="enter", ...) for single-leg stochastic.
          - Return DeterministicEdgeDecision(outcome="enter", ...) for pathwise-certain
            single-leg arb (settlement capture, resolution window maker).
          - Return VectorEdgeDecision(outcome="enter", ...) for multi-leg basket arb.
          - Return CandidateDecision(outcome="no_trade", reason=<specific reason>, ...)
            when no edge is found or a guard condition fires.

        Default implementation raises NotImplementedError so that T4-deferred
        stubs (cross_market_correlation_hedge, neg_risk_basket) remain
        instantiable without triggering AbstractMethodError at import time.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.evaluate() is not implemented. "
            "Override this method in the concrete subclass."
        )


from .weather_event_arbitrage import WeatherEventArbitrage
from .stale_quote_detector import StaleQuoteDetector
from .resolution_window_maker import ResolutionWindowMaker
from .neg_risk_basket import NegRiskBasket
from .cross_market_correlation_hedge import CrossMarketCorrelationHedge
from .liquidity_provision_with_heartbeat import LiquidityProvisionWithHeartbeat
from .center_sell_parity import CenterSellParity
from .center_sell_model_no import CenterSellModelNo
from .shoulder_impossible_tail_capture import ShoulderImpossibleTailCapture
from .settlement_capture_shadow import PhysicalIntervalBound, SettlementCaptureShadow

__all__ = [
    "_is_world_db_conn",
    "BaseStrategyCandidate",
    "CandidateContext",
    "CandidateDecision",
    "CandidateMetadata",
    "CenterSellModelNo",
    "CenterSellParity",
    "CrossMarketCorrelationHedge",
    "DeterministicEdgeDecision",
    "FamilyOrderBookSnapshot",
    "LegBook",
    "LegIntent",
    "LiquidityProvisionWithHeartbeat",
    "NegRiskBasket",
    "PhysicalIntervalBound",
    "PriceLevel",
    "ResolutionWindowMaker",
    "SettlementCaptureShadow",
    "ShoulderImpossibleTailCapture",
    "StaleQuoteDetector",
    "StrategyProtocol",
    "VectorEdgeDecision",
    "WeatherEventArbitrage",
    "write_candidate_no_trade_row",
]
