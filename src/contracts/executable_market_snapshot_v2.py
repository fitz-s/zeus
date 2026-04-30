# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/U1.yaml
"""Executable CLOB market snapshot contract.

U1 makes executable market facts immutable, externally reconcilable, and
mandatory for venue-command persistence.  This contract is deliberately small:
it validates the snapshot shape and exposes the fail-closed checks that the
single command insertion seam calls before any venue side effect can happen.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Literal, Mapping, Optional


AuthorityTier = Literal["GAMMA", "DATA", "CLOB", "CHAIN"]
OutcomeLabel = Literal["YES", "NO"]

FRESHNESS_WINDOW_DEFAULT = timedelta(seconds=30)


class MarketSnapshotError(ValueError):
    """Base class for executable market snapshot gate failures."""


class StaleMarketSnapshotError(MarketSnapshotError):
    """Raised when a required executable market snapshot is missing or stale."""


class MarketSnapshotMismatchError(MarketSnapshotError):
    """Raised when a command intent does not match the executable snapshot."""


class MarketNotTradableError(MarketSnapshotError):
    """Raised when snapshot tradability flags forbid submission."""


FEE_RATE_BPS_FIELDS = ("fee_rate_bps", "feeRateBps", "base_fee", "baseFee", "bps")
FEE_RATE_FRACTION_FIELDS = (
    "fee_rate_fraction",
    "feeRate",
    "fee_rate",
    "takerFeeRate",
    "taker_fee_rate",
)


@dataclass(frozen=True)
class ExecutableMarketSnapshotV2:
    """Immutable executable market truth captured before order submission."""

    snapshot_id: str
    gamma_market_id: str
    event_id: str
    event_slug: str
    condition_id: str
    question_id: str
    yes_token_id: str
    no_token_id: str
    selected_outcome_token_id: Optional[str]
    outcome_label: Optional[OutcomeLabel]
    enable_orderbook: bool
    active: bool
    closed: bool
    accepting_orders: Optional[bool]
    market_start_at: Optional[datetime]
    market_end_at: Optional[datetime]
    market_close_at: Optional[datetime]
    sports_start_at: Optional[datetime]
    min_tick_size: Decimal
    min_order_size: Decimal
    fee_details: dict[str, Any]
    token_map_raw: dict[str, Any]
    rfqe: Optional[bool]
    neg_risk: bool
    orderbook_top_bid: Decimal
    orderbook_top_ask: Decimal
    orderbook_depth_jsonb: str
    raw_gamma_payload_hash: str
    raw_clob_market_info_hash: str
    raw_orderbook_hash: str
    authority_tier: AuthorityTier
    captured_at: datetime
    freshness_deadline: datetime

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id is required")
        if self.outcome_label not in {"YES", "NO", None}:
            raise ValueError(f"outcome_label must be YES, NO, or None; got {self.outcome_label!r}")
        if self.authority_tier not in {"GAMMA", "DATA", "CLOB", "CHAIN"}:
            raise ValueError(
                "authority_tier must be one of GAMMA, DATA, CLOB, CHAIN; "
                f"got {self.authority_tier!r}"
            )
        if self.accepting_orders not in {True, False, None}:
            raise ValueError("accepting_orders must be bool or None")
        if self.rfqe not in {True, False, None}:
            raise ValueError("rfqe must be bool or None")
        if self.min_tick_size <= 0:
            raise ValueError("min_tick_size must be positive")
        if self.min_order_size <= 0:
            raise ValueError("min_order_size must be positive")
        for name in (
            "raw_gamma_payload_hash",
            "raw_clob_market_info_hash",
            "raw_orderbook_hash",
        ):
            value = getattr(self, name)
            if len(value) != 64:
                raise ValueError(f"{name} must be a sha256 hex digest")
        if self.selected_outcome_token_id:
            valid_tokens = {self.yes_token_id, self.no_token_id}
            if self.selected_outcome_token_id not in valid_tokens:
                raise ValueError("selected_outcome_token_id must match yes_token_id or no_token_id")
            if self.outcome_label == "YES" and self.selected_outcome_token_id != self.yes_token_id:
                raise ValueError("outcome_label YES must select yes_token_id")
            if self.outcome_label == "NO" and self.selected_outcome_token_id != self.no_token_id:
                raise ValueError("outcome_label NO must select no_token_id")
        captured = _as_utc(self.captured_at, field_name="captured_at")
        deadline = _as_utc(self.freshness_deadline, field_name="freshness_deadline")
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "freshness_deadline", deadline)
        for name in ("market_start_at", "market_end_at", "market_close_at", "sports_start_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _as_utc(value, field_name=name))
        if deadline < captured:
            raise ValueError("freshness_deadline must be >= captured_at")

    @property
    def is_fresh(self) -> bool:
        """Compatibility property for the V2 adapter's existing snapshot seam."""

        return is_fresh(self, datetime.now(timezone.utc))

    @property
    def freshness_window_seconds(self) -> float:
        """Compatibility field for adapter freshness checks."""

        return (self.freshness_deadline - self.captured_at).total_seconds()

    @property
    def tick_size(self) -> Decimal:
        """Compatibility alias used by VenueSubmissionEnvelope creation."""

        return self.min_tick_size

    def with_selected_outcome(
        self,
        *,
        selected_outcome_token_id: str,
        outcome_label: OutcomeLabel,
    ) -> "ExecutableMarketSnapshotV2":
        """Return a copy with post-decision token selection populated."""

        return replace(
            self,
            selected_outcome_token_id=selected_outcome_token_id,
            outcome_label=outcome_label,
        )


