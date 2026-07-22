"""Independent current family scope for the global single-order auction.

The reactor queue is a scheduling surface, not the feasible set.  This module
enumerates every current posterior-ready, phase-admissible family directly from
the forecast trigger's read-only scan and binds that scope to the full
probability/token witnesses later prepared for selection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from functools import cached_property
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.executable_market_snapshot import (
    fee_details_from_gamma_fee_schedule,
    fee_rate_fraction_from_details,
)
from src.contracts.fee_authority import resolve_taker_fee_fraction
from src.events.candidate_binding import weather_family_id
from src.events.event_writer import EventWriter
from src.events.opportunity_event import OpportunityEvent
from src.events.triggers.forecast_snapshot_ready import (
    ForecastSnapshotReadyTrigger,
    executable_forecast_live_eligible_reader,
)
from src.solve.solver import (
    actionable_family_payoff_bindings,
    CurrentExecutionAuthority,
    ExecutableSellCurve,
    FamilyPayoffWitness,
    GlobalAuctionUniverseWitness,
    OutcomeTokenBinding,
    PortfolioWealthWitness,
    executable_curve_identity,
    global_auction_universe_identity,
    portfolio_wealth_identity,
    rebind_family_payoff_witness,
)
@dataclass(frozen=True, init=False)
class CurrentGlobalAuctionScope:
    """Complete probability-ready family set at one decision instant."""

    events_by_family: tuple[tuple[str, OpportunityEvent], ...]
    family_resolution_at_utc: tuple[tuple[str, datetime], ...]
    scope_identity: str
    captured_at_utc: datetime

    def __init__(
        self,
        *,
        events: Sequence[OpportunityEvent],
        captured_at_utc: datetime,
    ) -> None:
        if captured_at_utc.tzinfo is None:
            raise ValueError("current global auction scope is incomplete or ambiguous")
        rows, resolutions, identity = _current_global_scope_parts(events)
        object.__setattr__(self, "events_by_family", rows)
        object.__setattr__(self, "family_resolution_at_utc", resolutions)
        object.__setattr__(self, "scope_identity", identity)
        object.__setattr__(
            self,
            "captured_at_utc",
            captured_at_utc,
        )

    @property
    def family_keys(self) -> tuple[str, ...]:
        return tuple(family_key for family_key, _ in self.events_by_family)

    @property
    def events(self) -> tuple[OpportunityEvent, ...]:
        return tuple(event for _, event in self.events_by_family)

    @property
    def resolution_at_by_family(self) -> Mapping[str, datetime]:
        return dict(self.family_resolution_at_utc)


@dataclass(frozen=True)
class CurrentGlobalBookAsset:
    """One current side-native book in a complete venue epoch.

    ``curve`` owns executable BUY asks. ``bid_levels`` preserves the same
    immutable capture's native bids so downstream maker/taker policy does not
    reinterpret an ask-only projection as a one-sided venue book.
    """

    family_key: str
    bin_id: str
    condition_id: str
    gamma_market_id: str
    market_event_id: str
    side: str
    token_id: str
    curve: ExecutableCostCurve
    captured_at_utc: datetime
    bid_levels: tuple[BookLevel, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.side not in {"YES", "NO"}
            or not all(
                str(value).strip()
                for value in (
                    self.family_key,
                    self.bin_id,
                    self.condition_id,
                    self.gamma_market_id,
                    self.market_event_id,
                    self.token_id,
                )
            )
            or self.curve.side != self.side
            or self.curve.token_id != self.token_id
            or self.captured_at_utc.tzinfo is None
        ):
            raise ValueError("GLOBAL_BOOK_ASSET_INVALID")


@dataclass(frozen=True)
class CurrentGlobalSellAsset:
    """One current side-native bid ladder for an exact held-position SELL."""

    family_key: str
    bin_id: str
    condition_id: str
    gamma_market_id: str
    market_event_id: str
    side: str
    token_id: str
    curve: ExecutableSellCurve
    captured_at_utc: datetime

    def __post_init__(self) -> None:
        if (
            self.side not in {"YES", "NO"}
            or not all(
                str(value).strip()
                for value in (
                    self.family_key,
                    self.bin_id,
                    self.condition_id,
                    self.gamma_market_id,
                    self.market_event_id,
                    self.token_id,
                )
            )
            or self.curve.side != self.side
            or self.curve.token_id != self.token_id
            or self.captured_at_utc.tzinfo is None
        ):
            raise ValueError("GLOBAL_SELL_BOOK_ASSET_INVALID")


def current_global_book_epoch_identity(
    *,
    asset_states: Sequence[tuple[str, ...]],
    captured_at_utc: datetime,
) -> str:
    if captured_at_utc.tzinfo is None:
        raise ValueError("GLOBAL_BOOK_EPOCH_TIME_INVALID")
    digest = hashlib.sha256()
    for state in sorted(tuple(str(value) for value in row) for row in asset_states):
        digest.update(repr(state).encode("utf-8"))
        digest.update(b"\x1f")
    digest.update(captured_at_utc.isoformat().encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class CurrentGlobalBookEpoch:
    """Every candidate-capable YES/NO book in one bounded public-CLOB sweep."""

    assets: tuple[CurrentGlobalBookAsset, ...]
    asset_states: tuple[tuple[str, ...], ...]
    captured_at_utc: datetime
    max_age: timedelta
    witness_identity: str
    sell_assets: tuple[CurrentGlobalSellAsset, ...] = ()

    def __post_init__(self) -> None:
        states = tuple(sorted(tuple(str(value) for value in row) for row in self.asset_states))
        sell_keys = tuple(
            (asset.family_key, asset.bin_id, asset.side, asset.token_id)
            for asset in self.sell_assets
        )
        if (
            not states
            or len(set(states)) != len(states)
            or len(set(sell_keys)) != len(sell_keys)
            or self.captured_at_utc.tzinfo is None
            or self.max_age <= timedelta(0)
        ):
            raise ValueError("GLOBAL_BOOK_EPOCH_INVALID")
        expected = current_global_book_epoch_identity(
            asset_states=states,
            captured_at_utc=self.captured_at_utc,
        )
        if self.witness_identity != expected:
            raise ValueError("GLOBAL_BOOK_EPOCH_IDENTITY_MISMATCH")
        object.__setattr__(self, "asset_states", states)

    @cached_property
    def asset_by_key(self) -> Mapping[tuple[str, str, str, str], CurrentGlobalBookAsset]:
        return {
            (asset.family_key, asset.bin_id, asset.side, asset.token_id): asset
            for asset in self.assets
        }

    @cached_property
    def sell_asset_by_key(
        self,
    ) -> Mapping[tuple[str, str, str, str], CurrentGlobalSellAsset]:
        return {
            (asset.family_key, asset.bin_id, asset.side, asset.token_id): asset
            for asset in self.sell_assets
        }

    def current_identity(self, checked_at_utc: datetime) -> str | None:
        if checked_at_utc.tzinfo is None:
            return None
        age = checked_at_utc.astimezone(timezone.utc) - self.captured_at_utc.astimezone(
            timezone.utc
        )
        if age.total_seconds() < 0.0 or age > self.max_age:
            return None
        return self.witness_identity

    def execution_authority(
        self,
        candidate: object,
        *,
        checked_at_utc: datetime,
    ) -> CurrentExecutionAuthority | None:
        if self.current_identity(checked_at_utc) is None:
            return None
        key = (
            str(getattr(candidate, "family_key", "") or ""),
            str(getattr(candidate, "bin_id", "") or ""),
            str(getattr(candidate, "side", "") or ""),
            str(getattr(candidate, "token_id", "") or ""),
        )
        action = str(getattr(candidate, "action", "BUY") or "BUY")
        asset = (
            self.sell_asset_by_key.get(key)
            if action == "SELL"
            else self.asset_by_key.get(key)
        )
        if asset is None:
            return None
        return CurrentExecutionAuthority(
            token_id=asset.token_id,
            side=asset.side,  # type: ignore[arg-type]
            book_snapshot_id=asset.curve.snapshot_id,
            execution_curve_identity=executable_curve_identity(asset.curve),
            action=action,  # type: ignore[arg-type]
        )


def _global_book_snapshot_rows(
    trade_conn: sqlite3.Connection,
    *,
    condition_ids: Sequence[str],
    checked_at_utc: datetime | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    clean = tuple(dict.fromkeys(str(value or "").strip() for value in condition_ids))
    clean = tuple(value for value in clean if value)
    if checked_at_utc is not None and checked_at_utc.tzinfo is None:
        raise ValueError("GLOBAL_BOOK_INVALIDATION_CHECK_TIME_NAIVE")
    condition_invalidated_at: dict[str, datetime] = {}
    token_invalidated_at: dict[str, datetime] = {}
    if checked_at_utc is not None and _table_exists(
        trade_conn,
        "executable_market_snapshot_invalidations",
    ):
        invalidation_rows = trade_conn.execute(
            """
            SELECT condition_id, token_id, MAX(invalidated_at) AS invalidated_at
              FROM executable_market_snapshot_invalidations
             WHERE invalidated_at <= ?
             GROUP BY condition_id, token_id
            """,
            (checked_at_utc.astimezone(timezone.utc).isoformat(),),
        ).fetchall()
        for raw_condition, raw_token, raw_invalidated_at in invalidation_rows:
            try:
                invalidated_at = datetime.fromisoformat(
                    str(raw_invalidated_at).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            condition_id = str(raw_condition or "").strip()
            token_id = str(raw_token or "").strip()
            if condition_id and invalidated_at > condition_invalidated_at.get(
                condition_id,
                datetime.min.replace(tzinfo=timezone.utc),
            ):
                condition_invalidated_at[condition_id] = invalidated_at
            if token_id and invalidated_at > token_invalidated_at.get(
                token_id,
                datetime.min.replace(tzinfo=timezone.utc),
            ):
                token_invalidated_at[token_id] = invalidated_at

    def snapshot_invalidated(row: Mapping[str, object]) -> bool:
        if not condition_invalidated_at and not token_invalidated_at:
            return False
        try:
            captured_at = datetime.fromisoformat(
                str(row.get("captured_at") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return False
        identities = (
            condition_invalidated_at.get(str(row.get("condition_id") or "")),
            token_invalidated_at.get(
                str(row.get("selected_outcome_token_id") or "")
            ),
            token_invalidated_at.get(str(row.get("yes_token_id") or "")),
            token_invalidated_at.get(str(row.get("no_token_id") or "")),
        )
        return any(
            invalidated_at is not None and invalidated_at >= captured_at
            for invalidated_at in identities
        )

    for offset in range(0, len(clean), 400):
        chunk = clean[offset : offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        # Current CLOB responses own price/depth for this epoch.  Project only
        # topology/execution metadata here; ``s.*`` also loads the append row's
        # large historical depth payload and can outlive the quote it supports.
        cur = trade_conn.execute(
            f"""
            SELECT s.snapshot_id,
                   s.gamma_market_id,
                   s.event_id,
                   s.condition_id,
                   s.selected_outcome_token_id,
                   s.yes_token_id,
                   s.no_token_id,
                   s.enable_orderbook,
                   s.active,
                   s.closed,
                   s.accepting_orders,
                   s.min_tick_size,
                   s.min_order_size,
                   s.fee_details_json,
                   s.tradeability_status_json,
                   s.captured_at,
                   s.freshness_deadline
              FROM executable_market_snapshot_latest AS latest
              JOIN executable_market_snapshots AS s
                ON s.snapshot_id = latest.snapshot_id
             WHERE latest.condition_id IN ({placeholders})
             ORDER BY s.captured_at DESC, s.snapshot_id DESC
            """,
            chunk,
        )
        for row in cur.fetchall():
            item = _row_dict(cur, row)
            item["snapshot_invalidated"] = snapshot_invalidated(item)
            rows.append(item)
    return rows


def _global_book_latest_token_rows(
    trade_conn: sqlite3.Connection,
    *,
    condition_ids: Sequence[str],
) -> list[dict[str, object]]:
    """Read only the latest token topology used for speculative book I/O."""

    rows: list[dict[str, object]] = []
    clean = tuple(dict.fromkeys(str(value or "").strip() for value in condition_ids))
    clean = tuple(value for value in clean if value)
    for offset in range(0, len(clean), 400):
        chunk = clean[offset : offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        try:
            cur = trade_conn.execute(
                f"""
                SELECT condition_id,
                       yes_token_id,
                       no_token_id
                  FROM executable_market_snapshot_latest
                 WHERE condition_id IN ({placeholders})
                """,
                chunk,
            )
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc).lower():
                raise
            return _global_book_snapshot_rows(
                trade_conn,
                condition_ids=clean,
            )
        rows.extend(_row_dict(cur, row) for row in cur.fetchall())
    return rows


def _canonical_raw_book_hash(raw_book: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(raw_book),
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _global_book_curve(
    *,
    family_key: str,
    bin_id: str,
    condition_id: str,
    side: str,
    token_id: str,
    raw_book: Mapping[str, object],
    metadata: Mapping[str, object],
    captured_at_utc: datetime,
    max_age: timedelta,
) -> ExecutableCostCurve | None:
    raw_asks = raw_book.get("asks")
    if not isinstance(raw_asks, list):
        raise ValueError(f"GLOBAL_BOOK_ASKS_INVALID:{token_id}")
    if not raw_asks:
        return None
    levels: list[BookLevel] = []
    for raw in raw_asks:
        if not isinstance(raw, Mapping):
            raise ValueError(f"GLOBAL_BOOK_LEVEL_INVALID:{token_id}")
        try:
            levels.append(
                BookLevel(
                    price=Decimal(str(raw.get("price"))),
                    size=Decimal(str(raw.get("size"))),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"GLOBAL_BOOK_LEVEL_INVALID:{token_id}") from exc
    tick = Decimal(
        str(raw_book.get("tick_size") or metadata.get("min_tick_size") or "")
    )
    min_order = Decimal(
        str(raw_book.get("min_order_size") or metadata.get("min_order_size") or "")
    )
    try:
        fee_details = json.loads(str(metadata.get("fee_details_json") or "{}"))
        schedule_fee = fee_rate_fraction_from_details(fee_details)
        fee_rate, _fee_source = resolve_taker_fee_fraction(schedule_fee)
        fee_rate = Decimal(str(fee_rate))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"GLOBAL_BOOK_FEE_INVALID:{token_id}") from exc
    raw_hash = _canonical_raw_book_hash(raw_book)
    snapshot_id = hashlib.sha256(
        "|".join(
            (
                "global-book",
                family_key,
                bin_id,
                condition_id,
                side,
                token_id,
                raw_hash,
                captured_at_utc.isoformat(),
            )
        ).encode("utf-8")
    ).hexdigest()
    return ExecutableCostCurve(
        token_id=token_id,
        side=side,  # type: ignore[arg-type]
        snapshot_id=snapshot_id,
        book_hash=raw_hash,
        levels=tuple(levels),
        fee_model=FeeModel(fee_rate=fee_rate),
        min_tick=tick,
        min_order_size=min_order,
        quote_ttl=max_age,
        fee_details=fee_details,
    )


def _global_sell_curve(
    *,
    family_key: str,
    bin_id: str,
    condition_id: str,
    side: str,
    token_id: str,
    raw_book: Mapping[str, object],
    metadata: Mapping[str, object],
    captured_at_utc: datetime,
    max_age: timedelta,
) -> ExecutableSellCurve | None:
    raw_bids = raw_book.get("bids")
    if not isinstance(raw_bids, list):
        raise ValueError(f"GLOBAL_BOOK_BIDS_INVALID:{token_id}")
    if not raw_bids:
        return None
    try:
        levels = tuple(
            BookLevel(
                price=Decimal(str(raw.get("price"))),
                size=Decimal(str(raw.get("size"))),
            )
            for raw in raw_bids
            if isinstance(raw, Mapping)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"GLOBAL_BOOK_BID_LEVEL_INVALID:{token_id}") from exc
    if len(levels) != len(raw_bids):
        raise ValueError(f"GLOBAL_BOOK_BID_LEVEL_INVALID:{token_id}")
    try:
        tick = Decimal(
            str(raw_book.get("tick_size") or metadata.get("min_tick_size") or "")
        )
        min_order = Decimal(
            str(raw_book.get("min_order_size") or metadata.get("min_order_size") or "")
        )
        fee_details = json.loads(str(metadata.get("fee_details_json") or "{}"))
        schedule_fee = fee_rate_fraction_from_details(fee_details)
        fee_rate, _fee_source = resolve_taker_fee_fraction(schedule_fee)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"GLOBAL_BOOK_FEE_INVALID:{token_id}") from exc
    raw_hash = _canonical_raw_book_hash(raw_book)
    snapshot_id = hashlib.sha256(
        "|".join(
            (
                "global-sell-book",
                family_key,
                bin_id,
                condition_id,
                side,
                token_id,
                raw_hash,
                captured_at_utc.isoformat(),
            )
        ).encode("utf-8")
    ).hexdigest()
    return ExecutableSellCurve(
        token_id=token_id,
        side=side,  # type: ignore[arg-type]
        snapshot_id=snapshot_id,
        book_hash=raw_hash,
        levels=levels,
        fee_model=FeeModel(fee_rate=Decimal(str(fee_rate))),
        min_tick=tick,
        min_order_size=min_order,
        quote_ttl=max_age,
    )


def _global_book_metadata_is_current(
    metadata: Mapping[str, object],
    *,
    checked_at_utc: datetime,
) -> bool:
    """Require current tradeability facts for a freshly fetched book.

    A public CLOB book proves price/depth, not whether stale local Gamma/CLOB
    metadata still describes a live market.  Metadata fetched from Gamma in
    this same global epoch is current by construction.  Persisted metadata must
    still be inside its own executable-snapshot freshness interval.
    """

    if metadata.get("_global_current_gamma") is True:
        return True
    try:
        captured_at = datetime.fromisoformat(
            str(metadata.get("captured_at") or "").replace("Z", "+00:00")
        )
        freshness_deadline = datetime.fromisoformat(
            str(metadata.get("freshness_deadline") or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False
    if captured_at.tzinfo is None or freshness_deadline.tzinfo is None:
        return False
    checked = checked_at_utc.astimezone(timezone.utc)
    return (
        captured_at.astimezone(timezone.utc)
        <= checked
        <= freshness_deadline.astimezone(timezone.utc)
    )


def _global_book_metadata_is_executable(
    metadata: Mapping[str, object],
    *,
    checked_at_utc: datetime,
) -> bool:
    if not _global_book_metadata_is_current(
        metadata,
        checked_at_utc=checked_at_utc,
    ):
        return False
    return _global_book_metadata_tradeability(metadata) is True


def _global_book_metadata_tradeability(
    metadata: Mapping[str, object],
) -> bool | None:
    """Return payload tradeability without asserting metadata freshness."""

    required = ("enable_orderbook", "active", "closed", "accepting_orders")
    if any(key not in metadata for key in required):
        return None
    try:
        tradeability = json.loads(
            str(metadata.get("tradeability_status_json") or "{}")
        )
    except json.JSONDecodeError:
        tradeability = {}
    return (
        bool(metadata.get("enable_orderbook"))
        and bool(metadata.get("active"))
        and not bool(metadata.get("closed"))
        and bool(metadata.get("accepting_orders"))
        and not (
            isinstance(tradeability, Mapping)
            and tradeability.get("executable_allowed") is False
        )
    )


def _current_global_book_asset_state(
    *,
    family_key: str,
    bin_id: str,
    condition_id: str,
    side: str,
    token_id: str,
    metadata: Mapping[str, object],
    raw_book: Mapping[str, object] | None,
    captured_at_utc: datetime,
    checked_at_utc: datetime,
    max_age: timedelta,
) -> tuple[
    tuple[str, ...],
    CurrentGlobalBookAsset | None,
    CurrentGlobalSellAsset | None,
]:
    if not metadata.get("_global_current_gamma") and bool(
        metadata.get("snapshot_invalidated")
    ):
        raise ValueError(f"GLOBAL_BOOK_METADATA_INVALIDATED:{condition_id}:{token_id}")
    market_event_id = str(metadata.get("event_id") or "").strip()
    gamma_market_id = str(metadata.get("gamma_market_id") or "").strip()
    if not gamma_market_id:
        raise ValueError(f"GLOBAL_BOOK_GAMMA_MARKET_ID_MISSING:{condition_id}:{token_id}")
    if not market_event_id:
        raise ValueError(f"GLOBAL_BOOK_MARKET_EVENT_ID_MISSING:{condition_id}:{token_id}")

    metadata_current = _global_book_metadata_is_current(
        metadata,
        checked_at_utc=checked_at_utc,
    )
    executable_metadata = _global_book_metadata_is_executable(
        metadata,
        checked_at_utc=checked_at_utc,
    )
    curve = None
    sell_curve = None
    status = "VENUE_NOT_EXECUTABLE" if metadata_current else "VENUE_METADATA_STALE"
    if executable_metadata:
        if raw_book is None:
            raise ValueError(f"GLOBAL_BOOK_RESPONSE_INCOMPLETE:{token_id}")
        raw_asset_id = str(
            raw_book.get("asset_id")
            or raw_book.get("assetId")
            or raw_book.get("token_id")
            or ""
        ).strip()
        if raw_asset_id != token_id:
            raise ValueError(f"GLOBAL_BOOK_TOKEN_MISMATCH:{token_id}")
        book_hash = _canonical_raw_book_hash(raw_book)
        curve = _global_book_curve(
            family_key=family_key,
            bin_id=bin_id,
            condition_id=condition_id,
            side=side,
            token_id=token_id,
            raw_book=raw_book,
            metadata=metadata,
            captured_at_utc=captured_at_utc,
            max_age=max_age,
        )
        sell_curve = _global_sell_curve(
            family_key=family_key,
            bin_id=bin_id,
            condition_id=condition_id,
            side=side,
            token_id=token_id,
            raw_book=raw_book,
            metadata=metadata,
            captured_at_utc=captured_at_utc,
            max_age=max_age,
        )
        status = "EXECUTABLE" if curve is not None else "NO_ASK"
    else:
        book_hash = "metadata:" + hashlib.sha256(
            json.dumps(
                dict(metadata),
                default=str,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    state = (
        family_key,
        bin_id,
        condition_id,
        side,
        token_id,
        status,
        book_hash,
        market_event_id,
        gamma_market_id,
    )
    asset = (
        CurrentGlobalBookAsset(
            family_key=family_key,
            bin_id=bin_id,
            condition_id=condition_id,
            gamma_market_id=gamma_market_id,
            market_event_id=market_event_id,
            side=side,
            token_id=token_id,
            curve=curve,
            captured_at_utc=captured_at_utc,
            bid_levels=(
                tuple(sell_curve.levels) if sell_curve is not None else ()
            ),
        )
        if curve is not None
        else None
    )
    sell_asset = (
        CurrentGlobalSellAsset(
            family_key=family_key,
            bin_id=bin_id,
            condition_id=condition_id,
            gamma_market_id=gamma_market_id,
            market_event_id=market_event_id,
            side=side,
            token_id=token_id,
            curve=sell_curve,
            captured_at_utc=captured_at_utc,
        )
        if sell_curve is not None
        else None
    )
    return state, asset, sell_asset


def capture_current_global_book_epoch(
    trade_conn: sqlite3.Connection,
    *,
    probability_witnesses: Mapping[str, FamilyPayoffWitness],
    get_books: Callable[[list[str]], Mapping[str, Mapping[str, object]]],
    clock: Callable[[], datetime],
    max_age: timedelta,
    batch_size: int = 500,
    book_fetch_workers: int = 1,
    metadata_overrides: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    prefetched_books: Mapping[str, Mapping[str, object]] | None = None,
    prefetched_at_utc: datetime | None = None,
) -> CurrentGlobalBookEpoch:
    """Fetch every candidate-capable native book without shrinking its set."""

    if (
        max_age <= timedelta(0)
        or not 1 <= batch_size <= 500
        or not 1 <= book_fetch_workers <= 4
    ):
        raise ValueError("GLOBAL_BOOK_FETCH_CONTRACT_INVALID")
    bindings: list[tuple[str, str, str, str, str]] = []
    for family_key, witness in sorted(probability_witnesses.items()):
        if family_key != witness.family_key:
            raise ValueError("GLOBAL_BOOK_PROBABILITY_FAMILY_MISMATCH")
        for binding in actionable_family_payoff_bindings(witness):
            for side, raw_token in (
                ("YES", binding.yes_token_id),
                ("NO", binding.no_token_id),
            ):
                token_id = str(raw_token or "").strip()
                if not token_id:
                    raise ValueError(
                        "GLOBAL_TOKEN_IDENTITY_INCOMPLETE:"
                        f"{family_key}:{binding.bin_id}:{side}"
                    )
                bindings.append(
                    (
                        family_key,
                        binding.bin_id,
                        binding.condition_id,
                        side,
                        token_id,
                    )
                )
    if not bindings or len({row[4] for row in bindings}) != len(bindings):
        raise ValueError("GLOBAL_TOKEN_UNIVERSE_AMBIGUOUS")

    started_at = prefetched_at_utc if prefetched_books is not None else clock()
    if started_at is None or started_at.tzinfo is None:
        raise ValueError("GLOBAL_BOOK_CLOCK_INVALID")
    started_at = started_at.astimezone(timezone.utc)
    metadata_rows = _global_book_snapshot_rows(
        trade_conn,
        condition_ids=[row[2] for row in bindings],
        checked_at_utc=started_at,
    )
    metadata_by_key: dict[tuple[str, str], dict[str, object]] = {}
    for row in metadata_rows:
        condition_id = str(row.get("condition_id") or "")
        for token_id in (
            row.get("selected_outcome_token_id"),
            row.get("yes_token_id"),
            row.get("no_token_id"),
        ):
            clean_token = str(token_id or "").strip()
            if condition_id and clean_token:
                metadata_by_key.setdefault((condition_id, clean_token), row)
    for key, row in (metadata_overrides or {}).items():
        metadata_by_key[(str(key[0]), str(key[1]))] = dict(row)

    tokens = []
    for _, _, condition_id, _, token_id in bindings:
        metadata = metadata_by_key.get((condition_id, token_id))
        if metadata is None:
            raise ValueError(
                f"GLOBAL_BOOK_METADATA_MISSING:{condition_id}:{token_id}"
            )
        if not metadata.get("_global_current_gamma") and bool(
            metadata.get("snapshot_invalidated")
        ):
            raise ValueError(
                f"GLOBAL_BOOK_METADATA_INVALIDATED:{condition_id}:{token_id}"
            )
        if _global_book_metadata_is_executable(
            metadata,
            checked_at_utc=started_at,
        ):
            tokens.append(token_id)
    books = (
        fetch_current_global_books(
            tokens,
            get_books=get_books,
            batch_size=batch_size,
            book_fetch_workers=book_fetch_workers,
        )
        if prefetched_books is None
        else _validated_global_book_batch(prefetched_books)
    )
    finished_at = clock()
    if finished_at.tzinfo is None:
        raise ValueError("GLOBAL_BOOK_CLOCK_INVALID")
    finished_at = finished_at.astimezone(timezone.utc)
    if finished_at < started_at or finished_at - started_at > max_age:
        raise ValueError("GLOBAL_BOOK_CAPTURE_WINDOW_EXPIRED")
    missing_books = [token for token in tokens if token not in books]
    if missing_books:
        raise ValueError(f"GLOBAL_BOOK_RESPONSE_INCOMPLETE:{len(missing_books)}")

    assets: list[CurrentGlobalBookAsset] = []
    sell_assets: list[CurrentGlobalSellAsset] = []
    states: list[tuple[str, ...]] = []
    for family_key, bin_id, condition_id, side, token_id in bindings:
        metadata = metadata_by_key.get((condition_id, token_id))
        if metadata is None:
            raise ValueError(f"GLOBAL_BOOK_METADATA_MISSING:{condition_id}:{token_id}")
        state, asset, sell_asset = _current_global_book_asset_state(
            family_key=family_key,
            bin_id=bin_id,
            condition_id=condition_id,
            side=side,
            token_id=token_id,
            metadata=metadata,
            raw_book=books.get(token_id),
            captured_at_utc=started_at,
            checked_at_utc=started_at,
            max_age=max_age,
        )
        states.append(state)
        if asset is not None:
            assets.append(asset)
        if sell_asset is not None:
            sell_assets.append(sell_asset)
    identity = current_global_book_epoch_identity(
        asset_states=states,
        captured_at_utc=started_at,
    )
    return CurrentGlobalBookEpoch(
        assets=tuple(assets),
        asset_states=tuple(states),
        captured_at_utc=started_at,
        max_age=max_age,
        witness_identity=identity,
        sell_assets=tuple(sell_assets),
    )


def refresh_current_global_book_epoch_tokens(
    trade_conn: sqlite3.Connection,
    *,
    epoch: CurrentGlobalBookEpoch,
    projected_books: Mapping[
        str,
        tuple[Mapping[str, object], datetime, str],
    ],
    required_tokens: Sequence[str],
    checked_at_utc: datetime,
    metadata_overrides: Mapping[
        tuple[str, str], Mapping[str, object]
    ] | None = None,
) -> tuple[CurrentGlobalBookEpoch, int]:
    """Replace newer projected token books while preserving the epoch cut.

    ``metadata_overrides`` carries current Gamma metadata already certified by
    this still-live epoch. Price-channel depth can then advance independently
    of the persisted snapshot metadata clock. Persisted metadata remains bound
    to the exact projected snapshot id; only same-epoch Gamma authority may
    cross that boundary.
    """

    if (
        checked_at_utc.tzinfo is None
        or epoch.current_identity(checked_at_utc) is None
    ):
        raise ValueError("GLOBAL_BOOK_TOKEN_DELTA_EPOCH_STALE")
    required = frozenset(str(token or "").strip() for token in required_tokens)
    if not required or any(not token for token in required):
        raise ValueError("GLOBAL_BOOK_TOKEN_DELTA_REQUIRED_EMPTY")
    state_by_token = {state[4]: state for state in epoch.asset_states}
    if len(state_by_token) != len(epoch.asset_states):
        raise ValueError("GLOBAL_BOOK_TOKEN_DELTA_TOPOLOGY_AMBIGUOUS")
    if not required.issubset(state_by_token):
        raise ValueError("GLOBAL_BOOK_TOKEN_DELTA_REQUIRED_UNKNOWN")

    clean_projected = {
        str(token or "").strip(): value
        for token, value in projected_books.items()
        if str(token or "").strip() in state_by_token
    }
    if not required.issubset(clean_projected):
        raise ValueError("GLOBAL_BOOK_TOKEN_DELTA_PROJECTION_MISSING")

    condition_ids = tuple(
        dict.fromkeys(state_by_token[token][2] for token in clean_projected)
    )
    metadata_by_token: dict[str, Mapping[str, object]] = {}
    for metadata in _global_book_snapshot_rows(
        trade_conn,
        condition_ids=condition_ids,
        checked_at_utc=checked_at_utc,
    ):
        token = str(metadata.get("selected_outcome_token_id") or "").strip()
        if token in clean_projected and token not in metadata_by_token:
            metadata_by_token[token] = metadata
    for raw_key, metadata in (metadata_overrides or {}).items():
        condition_id = str(raw_key[0] or "").strip()
        token = str(raw_key[1] or "").strip()
        if (
            token in clean_projected
            and str(metadata.get("condition_id") or "").strip() == condition_id
            and str(metadata.get("selected_outcome_token_id") or "").strip()
            == token
        ):
            metadata_by_token[token] = metadata

    assets_by_token = {asset.token_id: asset for asset in epoch.assets}
    sell_assets_by_token = {asset.token_id: asset for asset in epoch.sell_assets}
    changed = 0
    for token, (raw_book, captured_at, snapshot_id) in sorted(
        clean_projected.items()
    ):
        if captured_at.tzinfo is None:
            raise ValueError(f"GLOBAL_BOOK_TOKEN_DELTA_TIME_NAIVE:{token}")
        captured_at = captured_at.astimezone(timezone.utc)
        state = state_by_token[token]
        family_key, bin_id, condition_id, side = state[:4]
        base_asset = assets_by_token.get(token) or sell_assets_by_token.get(token)
        base_captured_at = (
            base_asset.captured_at_utc
            if base_asset is not None
            else epoch.captured_at_utc
        )
        if captured_at <= base_captured_at.astimezone(timezone.utc):
            projected_hash = _canonical_raw_book_hash(raw_book)
            if token in required and projected_hash != state[6]:
                raise ValueError(f"GLOBAL_BOOK_TOKEN_DELTA_NOT_NEWER:{token}")
            continue
        if captured_at > checked_at_utc.astimezone(timezone.utc):
            raise ValueError(f"GLOBAL_BOOK_TOKEN_DELTA_FROM_FUTURE:{token}")
        metadata = metadata_by_token.get(token)
        current_gamma = bool(
            metadata is not None
            and metadata.get("_global_current_gamma") is True
        )
        if metadata is None or (
            not current_gamma
            and str(metadata.get("snapshot_id") or "") != snapshot_id
        ):
            raise ValueError(f"GLOBAL_BOOK_TOKEN_DELTA_METADATA_MISSING:{token}")
        if not _global_book_metadata_is_current(
            metadata,
            checked_at_utc=checked_at_utc,
        ):
            raise ValueError(f"GLOBAL_BOOK_TOKEN_DELTA_METADATA_STALE:{token}")
        expected_token = str(
            metadata.get("yes_token_id" if side == "YES" else "no_token_id")
            or ""
        ).strip()
        if (
            str(metadata.get("condition_id") or "").strip() != condition_id
            or expected_token != token
        ):
            raise ValueError(f"GLOBAL_BOOK_TOKEN_DELTA_TOPOLOGY_CHANGED:{token}")
        new_state, asset, sell_asset = _current_global_book_asset_state(
            family_key=family_key,
            bin_id=bin_id,
            condition_id=condition_id,
            side=side,
            token_id=token,
            metadata=metadata,
            raw_book=raw_book,
            captured_at_utc=captured_at,
            checked_at_utc=checked_at_utc,
            max_age=epoch.max_age,
        )
        if new_state == state:
            continue
        state_by_token[token] = new_state
        changed += 1
        if asset is None:
            assets_by_token.pop(token, None)
        else:
            assets_by_token[token] = asset
        if sell_asset is None:
            sell_assets_by_token.pop(token, None)
        else:
            sell_assets_by_token[token] = sell_asset

    if not changed:
        return epoch, 0
    states = tuple(state_by_token.values())
    identity = current_global_book_epoch_identity(
        asset_states=states,
        captured_at_utc=epoch.captured_at_utc,
    )
    return (
        CurrentGlobalBookEpoch(
            assets=tuple(assets_by_token.values()),
            asset_states=states,
            captured_at_utc=epoch.captured_at_utc,
            max_age=epoch.max_age,
            witness_identity=identity,
            sell_assets=tuple(sell_assets_by_token.values()),
        ),
        changed,
    )


def _validated_global_book_batch(
    batch: Mapping[str, Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    if not isinstance(batch, Mapping):
        raise ValueError("GLOBAL_BOOK_BATCH_RESPONSE_INVALID")
    return {
        str(token): raw
        for token, raw in batch.items()
        if isinstance(raw, Mapping)
    }


def fetch_current_global_books(
    tokens: Sequence[str],
    *,
    get_books: Callable[[list[str]], Mapping[str, Mapping[str, object]]],
    batch_size: int = 500,
    book_fetch_workers: int = 1,
) -> dict[str, Mapping[str, object]]:
    """Fetch one bounded CLOB book universe without interpreting market metadata."""

    if not 1 <= batch_size <= 500 or not 1 <= book_fetch_workers <= 4:
        raise ValueError("GLOBAL_BOOK_FETCH_CONTRACT_INVALID")
    clean_tokens = tuple(str(token or "").strip() for token in tokens)
    if any(not token for token in clean_tokens) or len(set(clean_tokens)) != len(
        clean_tokens
    ):
        raise ValueError("GLOBAL_TOKEN_UNIVERSE_AMBIGUOUS")
    chunks = tuple(
        list(clean_tokens[offset : offset + batch_size])
        for offset in range(0, len(clean_tokens), batch_size)
    )
    if not chunks:
        return {}

    books: dict[str, Mapping[str, object]] = {}

    def merge(batch: Mapping[str, Mapping[str, object]]) -> None:
        books.update(_validated_global_book_batch(batch))

    if len(chunks) == 1 or book_fetch_workers == 1:
        for chunk in chunks:
            merge(get_books(chunk))
        return books

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(
        max_workers=min(book_fetch_workers, len(chunks)),
        thread_name_prefix="global-clob-books",
    ) as executor:
        futures = tuple(executor.submit(get_books, chunk) for chunk in chunks)
        for future in as_completed(futures):
            merge(future.result())
    return books


def _rebind_probability_witness_tokens(
    witness: FamilyPayoffWitness,
    *,
    token_map_by_condition: Mapping[str, tuple[str, str]],
) -> FamilyPayoffWitness:
    bindings: list[OutcomeTokenBinding] = []
    for binding in witness.bindings:
        current = token_map_by_condition.get(binding.condition_id)
        yes = str(binding.yes_token_id or "").strip()
        no = str(binding.no_token_id or "").strip()
        if current is not None:
            current_yes, current_no = current
            if (yes and yes != current_yes) or (no and no != current_no):
                raise ValueError(
                    f"GLOBAL_TOKEN_IDENTITY_MISMATCH:{binding.condition_id}"
                )
            yes = current_yes
            no = current_no
        if not yes or not no:
            raise ValueError(
                f"GLOBAL_TOKEN_IDENTITY_INCOMPLETE:{binding.condition_id}"
            )
        bindings.append(
            OutcomeTokenBinding(
                bin_id=binding.bin_id,
                condition_id=binding.condition_id,
                yes_token_id=yes,
                no_token_id=no,
            )
        )
    rebound = tuple(bindings)
    return rebind_family_payoff_witness(witness, bindings=rebound)


def fetch_current_gamma_markets(
    condition_ids: Sequence[str],
    *,
    gamma_get: Callable[..., object],
    timeout: float,
    total_timeout: float | None = None,
    chunk_size: int = 100,
    max_workers: int = 16,
) -> tuple[tuple[Mapping[str, object], ...], int]:
    """Fetch one complete current Gamma market batch or fail closed."""

    from concurrent.futures import (
        ThreadPoolExecutor,
        TimeoutError as FuturesTimeoutError,
        as_completed,
    )

    conditions = tuple(
        dict.fromkeys(
            condition_id
            for raw in condition_ids
            if (condition_id := str(raw or "").strip())
        )
    )
    size = max(1, int(chunk_size))
    chunks = tuple(
        conditions[offset : offset + size]
        for offset in range(0, len(conditions), size)
    )
    if not chunks:
        return (), 0
    deadline = (
        _time.monotonic() + float(total_timeout)
        if total_timeout is not None and float(total_timeout) > 0.0
        else None
    )
    if total_timeout is not None and deadline is None:
        raise ValueError("GLOBAL_CURRENT_GAMMA_MARKETS_TIMEOUT_INVALID")

    def remaining_timeout() -> float:
        if deadline is None:
            return float(timeout)
        remaining = deadline - _time.monotonic()
        if remaining <= 0.0:
            raise ValueError("GLOBAL_CURRENT_GAMMA_MARKETS_DEADLINE_EXCEEDED")
        return min(float(timeout), remaining)

    def _fetch(chunk: Sequence[str]) -> tuple[Mapping[str, object], ...]:
        response = gamma_get(
            "/markets",
            params={"condition_ids": list(chunk), "limit": len(chunk)},
            timeout=remaining_timeout(),
        )
        if getattr(response, "status_code", None) != 200:
            raise ValueError(
                "GLOBAL_CURRENT_GAMMA_MARKETS_HTTP:"
                f"{getattr(response, 'status_code', None)}"
            )
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("GLOBAL_CURRENT_GAMMA_MARKETS_RESPONSE_INVALID")
        if any(not isinstance(market, Mapping) for market in payload):
            raise ValueError("GLOBAL_CURRENT_GAMMA_MARKET_INVALID")
        return tuple(payload)

    if len(chunks) == 1 and deadline is None:
        return _fetch(chunks[0]), 1
    markets: list[Mapping[str, object]] = []
    workers = max(1, min(int(max_workers), len(chunks)))
    pool = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="global-market-metadata",
    )
    futures = ()
    try:
        futures = tuple(pool.submit(_fetch, chunk) for chunk in chunks)
        completion_timeout = (
            max(0.0, deadline - _time.monotonic())
            if deadline is not None
            else None
        )
        for future in as_completed(futures, timeout=completion_timeout):
            markets.extend(future.result())
    except FuturesTimeoutError as exc:
        raise ValueError(
            "GLOBAL_CURRENT_GAMMA_MARKETS_DEADLINE_EXCEEDED"
        ) from exc
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    return tuple(markets), len(chunks)


def bind_current_global_probability_tokens(
    forecasts_conn: sqlite3.Connection,
    *,
    probability_witnesses: Mapping[str, FamilyPayoffWitness],
    get_gamma_event: Callable[[str], Mapping[str, object] | None] | None = None,
    get_gamma_markets: Callable[
        [Sequence[str]], Sequence[Mapping[str, object]]
    ]
    | None = None,
    trade_conn: sqlite3.Connection | None = None,
    checked_at_utc: datetime | None = None,
    max_workers: int = 8,
    metadata_sink: dict[tuple[str, str], Mapping[str, object]] | None = None,
) -> Mapping[str, FamilyPayoffWitness]:
    """Bind tokens and, when requested, current Gamma tradeability metadata."""

    missing_by_family = {
        family_key: witness
        for family_key, witness in probability_witnesses.items()
        if any(
            not binding.yes_token_id or not binding.no_token_id
            for binding in witness.bindings
        )
    }
    refresh_metadata = metadata_sink is not None
    work_by_family = (
        dict(probability_witnesses) if refresh_metadata else missing_by_family
    )
    if not work_by_family:
        return dict(probability_witnesses)

    local_tokens: dict[str, tuple[str, str]] = {}
    local_metadata_by_token: dict[tuple[str, str], Mapping[str, object]] = {}
    if trade_conn is not None:
        if checked_at_utc is None or checked_at_utc.tzinfo is None:
            raise ValueError("GLOBAL_LOCAL_TOKEN_CHECK_TIME_INVALID")
        checked_at_utc = checked_at_utc.astimezone(timezone.utc)
        local_source = work_by_family if refresh_metadata else missing_by_family
        condition_ids = tuple(
            binding.condition_id
            for witness in local_source.values()
            for binding in witness.bindings
        )
        for row in _global_book_snapshot_rows(
            trade_conn,
            condition_ids=condition_ids,
            checked_at_utc=checked_at_utc,
        ):
            if bool(row.get("snapshot_invalidated")):
                continue
            condition_id = str(row.get("condition_id") or "").strip()
            yes = str(row.get("yes_token_id") or "").strip()
            no = str(row.get("no_token_id") or "").strip()
            if not condition_id or not yes or not no:
                continue
            if not _global_book_metadata_is_current(
                row,
                checked_at_utc=checked_at_utc,
            ):
                continue
            pair = (yes, no)
            previous = local_tokens.get(condition_id)
            if previous is not None and previous != pair:
                raise ValueError(
                    f"GLOBAL_LOCAL_TOKEN_IDENTITY_AMBIGUOUS:{condition_id}"
                )
            local_tokens[condition_id] = pair
            selected = str(row.get("selected_outcome_token_id") or "").strip()
            if selected not in pair:
                raise ValueError(
                    f"GLOBAL_LOCAL_SELECTED_TOKEN_IDENTITY_INVALID:{condition_id}"
                )
            local_metadata_by_token.setdefault((condition_id, selected), row)

    local_metadata_family_keys: set[str] = set()
    if metadata_sink is not None:
        for family_key, witness in work_by_family.items():
            token_keys = {
                (binding.condition_id, token_id)
                for binding in witness.bindings
                for token_id in (binding.yes_token_id, binding.no_token_id)
            }
            if not token_keys or not token_keys.issubset(local_metadata_by_token):
                continue
            local_metadata_family_keys.add(family_key)
            for binding in witness.bindings:
                yes, no = local_tokens[binding.condition_id]
                metadata_sink[(binding.condition_id, yes)] = local_metadata_by_token[
                    (binding.condition_id, yes)
                ]
                metadata_sink[(binding.condition_id, no)] = local_metadata_by_token[
                    (binding.condition_id, no)
                ]

    remote_work_by_family = {
        family_key: witness
        for family_key, witness in work_by_family.items()
        if family_key not in local_metadata_family_keys
    }

    from concurrent.futures import ThreadPoolExecutor
    from src.data.market_scanner import _boolish_market_field, _extract_outcomes

    def _family_slug(witness: FamilyPayoffWitness) -> str:
        condition_ids = tuple(binding.condition_id for binding in witness.bindings)
        placeholders = ",".join("?" for _ in condition_ids)
        row = forecasts_conn.execute(
            f"""
            SELECT market_slug
              FROM market_events
             WHERE condition_id IN ({placeholders})
               AND market_slug IS NOT NULL
               AND TRIM(market_slug) != ''
             ORDER BY created_at DESC
             LIMIT 1
            """,
            condition_ids,
        ).fetchone()
        slug = str((row or ("",))[0] or "").strip()
        if not slug:
            raise ValueError(f"GLOBAL_GAMMA_SLUG_MISSING:{witness.family_key}")
        return slug

    events: dict[str, Mapping[str, object] | None] = {}
    if remote_work_by_family and refresh_metadata and get_gamma_markets is not None:
        condition_ids = tuple(
            dict.fromkeys(
                binding.condition_id
                for witness in remote_work_by_family.values()
                for binding in witness.bindings
            )
        )
        requested_conditions = frozenset(condition_ids)
        def _market_map(
            rows: Sequence[Mapping[str, object]],
            expected: frozenset[str],
            *,
            require_complete: bool = True,
        ) -> dict[str, Mapping[str, object]]:
            out: dict[str, Mapping[str, object]] = {}
            for market in rows:
                if not isinstance(market, Mapping):
                    raise ValueError("GLOBAL_CURRENT_GAMMA_MARKET_INVALID")
                condition_id = str(market.get("conditionId") or "").strip()
                if not condition_id:
                    raise ValueError("GLOBAL_CURRENT_GAMMA_MARKET_INVALID")
                if condition_id not in expected:
                    raise ValueError(
                        f"GLOBAL_CURRENT_GAMMA_MARKET_UNEXPECTED:{condition_id}"
                    )
                if condition_id in out:
                    raise ValueError(
                        f"GLOBAL_CURRENT_GAMMA_MARKET_AMBIGUOUS:{condition_id}"
                    )
                out[condition_id] = market
            missing = expected.difference(out)
            if missing and require_complete:
                raise ValueError(
                    "GLOBAL_CURRENT_GAMMA_MARKETS_INCOMPLETE:"
                    + ",".join(sorted(missing))
                )
            return out

        market_by_condition = _market_map(
            get_gamma_markets(condition_ids),
            requested_conditions,
            require_complete=False,
        )
        batch_missing = requested_conditions.difference(market_by_condition)
        if batch_missing and get_gamma_event is None:
            raise ValueError(
                "GLOBAL_CURRENT_GAMMA_MARKETS_INCOMPLETE:"
                + ",".join(sorted(batch_missing))
            )

        def _family_event_map(
            family_key: str,
            family_markets: Sequence[Mapping[str, object]],
        ) -> tuple[dict[str, Mapping[str, object]], bool]:
            event_by_id: dict[str, Mapping[str, object]] = {}
            metadata_ambiguous = False
            for market in family_markets:
                nested_events = market.get("events")
                if not isinstance(nested_events, list) or len(nested_events) != 1:
                    raise ValueError(
                        f"GLOBAL_CURRENT_GAMMA_EVENT_INVALID:{family_key}"
                    )
                nested_event = nested_events[0]
                if not isinstance(nested_event, Mapping):
                    raise ValueError(
                        f"GLOBAL_CURRENT_GAMMA_EVENT_INVALID:{family_key}"
                    )
                event_id = str(nested_event.get("id") or "").strip()
                if not event_id:
                    raise ValueError(
                        f"GLOBAL_CURRENT_GAMMA_EVENT_INVALID:{family_key}"
                    )
                previous = event_by_id.get(event_id)
                if previous is not None and dict(previous) != dict(nested_event):
                    metadata_ambiguous = True
                event_by_id[event_id] = nested_event
            return event_by_id, metadata_ambiguous

        for family_key, witness in remote_work_by_family.items():
            family_condition_ids = tuple(
                binding.condition_id for binding in witness.bindings
            )
            missing_family_conditions = set(family_condition_ids).difference(
                market_by_condition
            )
            if missing_family_conditions:
                event = get_gamma_event(_family_slug(witness))
                if not isinstance(event, Mapping):
                    raise ValueError(
                        f"GLOBAL_CURRENT_GAMMA_EVENT_MISSING:{family_key}"
                    )
                events[family_key] = event
                continue
            family_markets = tuple(
                market_by_condition[binding.condition_id]
                for binding in witness.bindings
            )
            event_by_id, metadata_ambiguous = _family_event_map(
                family_key, family_markets
            )
            if metadata_ambiguous:
                # A family can straddle the public Gamma batch boundary.  Its
                # embedded event decoration may then come from two adjacent API
                # snapshots even though every market binds to one event ID.
                # Recapture that exact family once in one request; never accept
                # the mixed snapshot and never fall back to cached metadata.
                family_expected = frozenset(family_condition_ids)
                refreshed = _market_map(
                    get_gamma_markets(family_condition_ids), family_expected
                )
                family_markets = tuple(
                    refreshed[condition_id]
                    for condition_id in family_condition_ids
                )
                event_by_id, metadata_ambiguous = _family_event_map(
                    family_key, family_markets
                )
                if metadata_ambiguous:
                    raise ValueError(
                        "GLOBAL_CURRENT_GAMMA_EVENT_METADATA_AMBIGUOUS:"
                        f"{family_key}"
                    )
            if len(event_by_id) != 1:
                raise ValueError(
                    f"GLOBAL_CURRENT_GAMMA_EVENT_IDENTITY_AMBIGUOUS:{family_key}"
                )
            event = next(iter(event_by_id.values()))
            events[family_key] = {
                **event,
                "markets": family_markets,
            }
    else:
        slug_by_family: dict[str, str] = {}
        for family_key, witness in remote_work_by_family.items():
            tokens_bound = all(
                (binding.yes_token_id and binding.no_token_id)
                or binding.condition_id in local_tokens
                for binding in witness.bindings
            )
            if tokens_bound and not refresh_metadata:
                continue
            slug_by_family[family_key] = _family_slug(witness)
        if slug_by_family:
            if get_gamma_event is None:
                raise ValueError("GLOBAL_GAMMA_EVENT_READER_MISSING")
            workers = max(1, min(int(max_workers), 16, len(slug_by_family)))
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="global-token-identity",
            ) as pool:
                futures = {
                    family_key: pool.submit(get_gamma_event, slug)
                    for family_key, slug in slug_by_family.items()
                }
                for family_key, future in futures.items():
                    events[family_key] = future.result()

    rebound: dict[str, FamilyPayoffWitness] = {}
    for family_key, witness in probability_witnesses.items():
        if family_key not in work_by_family:
            rebound[family_key] = witness
            continue
        event = events.get(family_key)
        if (
            refresh_metadata
            and event is None
            and family_key not in local_metadata_family_keys
        ):
            raise ValueError(f"GLOBAL_CURRENT_GAMMA_EVENT_MISSING:{family_key}")
        condition_ids = {binding.condition_id for binding in witness.bindings}
        token_map = {
            condition_id: pair
            for condition_id, pair in local_tokens.items()
            if condition_id in condition_ids
        }
        if event is not None:
            metadata_conditions: set[str] = set()
            for outcome in _extract_outcomes(dict(event)):
                condition_id = str(outcome.get("condition_id") or "").strip()
                yes = str(outcome.get("token_id") or "").strip()
                no = str(outcome.get("no_token_id") or "").strip()
                if condition_id in condition_ids and yes and no:
                    pair = (yes, no)
                    previous = token_map.get(condition_id)
                    if previous is not None and previous != pair:
                        raise ValueError(
                            f"GLOBAL_TOKEN_IDENTITY_CONFLICT:{condition_id}"
                        )
                    token_map[condition_id] = pair
                    if metadata_sink is not None:
                        raw = outcome.get("gamma_market_raw")
                        if not isinstance(raw, Mapping):
                            raise ValueError(
                                f"GLOBAL_GAMMA_MARKET_METADATA_MISSING:{condition_id}"
                            )
                        fee_details = fee_details_from_gamma_fee_schedule(
                            raw.get("feeSchedule"),
                            source="global_current_gamma_fee_schedule",
                            fee_type=str(raw.get("feeType") or "") or None,
                        )
                        enable_orderbook = _boolish_market_field(
                            raw,
                            "enableOrderBook",
                            "enable_orderbook",
                            "orderbookEnabled",
                        )
                        active = _boolish_market_field(raw, "active", "isActive")
                        closed = _boolish_market_field(raw, "closed", "isClosed")
                        accepting_orders = _boolish_market_field(
                            raw,
                            "acceptingOrders",
                            "accepting_orders",
                        )
                        executable_allowed = (
                            enable_orderbook is True
                            and active is True
                            and closed is not True
                            and accepting_orders is True
                        )
                        base = {
                            "gamma_market_id": str(
                                raw.get("id")
                                or raw.get("market_id")
                                or raw.get("marketId")
                                or ""
                            ),
                            "event_id": str(
                                event.get("slug")
                                or event.get("event_slug")
                                or event.get("event_id")
                                or event.get("id")
                                or ""
                            ),
                            "condition_id": condition_id,
                            "yes_token_id": yes,
                            "no_token_id": no,
                            "enable_orderbook": enable_orderbook is True,
                            "active": active is True,
                            "closed": closed is True,
                            "accepting_orders": accepting_orders is True,
                            "market_end_at": str(
                                outcome.get("market_end_at")
                                or raw.get("endDate")
                                or raw.get("end_date")
                                or ""
                            ),
                            "fee_details_json": json.dumps(
                                fee_details,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            "min_tick_size": str(
                                raw.get("orderPriceMinTickSize") or ""
                            ),
                            "min_order_size": str(raw.get("orderMinSize") or ""),
                            "tradeability_status_json": json.dumps(
                                {
                                    "executable_allowed": executable_allowed,
                                    "reason": "global_current_gamma_market",
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            "_global_current_gamma": True,
                        }
                        metadata_conditions.add(condition_id)
                        metadata_sink[(condition_id, yes)] = {
                            **base,
                            "selected_outcome_token_id": yes,
                        }
                        metadata_sink[(condition_id, no)] = {
                            **base,
                            "selected_outcome_token_id": no,
                        }
            if metadata_sink is not None and metadata_conditions != condition_ids:
                missing = ",".join(sorted(condition_ids - metadata_conditions))
                raise ValueError(
                    f"GLOBAL_CURRENT_GAMMA_METADATA_INCOMPLETE:{family_key}:{missing}"
                )
        rebound[family_key] = _rebind_probability_witness_tokens(
            witness,
            token_map_by_condition=token_map,
        )
    return rebound


def _event_payload(event: OpportunityEvent) -> dict[str, object]:
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("global universe event payload is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("global universe event payload must be an object")
    return payload


def _payload_family(payload: Mapping[str, object]) -> tuple[str, str, str]:
    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    metric = str(payload.get("metric") or "").strip().lower()
    if not city or not target_date or metric not in {"high", "low"}:
        raise ValueError("global universe event lacks a weather family identity")
    return city, target_date, metric


def _event_family(event: OpportunityEvent) -> tuple[str, str, str]:
    return _payload_family(_event_payload(event))


def _payload_probability_identity(
    event: OpportunityEvent,
    payload: Mapping[str, object],
) -> str:
    return str(
        payload.get("source_run_id")
        or payload.get("snapshot_hash")
        or event.causal_snapshot_id
        or ""
    ).strip()


def _event_probability_identity(event: OpportunityEvent) -> str:
    return _payload_probability_identity(event, _event_payload(event))


def _event_family_key(event: OpportunityEvent) -> str:
    city, target_date, metric = _event_family(event)
    return weather_family_id(
        city=city,
        target_date=target_date,
        metric=metric,
    )


def _payload_resolution_at_utc(
    payload: Mapping[str, object],
    family: tuple[str, str, str],
    *,
    city_configs: Mapping[str, object] | None = None,
) -> datetime:
    city, target_date, _metric = family
    timezone_name = str(payload.get("city_timezone") or "").strip()
    if not timezone_name:
        if city_configs is None:
            from src.config import runtime_cities_by_name

            city_configs = runtime_cities_by_name()
        city_config = city_configs.get(city)
        timezone_name = str(
            getattr(city_config, "timezone", "") or ""
        ).strip()
    if not timezone_name:
        raise ValueError(
            f"global universe family lacks settlement timezone: {city}"
        )
    try:
        target_local_date = date.fromisoformat(target_date)
        resolution_local = datetime.combine(
            target_local_date + timedelta(days=1),
            time.min,
            tzinfo=ZoneInfo(timezone_name),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(
            f"global universe family resolution horizon is invalid: {city}|{target_date}"
        ) from exc
    return resolution_local.astimezone(timezone.utc)


def _event_resolution_at_utc(event: OpportunityEvent) -> datetime:
    """Current family resolution horizon: the end of its settlement-local day."""

    payload = _event_payload(event)
    return _payload_resolution_at_utc(payload, _payload_family(payload))


def _current_global_scope_parts(
    events: Sequence[OpportunityEvent],
) -> tuple[
    tuple[tuple[str, OpportunityEvent], ...],
    tuple[tuple[str, datetime], ...],
    str,
]:
    from src.config import runtime_cities_by_name

    city_configs = runtime_cities_by_name()
    facts: list[tuple[str, OpportunityEvent, str, datetime]] = []
    for event in events:
        payload = _event_payload(event)
        family = _payload_family(payload)
        family_key = weather_family_id(
            city=family[0],
            target_date=family[1],
            metric=family[2],
        )
        probability_identity = _payload_probability_identity(event, payload)
        if not probability_identity:
            raise ValueError("global universe event lacks probability identity")
        facts.append(
            (
                family_key,
                event,
                probability_identity,
                _payload_resolution_at_utc(
                    payload,
                    family,
                    city_configs=city_configs,
                ),
            )
        )
    facts.sort(key=lambda row: row[0])
    if not facts or len({row[0] for row in facts}) != len(facts):
        raise ValueError("global universe must contain one event per family")

    digest = hashlib.sha256()
    for family_key, _event, probability_identity, resolution_at in facts:
        digest.update(
            repr(
                (
                    family_key,
                    probability_identity,
                    resolution_at.isoformat(),
                )
            ).encode("utf-8")
        )
        digest.update(b"\x1f")
    return (
        tuple((family_key, event) for family_key, event, _, _ in facts),
        tuple((family_key, resolution_at) for family_key, _, _, resolution_at in facts),
        digest.hexdigest(),
    )


def current_global_auction_scope_identity(
    events: Sequence[OpportunityEvent],
) -> str:
    """Hash each current probability carrier and its settlement-time horizon."""

    return _current_global_scope_parts(events)[2]


def current_global_auction_scope_from_events(
    events: Sequence[OpportunityEvent],
    *,
    captured_at_utc: datetime,
) -> CurrentGlobalAuctionScope:
    return CurrentGlobalAuctionScope(
        events=events,
        captured_at_utc=captured_at_utc,
    )


def current_global_scope_events_with_day0(
    forecast_events: Sequence[OpportunityEvent],
    day0_events: Sequence[OpportunityEvent],
) -> tuple[OpportunityEvent, ...]:
    """Let current Day0 truth replace its forecast-only carrier per family."""

    by_family = {_event_family_key(event): event for event in forecast_events}
    for event in day0_events:
        family_key = _event_family_key(event)
        previous = by_family.get(family_key)
        if (
            previous is None
            or previous.event_type != "DAY0_EXTREME_UPDATED"
            or (event.available_at, event.created_at, event.event_id)
            > (previous.available_at, previous.created_at, previous.event_id)
        ):
            by_family[family_key] = event
    return tuple(by_family[key] for key in sorted(by_family))


def _day0_event_is_current_for_entry(
    payload: Mapping[str, object],
    *,
    decision_at_utc: datetime,
    city_configs: Mapping[str, object] | None = None,
) -> bool:
    """Admit a Day0 fact only on its target city's current local day."""

    if decision_at_utc.tzinfo is None:
        return False
    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    if not city or not target_date:
        return False
    if city_configs is None:
        from src.config import runtime_cities_by_name

        city_configs = runtime_cities_by_name()
    city_config = city_configs.get(city)
    timezone_name = str(getattr(city_config, "timezone", "") or "").strip()
    if not timezone_name:
        return False
    try:
        target_local_date = date.fromisoformat(target_date)
        current_local_date = decision_at_utc.astimezone(
            ZoneInfo(timezone_name)
        ).date()
    except (TypeError, ValueError, KeyError):
        return False
    return target_local_date == current_local_date


