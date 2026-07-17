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
import threading
import time
import zlib
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence

from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
from src.data.market_topology_rows import prime_frozen_schema_reads
from src.engine.global_auction_universe import (
    CurrentGlobalBookEpoch,
    current_global_book_epoch_identity,
    current_global_auction_scope_from_events,
    current_portfolio_wealth_witness,
    current_venue_auction_identity,
    probe_inflight_buy_ambiguity,
    scan_current_global_auction_scope,
)
from src.engine.global_single_order_auction import (
    global_single_order_actuation_identity,
    select_prepared_global_auction,
)
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt, GlobalBatchSubmitResult
from src.solve.solver import (
    CurrentFamilyProbabilityAuthority,
    executable_curve_identity,
)
from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS

UTC = timezone.utc
_LOG = logging.getLogger(__name__)
_GLOBAL_AUCTION_PAYLOAD_REFS: dict[str, tuple[str, int, str]] = {}
_GLOBAL_AUCTION_PAYLOAD_REFS_LOCK = threading.Lock()
_GLOBAL_AUCTION_HEAVY_RECEIPT_FIELDS = frozenset(
    {
        "book_native_side_states_zlib_b64",
        "buy_sizing_rejections_zlib_b64",
        "candidate_evaluations_zlib_b64",
    }
)


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


def _global_preflight_exhaustion_reason(
    no_trade_reason: str | None,
    *,
    excluded_by_family: Mapping[str, str],
    excluded_by_candidate: Mapping[
        tuple[str, str, str, str, str], str
    ],
) -> str:
    """Separate a proved CASH/HOLD optimum from an unfinished auction."""

    reason = str(no_trade_reason or "unknown")
    # A selected-size failure is not a proof that every smaller executable size
    # has non-positive utility.  CASH/HOLD is terminal only when the same cut
    # retained the entire action set; any exclusion leaves the auction unfinished.
    complete = not excluded_by_family and not excluded_by_candidate
    base = (
        "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL"
        if complete
        and reason
        in {
            "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
            "ROBUST_MAJORITY_LOSS",
        }
        else "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED"
    )
    return (
        f"{base}:{reason}:families={len(excluded_by_family)}:"
        f"candidates={len(excluded_by_candidate)}"
    )


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
    wealth_witness: object,
) -> dict[str, object]:
    """Bind every family holding to the same selection-time ledger generation."""

    from src.solve.menu_adapter import native_holdings_snapshot_from_positions

    positions = tuple(getattr(portfolio_state, "positions", ()) or ())
    ledger_snapshot_id = str(getattr(wealth_witness, "ledger_snapshot_id", "") or "")
    token_shares_by_id = {
        str(token): Decimal(int(amount)) / Decimal("1000000")
        for token, amount in tuple(
            getattr(wealth_witness, "native_holdings_micro", ()) or ()
        )
    }
    if not ledger_snapshot_id:
        raise ValueError("GLOBAL_HOLDINGS_LEDGER_IDENTITY_MISSING")
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
            token_shares_by_id=token_shares_by_id,
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


def _current_probability_authorities(
    probabilities: Mapping[str, object],
) -> dict[str, CurrentFamilyProbabilityAuthority | None]:
    authorities: dict[str, CurrentFamilyProbabilityAuthority | None] = {}
    for family_key, witness in probabilities.items():
        try:
            authorities[family_key] = (
                CurrentFamilyProbabilityAuthority.from_witness(witness)
            )
        except Exception:  # noqa: BLE001 - invalid family authority fails closed
            authorities[family_key] = None
    return authorities


_BOOK_NATIVE_SIDE_STATE_FIELDS = (
    "family_key",
    "bin_id",
    "condition_id",
    "side",
    "token_id",
    "status",
    "book_hash",
    "market_event_id",
    "gamma_market_id",
)
_BOOK_NATIVE_SIDE_STATUSES = {
    "EXECUTABLE",
    "NO_ASK",
    "VENUE_NOT_EXECUTABLE",
    "VENUE_METADATA_STALE",
}


