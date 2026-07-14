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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
    CurrentExecutionAuthority,
    ExecutableSellCurve,
    GlobalAuctionUniverseWitness,
    JointOutcomeProbabilityWitness,
    OutcomeTokenBinding,
    PortfolioWealthWitness,
    executable_curve_identity,
    global_auction_universe_identity,
    joint_probability_witness_identity,
    portfolio_wealth_identity,
)
from src.state.snapshot_repo import snapshot_row_is_invalidated


@dataclass(frozen=True)
class CurrentGlobalAuctionScope:
    """Complete probability-ready family set at one decision instant."""

    events_by_family: tuple[tuple[str, OpportunityEvent], ...]
    scope_identity: str
    captured_at_utc: datetime

    def __post_init__(self) -> None:
        rows = tuple(sorted(self.events_by_family, key=lambda item: item[0]))
        keys = tuple(family_key for family_key, _ in rows)
        expected_keys = tuple(_event_family_key(event) for _, event in rows)
        if (
            not rows
            or len(set(keys)) != len(keys)
            or keys != expected_keys
            or not all(family_key and event.event_id for family_key, event in rows)
            or self.captured_at_utc.tzinfo is None
        ):
            raise ValueError("current global auction scope is incomplete or ambiguous")
        expected = current_global_auction_scope_identity(
            tuple(event for _, event in rows)
        )
        if self.scope_identity != expected:
            raise ValueError("current global auction scope identity mismatch")
        object.__setattr__(self, "events_by_family", rows)

    @property
    def family_keys(self) -> tuple[str, ...]:
        return tuple(family_key for family_key, _ in self.events_by_family)

    @property
    def events(self) -> tuple[OpportunityEvent, ...]:
        return tuple(event for _, event in self.events_by_family)