def _current_day0_events(
    world_conn: sqlite3.Connection,
    *,
    decision_at_utc: datetime,
    held_families: Sequence[tuple[str, str, str]] = (),
    restrict_to_families: Sequence[tuple[str, str, str]] | None = None,
) -> tuple[OpportunityEvent, ...]:
    if not _table_exists(world_conn, "opportunity_events"):
        return ()
    from src.events.day0_authority import normalize_day0_live_authority_status
    from src.config import runtime_cities_by_name

    city_configs = runtime_cities_by_name()
    held = frozenset(
        (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip().lower(),
        )
        for city, target_date, metric in held_families
    )
    restricted = (
        frozenset(
            (
                str(city or "").strip(),
                str(target_date or "").strip(),
                str(metric or "").strip().lower(),
            )
            for city, target_date, metric in restrict_to_families
        )
        if restrict_to_families is not None
        else None
    )
    if any(
        not city or not target_date or metric not in {"high", "low"}
        for city, target_date, metric in held
    ):
        raise ValueError("GLOBAL_HELD_FAMILY_IDENTITY_INVALID")
    if restricted is not None and (
        not restricted
        or any(
            not city or not target_date or metric not in {"high", "low"}
            for city, target_date, metric in restricted
        )
    ):
        raise ValueError("GLOBAL_DAY0_RESTRICTED_FAMILY_IDENTITY_INVALID")
    utc_date = decision_at_utc.astimezone(timezone.utc).date()
    target_floor = (utc_date - timedelta(days=1)).isoformat()
    target_ceiling = (utc_date + timedelta(days=1)).isoformat()
    select = """
        SELECT opportunity_events.*,
               json_extract(payload_json, '$.city') AS _day0_city,
               json_extract(payload_json, '$.target_date') AS _day0_target_date,
               json_extract(payload_json, '$.metric') AS _day0_metric,
               json_extract(payload_json, '$.source_match_status') AS _day0_source_match,
               json_extract(payload_json, '$.local_date_status') AS _day0_local_date,
               json_extract(payload_json, '$.station_match_status') AS _day0_station_match,
               json_extract(payload_json, '$.dst_status') AS _day0_dst,
               json_extract(payload_json, '$.metric_match_status') AS _day0_metric_match,
               json_extract(payload_json, '$.rounding_status') AS _day0_rounding,
               json_extract(payload_json, '$.source_authorized_status') AS _day0_source_authorized,
               json_extract(payload_json, '$.live_authority_status') AS _day0_live_authority
         FROM opportunity_events
         INDEXED BY idx_opportunity_events_fsr_target_date
         WHERE event_type='DAY0_EXTREME_UPDATED'
           AND source NOT LIKE 'global_auction_winner_target:%'
    """
    if restricted is None:
        cur = world_conn.execute(
            select
            + " AND json_extract(payload_json, '$.target_date') BETWEEN ? AND ? "
            + "AND available_at<=? AND received_at<=?",
            (
                target_floor,
                target_ceiling,
                decision_at_utc.isoformat(),
                decision_at_utc.isoformat(),
            ),
        )
    else:
        requested = tuple(sorted(restricted))
        requested_values = ",".join("(?, ?, ?)" for _ in requested)
        requested_params = tuple(value for family in requested for value in family)
        cur = world_conn.execute(
            f"""
            WITH requested(city, target_date, metric) AS (
                VALUES {requested_values}
            )
            SELECT e.*,
                   json_extract(e.payload_json, '$.city') AS _day0_city,
                   json_extract(e.payload_json, '$.target_date') AS _day0_target_date,
                   json_extract(e.payload_json, '$.metric') AS _day0_metric,
                   json_extract(e.payload_json, '$.source_match_status') AS _day0_source_match,
                   json_extract(e.payload_json, '$.local_date_status') AS _day0_local_date,
                   json_extract(e.payload_json, '$.station_match_status') AS _day0_station_match,
                   json_extract(e.payload_json, '$.dst_status') AS _day0_dst,
                   json_extract(e.payload_json, '$.metric_match_status') AS _day0_metric_match,
                   json_extract(e.payload_json, '$.rounding_status') AS _day0_rounding,
                   json_extract(e.payload_json, '$.source_authorized_status') AS _day0_source_authorized,
                   json_extract(e.payload_json, '$.live_authority_status') AS _day0_live_authority
              FROM requested AS r
              JOIN opportunity_events AS e
                   INDEXED BY idx_opportunity_events_fsr_target_date
                ON json_extract(e.payload_json, '$.city') = r.city
               AND json_extract(e.payload_json, '$.target_date') = r.target_date
               AND json_extract(e.payload_json, '$.metric') = r.metric
             WHERE e.event_type = 'DAY0_EXTREME_UPDATED'
               AND e.source NOT LIKE 'global_auction_winner_target:%'
               AND e.available_at <= ?
               AND e.received_at <= ?
            """,
            (
                *requested_params,
                decision_at_utc.isoformat(),
                decision_at_utc.isoformat(),
            ),
        )
    rows = list(cur.fetchall())
    if restricted is None:
        for target_date in sorted(
            {
                target_date
                for _, target_date, _ in held
                if not target_floor <= target_date <= target_ceiling
            }
        ):
            held_cur = world_conn.execute(
                select
                + " AND json_extract(payload_json, '$.target_date')=? "
                + "AND available_at<=? AND received_at<=?",
                (
                    target_date,
                    decision_at_utc.isoformat(),
                    decision_at_utc.isoformat(),
                ),
            )
            rows.extend(held_cur.fetchall())
    names = tuple(description[0] for description in cur.description or ())
    indexes = {name: index for index, name in enumerate(names)}

    def _value(raw: object, name: str) -> object:
        if isinstance(raw, sqlite3.Row):
            return raw[name]
        return raw[indexes[name]]  # type: ignore[index]

    latest: dict[str, tuple[tuple[str, str, str], object]] = {}
    for raw in rows:
        family = (
            str(_value(raw, "_day0_city") or "").strip(),
            str(_value(raw, "_day0_target_date") or "").strip(),
            str(_value(raw, "_day0_metric") or "").strip().lower(),
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            continue
        if restricted is not None and family not in restricted:
            continue
        if not (
            _value(raw, "_day0_source_match") == "MATCH"
            and _value(raw, "_day0_local_date") == "MATCH"
            and _value(raw, "_day0_station_match") == "MATCH"
            and _value(raw, "_day0_dst") == "UNAMBIGUOUS"
            and _value(raw, "_day0_metric_match") == "MATCH"
            and _value(raw, "_day0_rounding") == "MATCH"
            and (_value(raw, "_day0_source_authorized") or "AUTHORIZED")
            == "AUTHORIZED"
            and normalize_day0_live_authority_status(
                _value(raw, "_day0_live_authority")
            )
            == "live"
        ):
            continue
        expires_at = _value(raw, "expires_at")
        if expires_at:
            try:
                expires = datetime.fromisoformat(
                    str(expires_at).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                continue
            if expires.tzinfo is None or expires.astimezone(timezone.utc) < decision_at_utc:
                continue
        family_key = weather_family_id(
            city=family[0],
            target_date=family[1],
            metric=family[2],
        )
        previous = latest.get(family_key)
        if previous is None or (
            _value(raw, "available_at"),
            _value(raw, "created_at"),
            _value(raw, "event_id"),
        ) > (
            _value(previous[1], "available_at"),
            _value(previous[1], "created_at"),
            _value(previous[1], "event_id"),
        ):
            latest[family_key] = family, raw

    out = []
    for family_key in sorted(latest):
        family, raw = latest[family_key]
        if (
            family not in held
            and not _day0_event_is_current_for_entry(
                {"city": family[0], "target_date": family[1]},
                decision_at_utc=decision_at_utc,
                city_configs=city_configs,
            )
        ):
            continue
        row = _row_dict(cur, raw)
        try:
            out.append(
                OpportunityEvent(
                    **{
                        field: row[field]
                        for field in OpportunityEvent.__dataclass_fields__
                    }
                )
            )
        except (KeyError, TypeError):
            continue
    return tuple(out)


def scan_current_global_auction_scope(
    *,
    world_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    decision_at_utc: datetime,
    held_families: Sequence[tuple[str, str, str]] = (),
    restrict_to_families: Sequence[tuple[str, str, str]] | None = None,
    day0_only: bool = False,
) -> CurrentGlobalAuctionScope:
    """Read the current decision scope and its held-family obligations."""

    if decision_at_utc.tzinfo is None:
        raise ValueError("decision_at_utc must be timezone-aware")
    held = tuple(
        sorted(
            {
                (
                    str(city or "").strip(),
                    str(target_date or "").strip(),
                    str(metric or "").strip().lower(),
                )
                for city, target_date, metric in held_families
            }
        )
    )
    restricted = (
        frozenset(
            (
                str(city or "").strip(),
                str(target_date or "").strip(),
                str(metric or "").strip().lower(),
            )
            for city, target_date, metric in restrict_to_families
        )
        if restrict_to_families is not None
        else None
    )
    if restricted is not None and (
        not restricted
        or any(
            not city or not target_date or metric not in {"high", "low"}
            for city, target_date, metric in restricted
        )
    ):
        raise ValueError("GLOBAL_AUCTION_RESTRICTED_FAMILY_INVALID")
    if day0_only and restricted is None:
        raise ValueError("GLOBAL_DAY0_ONLY_SCOPE_REQUIRES_FAMILY_RESTRICTION")
    # A producer wake may restrict which new BUY families need rebuilding, but
    # it cannot restrict the already-held SELL obligations.  The auction owns
    # every runtime-open holding on every epoch, so its read scope is the union.
    scope_held = held
    scoped_families = (
        None
        if restricted is None
        else set(restricted).union(scope_held)
    )
    forecast_events = ()
    if not day0_only or scope_held:
        trigger = ForecastSnapshotReadyTrigger(
            EventWriter(world_conn),
            live_eligibility_reader=executable_forecast_live_eligible_reader(
                forecasts_conn
            ),
        )
        forecast_events = trigger.build_committed_snapshot_events(
            forecasts_conn=forecasts_conn,
            decision_time=decision_at_utc,
            received_at=decision_at_utc.isoformat(),
            limit=None,
            source="global-auction-current-scope",
            phase_filter_exempt_families=set(scope_held),
            restrict_to_families=(
                scoped_families
            ),
        )
    events = current_global_scope_events_with_day0(
        forecast_events,
        _current_day0_events(
            world_conn,
            decision_at_utc=decision_at_utc,
            held_families=scope_held,
            restrict_to_families=scoped_families,
        ),
    )
    covered = {_event_family(event) for event in events}
    missing = sorted(set(scope_held) - covered)
    if missing:
        detail = ",".join("|".join(family) for family in missing)
        raise ValueError(
            f"GLOBAL_HELD_FAMILY_PROBABILITY_CARRIER_MISSING:{detail}"
        )
    return current_global_auction_scope_from_events(
        events,
        captured_at_utc=decision_at_utc,
    )


def global_universe_witness_from_scope(
    scope: CurrentGlobalAuctionScope,
    *,
    probability_witnesses: Mapping[str, FamilyPayoffWitness],
    venue_universe_identity: str,
    max_age: timedelta,
) -> GlobalAuctionUniverseWitness:
    """Bind an independently enumerated scope to complete family/token witnesses."""

    if set(probability_witnesses) != set(scope.family_keys):
        raise ValueError("GLOBAL_FEASIBLE_SET_INCOMPLETE")
    family_bindings = tuple(
        (
            family_key,
            probability_witnesses[family_key].family_binding_identity,
        )
        for family_key in scope.family_keys
    )
    identity = global_auction_universe_identity(
        family_bindings=family_bindings,
        family_resolution_at_utc=scope.family_resolution_at_utc,
        venue_universe_identity=venue_universe_identity,
        captured_at_utc=scope.captured_at_utc,
    )
    return GlobalAuctionUniverseWitness(
        family_bindings=family_bindings,
        family_resolution_at_utc=scope.family_resolution_at_utc,
        venue_universe_identity=venue_universe_identity,
        captured_at_utc=scope.captured_at_utc,
        max_age=max_age,
        witness_identity=identity,
    )


def current_venue_auction_identity(
    trade_conn: sqlite3.Connection,
    *,
    probability_witnesses: Mapping[str, FamilyPayoffWitness],
) -> str:
    """Hash current executable/non-executable state for every bound native token."""

    if not _table_exists(trade_conn, "executable_market_snapshots"):
        raise ValueError("GLOBAL_VENUE_SNAPSHOT_SCHEMA_MISSING")
    rows = []
    for family_key, witness in sorted(probability_witnesses.items()):
        if family_key != witness.family_key:
            raise ValueError("GLOBAL_VENUE_PROBABILITY_FAMILY_MISMATCH")
        for binding in witness.bindings:
            for side, token in (
                ("YES", binding.yes_token_id),
                ("NO", binding.no_token_id),
            ):
                token_id = str(token or "").strip()
                if not token_id:
                    rows.append(
                        (family_key, binding.bin_id, binding.condition_id, side, "", "MISSING")
                    )
                    continue
                cur = trade_conn.execute(
                    "SELECT * FROM executable_market_snapshots "
                    "WHERE condition_id=? AND selected_outcome_token_id=? "
                    "ORDER BY captured_at DESC,snapshot_id DESC LIMIT 1",
                    (binding.condition_id, token_id),
                )
                raw = cur.fetchone()
                if raw is None:
                    state = ("MISSING",)
                else:
                    item = _row_dict(cur, raw)
                    state = tuple(
                        item.get(field)
                        for field in (
                            "snapshot_id",
                            "raw_orderbook_hash",
                            "executable_book_hash",
                            "snapshot_hash",
                            "captured_at",
                            "freshness_deadline",
                            "active",
                            "closed",
                            "accepting_orders",
                            "enable_orderbook",
                            "tradeability_status_json",
                            "orderbook_depth_json",
                            "orderbook_depth_jsonb",
                        )
                    )
                rows.append(
                    (
                        family_key,
                        binding.bin_id,
                        binding.condition_id,
                        side,
                        token_id,
                        state,
                    )
                )
    if not rows:
        raise ValueError("GLOBAL_VENUE_UNIVERSE_EMPTY")
    return hashlib.sha256(repr(tuple(rows)).encode("utf-8")).hexdigest()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _row_dict(cur: sqlite3.Cursor, row: object) -> dict[str, object]:
    names = [description[0] for description in cur.description or ()]
    if isinstance(row, sqlite3.Row):
        return {name: row[name] for name in names}
    return dict(zip(names, row))  # type: ignore[arg-type]


def _position_token(position: object) -> str:
    raw_direction = getattr(position, "direction", "")
    direction = str(getattr(raw_direction, "value", raw_direction) or "").lower()
    if direction == "buy_no":
        token = getattr(position, "no_token_id", "")
    elif direction == "buy_yes":
        token = getattr(position, "token_id", "")
    else:
        return ""
    return str(token or "").strip()


def probe_inflight_buy_ambiguity(
    trade_conn: sqlite3.Connection,
) -> bool | None:
    """Reject only in-flight BUY cash effects lacking a persisted command bound."""

    execute = getattr(trade_conn, "execute", None)
    if execute is None:
        return None
    try:
        return _inflight_buy_wealth_bounds(trade_conn) is None
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise


def _inflight_buy_wealth_bounds(
    trade_conn: sqlite3.Connection,
) -> tuple[int, Decimal, tuple[tuple[str, str, int, str], ...]] | None:
    """Bound pending BUYs by reserved cash below and persisted order shares above."""
    pending = trade_conn.execute(
        """
        SELECT EXISTS(
                   SELECT 1
                     FROM collateral_reservations
                    WHERE reservation_type = 'PUSD_BUY'
                      AND released_at IS NULL
               )
            OR EXISTS(
                   SELECT 1
                     FROM collateral_unsettled_proceeds
                    WHERE direction = 'OUTGOING_DEDUCTION'
                      AND settled_at IS NULL
               )
        """
    ).fetchone()
    if not bool((pending or (0,))[0]):
        return 0, Decimal("0"), ()
    if not _table_exists(trade_conn, "venue_commands"):
        return None

    rows = trade_conn.execute(
        """
        SELECT r.command_id,
               r.amount AS amount_micro,
               vc.size,
               vc.side,
               vc.intent_kind,
               'RESERVATION' AS basis
          FROM collateral_reservations r
          LEFT JOIN venue_commands vc ON vc.command_id = r.command_id
         WHERE r.reservation_type = 'PUSD_BUY'
           AND r.released_at IS NULL
        UNION ALL
        SELECT u.command_id,
               u.amount_micro,
               vc.size,
               vc.side,
               vc.intent_kind,
               'OUTGOING_DEDUCTION' AS basis
          FROM collateral_unsettled_proceeds u
          LEFT JOIN venue_commands vc ON vc.command_id = u.command_id
         WHERE u.direction = 'OUTGOING_DEDUCTION'
           AND u.settled_at IS NULL
        """
    ).fetchall()
    if not rows:
        return 0, Decimal("0"), ()

    cash_micro = 0
    upper = Decimal("0")
    identities: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()
    for row in rows:
        command_id = str(row[0] or "").strip()
        try:
            amount_micro = int(row[1])
            size = Decimal(str(row[2]))
        except (TypeError, ValueError, InvalidOperation):
            return None
        side = str(row[3] or "").strip().upper()
        intent_kind = str(row[4] or "").strip().upper()
        basis = str(row[5] or "").strip().upper()
        if (
            not command_id
            or command_id in seen
            or amount_micro < 0
            or not size.is_finite()
            or size <= 0
            or side != "BUY"
            or intent_kind != "ENTRY"
        ):
            return None
        seen.add(command_id)
        cash_micro += amount_micro
        amount_usd = Decimal(amount_micro) / Decimal("1000000")
        upper += max(amount_usd, size)
        identities.append((command_id, basis, amount_micro, str(size)))
    return cash_micro, upper, tuple(sorted(identities))


def _pending_entry_endowments(
    trade_conn: sqlite3.Connection,
    *,
    positions: tuple[object, ...],
    native_holdings_micro: Mapping[str, int],
) -> tuple[
    tuple[tuple[str, str, int], ...],
    tuple[tuple[str, ...], ...],
    Mapping[str, int],
    frozenset[str],
]:
    """Return committed BUY exposure absent from the current native balance.

    ``position.shares`` can advance before ``chain_shares`` and an admitted
    command can precede both.  The endowment therefore takes the larger of the
    local fill-projection gap and still-open obligations for each token.  A
    An OPEN obligation remains non-sellable endowment until its own durable
    lifecycle is RESOLVED. A later chain timestamp alone cannot prove that an
    incremental fill is included in an unchanged aggregate balance.
    """

    rows = trade_conn.execute(
        """
        SELECT obligation.command_id,
               obligation.status,
               obligation.token_id,
               obligation.shares,
               obligation.cost_basis_usd,
               obligation.unbounded,
               obligation.created_at,
               command.position_id,
               command.token_id,
               command.side,
               command.size,
               command.price,
               command.intent_kind,
               command.state,
               MAX(
                   CASE WHEN event.event_type = 'FILL_CONFIRMED'
                        THEN event.occurred_at END
               ) AS fill_confirmed_at
          FROM entry_exposure_obligations obligation
          LEFT JOIN venue_commands command
            ON command.command_id = obligation.command_id
          LEFT JOIN venue_command_events event
            ON event.command_id = obligation.command_id
         GROUP BY obligation.command_id
         ORDER BY obligation.command_id
        """
    ).fetchall()

    positions_by_id: dict[str, object] = {}
    projected_by_token: dict[str, int] = {}
    for position in positions:
        position_id = str(
            getattr(position, "position_id", "")
            or getattr(position, "trade_id", "")
            or ""
        ).strip()
        token = _position_token(position)
        if position_id:
            positions_by_id[position_id] = position
        if not token:
            continue
        try:
            shares = Decimal(str(getattr(position, "shares", 0) or 0))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError("CURRENT_WEALTH_POSITION_PROJECTION_INVALID") from exc
        if not shares.is_finite() or shares < 0:
            raise ValueError("CURRENT_WEALTH_POSITION_PROJECTION_INVALID")
        projected_by_token[token] = projected_by_token.get(token, 0) + int(
            (shares * Decimal("1000000")).to_integral_value()
        )

    open_by_token: dict[str, list[tuple[str, int, int]]] = {}
    identities: list[tuple[str, ...]] = []
    obligation_ids: set[str] = set()
    for row in rows:
        command_id = str(row[0] or "").strip()
        status = str(row[1] or "").strip().upper()
        obligation_token = str(row[2] or "").strip()
        command_position_id = str(row[7] or "").strip()
        command_token = str(row[8] or "").strip()
        side = str(row[9] or "").strip().upper()
        intent_kind = str(row[12] or "").strip().upper()
        command_state = str(row[13] or "").strip().upper()
        fill_confirmed_at_raw = str(row[14] or "").strip()
        try:
            shares = Decimal(str(row[3]))
            cost = Decimal(str(row[4]))
            command_size = Decimal(str(row[10]))
            command_price = Decimal(str(row[11]))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError("CURRENT_WEALTH_ENTRY_OBLIGATION_INVALID") from exc
        if (
            not command_id
            or command_id in obligation_ids
            or status not in {"OPEN", "RESOLVED"}
            or not obligation_token
            or obligation_token != command_token
            or not command_position_id
            or side != "BUY"
            or intent_kind != "ENTRY"
            or bool(row[5])
            or not all(
                value.is_finite()
                for value in (shares, cost, command_size, command_price)
            )
            or shares <= 0
            or cost < 0
            or command_size <= 0
            or command_price <= 0
            or shares - command_size > Decimal("0.000001")
        ):
            raise ValueError("CURRENT_WEALTH_ENTRY_OBLIGATION_INVALID")
        obligation_ids.add(command_id)
        shares_micro = int((shares * Decimal("1000000")).to_integral_value())
        cost_micro = int((cost * Decimal("1000000")).to_integral_value())
        if shares_micro <= 0 or cost_micro < 0:
            raise ValueError("CURRENT_WEALTH_ENTRY_OBLIGATION_INVALID")

        position = positions_by_id.get(command_position_id)
        chain_seen_raw = str(
            getattr(position, "chain_verified_at", "") if position is not None else ""
        ).strip()
        represented = status == "RESOLVED"

        classification = "represented" if represented else "pending"
        identities.append(
            (
                command_id,
                status,
                obligation_token,
                str(shares),
                str(cost),
                command_position_id,
                command_state,
                fill_confirmed_at_raw,
                chain_seen_raw,
                classification,
            )
        )
        if status == "OPEN" and not represented:
            open_by_token.setdefault(obligation_token, []).append(
                (command_id, shares_micro, cost_micro)
            )

    pending: list[tuple[str, str, int]] = []
    pending_cost_micro: dict[str, int] = {}
    for token in sorted(set(projected_by_token) | set(open_by_token)):
        native = int(native_holdings_micro.get(token, 0))
        projection_gap = max(projected_by_token.get(token, 0) - native, 0)
        # Sub-cent-share projection dust is below the engine's smallest native
        # order quantum and must not override the exact micro-unit chain balance.
        if projection_gap < 10_000:
            projection_gap = 0
        obligations = open_by_token.get(token, [])
        obligation_total = sum(shares for _, shares, _ in obligations)
        for command_id, shares_micro, cost_micro in obligations:
            pending.append((command_id, token, shares_micro))
            pending_cost_micro[command_id] = cost_micro
        if projection_gap > obligation_total:
            pending.append(
                (
                    f"position_projection:{token}",
                    token,
                    projection_gap - obligation_total,
                )
            )

    return (
        tuple(sorted(pending)),
        tuple(sorted(identities)),
        pending_cost_micro,
        frozenset(obligation_ids),
    )


def current_portfolio_wealth_witness(
    trade_conn: sqlite3.Connection,
    *,
    decision_at_utc: datetime,
    max_age: timedelta,
    portfolio_state: object | None = None,
) -> PortfolioWealthWitness:
    """Build one current terminal-wealth bound from chain collateral and positions.

    The lower bound is current chain cash. The upper bound adds every verified
    binary claim and every unresolved local claim at its maximum $1 payoff.
    An unresolved claim is never spendable cash: representing only its maximum
    terminal payoff makes new-order growth no less conservative without turning
    one confirmed-fill dispute into a portfolio-wide veto. In-flight buys use
    their durable cash reservation as the loss bound and their persisted command
    size as the maximum $1 claim; missing command bounds still fail closed.
    """

    if decision_at_utc.tzinfo is None or max_age <= timedelta(0):
        raise ValueError("CURRENT_WEALTH_TIME_CONTRACT_INVALID")
    required = {
        "collateral_ledger_snapshots",
        "collateral_reservations",
        "collateral_unsettled_proceeds",
        "entry_exposure_obligations",
        "venue_commands",
        "venue_command_events",
    }
    if not all(_table_exists(trade_conn, table) for table in required):
        raise ValueError("CURRENT_WEALTH_LEDGER_SCHEMA_MISSING")

    owns_txn = not trade_conn.in_transaction
    if owns_txn:
        trade_conn.execute("BEGIN")
    try:
        # DEGRADED rows describe a failed refresh attempt, not authoritative
        # zero collateral. Match CollateralLedger.refresh() fallback semantics:
        # reuse the newest trusted snapshot only while this caller's freshness
        # contract allows it.
        cur = trade_conn.execute(
            "SELECT * FROM collateral_ledger_snapshots "
            "WHERE authority_tier IN ('CHAIN', 'VENUE') "
            "ORDER BY id DESC LIMIT 1"
        )
        raw_row = cur.fetchone()
        if raw_row is None:
            latest = trade_conn.execute(
                "SELECT authority_tier FROM collateral_ledger_snapshots "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if (
                latest is not None
                and str(latest[0] or "").strip().upper() == "DEGRADED"
            ):
                raise ValueError("CURRENT_WEALTH_COLLATERAL_DEGRADED")
            raise ValueError("CURRENT_WEALTH_COLLATERAL_MISSING")
        row = _row_dict(cur, raw_row)
        authority = str(row.get("authority_tier") or "DEGRADED").strip().upper()
        if authority not in {"CHAIN", "VENUE"}:
            raise ValueError("CURRENT_WEALTH_COLLATERAL_DEGRADED")
        try:
            captured_at = datetime.fromisoformat(
                str(row.get("captured_at") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("CURRENT_WEALTH_CAPTURE_TIME_INVALID") from exc
        if captured_at.tzinfo is None:
            raise ValueError("CURRENT_WEALTH_CAPTURE_TIME_INVALID")
        captured_at = captured_at.astimezone(timezone.utc)
        age = decision_at_utc.astimezone(timezone.utc) - captured_at
        if age.total_seconds() < 0.0 or age > max_age:
            raise ValueError("CURRENT_WEALTH_COLLATERAL_EXPIRED")

        inflight_bounds = _inflight_buy_wealth_bounds(trade_conn)
        if inflight_bounds is None:
            raise ValueError("CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS")
        inflight_cash_micro, _inflight_upper_usd, inflight_identities = inflight_bounds

        if portfolio_state is None:
            from src.state.portfolio import load_runtime_open_portfolio

            portfolio_state = load_runtime_open_portfolio(trade_conn)
        if (
            str(getattr(portfolio_state, "authority", "") or "") != "canonical_db"
            or str(getattr(portfolio_state, "authority_scope", "") or "")
            != "runtime_exposure"
        ):
            raise ValueError("CURRENT_WEALTH_POSITION_AUTHORITY_MISSING")

        try:
            token_balances_raw = json.loads(
                str(row.get("ctf_token_balances_json") or "{}")
            )
        except json.JSONDecodeError as exc:
            raise ValueError("CURRENT_WEALTH_TOKEN_BALANCES_INVALID") from exc
        if not isinstance(token_balances_raw, dict):
            raise ValueError("CURRENT_WEALTH_TOKEN_BALANCES_INVALID")
        token_balances = {
            str(token): int(amount or 0)
            for token, amount in token_balances_raw.items()
            if int(amount or 0) > 0
        }

        positions = tuple(getattr(portfolio_state, "positions", ()) or ())
        if tuple(getattr(portfolio_state, "chain_only_facts", ()) or ()):
            raise ValueError("CURRENT_WEALTH_UNKNOWN_CHAIN_INVENTORY")
        from src.contracts.position_truth import has_current_money_risk_chain_state
        from src.state.chain_reconciliation import _CHAIN_SEEN_AT_MAX_AGE_SECONDS
        from src.state.portfolio import has_verified_trade_fill

        position_max_age = timedelta(seconds=_CHAIN_SEEN_AT_MAX_AGE_SECONDS)
        represented_micro: dict[str, int] = {}
        uncertain_micro: dict[str, int] = {}
        native_commitments_micro: dict[str, int] = {}
        position_rows = []
        for position in positions:
            token = _position_token(position)
            if not token:
                raise ValueError("CURRENT_WEALTH_OPEN_POSITION_INVALID")
            chain_state = str(
                getattr(
                    getattr(position, "chain_state", ""),
                    "value",
                    getattr(position, "chain_state", ""),
                )
                or ""
            ).strip()
            if token in token_balances:
                micro = token_balances[token]
                shares = Decimal(micro) / Decimal("1000000")
                evidence = "collateral_snapshot"
            else:
                shares = Decimal(str(getattr(position, "chain_shares", 0) or 0))
                if shares <= 0 and has_verified_trade_fill(position):
                    shares = Decimal(str(getattr(position, "shares", 0) or 0))
                if shares <= 0:
                    raise ValueError("CURRENT_WEALTH_OPEN_POSITION_INVALID")
                micro = int((shares * Decimal("1000000")).to_integral_value())
                evidence = "uncertain_local_claim"
                try:
                    chain_verified_at = datetime.fromisoformat(
                        str(getattr(position, "chain_verified_at", "") or "").replace(
                            "Z", "+00:00"
                        )
                    )
                except ValueError:
                    chain_verified_at = None
                if (
                    chain_verified_at is not None
                    and chain_verified_at.tzinfo is not None
                    and has_current_money_risk_chain_state(chain_state)
                ):
                    chain_verified_at = chain_verified_at.astimezone(timezone.utc)
                    chain_age = (
                        decision_at_utc.astimezone(timezone.utc) - chain_verified_at
                    )
                    if 0.0 <= chain_age.total_seconds() <= position_max_age.total_seconds():
                        evidence = "position_chain_seen"
            target = represented_micro if evidence != "uncertain_local_claim" else uncertain_micro
            target[token] = target.get(token, 0) + micro
            try:
                cost = max(
                    Decimal(
                        str(
                            getattr(position, name, 0)
                            or 0
                        )
                    )
                    for name in (
                        "effective_cost_basis_usd",
                        "chain_cost_basis_usd",
                        "cost_basis_usd",
                        "size_usd",
                    )
                )
                basis_shares = max(
                    Decimal(str(getattr(position, name, 0) or 0))
                    for name in ("effective_shares", "chain_shares", "shares")
                )
                if cost <= 0:
                    price = max(
                        Decimal(str(getattr(position, name, 0) or 0))
                        for name in (
                            "entry_price_avg_fill",
                            "chain_avg_price",
                            "entry_price",
                        )
                    )
                    cost = basis_shares * price
            except (TypeError, ValueError, InvalidOperation) as exc:
                raise ValueError("CURRENT_WEALTH_POSITION_COST_INVALID") from exc
            if (
                not cost.is_finite()
                or not basis_shares.is_finite()
                or cost <= 0
                or basis_shares <= 0
            ):
                raise ValueError("CURRENT_WEALTH_POSITION_COST_INVALID")
            # A venue-confirmed fill owns capital immediately, even while the
            # slower chain-balance projection still reports the pre-fill share
            # count. Prorating its verified open cost by that stale balance
            # re-minted the same family Fractional-Kelly budget after every
            # fast FAK fill. Keep sellable inventory on the chain-observed
            # share count, but charge the full verified open cost to BUY
            # allocation until chain reconciliation catches up.
            if has_verified_trade_fill(position):
                represented_cost = cost
            else:
                represented_cost = cost * min(
                    shares / basis_shares,
                    Decimal("1"),
                )
            cost_micro = int(
                (represented_cost * Decimal("1000000")).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            if cost_micro <= 0:
                raise ValueError("CURRENT_WEALTH_POSITION_COST_INVALID")
            native_commitments_micro[token] = (
                native_commitments_micro.get(token, 0) + cost_micro
            )
            position_rows.append(
                (
                    str(getattr(position, "trade_id", "") or ""),
                    token,
                    str(shares),
                    chain_state,
                    str(getattr(position, "state", "") or ""),
                    evidence,
                    cost_micro,
                )
            )
        if token_balances:
            if set(token_balances) - set(represented_micro):
                raise ValueError("CURRENT_WEALTH_CHAIN_POSITION_SET_MISMATCH")
            if any(
                abs(represented_micro[token] - token_balances[token]) > 1
                for token in token_balances
            ):
                raise ValueError("CURRENT_WEALTH_CHAIN_POSITION_SIZE_MISMATCH")
        held_balances = represented_micro
        native_holdings = dict(held_balances)
        for token, amount in uncertain_micro.items():
            native_holdings[token] = native_holdings.get(token, 0) + amount
        (
            pending_endowments,
            obligation_identities,
            pending_cost_micro,
            obligation_ids,
        ) = _pending_entry_endowments(
            trade_conn,
            positions=positions,
            native_holdings_micro=native_holdings,
        )
        inflight_command_ids = {identity[0] for identity in inflight_identities}
        if not inflight_command_ids.issubset(obligation_ids):
            raise ValueError("CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS")
        uncovered_pending_cash_micro = sum(
            amount
            for command_id, amount in pending_cost_micro.items()
            if command_id not in inflight_command_ids
        )
        pending_token_by_id = {
            command_id: token
            for command_id, token, _ in pending_endowments
            if command_id in pending_cost_micro
        }
        for command_id, amount in pending_cost_micro.items():
            token = pending_token_by_id.get(command_id)
            if token is None or amount <= 0:
                raise ValueError("CURRENT_WEALTH_ENTRY_OBLIGATION_COST_INVALID")
            native_commitments_micro[token] = (
                native_commitments_micro.get(token, 0) + amount
            )

        pusd_micro = int(row.get("pusd_balance_micro") or 0)
        allowance_micro = int(row.get("pusd_allowance_micro") or 0)
        legacy_micro = int(row.get("usdc_e_legacy_balance_micro") or 0)
        cash_at_risk_micro = inflight_cash_micro + uncovered_pending_cash_micro
        spendable_micro = pusd_micro - cash_at_risk_micro
        if spendable_micro < 0:
            raise ValueError("CURRENT_WEALTH_SPENDABLE_CASH_INVALID")
        # Allowance is submit-time permission, not owned cash.  The executor
        # refreshes and checks it against the exact order notional immediately
        # before persistence or SDK contact; selection keeps the wallet's pUSD
        # balance as its cash endowment instead of erasing every BUY on one
        # transient zero-allowance snapshot.
        floor = (Decimal(spendable_micro) + Decimal(legacy_micro)) / Decimal(
            "1000000"
        )
        ceiling = floor + sum(
            (Decimal(amount) / Decimal("1000000") for amount in held_balances.values()),
            Decimal("0"),
        )
        ceiling += sum(
            (Decimal(amount) / Decimal("1000000") for amount in uncertain_micro.values()),
            Decimal("0"),
        )
        ceiling += sum(
            (
                Decimal(amount) / Decimal("1000000")
                for _, _, amount in pending_endowments
            ),
            Decimal("0"),
        )
        spendable = Decimal(spendable_micro) / Decimal("1000000")
        reservations = Decimal(cash_at_risk_micro) / Decimal("1000000")

        ledger_snapshot_id = hashlib.sha256(
            repr(
                (
                    int(row.get("id") or 0),
                    row.get("raw_balance_payload_hash"),
                    captured_at.isoformat(),
                    authority,
                    pusd_micro,
                    allowance_micro,
                    legacy_micro,
                    tuple(sorted(token_balances.items())),
                    inflight_identities,
                    tuple(sorted(pending_endowments)),
                    tuple(sorted(native_commitments_micro.items())),
                )
            ).encode("utf-8")
        ).hexdigest()
        position_set_hash = hashlib.sha256(
            repr(
                (
                    tuple(sorted(position_rows)),
                    tuple(sorted(held_balances.items())),
                    tuple(sorted(uncertain_micro.items())),
                    obligation_identities,
                )
            ).encode("utf-8")
        ).hexdigest()
        identity = portfolio_wealth_identity(
            ledger_snapshot_id=ledger_snapshot_id,
            position_set_hash=position_set_hash,
            wealth_floor_usd=floor,
            wealth_ceiling_usd=ceiling,
            spendable_cash_usd=spendable,
            reservations_usd=reservations,
            collateral_authority=authority,
            captured_at_utc=captured_at,
        )
        return PortfolioWealthWitness(
            ledger_snapshot_id=ledger_snapshot_id,
            position_set_hash=position_set_hash,
            wealth_floor_usd=floor,
            wealth_ceiling_usd=ceiling,
            spendable_cash_usd=spendable,
            reservations_usd=reservations,
            collateral_authority=authority,
            captured_at_utc=captured_at,
            max_age=max_age,
            witness_identity=identity,
            native_holdings_micro=tuple(sorted(native_holdings.items())),
            pending_entry_endowments_micro=pending_endowments,
            native_commitments_micro=tuple(
                sorted(native_commitments_micro.items())
            ),
        )
    finally:
        if owns_txn and trade_conn.in_transaction:
            trade_conn.rollback()