def _book_native_side_receipt(
    *,
    asset_states: Sequence[tuple[str, ...]],
    probability_keys: Sequence[str],
    buy_candidate_index: Sequence[Sequence[str]],
    excluded_by_family: Mapping[str, str],
    required: bool = True,
) -> dict[str, object]:
    """Prove every bound side became a candidate or a typed current-book exclusion."""

    if not required:
        payload = {
            "fields": list(_BOOK_NATIVE_SIDE_STATE_FIELDS),
            "rows": [],
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "book_native_side_state_count": 0,
            "book_native_side_executable_count": 0,
            "book_native_side_non_executable_count": 0,
            "book_native_side_status_counts": {},
            "book_native_side_candidate_coverage_complete": False,
            "book_native_side_candidate_coverage_status": "UNAVAILABLE",
            "book_native_side_candidate_missing_count": 0,
            "book_native_side_candidate_extra_count": 0,
            "book_native_side_encoding": "zlib+base64+canonical-json-v1",
            "book_native_side_states_sha256": hashlib.sha256(encoded).hexdigest(),
            "book_native_side_states_zlib_b64": base64.b64encode(
                zlib.compress(encoded, level=9)
            ).decode("ascii"),
        }

    rows = tuple(
        sorted(tuple(str(value) for value in row) for row in asset_states)
    )
    if not rows or any(
        len(row) != len(_BOOK_NATIVE_SIDE_STATE_FIELDS) for row in rows
    ):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_BOOK_SIDE_STATE_INVALID")
    keys = tuple(row[:5] for row in rows)
    if (
        len(keys) != len(set(keys))
        or any(not all(key) or key[3] not in {"YES", "NO"} for key in keys)
        or any(
            not row[5] or row[5] not in _BOOK_NATIVE_SIDE_STATUSES
            for row in rows
        )
        or {row[0] for row in rows} != set(probability_keys)
    ):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_BOOK_SIDE_COVERAGE_INVALID")

    candidate_keys = tuple(
        tuple(str(value) for value in row[1:6])
        for row in buy_candidate_index
    )
    if len(candidate_keys) != len(set(candidate_keys)):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_BUY_BOOK_KEY_DUPLICATE")
    excluded_families = set(excluded_by_family)
    executable_keys = {
        row[:5]
        for row in rows
        if row[5] == "EXECUTABLE" and row[0] not in excluded_families
    }
    candidate_key_set = set(candidate_keys)
    missing = sorted(executable_keys - candidate_key_set)
    extra = sorted(candidate_key_set - executable_keys)
    if missing or extra:
        raise ValueError(
            "GLOBAL_AUCTION_RECEIPT_BUY_BOOK_MATERIALIZATION_MISMATCH:"
            f"missing={len(missing)}:extra={len(extra)}"
        )

    status_counts = {
        side: {
            status: sum(
                1 for row in rows if row[3] == side and row[5] == status
            )
            for status in sorted(_BOOK_NATIVE_SIDE_STATUSES)
        }
        for side in ("YES", "NO")
    }
    payload = {
        "fields": list(_BOOK_NATIVE_SIDE_STATE_FIELDS),
        "rows": rows,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "book_native_side_state_count": len(rows),
        "book_native_side_executable_count": sum(
            1 for row in rows if row[5] == "EXECUTABLE"
        ),
        "book_native_side_non_executable_count": sum(
            1 for row in rows if row[5] != "EXECUTABLE"
        ),
        "book_native_side_status_counts": status_counts,
        "book_native_side_candidate_coverage_complete": True,
        "book_native_side_candidate_coverage_status": "COMPLETE",
        "book_native_side_candidate_missing_count": 0,
        "book_native_side_candidate_extra_count": 0,
        "book_native_side_encoding": "zlib+base64+canonical-json-v1",
        "book_native_side_states_sha256": hashlib.sha256(encoded).hexdigest(),
        "book_native_side_states_zlib_b64": base64.b64encode(
            zlib.compress(encoded, level=9)
        ).decode("ascii"),
    }


def _decision_log_connection_key(conn: sqlite3.Connection) -> str:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return f"connection:{id(conn)}"
    for row in rows:
        if str(row[1]) == "main":
            path = str(row[2] or "")
            return path or f"memory:{id(conn)}"
    return f"connection:{id(conn)}"