@dataclass(frozen=True)
class CurrentGlobalBookAsset:
    """One current side-native ask ladder in a complete venue epoch."""

    family_key: str
    bin_id: str
    condition_id: str
    market_event_id: str
    side: str
    token_id: str
    curve: ExecutableCostCurve
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
    """Every bound YES/NO book observed in one bounded public-CLOB sweep."""

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
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    clean = tuple(dict.fromkeys(str(value or "").strip() for value in condition_ids))
    clean = tuple(value for value in clean if value)
    for offset in range(0, len(clean), 400):
        chunk = clean[offset : offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        # Current CLOB responses own price/depth for this epoch.  Project only
        # topology/execution metadata here; ``s.*`` also loads the append row's
        # large historical depth payload and can outlive the quote it supports.
        cur = trade_conn.execute(
            f"""
            SELECT s.snapshot_id,
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
        rows.extend(_row_dict(cur, row) for row in cur.fetchall())
    return rows


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
    raw_hash = str(raw_book.get("hash") or "").strip()
    if not raw_hash:
        raw_hash = hashlib.sha256(
            json.dumps(raw_book, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
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
    raw_hash = str(raw_book.get("hash") or "").strip()
    if not raw_hash:
        raw_hash = hashlib.sha256(
            json.dumps(raw_book, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
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


def capture_current_global_book_epoch(
    trade_conn: sqlite3.Connection,
    *,
    probability_witnesses: Mapping[str, JointOutcomeProbabilityWitness],
    get_books: Callable[[list[str]], Mapping[str, Mapping[str, object]]],
    clock: Callable[[], datetime],
    max_age: timedelta,
    batch_size: int = 500,
    book_fetch_workers: int = 1,
    metadata_overrides: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> CurrentGlobalBookEpoch:
    """Fetch every bound native book; no missing token may shrink the feasible set."""

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
        for binding in witness.bindings:
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

    started_at = clock()
    if started_at.tzinfo is None:
        raise ValueError("GLOBAL_BOOK_CLOCK_INVALID")
    started_at = started_at.astimezone(timezone.utc)
    tokens = [row[4] for row in bindings]
    chunks = [
        tokens[offset : offset + batch_size]
        for offset in range(0, len(tokens), batch_size)
    ]
    books: dict[str, Mapping[str, object]] = {}

    def _merge_batch(batch: Mapping[str, Mapping[str, object]]) -> None:
        if not isinstance(batch, Mapping):
            raise ValueError("GLOBAL_BOOK_BATCH_RESPONSE_INVALID")
        books.update(
            {
                str(token): raw
                for token, raw in batch.items()
                if isinstance(raw, Mapping)
            }
        )

    if len(chunks) == 1 or book_fetch_workers == 1:
        for chunk in chunks:
            _merge_batch(get_books(chunk))
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(
            max_workers=min(book_fetch_workers, len(chunks))
        ) as executor:
            futures = [executor.submit(get_books, chunk) for chunk in chunks]
            for future in futures:
                _merge_batch(future.result())
    finished_at = clock()
    if finished_at.tzinfo is None:
        raise ValueError("GLOBAL_BOOK_CLOCK_INVALID")
    finished_at = finished_at.astimezone(timezone.utc)
    if finished_at < started_at or finished_at - started_at > max_age:
        raise ValueError("GLOBAL_BOOK_CAPTURE_WINDOW_EXPIRED")
    missing_books = [token for token in tokens if token not in books]
    if missing_books:
        raise ValueError(f"GLOBAL_BOOK_RESPONSE_INCOMPLETE:{len(missing_books)}")

    metadata_rows = _global_book_snapshot_rows(
        trade_conn,
        condition_ids=[row[2] for row in bindings],
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
    assets: list[CurrentGlobalBookAsset] = []
    sell_assets: list[CurrentGlobalSellAsset] = []
    states: list[tuple[str, ...]] = []
    for family_key, bin_id, condition_id, side, token_id in bindings:
        metadata = metadata_by_key.get((condition_id, token_id))
        if metadata is None:
            raise ValueError(f"GLOBAL_BOOK_METADATA_MISSING:{condition_id}:{token_id}")
        if not metadata.get("_global_current_gamma") and snapshot_row_is_invalidated(
            trade_conn,
            metadata,
            checked_at=started_at,
        ):
            raise ValueError(f"GLOBAL_BOOK_METADATA_INVALIDATED:{condition_id}:{token_id}")
        raw_book = books[token_id]
        raw_asset_id = str(
            raw_book.get("asset_id")
            or raw_book.get("assetId")
            or raw_book.get("token_id")
            or ""
        ).strip()
        if raw_asset_id != token_id:
            raise ValueError(f"GLOBAL_BOOK_TOKEN_MISMATCH:{token_id}")
        market_event_id = str(metadata.get("event_id") or "").strip()
        if not market_event_id:
            raise ValueError(
                f"GLOBAL_BOOK_MARKET_EVENT_ID_MISSING:{condition_id}:{token_id}"
            )
        book_hash = str(raw_book.get("hash") or "").strip() or hashlib.sha256(
            json.dumps(raw_book, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        tradeability = {}
        try:
            tradeability = json.loads(
                str(metadata.get("tradeability_status_json") or "{}")
            )
        except json.JSONDecodeError:
            pass
        metadata_current = _global_book_metadata_is_current(
            metadata,
            checked_at_utc=started_at,
        )
        executable_metadata = metadata_current and (
            bool(metadata.get("enable_orderbook"))
            and bool(metadata.get("active"))
            and not bool(metadata.get("closed"))
            and bool(metadata.get("accepting_orders"))
            and not (
                isinstance(tradeability, Mapping)
                and tradeability.get("executable_allowed") is False
            )
        )
        curve = None
        sell_curve = None
        status = (
            "VENUE_NOT_EXECUTABLE"
            if metadata_current
            else "VENUE_METADATA_STALE"
        )
        if executable_metadata:
            curve = _global_book_curve(
                family_key=family_key,
                bin_id=bin_id,
                condition_id=condition_id,
                side=side,
                token_id=token_id,
                raw_book=raw_book,
                metadata=metadata,
                captured_at_utc=started_at,
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
                captured_at_utc=started_at,
                max_age=max_age,
            )
            status = "EXECUTABLE" if curve is not None else "NO_ASK"
        states.append(
            (
                family_key,
                bin_id,
                condition_id,
                side,
                token_id,
                status,
                book_hash,
                market_event_id,
            )
        )
        if curve is not None:
            assets.append(
                CurrentGlobalBookAsset(
                    family_key=family_key,
                    bin_id=bin_id,
                    condition_id=condition_id,
                    market_event_id=market_event_id,
                    side=side,
                    token_id=token_id,
                    curve=curve,
                    captured_at_utc=started_at,
                )
            )
        if sell_curve is not None:
            sell_assets.append(
                CurrentGlobalSellAsset(
                    family_key=family_key,
                    bin_id=bin_id,
                    condition_id=condition_id,
                    market_event_id=market_event_id,
                    side=side,
                    token_id=token_id,
                    curve=sell_curve,
                    captured_at_utc=started_at,
                )
            )
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


def _rebind_probability_witness_tokens(
    witness: JointOutcomeProbabilityWitness,
    *,
    token_map_by_condition: Mapping[str, tuple[str, str]],
) -> JointOutcomeProbabilityWitness:
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
    identity = joint_probability_witness_identity(
        family_key=witness.family_key,
        bindings=rebound,
        q_version=witness.q_version,
        resolution_identity=witness.resolution_identity,
        topology_identity=witness.topology_identity,
        posterior_identity_hash=witness.posterior_identity_hash,
        source_truth_identity=witness.source_truth_identity,
        authority_certificate_hash=witness.authority_certificate_hash,
        band_alpha=witness.band_alpha,
        band_basis=witness.band_basis,
        yes_q_samples=witness.yes_q_samples,
        captured_at_utc=witness.captured_at_utc,
    )
    return JointOutcomeProbabilityWitness(
        family_key=witness.family_key,
        bindings=rebound,
        yes_q_samples=witness.yes_q_samples,
        q_version=witness.q_version,
        resolution_identity=witness.resolution_identity,
        topology_identity=witness.topology_identity,
        posterior_identity_hash=witness.posterior_identity_hash,
        source_truth_identity=witness.source_truth_identity,
        authority_certificate_hash=witness.authority_certificate_hash,
        band_alpha=witness.band_alpha,
        band_basis=witness.band_basis,
        captured_at_utc=witness.captured_at_utc,
        max_age=witness.max_age,
        witness_identity=identity,
    )


def fetch_current_gamma_markets(
    condition_ids: Sequence[str],
    *,
    gamma_get: Callable[..., object],
    timeout: float,
    chunk_size: int = 100,
    max_workers: int = 16,
) -> tuple[tuple[Mapping[str, object], ...], int]:
    """Fetch one complete current Gamma market batch or fail closed."""

    from concurrent.futures import ThreadPoolExecutor, as_completed

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

    def _fetch(chunk: Sequence[str]) -> tuple[Mapping[str, object], ...]:
        response = gamma_get(
            "/markets",
            params={"condition_ids": list(chunk), "limit": len(chunk)},
            timeout=timeout,
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

    if len(chunks) == 1:
        return _fetch(chunks[0]), 1
    markets: list[Mapping[str, object]] = []
    workers = max(1, min(int(max_workers), len(chunks)))
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="global-market-metadata",
    ) as pool:
        futures = tuple(pool.submit(_fetch, chunk) for chunk in chunks)
        for future in as_completed(futures):
            markets.extend(future.result())
    return tuple(markets), len(chunks)


def bind_current_global_probability_tokens(
    forecasts_conn: sqlite3.Connection,
    *,
    probability_witnesses: Mapping[str, JointOutcomeProbabilityWitness],
    get_gamma_event: Callable[[str], Mapping[str, object] | None] | None = None,
    get_gamma_markets: Callable[
        [Sequence[str]], Sequence[Mapping[str, object]]
    ]
    | None = None,
    trade_conn: sqlite3.Connection | None = None,
    checked_at_utc: datetime | None = None,
    max_workers: int = 8,
    metadata_sink: dict[tuple[str, str], Mapping[str, object]] | None = None,
) -> Mapping[str, JointOutcomeProbabilityWitness]:
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
    if trade_conn is not None:
        if checked_at_utc is None or checked_at_utc.tzinfo is None:
            raise ValueError("GLOBAL_LOCAL_TOKEN_CHECK_TIME_INVALID")
        checked_at_utc = checked_at_utc.astimezone(timezone.utc)
        condition_ids = tuple(
            binding.condition_id
            for witness in missing_by_family.values()
            for binding in witness.bindings
        )
        for row in _global_book_snapshot_rows(
            trade_conn,
            condition_ids=condition_ids,
        ):
            condition_id = str(row.get("condition_id") or "").strip()
            yes = str(row.get("yes_token_id") or "").strip()
            no = str(row.get("no_token_id") or "").strip()
            if not condition_id or not yes or not no:
                continue
            try:
                captured_at = datetime.fromisoformat(
                    str(row.get("captured_at") or "").replace("Z", "+00:00")
                )
                freshness_deadline = datetime.fromisoformat(
                    str(row.get("freshness_deadline") or "").replace(
                        "Z", "+00:00"
                    )
                )
            except (TypeError, ValueError):
                continue
            if captured_at.tzinfo is None or freshness_deadline.tzinfo is None:
                continue
            captured_at = captured_at.astimezone(timezone.utc)
            freshness_deadline = freshness_deadline.astimezone(timezone.utc)
            if not captured_at <= checked_at_utc <= freshness_deadline:
                continue
            pair = (yes, no)
            previous = local_tokens.get(condition_id)
            if previous is not None and previous != pair:
                raise ValueError(
                    f"GLOBAL_LOCAL_TOKEN_IDENTITY_AMBIGUOUS:{condition_id}"
                )
            local_tokens[condition_id] = pair

    from concurrent.futures import ThreadPoolExecutor
    from src.data.market_scanner import _boolish_market_field, _extract_outcomes

    events: dict[str, Mapping[str, object] | None] = {}
    if refresh_metadata and get_gamma_markets is not None:
        condition_ids = tuple(
            dict.fromkeys(
                binding.condition_id
                for witness in work_by_family.values()
                for binding in witness.bindings
            )
        )
        requested_conditions = frozenset(condition_ids)
        def _market_map(
            rows: Sequence[Mapping[str, object]],
            expected: frozenset[str],
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
            if missing:
                raise ValueError(
                    "GLOBAL_CURRENT_GAMMA_MARKETS_INCOMPLETE:"
                    + ",".join(sorted(missing))
                )
            return out

        market_by_condition = _market_map(
            get_gamma_markets(condition_ids), requested_conditions
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

        for family_key, witness in work_by_family.items():
            family_condition_ids = tuple(
                binding.condition_id for binding in witness.bindings
            )
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
        for family_key, witness in work_by_family.items():
            tokens_bound = all(
                (binding.yes_token_id and binding.no_token_id)
                or binding.condition_id in local_tokens
                for binding in witness.bindings
            )
            if tokens_bound and not refresh_metadata:
                continue
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
                raise ValueError(f"GLOBAL_GAMMA_SLUG_MISSING:{family_key}")
            slug_by_family[family_key] = slug
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

    rebound: dict[str, JointOutcomeProbabilityWitness] = {}
    for family_key, witness in probability_witnesses.items():
        if family_key not in work_by_family:
            rebound[family_key] = witness
            continue
        event = events.get(family_key)
        if refresh_metadata and event is None:
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
                            "event_id": str(
                                event.get("id")
                                or event.get("event_id")
                                or event.get("slug")
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


def _event_family(event: OpportunityEvent) -> tuple[str, str, str]:
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("global universe event payload is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("global universe event payload must be an object")
    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    metric = str(payload.get("metric") or "").strip().lower()
    if not city or not target_date or metric not in {"high", "low"}:
        raise ValueError("global universe event lacks a weather family identity")
    return city, target_date, metric


def _event_probability_identity(event: OpportunityEvent) -> str:
    payload = json.loads(event.payload_json)
    return str(
        payload.get("source_run_id")
        or payload.get("snapshot_hash")
        or event.causal_snapshot_id
        or ""
    ).strip()


def _event_family_key(event: OpportunityEvent) -> str:
    city, target_date, metric = _event_family(event)
    return weather_family_id(
        city=city,
        target_date=target_date,
        metric=metric,
    )


def current_global_auction_scope_identity(
    events: Sequence[OpportunityEvent],
) -> str:
    """Hash the complete family set and each family's current probability carrier."""

    rows = []
    for event in events:
        family_key = _event_family_key(event)
        probability_identity = _event_probability_identity(event)
        if not probability_identity:
            raise ValueError("global universe event lacks probability identity")
        rows.append((family_key, probability_identity))
    rows.sort()
    if not rows or len({family_key for family_key, _ in rows}) != len(rows):
        raise ValueError("global universe must contain one event per family")
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(row).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def current_global_auction_scope_from_events(
    events: Sequence[OpportunityEvent],
    *,
    captured_at_utc: datetime,
) -> CurrentGlobalAuctionScope:
    rows = []
    for event in events:
        rows.append((_event_family_key(event), event))
    return CurrentGlobalAuctionScope(
        events_by_family=tuple(rows),
        scope_identity=current_global_auction_scope_identity(events),
        captured_at_utc=captured_at_utc,
    )


def current_global_scope_events_with_day0(
    forecast_events: Sequence[OpportunityEvent],
    day0_events: Sequence[OpportunityEvent],
) -> tuple[OpportunityEvent, ...]:
    """Let the latest current Day0 fact replace the forecast carrier per family."""

    by_family = {_event_family_key(event): event for event in forecast_events}
    for event in day0_events:
        family_key = _event_family_key(event)
        previous = by_family.get(family_key)
        if previous is None or (event.available_at, event.created_at, event.event_id) > (
            previous.available_at,
            previous.created_at,
            previous.event_id,
        ):
            by_family[family_key] = event
    return tuple(by_family[key] for key in sorted(by_family))


def _day0_event_is_current_for_entry(
    payload: Mapping[str, object],
    *,
    decision_at_utc: datetime,
) -> bool:
    """Admit a Day0 fact only on its target city's current local day."""

    if decision_at_utc.tzinfo is None:
        return False
    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    if not city or not target_date:
        return False
    from src.config import runtime_cities_by_name

    city_config = runtime_cities_by_name().get(city)
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
) -> tuple[OpportunityEvent, ...]:
    if not _table_exists(world_conn, "opportunity_events"):
        return ()
    from src.events.day0_authority import normalize_day0_live_authority_status

    utc_date = decision_at_utc.astimezone(timezone.utc).date()
    target_floor = (utc_date - timedelta(days=1)).isoformat()
    target_ceiling = (utc_date + timedelta(days=1)).isoformat()
    cur = world_conn.execute(
        "SELECT * FROM opportunity_events "
        "INDEXED BY idx_opportunity_events_fsr_target_date "
        "WHERE event_type='DAY0_EXTREME_UPDATED' "
        "AND json_extract(payload_json, '$.target_date') BETWEEN ? AND ? "
        "AND available_at<=? AND received_at<=?",
        (
            target_floor,
            target_ceiling,
            decision_at_utc.isoformat(),
            decision_at_utc.isoformat(),
        ),
    )
    out = {}
    for raw in cur.fetchall():
        row = _row_dict(cur, raw)
        try:
            event = OpportunityEvent(
                **{
                    field: row[field]
                    for field in OpportunityEvent.__dataclass_fields__
                }
            )
            payload = json.loads(event.payload_json)
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if not _day0_event_is_current_for_entry(
            payload,
            decision_at_utc=decision_at_utc,
        ):
            continue
        if not (
            payload.get("source_match_status") == "MATCH"
            and payload.get("local_date_status") == "MATCH"
            and payload.get("station_match_status") == "MATCH"
            and payload.get("dst_status") == "UNAMBIGUOUS"
            and payload.get("metric_match_status") == "MATCH"
            and payload.get("rounding_status") == "MATCH"
            and payload.get("source_authorized_status", "AUTHORIZED") == "AUTHORIZED"
            and normalize_day0_live_authority_status(
                payload.get("live_authority_status")
            )
            == "live"
        ):
            continue
        expires_at = event.expires_at
        if expires_at:
            try:
                expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expires.tzinfo is None or expires.astimezone(timezone.utc) < decision_at_utc:
                continue
        family_key = _event_family_key(event)
        previous = out.get(family_key)
        if previous is None or (
            event.available_at,
            event.created_at,
            event.event_id,
        ) > (
            previous.available_at,
            previous.created_at,
            previous.event_id,
        ):
            out[family_key] = event
    return tuple(out[key] for key in sorted(out))


def scan_current_global_auction_scope(
    *,
    world_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    decision_at_utc: datetime,
) -> CurrentGlobalAuctionScope:
    """Read every current family independently of queue pagination or fairness caps."""

    if decision_at_utc.tzinfo is None:
        raise ValueError("decision_at_utc must be timezone-aware")
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
    )
    events = current_global_scope_events_with_day0(
        forecast_events,
        _current_day0_events(world_conn, decision_at_utc=decision_at_utc),
    )
    return current_global_auction_scope_from_events(
        events,
        captured_at_utc=decision_at_utc,
    )


