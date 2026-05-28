"""Native quote feasibility adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.decision_kernel import claims
from src.decision_kernel.authority import DECISION_KERNEL_AUTHORITY_ID, DECISION_KERNEL_AUTHORITY_VERSION
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate

OrderSide = Literal["BUY", "SELL"]
OutcomeSide = Literal["YES", "NO"]


@dataclass(frozen=True)
class NativeQuote:
    outcome: OutcomeSide
    best_bid: float | None
    best_ask: float | None
    visible_depth: float | None = None
    tick_size: str | None = None
    min_order_size: str | None = None
    neg_risk: bool | None = None


def native_execution_price(*, side: OrderSide, quote: NativeQuote) -> float:
    if side == "BUY":
        if quote.best_ask is None:
            raise ValueError("BUY requires native best ask")
        return float(quote.best_ask)
    if side == "SELL":
        if quote.best_bid is None:
            raise ValueError("SELL requires held-token best bid")
        return float(quote.best_bid)
    raise ValueError(f"unsupported side: {side}")


def reject_display_price(price_kind: str) -> None:
    if price_kind in {"midpoint", "display_probability", "last_trade", "complement_cost"}:
        raise ValueError(f"{price_kind} is forbidden as executable cost")


def build_quote_feasibility_certificate(
    *,
    semantic_key: str,
    decision_time: datetime,
    side: OrderSide,
    quote: NativeQuote,
    parent_edges: tuple[ParentEdge, ...] = (),
    parent_certificates: tuple[DecisionCertificate, ...] = (),
) -> DecisionCertificate:
    price = native_execution_price(side=side, quote=quote)
    return build_certificate(
        certificate_type=claims.QUOTE_FEASIBILITY,
        semantic_key=semantic_key,
        claim_type="quote_feasibility",
        mode="NO_SUBMIT",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload={
            "side": side,
            "outcome": quote.outcome,
            "execution_price_type": "ExecutionPrice",
            "native_execution_price": price,
            "best_bid": quote.best_bid,
            "best_ask": quote.best_ask,
            "visible_depth": quote.visible_depth,
            "tick_size": quote.tick_size,
            "min_order_size": quote.min_order_size,
            "neg_risk": quote.neg_risk,
            "fill_claim": False,
        },
        authority_id=DECISION_KERNEL_AUTHORITY_ID,
        authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
        algorithm_id="decision_kernel.quote_adapter.native_bid_ask",
        algorithm_version="v1",
        parent_edges=parent_edges,
        parent_certificates=parent_certificates,
    )
