"""Runtime ownership for one current cross-family auction epoch."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import sqlite3
import time
import zlib
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence

from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
from src.data.market_topology_rows import prime_frozen_schema_reads
from src.engine.global_auction_universe import (
    CurrentGlobalBookEpoch,
    current_global_auction_scope_from_events,
    current_portfolio_wealth_witness,
    current_venue_auction_identity,
    scan_current_global_auction_scope,
)
from src.engine.global_single_order_auction import (
    global_single_order_actuation_identity,
    select_prepared_global_auction,
)
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt, GlobalBatchSubmitResult
from src.solve.solver import CurrentFamilyProbabilityAuthority
from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS

UTC = timezone.utc
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class GlobalWinnerPreflight:
    """Typed, venue-side-effect-free binding of one selected winner."""

    status: str
    binding_token: object | None = None
    replacement_candidate: object | None = None
    probability_tightening: "GlobalCandidateProbabilityTightening | None" = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in {
            "STABLE",
            "CURVE_SUPERSEDED",
            "PROBABILITY_TIGHTENED",
            "CANDIDATE_BLOCKED",
            "BLOCKED",
            "BATCH_BLOCKED",
        }:
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_STATUS_INVALID")
        if (self.status == "STABLE") != (self.binding_token is not None):
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_TOKEN_INVALID")
        if (self.status == "CURVE_SUPERSEDED") != (
            self.replacement_candidate is not None
        ):
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_REPLACEMENT_INVALID")
        if (self.status == "PROBABILITY_TIGHTENED") != (
            self.probability_tightening is not None
        ):
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_Q_TIGHTENING_INVALID")
        if self.status != "STABLE" and not str(self.reason or "").strip():
            raise ValueError("GLOBAL_WINNER_PREFLIGHT_REASON_MISSING")


@dataclass(frozen=True)
class GlobalCandidateProbabilityTightening:
    """A candidate-local executable q bound discovered by winner preflight."""

    family_key: str
    bin_id: str
    side: str
    token_id: str
    probability_witness_identity: str
    payoff_q_lcb: float

    def __post_init__(self) -> None:
        if (
            not all(
                str(value or "").strip()
                for value in (
                    self.family_key,
                    self.bin_id,
                    self.token_id,
                    self.probability_witness_identity,
                )
            )
            or self.side not in {"YES", "NO"}
            or not 0.0 <= float(self.payoff_q_lcb) <= 1.0
        ):
            raise ValueError("GLOBAL_CANDIDATE_Q_TIGHTENING_INVALID")

    @property
    def candidate_key(self) -> tuple[str, str, str, str]:
        return self.family_key, self.bin_id, self.side, self.token_id


@dataclass(frozen=True)
class GlobalPreflightAuthority:
    """Frozen whole-universe authority carried by one one-shot preflight."""

    probability_manifest: tuple[tuple[str, str], ...]
    book_epoch_identity: str
    book_economics_manifest: tuple[tuple[object, ...], ...]
    wealth_witness_identity: str
    actuation_deadline: datetime

    def __post_init__(self) -> None:
        if (
            not self.probability_manifest
            or not self.book_epoch_identity
            or not self.book_economics_manifest
            or not self.wealth_witness_identity
            or self.actuation_deadline.tzinfo is None
        ):
            raise ValueError("GLOBAL_PREFLIGHT_AUTHORITY_INCOMPLETE")


class GlobalOneShotActuator:
    """Consume exactly one final-actuation capability for one batch."""

    def __init__(self, callback: Callable[..., EventSubmissionReceipt]) -> None:
        self._callback = callback
        self._consumed = False

    def consume(self, *args) -> EventSubmissionReceipt:
        if self._consumed:
            raise RuntimeError("GLOBAL_ACTUATION_CAPABILITY_CONSUMED")
        self._consumed = True
        return self._callback(*args)


def _bind_selection_holdings(
    prepared_by_event: Mapping[str, object],
    *,
    portfolio_state: object,
    ledger_snapshot_id: str,
) -> dict[str, object]:
    """Bind every family holding to the same selection-time ledger generation."""

    from src.solve.menu_adapter import native_holdings_snapshot_from_positions

    positions = tuple(getattr(portfolio_state, "positions", ()) or ())
    rebound: dict[str, object] = {}
    for event_id, prepared in prepared_by_event.items():
        witness = getattr(prepared, "probability_witness", None)
        family_key = str(getattr(witness, "family_key", "") or "")
        bindings = tuple(getattr(witness, "bindings", ()) or ())
        if not family_key or not bindings:
            raise ValueError("GLOBAL_HOLDINGS_PROBABILITY_BINDING_MISSING")
        holdings = native_holdings_snapshot_from_positions(
            family_key=family_key,
            omega=SimpleNamespace(bins=bindings),
            positions=positions,
            ledger_snapshot_id=ledger_snapshot_id,
        )
        rebound[event_id] = replace(prepared, holdings_snapshot=holdings)
    return rebound


def _probability_manifest(probabilities: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    """Freeze q plus token bindings while allowing only book and wealth to move."""

    return tuple(
        sorted(
            (
                str(family_key),
                str(getattr(witness, "witness_identity", "") or ""),
            )
            for family_key, witness in probabilities.items()
        )
    )


def _store_global_auction_receipt(
    conn,
    *,
    selected: object,
    selection_epoch_identity: str,
    selection_cut_at_utc: datetime,
    decision_at_utc: datetime,
    probability_manifest: tuple[tuple[str, str], ...],
    full_scope_identity: str,
    full_scope_family_keys: Sequence[str],
    probability_ineligible_by_family: Mapping[str, str],
    book_epoch_identity: str,
    book_asset_count: int | None,
    wealth_witness: object,
    fractional_kelly_multiplier: Decimal,
    excluded_by_family: Mapping[str, str] | None = None,
    excluded_by_candidate: Mapping[
        tuple[str, str, str, str, str], str
    ] | None = None,
) -> int | None:
    """Persist one complete auction comparison before any venue side effect."""

    if not isinstance(conn, sqlite3.Connection):
        return None
    from src.state.decision_chain import CycleArtifact, store_artifact

    scope_keys = tuple(str(key) for key in full_scope_family_keys)
    probability_keys = tuple(str(key) for key, _ in probability_manifest)
    ineligible = dict(
        sorted(
            (str(key), str(reason))
            for key, reason in probability_ineligible_by_family.items()
        )
    )
    scope_key_set = set(scope_keys)
    probability_key_set = set(probability_keys)
    ineligible_key_set = set(ineligible)
    scope_coverage_complete = (
        bool(str(full_scope_identity or "").strip())
        and len(scope_keys) == len(scope_key_set)
        and len(probability_keys) == len(probability_key_set)
        and not probability_key_set.intersection(ineligible_key_set)
        and scope_key_set == probability_key_set.union(ineligible_key_set)
        and all(reason.strip() for reason in ineligible.values())
    )
    if not scope_coverage_complete:
        raise ValueError("GLOBAL_AUCTION_RECEIPT_SCOPE_INCOMPLETE")

    decision = getattr(selected, "decision", None)
    if decision is None:
        raise ValueError("GLOBAL_AUCTION_RECEIPT_DECISION_MISSING")
    evaluations = tuple(getattr(decision, "candidate_evaluations", ()) or ())
    evaluation_rows = tuple(asdict(evaluation) for evaluation in evaluations)
    rejection_groups: dict[tuple[str, str, str], list[str]] = {}
    detailed_rows: list[dict] = []
    for row in evaluation_rows:
        if row.get("status") == "REJECTED" and row.get("action") == "BUY":
            key = (
                str(row["action"]),
                str(row["side"]),
                str(row["rejection_reason"]),
            )
            rejection_groups.setdefault(key, []).append(str(row["candidate_id"]))
        else:
            detailed_rows.append(row)
    buy_condition_masks: dict[str, int] = {}
    for row in evaluation_rows:
        if row.get("action") != "BUY":
            continue
        side_mask = 1 if row.get("side") == "YES" else 2
        condition_id = str(row["condition_id"])
        buy_condition_masks[condition_id] = (
            buy_condition_masks.get(condition_id, 0) | side_mask
        )
    compact_evaluations = {
        "rejected_groups": [
            {
                "action": action,
                "side": side,
                "reason": reason,
                "candidate_ids": candidate_ids,
            }
            for (action, side, reason), candidate_ids in sorted(
                rejection_groups.items()
            )
        ],
        "detailed": detailed_rows,
        "buy_condition_side_masks": sorted(buy_condition_masks.items()),
    }
    evaluation_json = json.dumps(
        compact_evaluations,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    evaluation_zlib = zlib.compress(evaluation_json, level=9)
    candidate_ids = tuple(
        str(row.get("candidate_id") or "") for row in evaluation_rows
    )
    condition_ids = tuple(
        str(row.get("condition_id") or "") for row in evaluation_rows
    )
    selected_rows = tuple(
        row for row in evaluation_rows if row.get("status") == "SELECTED"
    )
    winner = getattr(decision, "candidate", None)
    winner_id = str(getattr(winner, "candidate_id", "") or "")
    candidate_input_count = getattr(decision, "candidate_input_count", None)
    condition_index_complete = all(condition_ids) and all(
        row.get("action") != "BUY" or row.get("side") in {"YES", "NO"}
        for row in evaluation_rows
    )
    coverage_complete = (
        candidate_input_count is not None
        and len(evaluation_rows) == candidate_input_count
        and len(candidate_ids) == len(set(candidate_ids))
        and all(candidate_ids)
        and condition_index_complete
        and len(selected_rows) == (1 if winner is not None else 0)
        and (
            winner is None
            or str(selected_rows[0].get("candidate_id") or "") == winner_id
        )
    )
    receipt = {
        "schema_version": 7,
        "selection_epoch_identity": selection_epoch_identity,
        "selection_cut_at_utc": selection_cut_at_utc.isoformat(),
        "decision_at_utc": decision_at_utc.isoformat(),
        "probability_manifest": probability_manifest,
        "full_scope_identity": full_scope_identity,
        "full_scope_family_count": len(scope_keys),
        "eligible_probability_family_count": len(probability_keys),
        "probability_ineligible_family_count": len(ineligible),
        "probability_ineligible_by_family": ineligible,
        "scope_family_coverage_complete": scope_coverage_complete,
        "book_epoch_identity": book_epoch_identity,
        "book_asset_count": book_asset_count,
        "excluded_by_family": dict(sorted((excluded_by_family or {}).items())),
        "excluded_by_candidate": [
            {
                "action": key[0],
                "family_key": key[1],
                "bin_id": key[2],
                "side": key[3],
                "token_id": key[4],
                "reason": reason,
            }
            for key, reason in sorted((excluded_by_candidate or {}).items())
        ],
        "wealth_witness_identity": str(
            getattr(wealth_witness, "witness_identity", "") or ""
        ),
        "wealth_economic_identity": str(
            getattr(wealth_witness, "economic_identity", "") or ""
        ),
        "fractional_kelly_multiplier": str(fractional_kelly_multiplier),
        "hold_cash": {
            "robust_delta_log_wealth": "0",
            "robust_ev_usd": "0",
            "selected": winner is None,
        },
        "winner_candidate_id": winner_id or None,
        "no_trade_reason": getattr(decision, "no_trade_reason", None),
        "candidate_evaluation_count": len(evaluation_rows),
        "candidate_input_count": candidate_input_count,
        "candidate_detailed_count": len(detailed_rows),
        "candidate_rejection_group_count": len(rejection_groups),
        "candidate_coverage_complete": coverage_complete,
        "candidate_condition_index_complete": condition_index_complete,
        "buy_condition_membership_count": sum(
            1 + (mask == 3) for mask in buy_condition_masks.values()
        ),
        "candidate_evaluation_encoding": "zlib+base64+canonical-json-v4",
        "candidate_evaluations_sha256": hashlib.sha256(
            evaluation_json
        ).hexdigest(),
        "candidate_evaluations_zlib_b64": base64.b64encode(
            evaluation_zlib
        ).decode("ascii"),
    }
    encoded = json.dumps(
        receipt,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt["receipt_hash"] = hashlib.sha256(encoded).hexdigest()
    row_id = store_artifact(
        conn,
        CycleArtifact(
            mode="global_single_order_auction",
            started_at=selection_cut_at_utc.isoformat(),
            completed_at=decision_at_utc.isoformat(),
            skipped_reason=str(getattr(decision, "no_trade_reason", "") or ""),
            summary=receipt,
        ),
    )
    if row_id is None:
        raise RuntimeError("GLOBAL_AUCTION_RECEIPT_ID_MISSING")
    _LOG.info(
        "global auction receipt persisted: row_id=%s epoch=%s candidates=%d "
        "coverage_complete=%s bytes=%d compressed_bytes=%d receipt_hash=%s",
        row_id,
        selection_epoch_identity,
        len(evaluation_rows),
        coverage_complete,
        len(evaluation_json),
        len(evaluation_zlib),
        receipt["receipt_hash"],
    )
    return row_id


def _book_economics_manifest(
    book_epoch: CurrentGlobalBookEpoch,
) -> tuple[tuple[object, ...], ...]:
    """Compare the complete native YES/NO economy without evidence carriers."""

    rows = []
    for asset in book_epoch.assets:
        curve = asset.curve
        rows.append(
            (
                asset.family_key,
                asset.bin_id,
                asset.condition_id,
                asset.market_event_id,
                asset.side,
                asset.token_id,
                str(curve.fee_model.fee_rate),
                str(curve.min_tick),
                str(curve.min_order_size),
                tuple((str(level.price), str(level.size)) for level in curve.levels),
            )
        )
    for asset in getattr(book_epoch, "sell_assets", ()):
        curve = asset.curve
        rows.append(
            (
                "SELL",
                asset.family_key,
                asset.bin_id,
                asset.condition_id,
                asset.market_event_id,
                asset.side,
                asset.token_id,
                str(curve.fee_model.fee_rate),
                str(curve.min_tick),
                str(curve.min_order_size),
                tuple((str(level.price), str(level.size)) for level in curve.levels),
            )
        )
    manifest = tuple(sorted(rows, key=repr))
    if not manifest:
        raise ValueError("GLOBAL_BOOK_ECONOMICS_MISSING")
    return manifest


def _begin_selection_read_snapshot(
    connections: Sequence[sqlite3.Connection],
) -> Callable[[], None]:
    """Own one frozen read view for selection; reject caller-owned transactions."""

    owned: list[sqlite3.Connection] = []
    seen: set[int] = set()
    try:
        for conn in connections:
            identity = id(conn)
            if identity in seen:
                continue
            seen.add(identity)
            if not isinstance(conn, sqlite3.Connection):
                raise TypeError("GLOBAL_SELECTION_SNAPSHOT_CONNECTION_INVALID")
            if conn.in_transaction:
                raise RuntimeError("GLOBAL_SELECTION_SNAPSHOT_CALLER_TXN_OPEN")
            conn.execute("BEGIN")
            owned.append(conn)
            # A deferred transaction does not acquire its read view until the first
            # statement. Establish every authority view before the cut is named.
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except Exception:
        for conn in reversed(owned):
            conn.rollback()
        raise

    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        for conn in reversed(owned):
            conn.rollback()

    return release


def _current_probability_ineligible(receipt: EventSubmissionReceipt) -> bool:
    """A typed ValueError means this family has no current q certificate."""

    return (
        receipt.prepared_global_family is None
        and str(receipt.reason or "").startswith(
            "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
        )
    )


def _family_key(event: OpportunityEvent, payload: Mapping[str, object]) -> str:
    return weather_family_id(
        city=str(payload.get("city") or ""),
        target_date=str(payload.get("target_date") or ""),
        metric=str(payload.get("metric") or "").lower(),
    )


def _forecast_carrier_matches(
    event: OpportunityEvent,
    payload: Mapping[str, object],
    witness: object,
) -> bool:
    """Bind forecast-scope identity to the exact prepared posterior carrier."""

    if event.event_type != "FORECAST_SNAPSHOT_READY":
        return True
    carrier = str(
        payload.get("source_run_id") or payload.get("snapshot_hash") or ""
    ).strip()
    return bool(carrier) and carrier == str(
        getattr(witness, "posterior_identity_hash", "") or ""
    ).strip()


def _selection_epoch_identity(
    *,
    full_scope: CurrentGlobalAuctionScope,
    eligible_scope: CurrentGlobalAuctionScope,
    probability_witnesses: Mapping[str, object],
    ineligible_by_family: Mapping[str, str],
) -> str:
    """Bind the full cut, its executable q manifest, and every typed exclusion."""

    digest = hashlib.sha256()
    rows = (
        ("cut_at", full_scope.captured_at_utc.isoformat()),
        ("full_scope", full_scope.scope_identity),
        ("eligible_scope", eligible_scope.scope_identity),
    )
    for row in rows:
        digest.update(repr(row).encode("utf-8"))
        digest.update(b"\x1f")
    for family_key in full_scope.family_keys:
        witness = probability_witnesses.get(family_key)
        row = (
            family_key,
            str(getattr(witness, "witness_identity", "") or ""),
            str(getattr(witness, "q_version", "") or ""),
            str(getattr(witness, "posterior_identity_hash", "") or ""),
            str(ineligible_by_family.get(family_key) or ""),
        )
        if witness is None and not row[-1]:
            raise ValueError("GLOBAL_SELECTION_EPOCH_FAMILY_UNACCOUNTED")
        digest.update(repr(row).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def _selection_epoch_identity_with_preflight_exclusions(
    selection_epoch_identity: str,
    excluded_by_family: Mapping[str, str],
    excluded_by_candidate: Mapping[
        tuple[str, str, str, str, str], str
    ] | None = None,
    payoff_q_lcb_by_candidate: Mapping[tuple[str, str, str, str], float]
    | None = None,
) -> str:
    """Bind every candidate-local preflight refinement into re-auction."""

    digest = hashlib.sha256()
    digest.update(str(selection_epoch_identity or "").encode("utf-8"))
    digest.update(b"\x1f")
    for family_key, reason in sorted(excluded_by_family.items()):
        digest.update(repr((family_key, reason)).encode("utf-8"))
        digest.update(b"\x1f")
    for candidate_key, reason in sorted((excluded_by_candidate or {}).items()):
        digest.update(repr((*candidate_key, reason)).encode("utf-8"))
        digest.update(b"\x1f")
    for candidate_key, q_lcb in sorted((payoff_q_lcb_by_candidate or {}).items()):
        digest.update(repr((*candidate_key, float(q_lcb))).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def _next_claim_carrier(
    event: OpportunityEvent,
    *,
    targeted_at: datetime,
    economic_identity: str,
    payload: Mapping[str, object],
) -> OpportunityEvent:
    """Create a fresh event identity for one selected current family fact."""

    stamp = targeted_at.astimezone(UTC).isoformat()
    identity = str(economic_identity or "").strip()
    if not identity:
        raise ValueError("GLOBAL_WINNER_ACTUATION_IDENTITY_MISSING")
    return make_opportunity_event(
        event_type=event.event_type,
        entity_key=event.entity_key,
        source=f"global_auction_winner_target:{event.event_id}:{identity}",
        observed_at=event.observed_at,
        available_at=event.available_at,
        received_at=stamp,
        causal_snapshot_id=event.causal_snapshot_id,
        payload=payload,
        priority=event.priority,
        expires_at=event.expires_at,
        created_at=stamp,
    )


def process_current_global_batch(
    events: Sequence[OpportunityEvent],
    *,
    decision_time: datetime,
    world_conn,
    forecast_conn,
    trade_conn,
    payload_reader: Callable[[OpportunityEvent], Mapping[str, object]],
    prepare_event: Callable[[OpportunityEvent, datetime], EventSubmissionReceipt],
    actuate_winner: Callable[[OpportunityEvent, object, datetime], EventSubmissionReceipt],
    stamp_receipt: Callable[[EventSubmissionReceipt], EventSubmissionReceipt],
    venue_submit_count: Callable[[], int],
    current_execution: Callable[[object, datetime], object | None],
    current_time_provider: Callable[[], datetime],
    preflight_winner: Callable[
        [OpportunityEvent, object, datetime, GlobalPreflightAuthority],
        GlobalWinnerPreflight,
    ]
    | None = None,
    actuate_preflighted_winner: GlobalOneShotActuator | None = None,
    portfolio_state_provider: Callable[[], object] | None = None,
    current_book_epoch_provider: Callable[
        [Mapping[str, object], datetime],
        tuple[Mapping[str, object], CurrentGlobalBookEpoch],
    ]
    | None = None,
    selection_snapshot_connections: Sequence[sqlite3.Connection] = (),
    current_capital_limit_resolver: Callable[[object, str, str], object]
    | None = None,
    candidate_policy_rejection_resolver: Callable[[object], str | None]
    | None = None,
    fractional_kelly_multiplier: Decimal = Decimal("1"),
    claim_unpaged_winner: Callable[[OpportunityEvent], bool] | None = None,
) -> GlobalBatchSubmitResult:
    """Select once from every family holding a current q certificate."""

    if decision_time.tzinfo is None:
        raise ValueError("GLOBAL_AUCTION_DECISION_TIME_NAIVE")
    decision_time = decision_time.astimezone(UTC)
    event_tuple = tuple(events)
    claimed_target_by_scope_and_economics: dict[
        tuple[str, str], OpportunityEvent
    ] = {}
    release_selection_snapshot: Callable[[], None] = lambda: None
    batch_started = time.monotonic()
    stage_started = batch_started

    def log_stage(stage: str, *, families: int | None = None) -> None:
        nonlocal stage_started
        now = time.monotonic()
        _LOG.info(
            "global batch stage completed: %s elapsed_s=%.3f total_s=%.3f "
            "events=%d families=%s",
            stage,
            now - stage_started,
            now - batch_started,
            len(event_tuple),
            families if families is not None else "unknown",
        )
        stage_started = now

    def log_no_trade(stage: str, decision: object) -> None:
        counts: dict[str, int] = {}
        for reason in getattr(decision, "rejection_reasons", {}).values():
            key = str(reason or "unknown")
            counts[key] = counts.get(key, 0) + 1
        _LOG.info(
            "global batch no-trade detail: stage=%s reason=%s rejections=%s",
            stage,
            str(getattr(decision, "no_trade_reason", "") or "unknown"),
            dict(sorted(counts.items())),
        )

    def log_winner(
        stage: str,
        selected: object,
        witnesses: Mapping[str, object],
    ) -> None:
        decision = getattr(selected, "decision", None)
        candidate = getattr(decision, "candidate", None)
        if candidate is None:
            return
        family_key = str(getattr(candidate, "family_key", "") or "")
        bin_id = str(getattr(candidate, "bin_id", "") or "")
        side = str(getattr(candidate, "side", "") or "")
        if not family_key or not bin_id or side not in {"YES", "NO"}:
            return
        witness = witnesses.get(family_key)
        q_mean = None
        if witness is not None:
            try:
                column = tuple(witness.bin_ids).index(bin_id)
                yes = witness.yes_q_samples[:, column]
                q_mean = float(yes.mean())
                if side == "NO":
                    q_mean = 1.0 - q_mean
            except (AttributeError, TypeError, ValueError):
                q_mean = None
        _LOG.info(
            "global batch winner detail: stage=%s family=%s bin=%s side=%s "
            "condition=%s token=%s "
            "q_mean=%s shares=%s cost_usd=%s fill_price=%s limit_price=%s "
            "max_spend_usd=%s win_probability_lcb=%s loss_probability_ucb=%s "
            "ev_diagnostic_usd=%.6f robust_dlog=%.12f "
            "capital_efficiency=%.12f candidate=%s",
            stage,
            family_key,
            bin_id,
            side,
            getattr(candidate, "condition_id", "unknown"),
            getattr(candidate, "token_id", "unknown"),
            "unknown" if q_mean is None else f"{q_mean:.9f}",
            getattr(decision, "shares", "unknown"),
            getattr(decision, "cost_usd", "unknown"),
            getattr(decision, "expected_fill_price_before_fee", "unknown"),
            getattr(decision, "limit_price", "unknown"),
            getattr(decision, "max_spend_usd", "unknown"),
            getattr(
                getattr(decision, "terminal_wealth", None),
                "win_probability_lcb",
                "unknown",
            ),
            getattr(
                getattr(decision, "terminal_wealth", None),
                "loss_probability_ucb",
                "unknown",
            ),
            float(getattr(decision, "robust_ev_usd", 0.0) or 0.0),
            float(getattr(decision, "robust_delta_log_wealth", 0.0) or 0.0),
            float(getattr(decision, "capital_efficiency", 0.0) or 0.0),
            getattr(candidate, "candidate_id", "unknown"),
        )

    def current_time() -> datetime:
        now = current_time_provider()
        if now.tzinfo is None:
            raise ValueError("GLOBAL_AUCTION_CURRENT_TIME_NAIVE")
        now = now.astimezone(UTC)
        if now < decision_time:
            raise ValueError("GLOBAL_AUCTION_CLOCK_REGRESSION")
        return now

    def bind_selected_winner(selected):
        """Bind one selected scope event to a committed claim in this epoch."""

        nonlocal event_tuple
        scope_winner_id = str(getattr(selected, "winner_event_id", "") or "")
        winner = next(
            (event for event in event_tuple if event.event_id == scope_winner_id),
            None,
        )
        if winner is not None:
            return selected, winner, None
        actuation = getattr(selected, "actuation", None)
        if actuation is None:
            raise ValueError("GLOBAL_WINNER_ACTUATION_MISSING")

        def rebound(target):
            rebound_actuation = replace(
                actuation,
                winner_event_id=target.event_id,
                actuation_identity=global_single_order_actuation_identity(
                    decision=actuation.decision,
                    winner_event_id=target.event_id,
                    universe_witness_identity=actuation.universe_witness_identity,
                    wealth_witness_identity=actuation.wealth_witness_identity,
                    selection_epoch_identity=actuation.selection_epoch_identity,
                    selection_cut_at_utc=actuation.selection_cut_at_utc,
                    decision_at_utc=actuation.decision_at_utc,
                ),
            )
            return (
                replace(
                    selected,
                    winner_event_id=target.event_id,
                    actuation=rebound_actuation,
                ),
                target,
                None,
            )

        target_key = (scope_winner_id, str(actuation.economic_identity or ""))
        target = claimed_target_by_scope_and_economics.get(target_key)
        if target is None:
            scope_event = next(
                (
                    event
                    for event in full_scope_event_by_family.values()
                    if event.event_id == scope_winner_id
                ),
                None,
            )
            if scope_event is None:
                return selected, None, None
            carrier_prefix = f"global_auction_winner_target:{scope_event.event_id}:"
            carrier_fields = (
                "event_type",
                "entity_key",
                "observed_at",
                "available_at",
                "causal_snapshot_id",
                "payload_hash",
                "priority",
                "expires_at",
                "payload_json",
                "schema_version",
            )
            target = next(
                (
                    event
                    for event in event_tuple
                    if str(event.source or "").startswith(carrier_prefix)
                    and all(
                        getattr(event, field) == getattr(scope_event, field)
                        for field in carrier_fields
                    )
                ),
                None,
            )
            # The event claim owns the selected source fact; the actuation below owns
            # this epoch's q/book/wealth economics.  Reuse an already-claimed carrier
            # for the exact same causal fact even when those economics have changed.
            # Encoding economic identity into a new carrier on every re-decision made
            # a valid current winner chase an unclaimed event forever.
            if target is not None:
                claimed_target_by_scope_and_economics[target_key] = target
                return rebound(target)
            target = _next_claim_carrier(
                scope_event,
                targeted_at=current_time(),
                economic_identity=actuation.economic_identity,
                payload=payload_reader(scope_event),
            )
            existing_target = next(
                (event for event in event_tuple if event.event_id == target.event_id),
                None,
            )
            if existing_target is not None:
                semantic_fields = (
                    "event_type",
                    "entity_key",
                    "source",
                    "observed_at",
                    "available_at",
                    "causal_snapshot_id",
                    "payload_hash",
                    "idempotency_key",
                    "priority",
                    "expires_at",
                    "payload_json",
                    "schema_version",
                )
                if any(
                    getattr(existing_target, field) != getattr(target, field)
                    for field in semantic_fields
                ):
                    raise ValueError("GLOBAL_WINNER_TARGET_CARRIER_MISMATCH")
                target = existing_target
            else:
                if claim_unpaged_winner is None or not claim_unpaged_winner(target):
                    return selected, None, target
                event_tuple = (*event_tuple, target)
            claimed_target_by_scope_and_economics[target_key] = target
        return rebound(target)

    def reject(
        reason: str,
        *,
        next_claim_event: OpportunityEvent | None = None,
    ) -> GlobalBatchSubmitResult:
        release_selection_snapshot()
        return GlobalBatchSubmitResult(
            receipts={
                event.event_id: stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=reason,
                        proof_accepted=False,
                    )
                )
                for event in event_tuple
            },
            winner_event_id=None,
            venue_submit_count=0,
            next_claim_event=next_claim_event,
        )

    try:
        release_selection_snapshot = _begin_selection_read_snapshot(
            selection_snapshot_connections
        )
        release_schema = prime_frozen_schema_reads(selection_snapshot_connections)
        release_snapshot_only = release_selection_snapshot
        released_schema = False

        def release_schema_snapshot() -> None:
            nonlocal released_schema
            if released_schema:
                return
            released_schema = True
            try:
                release_schema()
            finally:
                release_snapshot_only()

        release_selection_snapshot = release_schema_snapshot
        log_stage("selection_snapshot")
        scope_at = current_time()
        full_scope = scan_current_global_auction_scope(
            world_conn=world_conn,
            forecasts_conn=forecast_conn,
            decision_at_utc=scope_at,
        )
        log_stage("scope_scan", families=len(full_scope.events_by_family))
        from src.data.replacement_input_hwm import (
            prime_frozen_replacement_artifact_hwm,
        )

        release_hwm = prime_frozen_replacement_artifact_hwm(
            forecast_conn,
            requests=(
                (
                    str(payload.get("city") or ""),
                    str(payload.get("target_date") or ""),
                    str(payload.get("metric") or ""),
                )
                for _, event in full_scope.events_by_family
                for payload in (payload_reader(event),)
            ),
            decision_time=scope_at,
        )
        release_read_snapshot = release_selection_snapshot
        released_hwm = False

        def release_primed_snapshot() -> None:
            nonlocal released_hwm
            if released_hwm:
                return
            released_hwm = True
            try:
                release_hwm()
            finally:
                release_read_snapshot()

        release_selection_snapshot = release_primed_snapshot
        claimed_by_family = {}
        duplicate_owner_by_event: dict[str, str] = {}
        for event in event_tuple:
            family_key = _family_key(event, payload_reader(event))
            if family_key in claimed_by_family:
                duplicate_owner_by_event[event.event_id] = claimed_by_family[
                    family_key
                ].event_id
                continue
            claimed_by_family[family_key] = event

        prepared_by_event = {}
        full_scope_event_by_family = dict(full_scope.events_by_family)
        ineligible_by_family: dict[str, str] = {}
        ineligible_by_event: dict[str, str] = {}
        for family_key, scope_event in full_scope.events_by_family:
            owner = claimed_by_family.get(family_key, scope_event)
            prepared_receipt = prepare_event(scope_event, scope_at)
            prepared = prepared_receipt.prepared_global_family
            if prepared is None:
                if _current_probability_ineligible(prepared_receipt):
                    reason = str(prepared_receipt.reason)
                    ineligible_by_family[family_key] = reason
                    if family_key in claimed_by_family:
                        ineligible_by_event[owner.event_id] = reason
                    continue
                return reject(
                    "GLOBAL_PREPARED_FAMILY_INCOMPLETE:"
                    f"{family_key}:{prepared_receipt.reason or 'missing'}"
                )
            if not _forecast_carrier_matches(
                scope_event,
                payload_reader(scope_event),
                prepared.probability_witness,
            ):
                return reject(
                    f"GLOBAL_PROBABILITY_EPOCH_CARRIER_MISMATCH:{family_key}"
                )
            # Queue ownership cannot rename the current probability carrier.  The
            # winner is rebound to a claimed target below; keeping the scope event
            # here makes JIT probability revalidation rebuild the same random
            # variable instead of the stale queue owner's carrier.
            prepared_by_event[scope_event.event_id] = prepared
        log_stage("prepare_families", families=len(prepared_by_event))
        if not prepared_by_event:
            return reject("GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY")

        eligible_family_keys = frozenset(
            prepared.probability_witness.family_key
            for prepared in prepared_by_event.values()
        )
        scope = current_global_auction_scope_from_events(
            tuple(
                full_scope_event_by_family[family_key]
                for family_key in sorted(eligible_family_keys)
            ),
            captured_at_utc=scope_at,
        )
        probabilities = {
            prepared.probability_witness.family_key: prepared.probability_witness
            for prepared in prepared_by_event.values()
        }
        if any(
            getattr(witness, "captured_at_utc", None) != scope_at
            for witness in probabilities.values()
        ):
            return reject("GLOBAL_PROBABILITY_EPOCH_MIXED_CUT")
        selection_epoch_identity = _selection_epoch_identity(
            full_scope=full_scope,
            eligible_scope=scope,
            probability_witnesses=probabilities,
            ineligible_by_family=ineligible_by_family,
        )
        book_epoch = None
        if current_book_epoch_provider is not None:
            probabilities, book_epoch = current_book_epoch_provider(
                probabilities,
                current_time(),
            )
            prepared_by_event = {
                event_id: replace(
                    prepared,
                    probability_witness=probabilities[
                        prepared.probability_witness.family_key
                    ],
                )
                for event_id, prepared in prepared_by_event.items()
            }
        initial_book_stage = (
            "book_epoch_fence"
            if preflight_winner is not None
            else "book_epoch_initial"
        )
        log_stage(initial_book_stage, families=len(prepared_by_event))
        probability_manifest = _probability_manifest(probabilities)
        # Selection is a comparison over one immutable information vector.  Scope and
        # q are frozen at ``scope_at``; the complete native YES/NO book and wealth
        # witnesses join that vector below.  A later family update belongs to the next
        # epoch.  Only the selected winner is allowed to cross into the side-effect
        # path, where probability, exact book/curve, and free cash are rebuilt JIT.
        wealth_age = timedelta(seconds=float(COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS))
        selection_state = portfolio_state_provider() if portfolio_state_provider else None
        if selection_state is None and hasattr(trade_conn, "execute"):
            from src.state.portfolio import load_runtime_open_portfolio

            selection_state = load_runtime_open_portfolio(trade_conn)
        selection_wealth = current_portfolio_wealth_witness(
            trade_conn,
            decision_at_utc=(
                book_epoch.captured_at_utc
                if book_epoch is not None
                else scope_at
            ),
            max_age=wealth_age,
            portfolio_state=selection_state,
        )

        def select_once(
            attempt_probabilities: Mapping[str, object],
            attempt_book_epoch: CurrentGlobalBookEpoch | None,
            attempt_prepared: Mapping[str, object],
            *,
            attempt_selection_epoch_identity: str = selection_epoch_identity,
            preflight_excluded_by_family: Mapping[str, str] | None = None,
            preflight_excluded_by_candidate: Mapping[
                tuple[str, str, str, str, str], str
            ]
            | None = None,
            payoff_q_lcb_by_candidate: Mapping[
                tuple[str, str, str, str], float
            ]
            | None = None,
        ):
            selection_at = current_time()
            prepared_for_selection = attempt_prepared
            if attempt_book_epoch is not None and selection_state is not None:
                prepared_for_selection = _bind_selection_holdings(
                    attempt_prepared,
                    portfolio_state=selection_state,
                    ledger_snapshot_id=selection_wealth.ledger_snapshot_id,
                )
            excluded_candidates = dict(preflight_excluded_by_candidate or {})
            if attempt_book_epoch is not None and excluded_candidates:
                known_candidate_keys = {
                    (
                        "BUY",
                        str(asset.family_key),
                        str(asset.bin_id),
                        str(asset.side),
                        str(asset.token_id),
                    )
                    for asset in tuple(
                        getattr(attempt_book_epoch, "assets", ()) or ()
                    )
                } | {
                    (
                        "SELL",
                        str(asset.family_key),
                        str(asset.bin_id),
                        str(asset.side),
                        str(asset.token_id),
                    )
                    for asset in tuple(
                        getattr(attempt_book_epoch, "sell_assets", ()) or ()
                    )
                }
                if not set(excluded_candidates).issubset(known_candidate_keys):
                    raise ValueError("GLOBAL_EXCLUDED_CANDIDATE_UNKNOWN")

            def candidate_policy(candidate):
                key = (
                    str(getattr(candidate, "action", "BUY") or "BUY").upper(),
                    str(getattr(candidate, "family_key", "") or ""),
                    str(getattr(candidate, "bin_id", "") or ""),
                    str(getattr(candidate, "side", "") or ""),
                    str(getattr(candidate, "token_id", "") or ""),
                )
                reason = excluded_candidates.get(key)
                if reason is not None:
                    return f"GLOBAL_PREFLIGHT_CANDIDATE_INELIGIBLE:{reason}"
                if candidate_policy_rejection_resolver is None:
                    return None
                return candidate_policy_rejection_resolver(candidate)
            venue_identity = (
                attempt_book_epoch.witness_identity
                if attempt_book_epoch is not None
                else current_venue_auction_identity(
                    trade_conn,
                    probability_witnesses=attempt_probabilities,
                )
            )

            def probability_resolver(family_key):
                witness = attempt_probabilities.get(family_key)
                return (
                    CurrentFamilyProbabilityAuthority.from_witness(witness)
                    if witness is not None
                    else None
                )

            def execution_resolver(candidate):
                if attempt_book_epoch is not None:
                    return attempt_book_epoch.execution_authority(
                        candidate,
                        checked_at_utc=selection_at,
                    )
                return current_execution(candidate, selection_at)

            selected = select_prepared_global_auction(
                prepared_for_selection,
                selection_epoch_identity=attempt_selection_epoch_identity,
                selection_cut_at_utc=scope_at,
                current_scope=scope,
                current_scope_identity_resolver=lambda: scope.scope_identity,
                venue_universe_identity=venue_identity,
                current_venue_universe_identity_resolver=lambda: venue_identity,
                universe_max_age=(
                    attempt_book_epoch.max_age
                    if attempt_book_epoch is not None
                    else FRESHNESS_WINDOW_DEFAULT
                ),
                current_probability_resolver=probability_resolver,
                current_execution_resolver=execution_resolver,
                current_wealth_identity_resolver=lambda: selection_wealth.economic_identity,
                wealth_witness=selection_wealth,
                capital_limit_usd=selection_wealth.spendable_cash_usd,
                fractional_kelly_multiplier=fractional_kelly_multiplier,
                decision_at_utc=selection_at,
                book_epoch=attempt_book_epoch,
                current_capital_limit_resolver=current_capital_limit_resolver,
                candidate_policy_rejection_resolver=candidate_policy,
                preflight_excluded_by_family=preflight_excluded_by_family,
                payoff_q_lcb_by_candidate=payoff_q_lcb_by_candidate,
            )
            _store_global_auction_receipt(
                trade_conn,
                selected=selected,
                selection_epoch_identity=attempt_selection_epoch_identity,
                selection_cut_at_utc=scope_at,
                decision_at_utc=selection_at,
                probability_manifest=_probability_manifest(
                    attempt_probabilities
                ),
                full_scope_identity=full_scope.scope_identity,
                full_scope_family_keys=full_scope.family_keys,
                probability_ineligible_by_family=ineligible_by_family,
                book_epoch_identity=venue_identity,
                book_asset_count=(
                    sum(
                        1
                        for asset in tuple(
                            getattr(attempt_book_epoch, "assets", ()) or ()
                        )
                        if str(getattr(asset, "family_key", "") or "")
                        not in (preflight_excluded_by_family or {})
                    )
                    + sum(
                        1
                        for asset in tuple(
                            getattr(attempt_book_epoch, "sell_assets", ()) or ()
                        )
                        if str(getattr(asset, "family_key", "") or "")
                        not in (preflight_excluded_by_family or {})
                    )
                    if attempt_book_epoch is not None
                    else None
                ),
                wealth_witness=selection_wealth,
                fractional_kelly_multiplier=fractional_kelly_multiplier,
                excluded_by_family=preflight_excluded_by_family,
                excluded_by_candidate=preflight_excluded_by_candidate,
            )
            return selected

        selected = select_once(probabilities, book_epoch, prepared_by_event)
        initial_select_stage = (
            "select_fence" if preflight_winner is not None else "select_initial"
        )
        log_stage(initial_select_stage, families=len(prepared_by_event))
        if selected.decision.candidate is None:
            log_no_trade(initial_select_stage, selected.decision)
            return reject(
                "GLOBAL_AUCTION_NO_TRADE:"
                f"{selected.decision.no_trade_reason or 'unknown'}"
            )
        log_winner(initial_select_stage, selected, probabilities)
        if selected.actuation is None:
            return reject("GLOBAL_WINNER_ACTUATION_MISSING")
        winner_id = selected.winner_event_id
        winner = next(
            (event for event in event_tuple if event.event_id == winner_id),
            None,
        )
        if preflight_winner is None:
            selected, winner, next_claim = bind_selected_winner(selected)
            if winner is None:
                if next_claim is None:
                    return reject("GLOBAL_WINNER_IDENTITY_MISSING")
                return reject(
                    "GLOBAL_WINNER_AWAITS_CLAIM",
                    next_claim_event=next_claim,
                )
            winner_id = winner.event_id

        binding_token = None
        preflight_ineligible_by_event: dict[str, str] = {}
        preflight_candidate_ineligible_by_event: dict[str, str] = {}
        if preflight_winner is not None:
            if actuate_preflighted_winner is None:
                return reject("GLOBAL_PREFLIGHT_ACTUATOR_MISSING")
            if current_book_epoch_provider is None or book_epoch is None:
                return reject("GLOBAL_PREFLIGHT_BOOK_PROVIDER_MISSING")
            probabilities_fence = probabilities
            book_epoch_fence = book_epoch
            prepared_fence = prepared_by_event
            selected, winner, next_claim = bind_selected_winner(selected)
            if winner is None:
                if next_claim is None:
                    return reject("GLOBAL_REAUCTION_WINNER_IDENTITY_MISSING")
                return reject(
                    "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM",
                    next_claim_event=next_claim,
                )
            winner_id = winner.event_id
            attempt_book_epoch = book_epoch_fence
            auction_deadline = (
                attempt_book_epoch.captured_at_utc + attempt_book_epoch.max_age
            )
            excluded_by_family: dict[str, str] = {}
            excluded_by_candidate: dict[
                tuple[str, str, str, str, str], str
            ] = {}
            payoff_q_lcb_by_candidate: dict[
                tuple[str, str, str, str], float
            ] = {}
            while True:
                preflight_at = current_time()
                if preflight_at > auction_deadline:
                    return reject("GLOBAL_REAUCTION_EPOCH_EXPIRED")
                preflight_authority = GlobalPreflightAuthority(
                    probability_manifest=probability_manifest,
                    book_epoch_identity=attempt_book_epoch.witness_identity,
                    book_economics_manifest=_book_economics_manifest(
                        attempt_book_epoch
                    ),
                    wealth_witness_identity=selected.actuation.wealth_witness_identity,
                    actuation_deadline=auction_deadline,
                )
                before_preflight = venue_submit_count()
                preflight = preflight_winner(
                    winner,
                    selected.actuation,
                    preflight_at,
                    preflight_authority,
                )
                log_stage("winner_preflight", families=len(prepared_by_event))
                if venue_submit_count() != before_preflight:
                    return reject("GLOBAL_PREFLIGHT_VENUE_SIDE_EFFECT")
                if preflight.status == "STABLE":
                    break
                if preflight.status == "BATCH_BLOCKED":
                    return reject(
                        "GLOBAL_PREFLIGHT_BATCH_BLOCKED:"
                        f"{preflight.reason or preflight.status}"
                    )
                if preflight.status == "CANDIDATE_BLOCKED":
                    candidate = selected.decision.candidate
                    if candidate is None or winner_id is None:
                        return reject("GLOBAL_PREFLIGHT_BLOCKED_CANDIDATE_MISSING")
                    candidate_key = (
                        str(getattr(candidate, "action", "BUY") or "BUY").upper(),
                        str(getattr(candidate, "family_key", "") or ""),
                        str(getattr(candidate, "bin_id", "") or ""),
                        str(getattr(candidate, "side", "") or ""),
                        str(getattr(candidate, "token_id", "") or ""),
                    )
                    if (
                        not all(candidate_key)
                        or candidate_key[0] not in {"BUY", "SELL"}
                        or candidate_key[3] not in {"YES", "NO"}
                    ):
                        return reject("GLOBAL_PREFLIGHT_BLOCKED_CANDIDATE_INVALID")
                    reason = preflight.reason or "GLOBAL_WINNER_PREFLIGHT_REJECTED"
                    excluded_by_candidate[candidate_key] = reason
                    preflight_candidate_ineligible_by_event[winner_id] = (
                        f"{getattr(candidate, 'candidate_id', '')}:{reason}"
                    )
                    _LOG.info(
                        "global batch preflight candidate excluded: candidate=%s "
                        "event=%s reason=%s excluded=%d",
                        getattr(candidate, "candidate_id", ""),
                        winner_id,
                        reason,
                        len(excluded_by_candidate),
                    )
                elif preflight.status == "CURVE_SUPERSEDED":
                    try:
                        next_probabilities, next_book_epoch = (
                            current_book_epoch_provider(
                                probabilities_fence,
                                current_time(),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - full cut is atomic
                        return reject(
                            "GLOBAL_REAUCTION_BOOK_REFRESH_FAILED:"
                            f"{type(exc).__name__}:{exc}"
                        )
                    if (
                        _probability_manifest(next_probabilities)
                        != probability_manifest
                    ):
                        return reject("GLOBAL_REAUCTION_PROBABILITY_MANIFEST_CHANGED")
                    if (
                        next_book_epoch.witness_identity
                        == attempt_book_epoch.witness_identity
                    ):
                        return reject(
                            "GLOBAL_REAUCTION_CURVE_NO_PROGRESS:"
                            f"{preflight.reason or preflight.status}"
                        )
                    prepared_fence = {
                        event_id: replace(
                            prepared,
                            probability_witness=next_probabilities[
                                prepared.probability_witness.family_key
                            ],
                        )
                        for event_id, prepared in prepared_fence.items()
                    }
                    probabilities_fence = next_probabilities
                    attempt_book_epoch = next_book_epoch
                    # Candidate exclusions are observations of one selected-token
                    # JIT book.  A newer complete book epoch invalidates that local
                    # evidence, so every candidate must be eligible for a fresh JIT
                    # proof again.  Family and probability exclusions bind different
                    # authorities and remain valid.
                    excluded_by_candidate.clear()
                    preflight_candidate_ineligible_by_event.clear()
                elif preflight.status == "PROBABILITY_TIGHTENED":
                    tightening = preflight.probability_tightening
                    candidate = selected.decision.candidate
                    terminal = selected.decision.terminal_wealth
                    if tightening is None or candidate is None or terminal is None:
                        return reject("GLOBAL_REAUCTION_Q_TIGHTENING_MISSING")
                    selected_key = (
                        candidate.family_key,
                        candidate.bin_id,
                        candidate.side,
                        candidate.token_id,
                    )
                    if (
                        tightening.candidate_key != selected_key
                        or tightening.probability_witness_identity
                        != candidate.probability_witness_identity
                    ):
                        return reject("GLOBAL_REAUCTION_Q_TIGHTENING_IDENTITY_MISMATCH")
                    prior = payoff_q_lcb_by_candidate.get(selected_key)
                    selected_q = float(terminal.win_probability_lcb)
                    tightened_q = float(tightening.payoff_q_lcb)
                    if tightened_q >= selected_q or (
                        prior is not None and tightened_q >= prior
                    ):
                        return reject("GLOBAL_REAUCTION_Q_TIGHTENING_NO_PROGRESS")
                    payoff_q_lcb_by_candidate[selected_key] = tightened_q
                else:
                    family_key = str(
                        getattr(selected.decision.candidate, "family_key", "") or ""
                    )
                    if not family_key or winner_id is None:
                        return reject("GLOBAL_PREFLIGHT_BLOCKED_FAMILY_MISSING")
                    reason = preflight.reason or "GLOBAL_WINNER_PREFLIGHT_REJECTED"
                    excluded_by_family[family_key] = reason
                    preflight_ineligible_by_event[winner_id] = reason
                    _LOG.info(
                        "global batch preflight family excluded: family=%s "
                        "event=%s reason=%s excluded=%d",
                        family_key,
                        winner_id,
                        reason,
                        len(excluded_by_family),
                    )
                fallthrough_epoch_identity = (
                    _selection_epoch_identity_with_preflight_exclusions(
                        selection_epoch_identity,
                        excluded_by_family,
                        excluded_by_candidate,
                        payoff_q_lcb_by_candidate,
                    )
                    if (
                        excluded_by_family
                        or excluded_by_candidate
                        or payoff_q_lcb_by_candidate
                    )
                    else selection_epoch_identity
                )
                selected = select_once(
                    probabilities_fence,
                    attempt_book_epoch,
                    prepared_fence,
                    attempt_selection_epoch_identity=fallthrough_epoch_identity,
                    preflight_excluded_by_family=excluded_by_family,
                    preflight_excluded_by_candidate=excluded_by_candidate,
                    payoff_q_lcb_by_candidate=payoff_q_lcb_by_candidate,
                )
                log_stage(
                    "select_preflight_fallthrough",
                    families=len(prepared_by_event) - len(excluded_by_family),
                )
                if selected.decision.candidate is None:
                    log_no_trade("select_preflight_fallthrough", selected.decision)
                    return reject(
                        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
                        f"{selected.decision.no_trade_reason or 'unknown'}:"
                        f"families={len(excluded_by_family)}:"
                        f"candidates={len(excluded_by_candidate)}"
                    )
                log_winner(
                    "select_preflight_fallthrough",
                    selected,
                    probabilities_fence,
                )
                if selected.actuation is None:
                    return reject("GLOBAL_REAUCTION_ACTUATION_MISSING")
                selected, winner, next_claim = bind_selected_winner(selected)
                if winner is None:
                    if next_claim is None:
                        return reject("GLOBAL_REAUCTION_WINNER_IDENTITY_MISSING")
                    return reject(
                        "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM",
                        next_claim_event=next_claim,
                    )
                winner_id = winner.event_id
            binding_token = preflight.binding_token

        actuation_at = current_time()
        if preflight_winner is not None and actuation_at > auction_deadline:
            return reject("GLOBAL_REAUCTION_EPOCH_EXPIRED")
        before_calls = venue_submit_count()
        release_selection_snapshot()
        winner_receipt = (
            actuate_preflighted_winner.consume(
                winner,
                selected.actuation,
                actuation_at,
                binding_token,
                preflight_authority,
            )
            if preflight_winner is not None
            else actuate_winner(winner, selected.actuation, actuation_at)
        )
        venue_delta = venue_submit_count() - before_calls
        if venue_delta not in {0, 1}:
            raise RuntimeError("GLOBAL_ACTUATION_VENUE_COUNT_INVALID")
        receipts = {
            event.event_id: (
                winner_receipt
                if event.event_id == winner_id
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_DUPLICATE_FAMILY_CARRIER:"
                            f"{duplicate_owner_by_event[event.event_id]}"
                        ),
                        proof_accepted=False,
                    )
                )
                if event.event_id in duplicate_owner_by_event
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_FAMILY_INELIGIBLE:"
                            f"{ineligible_by_event[event.event_id]}"
                        ),
                        proof_accepted=False,
                    )
                )
                if event.event_id in ineligible_by_event
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_PREFLIGHT_FAMILY_INELIGIBLE:"
                            f"{preflight_ineligible_by_event[event.event_id]}"
                        ),
                        proof_accepted=False,
                    )
                )
                if event.event_id in preflight_ineligible_by_event
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_PREFLIGHT_CANDIDATE_INELIGIBLE:"
                            f"{preflight_candidate_ineligible_by_event[event.event_id]}"
                        ),
                        proof_accepted=False,
                    )
                )
                if event.event_id in preflight_candidate_ineligible_by_event
                else stamp_receipt(
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=(
                            "GLOBAL_NOT_SELECTED:"
                            f"{selected.actuation.actuation_identity}"
                        ),
                        proof_accepted=False,
                    )
                )
            )
            for event in event_tuple
        }
        return GlobalBatchSubmitResult(
            receipts=receipts,
            winner_event_id=winner_id,
            venue_submit_count=venue_delta,
        )
    except Exception as exc:  # noqa: BLE001 - one authority fault invalidates epoch
        _LOG.exception("global auction epoch failed closed")
        return reject(f"GLOBAL_AUCTION_FAILED:{type(exc).__name__}:{exc}")