def global_universe_witness_from_scope(
    scope: CurrentGlobalAuctionScope,
    *,
    probability_witnesses: Mapping[str, JointOutcomeProbabilityWitness],
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
        venue_universe_identity=venue_universe_identity,
        captured_at_utc=scope.captured_at_utc,
    )
    return GlobalAuctionUniverseWitness(
        family_bindings=family_bindings,
        venue_universe_identity=venue_universe_identity,
        captured_at_utc=scope.captured_at_utc,
        max_age=max_age,
        witness_identity=identity,
    )


def current_venue_auction_identity(
    trade_conn: sqlite3.Connection,
    *,
    probability_witnesses: Mapping[str, JointOutcomeProbabilityWitness],
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
    direction = str(getattr(position, "direction", "") or "").lower()
    token = (
        getattr(position, "no_token_id", "")
        if direction == "buy_no"
        else getattr(position, "token_id", "")
    )
    return str(token or "").strip()


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
    one confirmed-fill dispute into a portfolio-wide veto. Unknown chain assets
    and in-flight buys still fail closed because their size or cash effect is not
    bounded by the canonical open-position projection.
    """

    if decision_at_utc.tzinfo is None or max_age <= timedelta(0):
        raise ValueError("CURRENT_WEALTH_TIME_CONTRACT_INVALID")
    required = {
        "collateral_ledger_snapshots",
        "collateral_reservations",
        "collateral_unsettled_proceeds",
    }
    if not all(_table_exists(trade_conn, table) for table in required):
        raise ValueError("CURRENT_WEALTH_LEDGER_SCHEMA_MISSING")

    owns_txn = not trade_conn.in_transaction
    if owns_txn:
        trade_conn.execute("BEGIN")
    try:
        cur = trade_conn.execute(
            "SELECT * FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1"
        )
        raw_row = cur.fetchone()
        if raw_row is None:
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

        reserved_row = trade_conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM collateral_reservations "
            "WHERE reservation_type='PUSD_BUY' AND released_at IS NULL"
        ).fetchone()
        unsettled_row = trade_conn.execute(
            "SELECT COALESCE(SUM(amount_micro),0) FROM collateral_unsettled_proceeds "
            "WHERE direction='OUTGOING_DEDUCTION' AND settled_at IS NULL"
        ).fetchone()
        reserved_micro = int((reserved_row or (0,))[0] or 0)
        unsettled_micro = int((unsettled_row or (0,))[0] or 0)
        if reserved_micro > 0 or unsettled_micro > 0:
            raise ValueError("CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS")

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

        position_max_age = timedelta(seconds=_CHAIN_SEEN_AT_MAX_AGE_SECONDS)
        represented_micro: dict[str, int] = {}
        uncertain_micro: dict[str, int] = {}
        position_rows = []
        for position in positions:
            token = _position_token(position)
            chain_state = str(
                getattr(
                    getattr(position, "chain_state", ""),
                    "value",
                    getattr(position, "chain_state", ""),
                )
                or ""
            ).strip()
            shares = Decimal(str(getattr(position, "chain_shares", 0) or 0))
            if not token or shares <= 0:
                raise ValueError("CURRENT_WEALTH_OPEN_POSITION_INVALID")
            micro = int((shares * Decimal("1000000")).to_integral_value())
            if token in token_balances:
                evidence = "collateral_snapshot"
            else:
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
            position_rows.append(
                (
                    str(getattr(position, "trade_id", "") or ""),
                    token,
                    str(shares),
                    chain_state,
                    str(getattr(position, "state", "") or ""),
                    evidence,
                )
            )
        if token_balances:
            if set(represented_micro) != set(token_balances):
                raise ValueError("CURRENT_WEALTH_CHAIN_POSITION_SET_MISMATCH")
            if any(
                abs(represented_micro[token] - token_balances[token]) > 1
                for token in represented_micro
            ):
                raise ValueError("CURRENT_WEALTH_CHAIN_POSITION_SIZE_MISMATCH")
            held_balances = token_balances
        else:
            held_balances = represented_micro

        pusd_micro = int(row.get("pusd_balance_micro") or 0)
        allowance_micro = int(row.get("pusd_allowance_micro") or 0)
        legacy_micro = int(row.get("usdc_e_legacy_balance_micro") or 0)
        spendable_micro = min(pusd_micro, allowance_micro)
        if spendable_micro <= 0:
            raise ValueError("CURRENT_WEALTH_NO_SPENDABLE_CASH")
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
        spendable = Decimal(spendable_micro) / Decimal("1000000")
        reservations = Decimal(reserved_micro + unsettled_micro) / Decimal("1000000")

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
                )
            ).encode("utf-8")
        ).hexdigest()
        position_set_hash = hashlib.sha256(
            repr(
                (
                    tuple(sorted(position_rows)),
                    tuple(sorted(held_balances.items())),
                    tuple(sorted(uncertain_micro.items())),
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
        )
    finally:
        if owns_txn and trade_conn.in_transaction:
            trade_conn.rollback()