def is_fresh(snapshot: ExecutableMarketSnapshotV2, now: datetime) -> bool:
    """Return whether ``snapshot`` is still inside its executable window."""

    return _as_utc(now, field_name="now") <= snapshot.freshness_deadline


def assert_snapshot_executable(
    snapshot: Optional[ExecutableMarketSnapshotV2],
    *,
    token_id: str,
    price: Any,
    size: Any,
    now: Optional[datetime] = None,
    expected_min_tick_size: Any = None,
    expected_min_order_size: Any = None,
    expected_neg_risk: Optional[bool] = None,
) -> None:
    """Fail closed unless ``snapshot`` authorizes this command shape."""

    if snapshot is None:
        raise StaleMarketSnapshotError("venue command requires executable market snapshot_id")

    checked_at = now or datetime.now(timezone.utc)
    if not is_fresh(snapshot, checked_at):
        raise StaleMarketSnapshotError(
            f"ExecutableMarketSnapshotV2 {snapshot.snapshot_id} is stale at {_as_utc(checked_at, field_name='now').isoformat()}"
        )
    if not snapshot.enable_orderbook:
        raise MarketNotTradableError("snapshot enable_orderbook=false blocks submit")
    if not snapshot.active:
        raise MarketNotTradableError("snapshot active=false blocks submit")
    if snapshot.closed:
        raise MarketNotTradableError("snapshot closed=true blocks submit")
    if snapshot.accepting_orders is False:
        raise MarketNotTradableError("snapshot accepting_orders=false blocks submit")

    token = str(token_id or "")
    if not token:
        raise MarketSnapshotMismatchError("token_id is required for snapshot validation")
    valid_tokens = {snapshot.yes_token_id, snapshot.no_token_id}
    if token not in valid_tokens:
        raise MarketSnapshotMismatchError(
            f"token_id {token!r} is not in executable snapshot token map"
        )
    if snapshot.selected_outcome_token_id and token != snapshot.selected_outcome_token_id:
        raise MarketSnapshotMismatchError(
            "token_id does not match selected_outcome_token_id from executable snapshot"
        )

    if expected_min_tick_size is not None:
        expected_tick = _as_decimal(expected_min_tick_size, "expected_min_tick_size")
        if expected_tick != snapshot.min_tick_size:
            raise MarketSnapshotMismatchError(
                f"intent min_tick_size {expected_tick} != snapshot min_tick_size {snapshot.min_tick_size}"
            )
    if expected_min_order_size is not None:
        expected_min_size = _as_decimal(expected_min_order_size, "expected_min_order_size")
        if expected_min_size != snapshot.min_order_size:
            raise MarketSnapshotMismatchError(
                f"intent min_order_size {expected_min_size} != snapshot min_order_size {snapshot.min_order_size}"
            )
    if expected_neg_risk is not None and bool(expected_neg_risk) != snapshot.neg_risk:
        raise MarketSnapshotMismatchError(
            f"intent neg_risk {bool(expected_neg_risk)} != snapshot neg_risk {snapshot.neg_risk}"
        )

    submitted_price = _as_decimal(price, "price")
    if submitted_price <= 0 or submitted_price >= 1:
        raise MarketSnapshotMismatchError("price must be inside (0, 1)")
    if submitted_price % snapshot.min_tick_size != 0:
        raise MarketSnapshotMismatchError(
            f"price {submitted_price} is not aligned to snapshot min_tick_size {snapshot.min_tick_size}"
        )

    submitted_size = _as_decimal(size, "size")
    if submitted_size < snapshot.min_order_size:
        raise MarketSnapshotMismatchError(
            f"size {submitted_size} is below snapshot min_order_size {snapshot.min_order_size}"
        )


