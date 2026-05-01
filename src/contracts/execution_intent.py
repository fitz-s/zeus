from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import TYPE_CHECKING, Any, Literal, Mapping

from src.contracts.semantic_types import Direction

if TYPE_CHECKING:
    from src.contracts.slippage_bps import SlippageBps
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2


CorrectedPricingSemanticsVersion = Literal["corrected_executable_cost_v1"]
ExecutionDirection = Literal["buy_yes", "buy_no", "sell_yes", "sell_no"]
EntryDirection = Literal["buy_yes", "buy_no"]
OrderPolicy = Literal[
    "limit_may_take_conservative",
    "post_only_passive_limit",
    "marketable_limit_depth_bound",
]
OrderSizeKind = Literal["shares", "notional_usd"]
OrderType = Literal["GTC", "GTD", "FOK", "FAK"]
OutcomeLabel = Literal["YES", "NO"]
SweepBookSide = Literal["asks", "bids"]
SweepDepthStatus = Literal["PASS", "DEPTH_INSUFFICIENT", "EMPTY_BOOK"]
CostBasisDepthStatus = Literal[
    "PASS",
    "DEPTH_INSUFFICIENT",
    "EMPTY_BOOK",
    "NOT_MARKETABLE_PASSIVE_LIMIT",
    "UNVERIFIED_DEPTH",
]
DepthProofSource = Literal["CLOB_SWEEP", "PASSIVE_LIMIT", "UNVERIFIED"]

CORRECTED_PRICING_SEMANTICS_VERSION: CorrectedPricingSemanticsVersion = (
    "corrected_executable_cost_v1"
)
_ALLOWED_ORDER_POLICIES = frozenset(
    {
        "limit_may_take_conservative",
        "post_only_passive_limit",
        "marketable_limit_depth_bound",
    }
)
_ALLOWED_DEPTH_STATUSES = frozenset(
    {
        "PASS",
        "DEPTH_INSUFFICIENT",
        "EMPTY_BOOK",
        "NOT_MARKETABLE_PASSIVE_LIMIT",
        "UNVERIFIED_DEPTH",
    }
)
_ALLOWED_DEPTH_PROOF_SOURCES = frozenset(
    {"CLOB_SWEEP", "PASSIVE_LIMIT", "UNVERIFIED"}
)


_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def _context_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text


def _context_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_payload_hash(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX_CHARS for char in value)