def _global_auction_payload_identity(receipt: Mapping[str, object]) -> str:
    payload = {
        "book": (
            receipt.get("book_native_side_encoding"),
            receipt.get("book_native_side_states_sha256"),
        ),
        "candidate_evaluations": (
            receipt.get("candidate_evaluation_encoding"),
            receipt.get("candidate_evaluations_sha256"),
        ),
        "buy_sizing_rejections": (
            receipt.get("buy_sizing_rejection_encoding"),
            receipt.get("buy_sizing_rejections_sha256"),
        ),
    }
    encoded = json.dumps(
        payload,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stored_global_auction_payload_ref(
    conn: sqlite3.Connection,
    *,
    connection_key: str,
    payload_identity: str,
) -> tuple[int, str] | None:
    ref = _GLOBAL_AUCTION_PAYLOAD_REFS.get(connection_key)
    if ref is None or ref[0] != payload_identity:
        return None
    row_id, receipt_hash = ref[1], ref[2]
    row = conn.execute(
        """
        SELECT 1
          FROM decision_log
         WHERE id = ?
           AND mode = 'global_single_order_auction'
        """,
        (row_id,),
    ).fetchone()
    return (row_id, receipt_hash) if row is not None else None


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
    book_asset_states: Sequence[tuple[str, ...]],
    wealth_witness: object,
    fractional_kelly_multiplier: Decimal,
    excluded_by_family: Mapping[str, str] | None = None,
    excluded_by_candidate: Mapping[
        tuple[str, str, str, str, str], str
    ] | None = None,
    book_captured_at_utc: datetime | None = None,
    book_max_age: timedelta | None = None,
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

    book_capture_complete = (
        book_captured_at_utc is not None and book_max_age is not None
    )
    if (book_captured_at_utc is None) != (book_max_age is None):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_BOOK_FRESHNESS_INCOMPLETE")
    if book_capture_complete:
        assert book_captured_at_utc is not None
        assert book_max_age is not None
        if book_captured_at_utc.tzinfo is None or book_max_age <= timedelta(0):
            raise ValueError("GLOBAL_AUCTION_RECEIPT_BOOK_FRESHNESS_INVALID")
        book_captured_at_utc = book_captured_at_utc.astimezone(UTC)
        book_deadline_at_utc = book_captured_at_utc + book_max_age
        book_max_age_seconds = book_max_age.total_seconds()
    else:
        book_deadline_at_utc = None
        book_max_age_seconds = None

    decision = getattr(selected, "decision", None)
    if decision is None:
        raise ValueError("GLOBAL_AUCTION_RECEIPT_DECISION_MISSING")
    evaluations = tuple(getattr(decision, "candidate_evaluations", ()) or ())
    buy_sizing_rejections = {
        str(evaluation.candidate_id): evaluation.buy_sizing_rejection
        for evaluation in evaluations
        if evaluation.buy_sizing_rejection is not None
    }
    evaluation_rows = tuple(
        {
            key: value
            for key, value in asdict(evaluation).items()
            if key != "buy_sizing_rejection"
        }
        for evaluation in evaluations
    )
    below_minimum_ids = {
        str(row["candidate_id"])
        for row in evaluation_rows
        if row.get("action") == "BUY"
        and row.get("status") == "REJECTED"
        and row.get("rejection_reason")
        == "FRACTIONAL_KELLY_INCREMENT_BELOW_MINIMUM"
    }
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
    buy_rows = tuple(row for row in evaluation_rows if row.get("action") == "BUY")
    buy_candidate_index = sorted(
        [
            str(row.get("candidate_id") or ""),
            str(row.get("family_key") or ""),
            str(row.get("bin_id") or ""),
            str(row.get("condition_id") or ""),
            str(row.get("side") or ""),
            str(row.get("token_id") or ""),
        ]
        for row in buy_rows
    )
    buy_candidate_index_complete = (
        len(buy_candidate_index) == len(buy_rows)
        and len({row[0] for row in buy_candidate_index}) == len(buy_rows)
        and all(
            all(value for value in row) and row[4] in {"YES", "NO"}
            for row in buy_candidate_index
        )
    )
    buy_candidate_positions = {
        row[0]: index for index, row in enumerate(buy_candidate_index)
    }
    sizing_rejection_fields = (
        "buy_candidate_index",
        "current_token_shares",
        "full_kelly_target_shares",
        "fractional_kelly_target_shares",
        "minimum_marketable_increment_shares",
        "minimum_fractional_kelly_multiplier",
        "continuous_full_kelly_target_shares",
        "continuous_fractional_kelly_target_shares",
        "continuous_full_robust_delta_log_wealth",
        "continuous_full_robust_ev_usd",
        "minimum_marketable_cost_usd",
        "minimum_marketable_robust_delta_log_wealth",
        "minimum_marketable_robust_ev_usd",
        "minimum_marketable_capital_efficiency",
        "minimum_marketable_positive",
    )
    buy_sizing_rejection_complete = (
        set(buy_sizing_rejections) == below_minimum_ids
        and all(
            candidate_id in buy_candidate_positions
            for candidate_id in buy_sizing_rejections
        )
    )
    if not buy_sizing_rejection_complete:
        raise ValueError(
            "GLOBAL_AUCTION_RECEIPT_BUY_SIZING_REJECTION_INCOMPLETE"
        )
    buy_sizing_rejection_rows = tuple(
        [
            buy_candidate_positions[candidate_id],
            str(certificate.current_token_shares),
            str(certificate.full_kelly_target_shares),
            str(certificate.fractional_kelly_target_shares),
            str(certificate.minimum_marketable_increment_shares),
            str(certificate.minimum_fractional_kelly_multiplier),
            str(certificate.continuous_full_kelly_target_shares),
            str(certificate.continuous_fractional_kelly_target_shares),
            certificate.continuous_full_robust_delta_log_wealth,
            certificate.continuous_full_robust_ev_usd,
            str(certificate.minimum_marketable_cost_usd),
            certificate.minimum_marketable_robust_delta_log_wealth,
            certificate.minimum_marketable_robust_ev_usd,
            certificate.minimum_marketable_capital_efficiency,
            certificate.minimum_marketable_positive,
        ]
        for candidate_id, certificate in sorted(
            buy_sizing_rejections.items(),
            key=lambda item: buy_candidate_positions[item[0]],
        )
    )
    sizing_rejection_json = json.dumps(
        {
            "fields": sizing_rejection_fields,
            "rows": buy_sizing_rejection_rows,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sizing_rejection_zlib = zlib.compress(sizing_rejection_json, level=9)
    book_native_side_receipt = _book_native_side_receipt(
        asset_states=book_asset_states,
        probability_keys=probability_keys,
        buy_candidate_index=buy_candidate_index,
        excluded_by_family=excluded_by_family or {},
        required=book_capture_complete,
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
        "buy_candidate_index_fields": [
            "candidate_id",
            "family_key",
            "bin_id",
            "condition_id",
            "side",
            "token_id",
        ],
        "buy_candidate_index": buy_candidate_index,
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
        and buy_candidate_index_complete
        and len(selected_rows) == (1 if winner is not None else 0)
        and (
            winner is None
            or str(selected_rows[0].get("candidate_id") or "") == winner_id
        )
    )
    receipt = {
        "schema_version": 14,
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
        "book_capture_freshness_complete": book_capture_complete,
        "book_captured_at_utc": (
            book_captured_at_utc.isoformat()
            if book_captured_at_utc is not None
            else None
        ),
        "book_deadline_at_utc": (
            book_deadline_at_utc.isoformat()
            if book_deadline_at_utc is not None
            else None
        ),
        "book_max_age_seconds": book_max_age_seconds,
        **book_native_side_receipt,
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
        "buy_candidate_index_complete": buy_candidate_index_complete,
        "buy_candidate_index_count": len(buy_candidate_index),
        "buy_condition_membership_count": sum(
            1 + (mask == 3) for mask in buy_condition_masks.values()
        ),
        "candidate_evaluation_encoding": "zlib+base64+canonical-json-v7",
        "candidate_evaluations_sha256": hashlib.sha256(
            evaluation_json
        ).hexdigest(),
        "candidate_evaluations_zlib_b64": base64.b64encode(
            evaluation_zlib
        ).decode("ascii"),
        "buy_sizing_rejection_count": len(buy_sizing_rejection_rows),
        "buy_sizing_rejection_complete": buy_sizing_rejection_complete,
        "buy_sizing_rejection_encoding": "zlib+base64+indexed-canonical-json-v3",
        "buy_sizing_rejection_index_source": (
            "candidate_evaluations.buy_candidate_index"
        ),
        "buy_sizing_rejections_sha256": hashlib.sha256(
            sizing_rejection_json
        ).hexdigest(),
        "buy_sizing_rejections_zlib_b64": base64.b64encode(
            sizing_rejection_zlib
        ).decode("ascii"),
    }
    encoded = json.dumps(
        receipt,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt["receipt_hash"] = hashlib.sha256(encoded).hexdigest()
    payload_identity = _global_auction_payload_identity(receipt)
    connection_key = _decision_log_connection_key(conn)
    with _GLOBAL_AUCTION_PAYLOAD_REFS_LOCK:
        payload_ref = (
            _stored_global_auction_payload_ref(
                conn,
                connection_key=connection_key,
                payload_identity=payload_identity,
            )
            if winner is None
            else None
        )
        if payload_ref is not None:
            reference_row_id, reference_receipt_hash = payload_ref
            compact_receipt = {
                key: value
                for key, value in receipt.items()
                if key not in _GLOBAL_AUCTION_HEAVY_RECEIPT_FIELDS
            }
            compact_receipt.update(
                {
                    "payload_compacted": True,
                    "payload_identity": payload_identity,
                    "payload_reference_decision_log_id": reference_row_id,
                    "payload_reference_mode": "global_single_order_auction",
                    "payload_reference_receipt_hash": reference_receipt_hash,
                }
            )
            row_id = store_artifact(
                conn,
                CycleArtifact(
                    mode="global_single_order_auction_duplicate",
                    started_at=selection_cut_at_utc.isoformat(),
                    completed_at=decision_at_utc.isoformat(),
                    skipped_reason=str(
                        getattr(decision, "no_trade_reason", "") or ""
                    ),
                    summary=compact_receipt,
                ),
            )
            if row_id is None:
                raise RuntimeError("GLOBAL_AUCTION_RECEIPT_ID_MISSING")
            saved_bytes = sum(
                len(str(receipt.get(field) or ""))
                for field in _GLOBAL_AUCTION_HEAVY_RECEIPT_FIELDS
            )
            _LOG.info(
                "global auction receipt payload reused: row_id=%s reference_row_id=%s "
                "payload_identity=%s saved_json_bytes=%d",
                row_id,
                reference_row_id,
                payload_identity,
                saved_bytes,
            )
            return row_id

        row_id = store_artifact(
            conn,
            CycleArtifact(
                mode="global_single_order_auction",
                started_at=selection_cut_at_utc.isoformat(),
                completed_at=decision_at_utc.isoformat(),
                skipped_reason=str(
                    getattr(decision, "no_trade_reason", "") or ""
                ),
                summary=receipt,
            ),
        )
        if row_id is not None:
            _GLOBAL_AUCTION_PAYLOAD_REFS[connection_key] = (
                payload_identity,
                row_id,
                str(receipt["receipt_hash"]),
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


def _store_global_preflight_receipt(
    conn,
    *,
    selected: object,
    preflight: GlobalWinnerPreflight,
    authority: GlobalPreflightAuthority,
    checked_at_utc: datetime,
    winner_event_id: str,
    venue_submit_count_before: int,
    venue_submit_count_after: int,
) -> int | None:
    """Persist the immutable outcome of one side-effect-free winner preflight."""

    if not isinstance(conn, sqlite3.Connection):
        return None
    if checked_at_utc.tzinfo is None:
        raise ValueError("GLOBAL_PREFLIGHT_RECEIPT_TIME_NAIVE")
    checked_at_utc = checked_at_utc.astimezone(UTC)
    decision = getattr(selected, "decision", None)
    candidate = getattr(decision, "candidate", None)
    actuation = getattr(selected, "actuation", None)
    if candidate is None or actuation is None:
        raise ValueError("GLOBAL_PREFLIGHT_RECEIPT_WINNER_MISSING")
    candidate_id = str(getattr(candidate, "candidate_id", "") or "")
    selection_epoch_identity = str(
        getattr(actuation, "selection_epoch_identity", "") or ""
    )
    actuation_identity = str(getattr(actuation, "actuation_identity", "") or "")
    selection_cut_at_utc = getattr(actuation, "selection_cut_at_utc", None)
    auction_decision_at_utc = getattr(actuation, "decision_at_utc", None)
    if not all(
        (
            candidate_id,
            selection_epoch_identity,
            actuation_identity,
            str(winner_event_id or ""),
        )
    ):
        raise ValueError("GLOBAL_PREFLIGHT_RECEIPT_IDENTITY_INCOMPLETE")
    if (
        not isinstance(selection_cut_at_utc, datetime)
        or selection_cut_at_utc.tzinfo is None
        or not isinstance(auction_decision_at_utc, datetime)
        or auction_decision_at_utc.tzinfo is None
    ):
        raise ValueError("GLOBAL_PREFLIGHT_RECEIPT_AUCTION_TIME_INVALID")
    action = str(getattr(candidate, "action", "BUY") or "BUY")
    family_key = str(getattr(candidate, "family_key", "") or "")
    bin_id = str(getattr(candidate, "bin_id", "") or "")
    condition_id = str(getattr(candidate, "condition_id", "") or "")
    side = str(getattr(candidate, "side", "") or "")
    token_id = str(getattr(candidate, "token_id", "") or "")
    if (
        action not in {"BUY", "SELL"}
        or side not in {"YES", "NO"}
        or not all((family_key, bin_id, condition_id, token_id))
    ):
        raise ValueError("GLOBAL_PREFLIGHT_RECEIPT_CANDIDATE_INVALID")
    if venue_submit_count_after != venue_submit_count_before:
        raise ValueError("GLOBAL_PREFLIGHT_RECEIPT_VENUE_SIDE_EFFECT")

    receipt = {
        "schema_version": 1,
        "selection_epoch_identity": selection_epoch_identity,
        "selection_cut_at_utc": selection_cut_at_utc.astimezone(UTC).isoformat(),
        "auction_decision_at_utc": auction_decision_at_utc.astimezone(
            UTC
        ).isoformat(),
        "preflight_checked_at_utc": checked_at_utc.isoformat(),
        "preflight_status": preflight.status,
        "preflight_reason": str(preflight.reason or ""),
        "winner_event_id": str(winner_event_id),
        "winner_candidate_id": candidate_id,
        "action": action,
        "family_key": family_key,
        "bin_id": bin_id,
        "condition_id": condition_id,
        "side": side,
        "token_id": token_id,
        "actuation_identity": actuation_identity,
        "probability_manifest": authority.probability_manifest,
        "book_epoch_identity": authority.book_epoch_identity,
        "wealth_witness_identity": authority.wealth_witness_identity,
        "actuation_deadline": authority.actuation_deadline.astimezone(
            UTC
        ).isoformat(),
        "venue_submit_count_before": venue_submit_count_before,
        "venue_submit_count_after": venue_submit_count_after,
        "venue_side_effect_free": True,
    }
    encoded = json.dumps(
        receipt,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt["receipt_hash"] = hashlib.sha256(encoded).hexdigest()

    from src.state.decision_chain import CycleArtifact, store_artifact

    row_id = store_artifact(
        conn,
        CycleArtifact(
            mode="global_single_order_auction_preflight",
            started_at=checked_at_utc.isoformat(),
            completed_at=checked_at_utc.isoformat(),
            skipped_reason=str(preflight.reason or ""),
            summary=receipt,
        ),
    )
    if row_id is None:
        raise RuntimeError("GLOBAL_PREFLIGHT_RECEIPT_ID_MISSING")
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


def _book_epoch_with_replacement_candidate(
    book_epoch: CurrentGlobalBookEpoch,
    selected_candidate: object,
    replacement_candidate: object,
) -> CurrentGlobalBookEpoch:
    """Overlay one exact JIT BUY curve without recapturing unrelated books."""

    selected_key = tuple(
        str(getattr(selected_candidate, field, "") or "")
        for field in ("family_key", "bin_id", "condition_id", "side", "token_id")
    )
    replacement_key = tuple(
        str(getattr(replacement_candidate, field, "") or "")
        for field in ("family_key", "bin_id", "condition_id", "side", "token_id")
    )
    if (
        str(getattr(selected_candidate, "action", "BUY") or "BUY") != "BUY"
        or str(getattr(replacement_candidate, "action", "BUY") or "BUY") != "BUY"
        or not all(selected_key)
        or replacement_key != selected_key
        or str(
            getattr(replacement_candidate, "probability_witness_identity", "") or ""
        )
        != str(
            getattr(selected_candidate, "probability_witness_identity", "") or ""
        )
        or str(getattr(replacement_candidate, "resolution_identity", "") or "")
        != str(getattr(selected_candidate, "resolution_identity", "") or "")
        or str(getattr(replacement_candidate, "ledger_snapshot_id", "") or "")
        != str(getattr(selected_candidate, "ledger_snapshot_id", "") or "")
    ):
        raise ValueError("GLOBAL_REAUCTION_REPLACEMENT_IDENTITY_MISMATCH")

    curve = getattr(replacement_candidate, "executable_cost_curve", None)
    captured_at = getattr(replacement_candidate, "book_captured_at_utc", None)
    if (
        curve is None
        or getattr(captured_at, "tzinfo", None) is None
        or str(getattr(curve, "token_id", "") or "") != replacement_key[4]
        or str(getattr(curve, "side", "") or "") != replacement_key[3]
        or str(getattr(curve, "snapshot_id", "") or "")
        != str(getattr(replacement_candidate, "book_snapshot_id", "") or "")
        or executable_curve_identity(curve)
        != str(getattr(replacement_candidate, "execution_curve_identity", "") or "")
    ):
        raise ValueError("GLOBAL_REAUCTION_REPLACEMENT_CURVE_INVALID")

    assets = []
    asset_matches = 0
    for asset in book_epoch.assets:
        asset_key = (
            str(asset.family_key),
            str(asset.bin_id),
            str(asset.condition_id),
            str(asset.side),
            str(asset.token_id),
        )
        if asset_key == selected_key:
            if (
                str(getattr(asset.curve, "snapshot_id", "") or "")
                != str(getattr(selected_candidate, "book_snapshot_id", "") or "")
                or executable_curve_identity(asset.curve)
                != str(
                    getattr(selected_candidate, "execution_curve_identity", "") or ""
                )
            ):
                raise ValueError("GLOBAL_REAUCTION_SELECTED_CURVE_MISMATCH")
            asset = replace(asset, curve=curve, captured_at_utc=captured_at)
            asset_matches += 1
        assets.append(asset)

    states = []
    state_matches = 0
    for state in book_epoch.asset_states:
        if tuple(str(value) for value in state[:5]) == selected_key:
            state = (
                *state[:5],
                "EXECUTABLE",
                str(getattr(curve, "book_hash", "") or ""),
                *state[7:],
            )
            state_matches += 1
        states.append(state)
    if asset_matches != 1 or state_matches != 1:
        raise ValueError("GLOBAL_REAUCTION_REPLACEMENT_ASSET_MISSING")

    identity = current_global_book_epoch_identity(
        asset_states=states,
        captured_at_utc=book_epoch.captured_at_utc,
    )
    return CurrentGlobalBookEpoch(
        assets=tuple(assets),
        asset_states=tuple(states),
        captured_at_utc=book_epoch.captured_at_utc,
        max_age=book_epoch.max_age,
        witness_identity=identity,
        sell_assets=book_epoch.sell_assets,
    )


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


def _current_held_weather_families(
    trade_conn: object,
) -> tuple[tuple[str, str, str], ...]:
    """Read every canonical runtime-open family that the auction must manage."""

    execute = getattr(trade_conn, "execute", None)
    if execute is None:
        return ()
    table = execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='position_current'"
    ).fetchone()
    if table is None:
        return ()

    from src.state.portfolio import load_runtime_open_portfolio

    state = load_runtime_open_portfolio(trade_conn)
    families = set()
    for position in tuple(getattr(state, "positions", ()) or ()):
        metric = str(
            getattr(position, "temperature_metric", "") or ""
        ).strip().lower()
        family = (
            str(getattr(position, "city", "") or "").strip(),
            str(getattr(position, "target_date", "") or "").strip(),
            metric,
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            raise ValueError("GLOBAL_HELD_FAMILY_IDENTITY_INVALID")
        families.add(family)
    return tuple(sorted(families))


def _no_trade_rejection_log_summary(
    decision: object,
    *,
    limit: int = 16,
) -> tuple[dict[str, int], int, int]:
    if limit <= 0:
        raise ValueError("GLOBAL_AUCTION_REJECTION_LOG_LIMIT_INVALID")
    exact_reasons: set[str] = set()
    counts: dict[str, int] = {}
    for reason in getattr(decision, "rejection_reasons", {}).values():
        exact = str(reason or "unknown")
        exact_reasons.add(exact)
        code = exact.partition(":")[0] or "unknown"
        counts[code] = counts.get(code, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    visible = dict(ranked[:limit])
    return visible, len(exact_reasons), max(0, len(ranked) - len(visible))


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
    buy_candidates_enabled: bool = True,
    fractional_kelly_multiplier: Decimal = Decimal("1"),
    claim_unpaged_winner: Callable[[OpportunityEvent], bool] | None = None,
    epoch_superseded: Callable[[], bool] | None = None,
    selection_cancelled: Callable[[], bool] | None = None,
    restrict_to_family_keys: frozenset[str] | None = None,
) -> GlobalBatchSubmitResult:
    """Select once from every family holding a current q certificate."""

    if decision_time.tzinfo is None:
        raise ValueError("GLOBAL_AUCTION_DECISION_TIME_NAIVE")
    decision_time = decision_time.astimezone(UTC)
    event_tuple = tuple(events)
    if restrict_to_family_keys is not None and (
        not restrict_to_family_keys
        or any(not str(family_key or "").strip() for family_key in restrict_to_family_keys)
    ):
        raise ValueError("GLOBAL_AUCTION_RESTRICTED_SCOPE_INVALID")
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
        counts, distinct_reasons, omitted_codes = _no_trade_rejection_log_summary(
            decision
        )
        _LOG.info(
            "global batch no-trade detail: stage=%s reason=%s "
            "rejection_codes=%s distinct_reasons=%d omitted_codes=%d",
            stage,
            str(getattr(decision, "no_trade_reason", "") or "unknown"),
            counts,
            distinct_reasons,
            omitted_codes,
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

    def superseded(stage: str) -> bool:
        if epoch_superseded is None:
            return False
        try:
            changed = bool(epoch_superseded())
        except Exception as exc:  # noqa: BLE001 - wake hint failure cannot block trading
            _LOG.warning(
                "global batch supersession probe failed: stage=%s error=%r",
                stage,
                exc,
            )
            return False
        if changed:
            _LOG.info(
                "global batch superseded by newer durable input: stage=%s "
                "elapsed_s=%.3f events=%d",
                stage,
                time.monotonic() - batch_started,
                len(event_tuple),
            )
        return changed

    def cancelled(stage: str) -> bool:
        if selection_cancelled is None:
            return False
        try:
            changed = bool(selection_cancelled())
        except Exception as exc:  # noqa: BLE001 - wake hints cannot invent a trade veto
            _LOG.warning(
                "global batch cancellation probe failed: stage=%s error=%r",
                stage,
                exc,
            )
            return False
        if changed:
            _LOG.info(
                "global batch preempted by urgent input: stage=%s "
                "elapsed_s=%.3f events=%d",
                stage,
                time.monotonic() - batch_started,
                len(event_tuple),
            )
        return changed

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
        if cancelled("selection_snapshot"):
            return reject("GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED")
        if probe_inflight_buy_ambiguity(trade_conn):
            raise ValueError("CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS")
        scope_at = current_time()
        held_families = _current_held_weather_families(trade_conn)
        restricted_families = None
        if restrict_to_family_keys is not None:
            restricted_families = frozenset(
                (
                    str(payload.get("city") or "").strip(),
                    str(payload.get("target_date") or "").strip(),
                    str(payload.get("metric") or "").strip().lower(),
                )
                for event in event_tuple
                for payload in (payload_reader(event),)
                if _family_key(event, payload) in restrict_to_family_keys
            )
            if (
                not restricted_families
                or frozenset(
                    weather_family_id(
                        city=city,
                        target_date=target_date,
                        metric=metric,
                    )
                    for city, target_date, metric in restricted_families
                )
                != restrict_to_family_keys
            ):
                return reject("GLOBAL_AUCTION_RESTRICTED_CARRIER_MISSING")
        day0_only_scope = bool(
            restricted_families
            and event_tuple
            and all(
                event.event_type == "DAY0_EXTREME_UPDATED"
                for event in event_tuple
            )
        )
        full_scope = scan_current_global_auction_scope(
            world_conn=world_conn,
            forecasts_conn=forecast_conn,
            decision_at_utc=scope_at,
            held_families=held_families,
            restrict_to_families=restricted_families,
            day0_only=day0_only_scope,
        )
        log_stage("scope_scan", families=len(full_scope.events_by_family))
        if cancelled("scope_scan"):
            return reject("GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED")
        if superseded("scope_scan"):
            return reject("GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT")
        decision_scope = full_scope
        if restrict_to_family_keys is not None:
            current_family_keys = frozenset(full_scope.family_keys)
            missing_family_keys = restrict_to_family_keys.difference(
                current_family_keys
            )
            if missing_family_keys:
                return reject(
                    "GLOBAL_AUCTION_RESTRICTED_SCOPE_MISSING:"
                    + ",".join(sorted(missing_family_keys))
                )
            decision_scope = current_global_auction_scope_from_events(
                tuple(
                    event
                    for family_key, event in full_scope.events_by_family
                    if family_key in restrict_to_family_keys
                ),
                captured_at_utc=scope_at,
            )
            _LOG.info(
                "global batch restricted scope: families=%d global_families=%d",
                len(decision_scope.events_by_family),
                len(full_scope.events_by_family),
            )
        if not buy_candidates_enabled:
            held_family_keys = frozenset(
                weather_family_id(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                )
                for city, target_date, metric in held_families
            )
            reduce_only_events = tuple(
                event
                for family_key, event in full_scope.events_by_family
                if family_key in held_family_keys
            )
            if not reduce_only_events:
                return reject("GLOBAL_AUCTION_NO_REDUCE_ONLY_FAMILY")
            decision_scope = current_global_auction_scope_from_events(
                reduce_only_events,
                captured_at_utc=scope_at,
            )
            _LOG.info(
                "global batch reduce-only scope: held_families=%d global_families=%d",
                len(decision_scope.events_by_family),
                len(full_scope.events_by_family),
            )
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
                for _, event in decision_scope.events_by_family
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
        full_scope_event_by_family = dict(decision_scope.events_by_family)
        ineligible_by_family: dict[str, str] = {}
        ineligible_by_event: dict[str, str] = {}
        for family_key, scope_event in decision_scope.events_by_family:
            if cancelled(f"prepare_family:{family_key}"):
                return reject("GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED")
            if superseded(f"prepare_family:{family_key}"):
                return reject("GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT")
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
        if cancelled("prepare_families"):
            return reject("GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED")
        if superseded("prepare_families"):
            return reject("GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT")
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
            full_scope=decision_scope,
            eligible_scope=scope,
            probability_witnesses=probabilities,
            ineligible_by_family=ineligible_by_family,
        )
        book_epoch = None
        if current_book_epoch_provider is not None:
            if cancelled("book_epoch_start"):
                return reject("GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED")
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
        if cancelled("book_epoch_fence"):
            return reject("GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED")
        if superseded("book_epoch_fence"):
            return reject("GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT")
        # The complete q/book/wealth cut is immutable from this point forward.
        # Later global wakes belong to the next epoch. Consulting them again
        # below would starve actuation whenever unrelated books update
        # continuously; the selected action still crosses exact JIT
        # probability/book/wealth preflight before any venue side effect.
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

        def capture_selection_wealth():
            state = portfolio_state_provider() if portfolio_state_provider else None
            if state is None and hasattr(trade_conn, "execute"):
                from src.state.portfolio import load_runtime_open_portfolio

                state = load_runtime_open_portfolio(trade_conn)
            # Wealth is a selection-time fact.  Validating a newly refreshed ledger
            # row against the earlier book-capture clock can falsely classify the
            # row as future data when the collateral heartbeat lands mid-auction.
            witness = current_portfolio_wealth_witness(
                trade_conn,
                decision_at_utc=current_time(),
                max_age=wealth_age,
                portfolio_state=state,
            )
            return state, witness

        selection_state, selection_wealth = capture_selection_wealth()

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
                    wealth_witness=selection_wealth,
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
            current_probability_authorities = (
                _current_probability_authorities(attempt_probabilities)
            )

            def probability_resolver(family_key):
                return current_probability_authorities.get(family_key)

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
                cancelled=selection_cancelled,
            )
            if (
                selected.decision.candidate is None
                and selected.decision.no_trade_reason
                == "GLOBAL_SELECTION_CANCELLED"
            ):
                return selected
            _store_global_auction_receipt(
                trade_conn,
                selected=selected,
                selection_epoch_identity=attempt_selection_epoch_identity,
                selection_cut_at_utc=scope_at,
                decision_at_utc=selection_at,
                probability_manifest=_probability_manifest(
                    attempt_probabilities
                ),
                full_scope_identity=decision_scope.scope_identity,
                full_scope_family_keys=decision_scope.family_keys,
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
                book_asset_states=(
                    tuple(
                        getattr(attempt_book_epoch, "asset_states", ()) or ()
                    )
                    if attempt_book_epoch is not None
                    else ()
                ),
                wealth_witness=selection_wealth,
                fractional_kelly_multiplier=fractional_kelly_multiplier,
                excluded_by_family=preflight_excluded_by_family,
                excluded_by_candidate=preflight_excluded_by_candidate,
                book_captured_at_utc=(
                    attempt_book_epoch.captured_at_utc
                    if attempt_book_epoch is not None
                    else None
                ),
                book_max_age=(
                    attempt_book_epoch.max_age
                    if attempt_book_epoch is not None
                    else None
                ),
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
                if cancelled("winner_preflight_start"):
                    return reject(
                        "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED"
                    )
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
                if cancelled("winner_preflight"):
                    return reject(
                        "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED"
                    )
                log_stage("winner_preflight", families=len(prepared_by_event))
                after_preflight = venue_submit_count()
                if after_preflight != before_preflight:
                    return reject("GLOBAL_PREFLIGHT_VENUE_SIDE_EFFECT")
                _store_global_preflight_receipt(
                    trade_conn,
                    selected=selected,
                    preflight=preflight,
                    authority=preflight_authority,
                    checked_at_utc=preflight_at,
                    winner_event_id=winner_id,
                    venue_submit_count_before=before_preflight,
                    venue_submit_count_after=after_preflight,
                )
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
                    candidate = selected.decision.candidate
                    if candidate is None:
                        return reject("GLOBAL_REAUCTION_SELECTED_CANDIDATE_MISSING")
                    try:
                        next_book_epoch = _book_epoch_with_replacement_candidate(
                            attempt_book_epoch,
                            candidate,
                            preflight.replacement_candidate,
                        )
                    except Exception as exc:  # noqa: BLE001 - invalid JIT evidence blocks
                        return reject(
                            "GLOBAL_REAUCTION_CURVE_OVERLAY_FAILED:"
                            f"{type(exc).__name__}:{exc}"
                        )
                    if (
                        next_book_epoch.witness_identity
                        == attempt_book_epoch.witness_identity
                    ):
                        return reject(
                            "GLOBAL_REAUCTION_CURVE_NO_PROGRESS:"
                            f"{preflight.reason or preflight.status}"
                        )
                    attempt_book_epoch = next_book_epoch
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
                        _global_preflight_exhaustion_reason(
                            selected.decision.no_trade_reason,
                            excluded_by_family=excluded_by_family,
                            excluded_by_candidate=excluded_by_candidate,
                        )
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
        if cancelled("actuation"):
            return reject("GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED")
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