def canonicalize_fee_details(
    fee_details: Mapping[str, Any],
    *,
    source: str | None = None,
    token_id: str | None = None,
) -> dict[str, Any]:
    """Return fee metadata with both fraction and bps units made explicit.

    Polymarket exposes two fee-rate shapes in current docs/API surfaces:
    ``/fee-rate`` returns ``base_fee`` in basis points, while market fee
    schedules use a fraction coefficient consumed by ``feeRate * p * (1-p)``.
    Snapshot/envelope consumers must not infer units from field names later.
    """

    if not isinstance(fee_details, Mapping):
        raise MarketSnapshotMismatchError("fee_details must be a mapping")

    canonical = dict(fee_details)
    if source:
        _assert_expected_fee_metadata(canonical, "source", source)
        canonical.setdefault("source", source)
    if token_id:
        _assert_expected_fee_metadata(canonical, "token_id", token_id)
        canonical.setdefault("token_id", token_id)

    if canonical.get("feesEnabled") is False:
        return _finalize_fee_details(
            canonical,
            fee_rate_fraction=0.0,
            fee_rate_bps=0.0,
            source_field="feesEnabled",
            raw_unit="disabled",
        )

    fraction_value, fraction_field = _first_fee_value(canonical, FEE_RATE_FRACTION_FIELDS)
    bps_value, bps_field = _first_fee_value(canonical, FEE_RATE_BPS_FIELDS)

    fraction: float | None = None
    bps: float | None = None
    source_field: str | None = None
    raw_unit: str | None = None

    if fraction_field is not None:
        fraction = _validate_fee_rate_fraction(fraction_value, fraction_field)
        bps = fraction * 10_000.0
        source_field = fraction_field
        raw_unit = "fraction"
    if bps_field is not None:
        bps_from_field = _validate_fee_rate_bps(bps_value, bps_field)
        fraction_from_bps = bps_from_field / 10_000.0
        if fraction is not None and not math.isclose(fraction, fraction_from_bps, rel_tol=0.0, abs_tol=1e-12):
            raise MarketSnapshotMismatchError(
                "fee_details contains inconsistent fee-rate units: "
                f"{fraction_field}={fraction_value!r} vs {bps_field}={bps_value!r}"
            )
        fraction = fraction_from_bps
        bps = bps_from_field
        source_field = bps_field
        raw_unit = "bps"

    if fraction is None or bps is None or source_field is None or raw_unit is None:
        raise MarketSnapshotMismatchError(
            "fee_details missing fee_rate_fraction/feeRate or fee_rate_bps/base_fee/bps"
        )

    return _finalize_fee_details(
        canonical,
        fee_rate_fraction=fraction,
        fee_rate_bps=bps,
        source_field=source_field,
        raw_unit=raw_unit,
    )


def canonicalize_legacy_fee_rate_value(
    value: Any,
    *,
    source: str,
    token_id: str,
) -> dict[str, Any]:
    """Canonicalize legacy ``get_fee_rate`` values and mark unit inference."""

    numeric = _as_fee_float(value, "legacy_get_fee_rate")
    if numeric > 1.0:
        details = canonicalize_fee_details(
            {
                "source": source,
                "token_id": token_id,
                "fee_rate_bps": numeric,
                "fee_rate_unit_inferred": "legacy_get_fee_rate_gt_1_bps",
            }
        )
    else:
        details = canonicalize_fee_details(
            {
                "source": source,
                "token_id": token_id,
                "fee_rate_fraction": numeric,
                "fee_rate_unit_inferred": "legacy_get_fee_rate_lte_1_fraction",
            }
        )
    return details


def fee_rate_fraction_from_details(fee_details: Mapping[str, Any]) -> float:
    """Extract the canonical fraction coefficient for fee math."""

    return _validate_fee_rate_fraction(
        canonicalize_fee_details(fee_details)["fee_rate_fraction"],
        "fee_rate_fraction",
    )


def _first_fee_value(details: Mapping[str, Any], fields: tuple[str, ...]) -> tuple[Any, str | None]:
    for field in fields:
        value = details.get(field)
        if value is not None:
            return value, field
    return None, None


def _assert_expected_fee_metadata(details: Mapping[str, Any], field_name: str, expected: str) -> None:
    actual = details.get(field_name)
    if actual is None or str(actual).strip() == "":
        return
    if str(actual) != str(expected):
        raise MarketSnapshotMismatchError(
            f"fee_details {field_name} {actual!r} does not match expected {expected!r}"
        )


def _finalize_fee_details(
    details: dict[str, Any],
    *,
    fee_rate_fraction: float,
    fee_rate_bps: float,
    source_field: str,
    raw_unit: str,
) -> dict[str, Any]:
    details["fee_rate_fraction"] = fee_rate_fraction
    details["fee_rate_bps"] = fee_rate_bps
    details["fee_rate_source_field"] = details.get("fee_rate_source_field") or source_field
    details["fee_rate_raw_unit"] = details.get("fee_rate_raw_unit") or raw_unit
    return details


def _validate_fee_rate_fraction(value: Any, field_name: str) -> float:
    rate = _as_fee_float(value, field_name)
    if rate < 0.0 or rate > 1.0:
        raise MarketSnapshotMismatchError(f"{field_name} must be in [0, 1], got {rate}")
    return rate


def _validate_fee_rate_bps(value: Any, field_name: str) -> float:
    bps = _as_fee_float(value, field_name)
    if bps < 0.0 or bps > 10_000.0:
        raise MarketSnapshotMismatchError(f"{field_name} must be in [0, 10000], got {bps}")
    return bps


def _as_fee_float(value: Any, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketSnapshotMismatchError(f"{field_name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise MarketSnapshotMismatchError(f"{field_name} must be finite")
    return numeric


def _as_decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketSnapshotMismatchError(f"{field_name} must be decimal-compatible") from exc


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