def _as_decimal(value: Any, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be decimal-compatible") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return decimal_value


def _decimal_text(value: Decimal) -> str:
    """Return context-independent decimal text for identity hashes."""

    value = _as_decimal(value, "decimal")
    if value.is_zero():
        return "0"
    sign, digits, exponent = value.as_tuple()
    digits_text = "".join(str(digit) for digit in digits) or "0"
    while digits_text.endswith("0"):
        digits_text = digits_text[:-1]
        exponent += 1
    if exponent >= 0:
        text = digits_text + ("0" * exponent)
    else:
        decimal_index = len(digits_text) + exponent
        if decimal_index > 0:
            text = digits_text[:decimal_index] + "." + digits_text[decimal_index:]
        else:
            text = "0." + ("0" * -decimal_index) + digits_text
    return f"-{text}" if sign else text


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_unit_interval_open(value: Decimal, field_name: str) -> None:
    if value <= Decimal("0") or value >= Decimal("1"):
        raise ValueError(f"{field_name} must be inside (0, 1), got {value}")


def _require_unit_interval_closed(value: Decimal, field_name: str) -> None:
    if value < Decimal("0") or value > Decimal("1"):
        raise ValueError(f"{field_name} must be inside [0, 1], got {value}")


def _outcome_label_for_direction(direction: str) -> OutcomeLabel:
    if direction in {"buy_yes", "sell_yes"}:
        return "YES"
    if direction in {"buy_no", "sell_no"}:
        return "NO"
    raise ValueError(f"unsupported execution direction {direction!r}")


def _selected_token_for_direction(
    snapshot: "ExecutableMarketSnapshotV2",
    direction: str,
) -> tuple[str, OutcomeLabel]:
    outcome_label = _outcome_label_for_direction(direction)
    selected_token_id = (
        snapshot.yes_token_id if outcome_label == "YES" else snapshot.no_token_id
    )
    if snapshot.selected_outcome_token_id and snapshot.selected_outcome_token_id != selected_token_id:
        raise ValueError(
            "snapshot selected_outcome_token_id does not match execution direction"
        )
    if snapshot.outcome_label and snapshot.outcome_label != outcome_label:
        raise ValueError("snapshot outcome_label does not match execution direction")
    return selected_token_id, outcome_label


def _assert_tick_aligned(price: Decimal, tick_size: Decimal) -> None:
    if tick_size <= Decimal("0"):
        raise ValueError("tick_size must be positive")
    if price % tick_size != Decimal("0"):
        raise ValueError(f"final_limit_price {price} is not aligned to tick_size {tick_size}")


def _assert_min_order_satisfied(
    *,
    size_kind: OrderSizeKind,
    size_value: Decimal,
    final_limit_price: Decimal,
    min_order_size: Decimal,
) -> None:
    if min_order_size <= Decimal("0"):
        raise ValueError("min_order_size must be positive")
    if size_value <= Decimal("0"):
        raise ValueError("size_value must be positive")
    shares = size_value
    if size_kind == "notional_usd":
        _require_unit_interval_open(final_limit_price, "final_limit_price")
        shares = size_value / final_limit_price
    if shares < min_order_size:
        raise ValueError(
            f"size {shares} shares is below min_order_size {min_order_size}"
        )


def _quantize_submit_shares(direction: ExecutionDirection, shares: Decimal) -> Decimal:
    if shares <= Decimal("0"):
        raise ValueError("submitted_shares must be positive")
    quantum = Decimal("0.01")
    rounding = ROUND_CEILING if direction.startswith("buy_") else ROUND_FLOOR
    quantized = (shares / quantum).to_integral_value(rounding=rounding) * quantum
    if quantized <= Decimal("0"):
        raise ValueError("submitted_shares rounded to zero")
    return quantized


def _submitted_shares_from_cost_basis(cost_basis: "ExecutableCostBasis") -> Decimal:
    if cost_basis.requested_size_kind == "shares":
        raw_shares = cost_basis.requested_size_value
    else:
        raw_shares = (
            cost_basis.requested_size_value
            / cost_basis.expected_fill_price_before_fee
        )
    return _quantize_submit_shares(cost_basis.direction, raw_shares)


def _fee_adjusted_price(
    *,
    expected_fill_price_before_fee: Decimal,
    fee_rate: Decimal,
    direction: str,
) -> Decimal:
    fee = fee_rate * expected_fill_price_before_fee * (
        Decimal("1") - expected_fill_price_before_fee
    )
    if direction.startswith("buy_"):
        return expected_fill_price_before_fee + fee
    if direction.startswith("sell_"):
        return expected_fill_price_before_fee - fee
    raise ValueError(f"unsupported execution direction {direction!r}")


def _adverse_slippage_bps(
    *,
    direction: str,
    reference_price: Decimal,
    final_limit_price: Decimal,
) -> Decimal:
    _require_unit_interval_open(reference_price, "slippage_reference_price")
    _require_unit_interval_open(final_limit_price, "final_limit_price")
    if direction.startswith("buy_"):
        adverse = final_limit_price - reference_price
    elif direction.startswith("sell_"):
        adverse = reference_price - final_limit_price
    else:
        raise ValueError(f"unsupported execution direction {direction!r}")
    if adverse <= Decimal("0"):
        return Decimal("0")
    return adverse / reference_price * Decimal("10000")


def _assert_fill_limit_coherent(
    *,
    direction: str,
    expected_fill_price_before_fee: Decimal,
    final_limit_price: Decimal,
) -> None:
    if direction.startswith("buy_") and expected_fill_price_before_fee > final_limit_price:
        raise ValueError("buy expected_fill_price_before_fee exceeds final_limit_price")
    if direction.startswith("sell_") and expected_fill_price_before_fee < final_limit_price:
        raise ValueError("sell expected_fill_price_before_fee is below final_limit_price")


def _assert_order_policy_coherent(
    *,
    order_policy: str,
    order_type: str,
    post_only: bool,
) -> None:
    if order_policy not in _ALLOWED_ORDER_POLICIES:
        raise ValueError(f"unsupported order_policy {order_policy!r}")
    if order_policy == "post_only_passive_limit":
        if not post_only:
            raise ValueError("post_only_passive_limit requires post_only=True")
        if order_type not in {"GTC", "GTD"}:
            raise ValueError("post_only_passive_limit requires GTC/GTD")
    if order_policy == "marketable_limit_depth_bound":
        if post_only:
            raise ValueError("marketable_limit_depth_bound forbids post_only=True")
        if order_type not in {"FOK", "FAK"}:
            raise ValueError("marketable_limit_depth_bound requires FOK/FAK")
    if order_policy == "limit_may_take_conservative" and post_only:
        raise ValueError("limit_may_take_conservative is not post_only")


def _assert_cost_basis_order_policy_coherent(
    *,
    order_policy: str,
    depth_status: str,
    depth_proof_source: str,
) -> None:
    if order_policy == "post_only_passive_limit":
        if depth_status != "NOT_MARKETABLE_PASSIVE_LIMIT":
            raise ValueError(
                "post_only_passive_limit cost basis requires "
                "NOT_MARKETABLE_PASSIVE_LIMIT"
            )
        if depth_proof_source != "PASSIVE_LIMIT":
            raise ValueError(
                "post_only_passive_limit cost basis requires PASSIVE_LIMIT proof"
            )
    elif order_policy == "marketable_limit_depth_bound":
        if depth_proof_source != "CLOB_SWEEP":
            raise ValueError(
                "marketable_limit_depth_bound requires CLOB_SWEEP depth proof"
            )
        if depth_status not in {"PASS", "DEPTH_INSUFFICIENT", "EMPTY_BOOK"}:
            raise ValueError(
                "marketable_limit_depth_bound requires sweep depth status"
            )
    elif order_policy == "limit_may_take_conservative":
        passive_only = (
            depth_proof_source == "PASSIVE_LIMIT"
            or depth_status == "NOT_MARKETABLE_PASSIVE_LIMIT"
        )
        if passive_only:
            raise ValueError(
                "limit_may_take_conservative cannot claim passive-only depth proof"
            )


def _orderbook_levels(snapshot: "ExecutableMarketSnapshotV2", side: SweepBookSide) -> list[tuple[Decimal, Decimal]]:
    try:
        orderbook = json.loads(snapshot.orderbook_depth_jsonb)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid orderbook_depth_jsonb: {exc}") from exc
    if not isinstance(orderbook, Mapping):
        raise ValueError("orderbook_depth_jsonb must decode to a mapping")
    raw_levels = orderbook.get(side)
    if not isinstance(raw_levels, list) or not raw_levels:
        return []
    levels: list[tuple[Decimal, Decimal]] = []
    for raw in raw_levels:
        if isinstance(raw, Mapping):
            price = raw.get("price")
            size = raw.get("size")
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            price, size = raw[0], raw[1]
        else:
            raise ValueError(f"malformed {side} level")
        price_decimal = _as_decimal(price, f"{side}.price")
        size_decimal = _as_decimal(size, f"{side}.size")
        _require_unit_interval_open(price_decimal, f"{side}.price")
        if size_decimal <= Decimal("0"):
            continue
        levels.append((price_decimal, size_decimal))
    reverse = side == "bids"
    return sorted(levels, key=lambda level: level[0], reverse=reverse)


@dataclass(frozen=True)
class ClobSweepResult:
    """Depth-weighted executable fill simulation for one selected token book."""

    direction: ExecutionDirection
    book_side: SweepBookSide
    requested_size_kind: OrderSizeKind
    requested_size_value: Decimal
    limit_price: Decimal
    filled_shares: Decimal
    gross_notional: Decimal
    average_price: Decimal | None
    worst_price: Decimal | None
    unfilled_size_value: Decimal
    depth_status: SweepDepthStatus
    levels_consumed: int

    @property
    def fully_filled(self) -> bool:
        return self.depth_status == "PASS"


def simulate_clob_sweep(
    *,
    snapshot: "ExecutableMarketSnapshotV2",
    direction: ExecutionDirection,
    requested_size_kind: OrderSizeKind,
    requested_size_value: Decimal,
    limit_price: Decimal,
) -> ClobSweepResult:
    """Simulate a depth-bound fill without mutating state or contacting venue."""

    requested = _as_decimal(requested_size_value, "requested_size_value")
    limit = _as_decimal(limit_price, "limit_price")
    if requested <= Decimal("0"):
        raise ValueError("requested_size_value must be positive")
    _require_unit_interval_open(limit, "limit_price")
    if requested_size_kind not in {"shares", "notional_usd"}:
        raise ValueError(f"unsupported requested_size_kind {requested_size_kind!r}")
    if direction not in {"buy_yes", "buy_no", "sell_yes", "sell_no"}:
        raise ValueError(f"unsupported execution direction {direction!r}")
    _selected_token_for_direction(snapshot, direction)

    book_side: SweepBookSide = "asks" if direction.startswith("buy_") else "bids"
    levels = _orderbook_levels(snapshot, book_side)
    if not levels:
        return ClobSweepResult(
            direction=direction,
            book_side=book_side,
            requested_size_kind=requested_size_kind,
            requested_size_value=requested,
            limit_price=limit,
            filled_shares=Decimal("0"),
            gross_notional=Decimal("0"),
            average_price=None,
            worst_price=None,
            unfilled_size_value=requested,
            depth_status="EMPTY_BOOK",
            levels_consumed=0,
        )

    remaining = requested
    filled_shares = Decimal("0")
    gross_notional = Decimal("0")
    worst_price: Decimal | None = None
    levels_consumed = 0
    for price, level_shares in levels:
        if direction.startswith("buy_"):
            if price > limit:
                break
            if requested_size_kind == "shares":
                take_shares = min(level_shares, remaining)
            else:
                take_shares = min(level_shares, remaining / price)
            take_notional = take_shares * price
        else:
            if price < limit:
                break
            if requested_size_kind == "shares":
                take_shares = min(level_shares, remaining)
                take_notional = take_shares * price
            else:
                take_shares = min(level_shares, remaining / price)
                take_notional = take_shares * price
        if take_shares <= Decimal("0"):
            break
        filled_shares += take_shares
        gross_notional += take_notional
        worst_price = price
        levels_consumed += 1
        remaining -= take_shares if requested_size_kind == "shares" else take_notional
        if remaining <= Decimal("0"):
            remaining = Decimal("0")
            break

    average_price = gross_notional / filled_shares if filled_shares > 0 else None
    depth_status: SweepDepthStatus = "PASS" if remaining == 0 else "DEPTH_INSUFFICIENT"
    return ClobSweepResult(
        direction=direction,
        book_side=book_side,
        requested_size_kind=requested_size_kind,
        requested_size_value=requested,
        limit_price=limit,
        filled_shares=filled_shares,
        gross_notional=gross_notional,
        average_price=average_price,
        worst_price=worst_price,
        unfilled_size_value=remaining,
        depth_status=depth_status,
        levels_consumed=levels_consumed,
    )


@dataclass(frozen=True)
class DecisionSourceContext:
    """Compact decision-source evidence consumed by execution capability proof.

    This is intentionally not a source-routing policy. Evaluator decides source
    authority before selection; execution only verifies that the accepted
    decision's source/timing evidence survived the handoff to live submit.
    """

    source_id: str = ""
    model_family: str = ""
    forecast_issue_time: str = ""
    forecast_valid_time: str = ""
    forecast_fetch_time: str = ""
    forecast_available_at: str = ""
    raw_payload_hash: str = ""
    degradation_level: str = ""
    forecast_source_role: str = ""
    authority_tier: str = ""
    decision_time: str = ""
    decision_time_status: str = ""

    @classmethod
    def from_forecast_context(cls, context: Mapping[str, object] | None) -> "DecisionSourceContext | None":
        if not isinstance(context, Mapping):
            return None
        return cls(
            source_id=_context_text(context.get("forecast_source_id") or context.get("source_id")),
            model_family=_context_text(context.get("model_family") or context.get("model")),
            forecast_issue_time=_context_text(context.get("forecast_issue_time") or context.get("issue_time")),
            forecast_valid_time=_context_text(context.get("forecast_valid_time") or context.get("valid_time")),
            forecast_fetch_time=_context_text(context.get("forecast_fetch_time") or context.get("fetch_time")),
            forecast_available_at=_context_text(context.get("forecast_available_at") or context.get("available_at")),
            raw_payload_hash=_context_text(context.get("raw_payload_hash")),
            degradation_level=_context_text(context.get("degradation_level")),
            forecast_source_role=_context_text(context.get("forecast_source_role")),
            authority_tier=_context_text(context.get("authority_tier")),
            decision_time=_context_text(context.get("decision_time") or context.get("decision_time_utc")),
            decision_time_status=_context_text(context.get("decision_time_status")),
        )

    def integrity_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        required_fields = {
            "source_id": self.source_id,
            "model_family": self.model_family,
            "forecast_issue_time": self.forecast_issue_time,
            "forecast_valid_time": self.forecast_valid_time,
            "forecast_fetch_time": self.forecast_fetch_time,
            "forecast_available_at": self.forecast_available_at,
            "raw_payload_hash": self.raw_payload_hash,
            "degradation_level": self.degradation_level,
            "forecast_source_role": self.forecast_source_role,
            "authority_tier": self.authority_tier,
            "decision_time": self.decision_time,
            "decision_time_status": self.decision_time_status,
        }
        for field, value in required_fields.items():
            if not value:
                errors.append(f"missing_{field}")

        if self.raw_payload_hash and not _valid_payload_hash(self.raw_payload_hash):
            errors.append("invalid_raw_payload_hash")

        parsed_times = {
            "forecast_issue_time": _context_time(self.forecast_issue_time),
            "forecast_valid_time": _context_time(self.forecast_valid_time),
            "forecast_fetch_time": _context_time(self.forecast_fetch_time),
            "forecast_available_at": _context_time(self.forecast_available_at),
            "decision_time": _context_time(self.decision_time),
        }
        for field, parsed in parsed_times.items():
            if required_fields.get(field) and parsed is None:
                errors.append(f"invalid_{field}")

        decision_time = parsed_times["decision_time"]
        issue_time = parsed_times["forecast_issue_time"]
        fetch_time = parsed_times["forecast_fetch_time"]
        available_at = parsed_times["forecast_available_at"]
        if decision_time is not None:
            if issue_time is not None and issue_time > decision_time:
                errors.append("forecast_issue_after_decision")
            if fetch_time is not None and fetch_time > decision_time:
                errors.append("forecast_fetch_after_decision")
            if available_at is not None and available_at > decision_time:
                errors.append("forecast_available_after_decision")
        if issue_time is not None and fetch_time is not None and issue_time > fetch_time:
            errors.append("forecast_issue_after_fetch_time")
        if issue_time is not None and available_at is not None and issue_time > available_at:
            errors.append("forecast_issue_after_available_at")
        if available_at is not None and fetch_time is not None and available_at > fetch_time:
            errors.append("forecast_available_after_fetch_time")

        if self.forecast_source_role and self.forecast_source_role != "entry_primary":
            errors.append(f"forecast_role_not_entry_primary:{self.forecast_source_role}")
        if self.degradation_level and self.degradation_level != "OK":
            errors.append(f"forecast_degraded:{self.degradation_level}")
        if self.authority_tier and self.authority_tier != "FORECAST":
            errors.append(f"authority_not_forecast:{self.authority_tier}")
        if self.decision_time_status and self.decision_time_status != "OK":
            errors.append(f"decision_time_status_not_ok:{self.decision_time_status}")

        return tuple(errors)

    def capability_details(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "model_family": self.model_family,
            "forecast_issue_time": self.forecast_issue_time,
            "forecast_valid_time": self.forecast_valid_time,
            "forecast_fetch_time": self.forecast_fetch_time,
            "forecast_available_at": self.forecast_available_at,
            "raw_payload_hash": self.raw_payload_hash,
            "degradation_level": self.degradation_level,
            "forecast_source_role": self.forecast_source_role,
            "authority_tier": self.authority_tier,
            "decision_time": self.decision_time,
            "decision_time_status": self.decision_time_status,
        }


@dataclass(frozen=True)
class ExecutionIntent:
    """Replaces loose size_usd passing and limits. 
    
    Contains toxicity budgets, sandbox flags, and 
    everything risk-related from the Adverse Execution Plane.
    """
    direction: Direction
    target_size_usd: float
    limit_price: float
    toxicity_budget: float
    # Slice P3.3 (PR #19 phase 3, 2026-04-26): typed slippage budget.
    # Pre-fix `max_slippage: float` was unit-ambiguous (caller read 0.02 as
    # either 0.02 bps or 2% — the type system couldn't distinguish) AND
    # had zero readers in src/, making it a dead budget. Promoting to
    # SlippageBps gives the magnitude an explicit unit (bps) and an
    # explicit direction semantic (adverse limit). create_execution_intent
    # enforces this against the executable quote reference before command
    # persistence/SDK contact.
    max_slippage: "SlippageBps"
    is_sandbox: bool
    market_id: str
    token_id: str
    timeout_seconds: int
    decision_edge: float = 0.0  # T5.a 2026-04-23: field was read at src/execution/executor.py:136,428 but missing from dataclass, latent TypeError on live entry; paired default maintains backward compatibility.
    # U1: executable CLOB snapshot citation required by venue_command_repo
    # before persistence/submission. Do not populate this from forecast
    # decision_snapshot_id; it is market/execution truth, not model truth.
    executable_snapshot_id: str = ""
    executable_snapshot_min_tick_size: Decimal | str | None = None
    executable_snapshot_min_order_size: Decimal | str | None = None
    executable_snapshot_neg_risk: bool | None = None
    # R3 A2 allocation metadata. These fields are intentionally typed on the
    # production intent boundary so per-event / per-resolution-window /
    # correlated-exposure caps do not depend on test-only dynamic attributes.
    event_id: str = ""
    resolution_window: str = "default"
    correlation_key: str = ""
    decision_source_context: DecisionSourceContext | None = None
    submit_order_type: OrderType | None = None

    def __post_init__(self) -> None:
        # Slice P3-fix1 (post-review BLOCKER from critic M1 + code-reviewer
        # M3, 2026-04-26): runtime isinstance check on max_slippage.
        # Pre-fix1, the type annotation was a string (forward-ref via
        # TYPE_CHECKING), so passing a raw float silently stored as float
        # — the typing seam was illusory. Now the dataclass refuses
        # construction at runtime; the antibody is universal across
        # production callers + every test fixture.
        from src.contracts.slippage_bps import SlippageBps
        if not isinstance(self.max_slippage, SlippageBps):
            raise TypeError(
                f"ExecutionIntent.max_slippage must be SlippageBps, "
                f"got {type(self.max_slippage).__name__} "
                f"(value={self.max_slippage!r}). Per P3.3 + P3-fix1, "
                f"raw floats are no longer accepted at the boundary."
            )
        normalized_event = str(self.event_id or self.market_id or "").strip()
        normalized_window = str(self.resolution_window or "default").strip() or "default"
        normalized_correlation = str(self.correlation_key or normalized_event or self.market_id or "").strip()
        object.__setattr__(self, "event_id", normalized_event)
        object.__setattr__(self, "resolution_window", normalized_window)
        object.__setattr__(self, "correlation_key", normalized_correlation)
        if self.submit_order_type is not None and self.submit_order_type not in {
            "GTC",
            "GTD",
            "FOK",
            "FAK",
        }:
            raise ValueError(f"unsupported submit_order_type {self.submit_order_type!r}")
        decision_source_context = self.decision_source_context
        if decision_source_context is not None and not isinstance(
            decision_source_context, DecisionSourceContext
        ):
            if isinstance(decision_source_context, Mapping):
                decision_source_context = DecisionSourceContext.from_forecast_context(
                    decision_source_context
                )
            if not isinstance(decision_source_context, DecisionSourceContext):
                raise TypeError(
                    "ExecutionIntent.decision_source_context must be "
                    "DecisionSourceContext or forecast-context mapping"
                )
            object.__setattr__(
                self,
                "decision_source_context",
                decision_source_context,
            )


@dataclass(frozen=True)
class ExecutableCostBasis:
    """Executable held-token cost basis tied to a single CLOB snapshot.

    This is the corrected pricing boundary between market microstructure and
    strategy sizing. It carries executable cost facts only; it does not carry
    posterior belief, model probability, or a raw BinEdge.
    """

    selected_token_id: str
    selected_outcome_label: OutcomeLabel
    direction: ExecutionDirection
    order_policy: OrderPolicy
    requested_size_kind: OrderSizeKind
    requested_size_value: Decimal
    final_limit_price: Decimal
    expected_fill_price_before_fee: Decimal
    fee_adjusted_execution_price: Decimal
    worst_case_fee_rate: Decimal
    fee_source: str
    tick_size: Decimal
    min_order_size: Decimal
    tick_status: Literal["PASS", "FAIL"]
    min_order_status: Literal["PASS", "FAIL"]
    depth_status: str
    depth_proof_source: str
    quote_snapshot_id: str
    quote_snapshot_hash: str
    neg_risk: bool
    cost_basis_id: str = ""
    cost_basis_hash: str = ""
    pricing_semantics_version: CorrectedPricingSemanticsVersion = (
        CORRECTED_PRICING_SEMANTICS_VERSION
    )

    @classmethod
    def from_snapshot(
        cls,
        *,
        snapshot: "ExecutableMarketSnapshotV2",
        direction: ExecutionDirection,
        order_policy: OrderPolicy,
        requested_size_kind: OrderSizeKind,
        requested_size_value: Decimal,
        final_limit_price: Decimal,
        expected_fill_price_before_fee: Decimal,
        fee_adjusted_execution_price: Decimal | None = None,
        depth_status: str = "UNVERIFIED_DEPTH",
        depth_proof_source: str | None = None,
        tick_status: Literal["PASS", "FAIL"] = "PASS",
        min_order_status: Literal["PASS", "FAIL"] = "PASS",
        _allow_depth_pass: bool = False,
    ) -> "ExecutableCostBasis":
        from src.contracts.executable_market_snapshot_v2 import (
            fee_rate_fraction_from_details,
        )

        selected_token_id, outcome_label = _selected_token_for_direction(
            snapshot,
            direction,
        )
        if depth_status == "PASS" and not _allow_depth_pass:
            raise ValueError("PASS depth_status requires CLOB_SWEEP proof")
        if depth_proof_source is None:
            depth_proof_source = (
                "PASSIVE_LIMIT"
                if depth_status == "NOT_MARKETABLE_PASSIVE_LIMIT"
                else "UNVERIFIED"
            )
        fee_details = dict(snapshot.fee_details)
        fee_rate = _as_decimal(
            fee_rate_fraction_from_details(fee_details),
            "worst_case_fee_rate",
        )
        fee_source = str(
            fee_details.get("fee_rate_source_field")
            or fee_details.get("source")
            or "snapshot_fee_details"
        )
        expected_fill = _as_decimal(
            expected_fill_price_before_fee,
            "expected_fill_price_before_fee",
        )
        computed_fee_adjusted = _fee_adjusted_price(
            expected_fill_price_before_fee=expected_fill,
            fee_rate=fee_rate,
            direction=direction,
        )
        if fee_adjusted_execution_price is not None:
            provided_fee_adjusted = _as_decimal(
                fee_adjusted_execution_price,
                "fee_adjusted_execution_price",
            )
            if provided_fee_adjusted != computed_fee_adjusted:
                raise ValueError(
                    "fee_adjusted_execution_price does not match snapshot fee metadata"
                )
        else:
            provided_fee_adjusted = computed_fee_adjusted
        return cls(
            selected_token_id=selected_token_id,
            selected_outcome_label=outcome_label,
            direction=direction,
            order_policy=order_policy,
            requested_size_kind=requested_size_kind,
            requested_size_value=requested_size_value,
            final_limit_price=final_limit_price,
            expected_fill_price_before_fee=expected_fill,
            fee_adjusted_execution_price=provided_fee_adjusted,
            worst_case_fee_rate=fee_rate,
            fee_source=fee_source,
            tick_size=snapshot.min_tick_size,
            min_order_size=snapshot.min_order_size,
            tick_status=tick_status,
            min_order_status=min_order_status,
            depth_status=depth_status,
            depth_proof_source=depth_proof_source,
            quote_snapshot_id=snapshot.snapshot_id,
            quote_snapshot_hash=snapshot.executable_snapshot_hash,
            neg_risk=snapshot.neg_risk,
        )

    @classmethod
    def from_snapshot_sweep(
        cls,
        *,
        snapshot: "ExecutableMarketSnapshotV2",
        direction: ExecutionDirection,
        order_policy: OrderPolicy,
        requested_size_kind: OrderSizeKind,
        requested_size_value: Decimal,
        final_limit_price: Decimal,
        fee_adjusted_execution_price: Decimal | None = None,
        tick_status: Literal["PASS", "FAIL"] = "PASS",
        min_order_status: Literal["PASS", "FAIL"] = "PASS",
    ) -> "ExecutableCostBasis":
        sweep = simulate_clob_sweep(
            snapshot=snapshot,
            direction=direction,
            requested_size_kind=requested_size_kind,
            requested_size_value=requested_size_value,
            limit_price=final_limit_price,
        )
        if sweep.average_price is None:
            raise ValueError("CLOB sweep produced no executable fill")
        return cls.from_snapshot(
            snapshot=snapshot,
            direction=direction,
            order_policy=order_policy,
            requested_size_kind=requested_size_kind,
            requested_size_value=requested_size_value,
            final_limit_price=final_limit_price,
            expected_fill_price_before_fee=sweep.average_price,
            fee_adjusted_execution_price=fee_adjusted_execution_price,
            depth_status=sweep.depth_status,
            depth_proof_source="CLOB_SWEEP",
            tick_status=tick_status,
            min_order_status=min_order_status,
            _allow_depth_pass=True,
        )

    def __post_init__(self) -> None:
        decimal_fields = (
            "requested_size_value",
            "final_limit_price",
            "expected_fill_price_before_fee",
            "fee_adjusted_execution_price",
            "worst_case_fee_rate",
            "tick_size",
            "min_order_size",
        )
        for field_name in decimal_fields:
            object.__setattr__(
                self,
                field_name,
                _as_decimal(getattr(self, field_name), field_name),
            )
        if self.pricing_semantics_version != CORRECTED_PRICING_SEMANTICS_VERSION:
            raise ValueError(
                "ExecutableCostBasis only supports corrected_executable_cost_v1"
            )
        if self.order_policy not in _ALLOWED_ORDER_POLICIES:
            raise ValueError(f"unsupported order_policy {self.order_policy!r}")
        if not self.selected_token_id:
            raise ValueError("selected_token_id is required")
        if self.selected_outcome_label != _outcome_label_for_direction(self.direction):
            raise ValueError("selected_outcome_label does not match direction")
        if self.requested_size_kind not in {"shares", "notional_usd"}:
            raise ValueError(f"unsupported requested_size_kind {self.requested_size_kind!r}")
        if self.tick_status not in {"PASS", "FAIL"}:
            raise ValueError("tick_status must be PASS or FAIL")
        if self.min_order_status not in {"PASS", "FAIL"}:
            raise ValueError("min_order_status must be PASS or FAIL")
        if self.depth_status not in _ALLOWED_DEPTH_STATUSES:
            raise ValueError(f"unsupported depth_status {self.depth_status!r}")
        if self.depth_proof_source not in _ALLOWED_DEPTH_PROOF_SOURCES:
            raise ValueError(
                f"unsupported depth_proof_source {self.depth_proof_source!r}"
            )
        if self.depth_status == "PASS" and self.depth_proof_source != "CLOB_SWEEP":
            raise ValueError("PASS depth_status requires CLOB_SWEEP proof")
        if (
            self.depth_proof_source == "CLOB_SWEEP"
            and self.depth_status not in {"PASS", "DEPTH_INSUFFICIENT", "EMPTY_BOOK"}
        ):
            raise ValueError("CLOB_SWEEP proof source requires sweep depth status")
        if (
            self.depth_proof_source == "PASSIVE_LIMIT"
            and self.depth_status != "NOT_MARKETABLE_PASSIVE_LIMIT"
        ):
            raise ValueError(
                "PASSIVE_LIMIT proof source requires NOT_MARKETABLE_PASSIVE_LIMIT"
            )
        _assert_cost_basis_order_policy_coherent(
            order_policy=self.order_policy,
            depth_status=str(self.depth_status),
            depth_proof_source=str(self.depth_proof_source),
        )
        if not self.quote_snapshot_id or not self.quote_snapshot_hash:
            raise ValueError("snapshot lineage is required")
        if not _valid_payload_hash(self.quote_snapshot_hash):
            raise ValueError("quote_snapshot_hash must be a sha256 hex digest")
        _require_unit_interval_open(self.final_limit_price, "final_limit_price")
        _require_unit_interval_open(
            self.expected_fill_price_before_fee,
            "expected_fill_price_before_fee",
        )
        _require_unit_interval_open(
            self.fee_adjusted_execution_price,
            "fee_adjusted_execution_price",
        )
        _assert_fill_limit_coherent(
            direction=self.direction,
            expected_fill_price_before_fee=self.expected_fill_price_before_fee,
            final_limit_price=self.final_limit_price,
        )
        _require_unit_interval_closed(self.worst_case_fee_rate, "worst_case_fee_rate")
        expected_fee_adjusted = _fee_adjusted_price(
            expected_fill_price_before_fee=self.expected_fill_price_before_fee,
            fee_rate=self.worst_case_fee_rate,
            direction=self.direction,
        )
        if self.fee_adjusted_execution_price != expected_fee_adjusted:
            raise ValueError(
                "fee_adjusted_execution_price does not match expected fill and worst_case_fee_rate"
            )
        if self.tick_size <= Decimal("0"):
            raise ValueError("tick_size must be positive")
        if self.min_order_size <= Decimal("0"):
            raise ValueError("min_order_size must be positive")
        if self.requested_size_value <= Decimal("0"):
            raise ValueError("requested_size_value must be positive")
        computed_hash = _canonical_hash(self._identity_payload())
        if self.cost_basis_hash and self.cost_basis_hash != computed_hash:
            raise ValueError("cost_basis_hash does not match cost basis payload")
        object.__setattr__(self, "cost_basis_hash", computed_hash)
        computed_id = f"cost_basis:{computed_hash[:16]}"
        if self.cost_basis_id and self.cost_basis_id != computed_id:
            raise ValueError("cost_basis_id does not match cost basis payload")
        object.__setattr__(self, "cost_basis_id", computed_id)

    def _identity_payload(self) -> dict[str, str]:
        return {
            "selected_token_id": self.selected_token_id,
            "selected_outcome_label": self.selected_outcome_label,
            "direction": self.direction,
            "order_policy": self.order_policy,
            "requested_size_kind": self.requested_size_kind,
            "requested_size_value": _decimal_text(self.requested_size_value),
            "final_limit_price": _decimal_text(self.final_limit_price),
            "expected_fill_price_before_fee": _decimal_text(
                self.expected_fill_price_before_fee
            ),
            "fee_adjusted_execution_price": _decimal_text(
                self.fee_adjusted_execution_price
            ),
            "worst_case_fee_rate": _decimal_text(self.worst_case_fee_rate),
            "fee_source": self.fee_source,
            "tick_size": _decimal_text(self.tick_size),
            "min_order_size": _decimal_text(self.min_order_size),
            "tick_status": self.tick_status,
            "min_order_status": self.min_order_status,
            "depth_status": str(self.depth_status),
            "depth_proof_source": str(self.depth_proof_source),
            "quote_snapshot_id": self.quote_snapshot_id,
            "quote_snapshot_hash": self.quote_snapshot_hash,
            "neg_risk": str(bool(self.neg_risk)),
            "pricing_semantics_version": self.pricing_semantics_version,
        }

    def assert_live_safe(self) -> None:
        """Fail closed unless this cost basis can authorize corrected live sizing."""

        if self.tick_status != "PASS":
            raise ValueError("tick validation failed")
        if self.min_order_status != "PASS":
            raise ValueError("min-order validation failed")
        if self.depth_status != "PASS":
            raise ValueError(f"depth validation failed: {self.depth_status}")
        _assert_tick_aligned(self.final_limit_price, self.tick_size)
        _assert_min_order_satisfied(
            size_kind=self.requested_size_kind,
            size_value=self.requested_size_value,
            final_limit_price=self.final_limit_price,
            min_order_size=self.min_order_size,
        )

    def assert_submit_safe(self) -> None:
        """Fail closed unless this cost basis can authorize a live limit submit."""

        if self.tick_status != "PASS":
            raise ValueError("tick validation failed")
        if self.min_order_status != "PASS":
            raise ValueError("min-order validation failed")
        if self.depth_status != "PASS":
            raise ValueError(f"depth validation failed: {self.depth_status}")
        if self.depth_proof_source != "CLOB_SWEEP":
            raise ValueError("PASS depth_status requires CLOB_SWEEP proof")
        _assert_tick_aligned(self.final_limit_price, self.tick_size)
        _assert_min_order_satisfied(
            size_kind=self.requested_size_kind,
            size_value=self.requested_size_value,
            final_limit_price=self.final_limit_price,
            min_order_size=self.min_order_size,
        )


@dataclass(frozen=True)
class ExecutableTradeHypothesis:
    """FDR identity for a concrete executable trade candidate."""

    event_id: str
    bin_id: str
    direction: EntryDirection
    selected_token_id: str
    payoff_probability: Decimal
    posterior_distribution_id: str
    market_prior_id: str | None
    executable_snapshot_id: str
    executable_snapshot_hash: str
    executable_cost_basis_id: str
    executable_cost_basis_hash: str
    order_policy: OrderPolicy
    fdr_family_id: str
    fdr_hypothesis_id: str
    pricing_semantics_version: CorrectedPricingSemanticsVersion = (
        CORRECTED_PRICING_SEMANTICS_VERSION
    )

    @classmethod
    def from_cost_basis(
        cls,
        *,
        event_id: str,
        bin_id: str,
        payoff_probability: Decimal,
        posterior_distribution_id: str,
        market_prior_id: str | None,
        fdr_family_id: str,
        cost_basis: ExecutableCostBasis,
    ) -> "ExecutableTradeHypothesis":
        direction = cost_basis.direction
        if direction not in {"buy_yes", "buy_no"}:
            raise ValueError("entry executable hypotheses require buy_yes or buy_no")
        payoff = _as_decimal(payoff_probability, "payoff_probability")
        if not str(posterior_distribution_id or "").strip():
            raise ValueError("posterior_distribution_id is required")
        expected_hypothesis_id = cls.expected_hypothesis_id(
            event_id=event_id,
            bin_id=bin_id,
            direction=direction,
            selected_token_id=cost_basis.selected_token_id,
            payoff_probability=payoff,
            posterior_distribution_id=posterior_distribution_id,
            market_prior_id=market_prior_id,
            executable_snapshot_id=cost_basis.quote_snapshot_id,
            executable_snapshot_hash=cost_basis.quote_snapshot_hash,
            executable_cost_basis_id=cost_basis.cost_basis_id,
            executable_cost_basis_hash=cost_basis.cost_basis_hash,
            order_policy=cost_basis.order_policy,
            fdr_family_id=fdr_family_id,
        )
        return cls(
            event_id=event_id,
            bin_id=bin_id,
            direction=direction,
            selected_token_id=cost_basis.selected_token_id,
            payoff_probability=payoff,
            posterior_distribution_id=posterior_distribution_id,
            market_prior_id=market_prior_id,
            executable_snapshot_id=cost_basis.quote_snapshot_id,
            executable_snapshot_hash=cost_basis.quote_snapshot_hash,
            executable_cost_basis_id=cost_basis.cost_basis_id,
            executable_cost_basis_hash=cost_basis.cost_basis_hash,
            order_policy=cost_basis.order_policy,
            fdr_family_id=fdr_family_id,
            fdr_hypothesis_id=expected_hypothesis_id,
        )

    @staticmethod
    def expected_hypothesis_id(
        *,
        event_id: str,
        bin_id: str,
        direction: EntryDirection,
        selected_token_id: str,
        payoff_probability: Decimal,
        posterior_distribution_id: str,
        market_prior_id: str | None,
        executable_snapshot_id: str,
        executable_snapshot_hash: str,
        executable_cost_basis_id: str,
        executable_cost_basis_hash: str,
        order_policy: OrderPolicy,
        fdr_family_id: str,
    ) -> str:
        hypothesis_seed = _canonical_hash(
            {
                "event_id": event_id,
                "bin_id": bin_id,
                "direction": direction,
                "selected_token_id": selected_token_id,
                "payoff_probability": _decimal_text(payoff_probability),
                "posterior_distribution_id": posterior_distribution_id,
                "market_prior_id": market_prior_id or "NO_MARKET_PRIOR",
                "snapshot_id": executable_snapshot_id,
                "snapshot_hash": executable_snapshot_hash,
                "cost_basis_id": executable_cost_basis_id,
                "cost_basis_hash": executable_cost_basis_hash,
                "order_policy": order_policy,
                "fdr_family_id": fdr_family_id,
            }
        )
        return f"hypothesis:{hypothesis_seed[:16]}"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payoff_probability",
            _as_decimal(self.payoff_probability, "payoff_probability"),
        )
        _require_unit_interval_closed(self.payoff_probability, "payoff_probability")
        if self.pricing_semantics_version != CORRECTED_PRICING_SEMANTICS_VERSION:
            raise ValueError(
                "ExecutableTradeHypothesis only supports corrected_executable_cost_v1"
            )
        if self.direction not in {"buy_yes", "buy_no"}:
            raise ValueError("entry executable hypotheses require buy_yes or buy_no")
        if self.order_policy not in _ALLOWED_ORDER_POLICIES:
            raise ValueError(f"unsupported order_policy {self.order_policy!r}")
        self.assert_identity_complete()
        expected_hypothesis_id = self.expected_hypothesis_id(
            event_id=self.event_id,
            bin_id=self.bin_id,
            direction=self.direction,
            selected_token_id=self.selected_token_id,
            payoff_probability=self.payoff_probability,
            posterior_distribution_id=self.posterior_distribution_id,
            market_prior_id=self.market_prior_id,
            executable_snapshot_id=self.executable_snapshot_id,
            executable_snapshot_hash=self.executable_snapshot_hash,
            executable_cost_basis_id=self.executable_cost_basis_id,
            executable_cost_basis_hash=self.executable_cost_basis_hash,
            order_policy=self.order_policy,
            fdr_family_id=self.fdr_family_id,
        )
        if self.fdr_hypothesis_id != expected_hypothesis_id:
            raise ValueError("fdr_hypothesis_id does not match executable hypothesis payload")

    @property
    def identity_tuple(self) -> tuple[str, ...]:
        return (
            self.event_id,
            self.bin_id,
            self.direction,
            self.selected_token_id,
            _decimal_text(self.payoff_probability),
            self.posterior_distribution_id,
            self.market_prior_id or "NO_MARKET_PRIOR",
            self.executable_snapshot_id,
            self.executable_snapshot_hash,
            self.executable_cost_basis_id,
            self.executable_cost_basis_hash,
            self.order_policy,
            self.fdr_family_id,
            self.fdr_hypothesis_id,
        )

    def assert_identity_complete(self) -> None:
        for part in self.identity_tuple:
            if not part:
                raise ValueError("executable hypothesis identity is incomplete")
        if not str(self.posterior_distribution_id or "").strip():
            raise ValueError("posterior_distribution_id is required")
        if not _valid_payload_hash(self.executable_snapshot_hash):
            raise ValueError("executable_snapshot_hash must be a sha256 hex digest")
        if not _valid_payload_hash(self.executable_cost_basis_hash):
            raise ValueError("executable_cost_basis_hash must be a sha256 hex digest")
        expected_cost_basis_id = f"cost_basis:{self.executable_cost_basis_hash[:16]}"
        if self.executable_cost_basis_id != expected_cost_basis_id:
            raise ValueError("executable_cost_basis_id does not match executable_cost_basis_hash")

    def assert_matches_cost_basis(self, cost_basis: ExecutableCostBasis) -> None:
        checks = {
            "selected_token_id": (
                self.selected_token_id,
                cost_basis.selected_token_id,
            ),
            "executable_snapshot_id": (
                self.executable_snapshot_id,
                cost_basis.quote_snapshot_id,
            ),
            "executable_snapshot_hash": (
                self.executable_snapshot_hash,
                cost_basis.quote_snapshot_hash,
            ),
            "executable_cost_basis_id": (
                self.executable_cost_basis_id,
                cost_basis.cost_basis_id,
            ),
            "executable_cost_basis_hash": (
                self.executable_cost_basis_hash,
                cost_basis.cost_basis_hash,
            ),
            "order_policy": (self.order_policy, cost_basis.order_policy),
        }
        for field_name, (left, right) in checks.items():
            if left != right:
                raise ValueError(f"{field_name} does not match cost basis")


@dataclass(frozen=True)
class FinalExecutionIntent:
    """Immutable corrected execution intent.

    Corrected submit paths consume this contract, not posterior/VWMP/BinEdge
    inputs. Legacy ExecutionIntent remains for existing non-corrected wiring.
    """

    hypothesis_id: str
    selected_token_id: str
    direction: ExecutionDirection
    size_kind: OrderSizeKind
    size_value: Decimal
    submitted_shares: Decimal
    final_limit_price: Decimal
    expected_fill_price_before_fee: Decimal
    fee_adjusted_execution_price: Decimal
    order_policy: OrderPolicy
    order_type: OrderType
    post_only: bool
    cancel_after: datetime | None
    snapshot_id: str
    snapshot_hash: str
    cost_basis_id: str
    cost_basis_hash: str
    max_slippage_bps: Decimal
    tick_size: Decimal
    min_order_size: Decimal
    fee_rate: Decimal
    neg_risk: bool
    event_id: str = ""
    resolution_window: str = "default"
    correlation_key: str = ""
    decision_source_context: DecisionSourceContext | None = None
    pricing_semantics_version: CorrectedPricingSemanticsVersion = (
        CORRECTED_PRICING_SEMANTICS_VERSION
    )

    @classmethod
    def from_hypothesis_and_cost_basis(
        cls,
        *,
        hypothesis: ExecutableTradeHypothesis,
        cost_basis: ExecutableCostBasis,
        order_type: OrderType = "GTC",
        post_only: bool = False,
        cancel_after: datetime | None = None,
        max_slippage_bps: Decimal = Decimal("200"),
        event_id: str = "",
        resolution_window: str = "default",
        correlation_key: str = "",
        decision_source_context: DecisionSourceContext | None = None,
    ) -> "FinalExecutionIntent":
        hypothesis.assert_matches_cost_basis(cost_basis)
        cost_basis.assert_submit_safe()
        normalized_event_id = str(event_id or hypothesis.event_id or "").strip()
        hypothesis_event_id = str(hypothesis.event_id or "").strip()
        if normalized_event_id and hypothesis_event_id and normalized_event_id != hypothesis_event_id:
            raise ValueError(
                "FinalExecutionIntent event_id does not match executable hypothesis"
            )
        return cls(
            hypothesis_id=hypothesis.fdr_hypothesis_id,
            selected_token_id=cost_basis.selected_token_id,
            direction=cost_basis.direction,
            size_kind=cost_basis.requested_size_kind,
            size_value=cost_basis.requested_size_value,
            submitted_shares=_submitted_shares_from_cost_basis(cost_basis),
            final_limit_price=cost_basis.final_limit_price,
            expected_fill_price_before_fee=cost_basis.expected_fill_price_before_fee,
            fee_adjusted_execution_price=cost_basis.fee_adjusted_execution_price,
            order_policy=cost_basis.order_policy,
            order_type=order_type,
            post_only=post_only,
            cancel_after=cancel_after,
            snapshot_id=cost_basis.quote_snapshot_id,
            snapshot_hash=cost_basis.quote_snapshot_hash,
            cost_basis_id=cost_basis.cost_basis_id,
            cost_basis_hash=cost_basis.cost_basis_hash,
            max_slippage_bps=max_slippage_bps,
            tick_size=cost_basis.tick_size,
            min_order_size=cost_basis.min_order_size,
            fee_rate=cost_basis.worst_case_fee_rate,
            neg_risk=cost_basis.neg_risk,
            event_id=normalized_event_id,
            resolution_window=resolution_window,
            correlation_key=correlation_key,
            decision_source_context=decision_source_context,
        )

    def __post_init__(self) -> None:
        for field_name in (
            "size_value",
            "submitted_shares",
            "final_limit_price",
            "expected_fill_price_before_fee",
            "fee_adjusted_execution_price",
            "max_slippage_bps",
            "tick_size",
            "min_order_size",
            "fee_rate",
        ):
            object.__setattr__(
                self,
                field_name,
                _as_decimal(getattr(self, field_name), field_name),
            )
        if self.pricing_semantics_version != CORRECTED_PRICING_SEMANTICS_VERSION:
            raise ValueError(
                "FinalExecutionIntent only supports corrected_executable_cost_v1"
            )
        normalized_event = str(self.event_id or "").strip()
        normalized_window = str(self.resolution_window or "default").strip() or "default"
        normalized_correlation = (
            str(self.correlation_key or normalized_event or self.hypothesis_id or "")
            .strip()
        )
        object.__setattr__(self, "event_id", normalized_event)
        object.__setattr__(self, "resolution_window", normalized_window)
        object.__setattr__(self, "correlation_key", normalized_correlation)
        if self.cancel_after is not None and not isinstance(self.cancel_after, datetime):
            raise TypeError("cancel_after must be datetime or None")
        self.assert_submit_ready()

    def assert_no_recompute_inputs(self) -> None:
        """Marker: corrected execution should not require posterior, VWMP, or BinEdge."""

    def assert_submit_ready(self) -> None:
        missing = [
            name
            for name in (
                "hypothesis_id",
                "selected_token_id",
                "snapshot_id",
                "snapshot_hash",
                "cost_basis_id",
                "cost_basis_hash",
            )
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"FinalExecutionIntent missing fields: {missing}")
        if self.direction not in {"buy_yes", "buy_no", "sell_yes", "sell_no"}:
            raise ValueError(f"unsupported direction {self.direction!r}")
        if self.size_kind not in {"shares", "notional_usd"}:
            raise ValueError(f"unsupported size_kind {self.size_kind!r}")
        if self.order_type not in {"GTC", "GTD", "FOK", "FAK"}:
            raise ValueError(f"unsupported order_type {self.order_type!r}")
        if self.post_only and self.order_type in {"FOK", "FAK"}:
            raise ValueError("post_only cannot be combined with FOK/FAK")
        _assert_order_policy_coherent(
            order_policy=self.order_policy,
            order_type=self.order_type,
            post_only=self.post_only,
        )
        if not _valid_payload_hash(self.snapshot_hash):
            raise ValueError("snapshot_hash must be a sha256 hex digest")
        if not _valid_payload_hash(self.cost_basis_hash):
            raise ValueError("cost_basis_hash must be a sha256 hex digest")
        expected_cost_basis_id = f"cost_basis:{self.cost_basis_hash[:16]}"
        if self.cost_basis_id != expected_cost_basis_id:
            raise ValueError("cost_basis_id does not match cost_basis_hash")
        _require_unit_interval_open(self.final_limit_price, "final_limit_price")
        _require_unit_interval_open(
            self.fee_adjusted_execution_price,
            "fee_adjusted_execution_price",
        )
        expected_fee_adjusted = _fee_adjusted_price(
            expected_fill_price_before_fee=self.expected_fill_price_before_fee,
            fee_rate=self.fee_rate,
            direction=self.direction,
        )
        if self.fee_adjusted_execution_price != expected_fee_adjusted:
            raise ValueError(
                "fee_adjusted_execution_price does not match expected fill and fee_rate"
            )
        _assert_fill_limit_coherent(
            direction=self.direction,
            expected_fill_price_before_fee=self.expected_fill_price_before_fee,
            final_limit_price=self.final_limit_price,
        )
        _require_unit_interval_closed(self.fee_rate, "fee_rate")
        if self.max_slippage_bps < Decimal("0"):
            raise ValueError("max_slippage_bps must be non-negative")
        if self.submitted_shares <= Decimal("0"):
            raise ValueError("submitted_shares must be positive")
        adverse_slippage = _adverse_slippage_bps(
            direction=self.direction,
            reference_price=self.expected_fill_price_before_fee,
            final_limit_price=self.final_limit_price,
        )
        if adverse_slippage > self.max_slippage_bps:
            raise ValueError(
                "MAX_SLIPPAGE_EXCEEDED: "
                f"adverse_slippage_bps={adverse_slippage} "
                f"max_slippage_bps={self.max_slippage_bps}"
            )
        if (
            self.order_policy == "limit_may_take_conservative"
            and self.order_type not in {"FOK", "FAK"}
        ):
            raise ValueError(
                "marketable final execution intent requires FOK/FAK order_type"
            )
        if self.size_kind == "shares" and self.submitted_shares != self.size_value:
            raise ValueError("submitted_shares must match share-sized final intent")
        if self.size_kind == "notional_usd":
            expected_submitted_shares = _quantize_submit_shares(
                self.direction,
                self.size_value / self.expected_fill_price_before_fee,
            )
            if self.submitted_shares != expected_submitted_shares:
                raise ValueError(
                    "submitted_shares must match expected-fill notional sizing"
                )
        _assert_tick_aligned(self.final_limit_price, self.tick_size)
        _assert_min_order_satisfied(
            size_kind=self.size_kind,
            size_value=self.size_value,
            final_limit_price=self.final_limit_price,
            min_order_size=self.min_order_size,
        )
        if self.submitted_shares < self.min_order_size:
            raise ValueError(
                f"submitted_shares {self.submitted_shares} is below "
                f"min_order_size {self.min_order_size}"
            )
