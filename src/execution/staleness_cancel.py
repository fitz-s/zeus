# Created: 2026-07-03
# Last reused or audited: 2026-10-01
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#   (SCH-W1.2-ORDER-STATE) §"C3" (cancel-set goes out through the existing CANCEL intent);
#   standing ENTRY keep-by-value law (operator, 2026-09-30): an open ENTRY rest keeps
#   working toward its current fractional-Kelly target; age and posterior identity are
#   revaluation triggers, never cancellation authority.
"""C3: value every open ENTRY rest -> KEEP / CANCEL / DEFER -> reconciled re-solve.

Every recurring tick (and every belief/Day0 wake for the rest's family)
revalues each open ENTRY rest as the order it is: its open remainder at its
own limit, on the selector's own laws, current probability, wealth and
holdings. ``entry_rest_disposition`` turns that valuation into one action:

- KEEP: the authority this valuation used is journaled as an append-only
  ``decision_log`` row bound to the same venue order id; no venue call. The
  submission certificate and ``venue_commands.q_version`` are never rewritten.
- CANCEL: a persisted batch cancel (``cancel_commands_batch``) with a named
  reason; the venue has no amend, so a smaller target is a cancel and the
  family's confirmed-cancel redecision sizes a fresh order. Unavailable or
  blocked authority cancels protectively; it never licenses further fills.
- DEFER: authority this process has not loaded yet (the allocator before its
  first publish, the fit corpus before its first install) yields no decision
  this pass; any other missing authority still fails closed.

All reads finish before the TRADE write lease (INV-37). Day0 dead-bin/anomaly
classification is a separate, unconditional protective lane merged before the
single batch cancel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Callable, Iterable, Mapping

from src.state.canonical_projections import OPEN_ORDER_FACT_STATES
from src.state.order_state_predicates import entry_rest_disposition

logger = logging.getLogger("zeus.staleness_cancel")

UTC = timezone.utc

# Latest-fact states that mean "this order is resting open at the venue" — the single
# canonical open-order-fact set (relocated from maker_rest_escalation.OPEN_REST_FACT_STATES;
# main.py's _edli_open_maker_rests_for_screen imports this constant from here now).
OPEN_REST_FACT_STATES = tuple(sorted(OPEN_ORDER_FACT_STATES))

FamilyKey = tuple[str, str, str]

STANDING_ENTRY_DECISION_MODE = "standing_entry_revaluation"
# Origin of a confirmed C3 cancel's redecision: the family just lost its rest
# and is owed the retired rest-pull continuity (phase-exempt emit, reactor
# expiry grace).
C3_CANCEL_REDECISION_ORIGIN = "c3_staleness_cancel"
_MICRO = Decimal("1000000")


def _venue_commands_q_version_select_expr(conn: sqlite3.Connection) -> str:
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(venue_commands)")}
    except sqlite3.DatabaseError:
        columns = set()
    return "vc.q_version" if "q_version" in columns else "NULL"


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}
    except sqlite3.DatabaseError:
        return set()


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone() is not None
    except sqlite3.DatabaseError:
        return False


def _decision_source_details_from_submit_payload(payload_json: object) -> dict[str, object] | None:
    try:
        payload = json.loads(str(payload_json or "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    capability = payload.get("execution_capability")
    if not isinstance(capability, dict):
        return None
    components = capability.get("components")
    if not isinstance(components, list):
        return None
    details: dict[str, object] | None = None
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("component") != "decision_source_integrity":
            continue
        raw_details = component.get("details")
        if isinstance(raw_details, dict):
            details = raw_details
        break
    if not isinstance(details, dict):
        return None
    return details


def _decision_q_authority_from_details(details: dict[str, object] | None) -> str | None:
    if not isinstance(details, dict):
        return None
    authority_tier = str(details.get("authority_tier") or "").strip().upper()
    source_role = str(details.get("forecast_source_role") or "").strip()
    if authority_tier == "FORECAST" and source_role == "entry_primary":
        return "forecast_entry_primary"
    if authority_tier in {"OBSERVATION", "DAY0_OBSERVATION"} or source_role in {
        "day0_live_observation",
        "day0_observed_probability",
    }:
        return "day0_observation"
    return None


def _decision_q_version_from_details(details: dict[str, object] | None) -> str | None:
    if not isinstance(details, dict):
        return None
    if str(details.get("authority_tier") or "") != "FORECAST":
        return None
    if str(details.get("forecast_source_role") or "") != "entry_primary":
        return None
    if str(details.get("source_id") or "") != "openmeteo_ecmwf_ifs9_bayes_fusion":
        return None
    for key in ("posterior_identity_hash", "raw_payload_hash"):
        value = str(details.get(key) or "").strip()
        if len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value):
            return value
    return None


def find_open_entry_rests(
    conn: sqlite3.Connection,
    *,
    include_pending_cancels: bool = False,
) -> list[dict[str, Any]]:
    """Every open ENTRY rest, with its stamped ``q_version``. No deadline filter:
    disposition is a current valuation, never an age or SQL predicate.

    When ``include_pending_cancels`` is true, also return ENTRY BUY commands in
    ``CANCEL_PENDING`` only when their latest command event is the matching,
    batch-owned ``CANCEL_REQUESTED`` intent. This is the C3 retry reader; the
    default preserves the day0 consumer's original open-command scope.
    """
    placeholders = ",".join("?" for _ in OPEN_REST_FACT_STATES)
    q_version_expr = _venue_commands_q_version_select_expr(conn)
    venue_command_columns = _table_columns(conn, "venue_commands")
    snapshot_id_select = (
        "vc.snapshot_id AS snapshot_id" if "snapshot_id" in venue_command_columns else "NULL AS snapshot_id"
    )
    snapshot_join = ""
    snapshot_min_order_select = "NULL AS min_order_size"
    if "snapshot_id" in venue_command_columns and _has_table(conn, "executable_market_snapshots"):
        snapshot_join = """
        LEFT JOIN executable_market_snapshots snap
          ON snap.snapshot_id = vc.snapshot_id
        """
        snapshot_min_order_select = "snap.min_order_size AS min_order_size"
    submit_payload_join = ""
    submit_payload_select = "NULL AS submit_payload_json"
    if _has_table(conn, "venue_command_events"):
        submit_payload_join = """
        LEFT JOIN (
            SELECT command_id, payload_json
            FROM (
                SELECT command_id, payload_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY command_id ORDER BY sequence_no DESC
                       ) AS rn
                FROM venue_command_events
                WHERE event_type = 'SUBMIT_REQUESTED'
            )
            WHERE rn = 1
        ) submit_payload
          ON submit_payload.command_id = vc.command_id
        """
        submit_payload_select = "submit_payload.payload_json AS submit_payload_json"
    pending_event_join = ""
    pending_event_select = "NULL AS latest_cancel_event_type, NULL AS latest_cancel_payload_json"
    if include_pending_cancels and _has_table(conn, "venue_command_events"):
        pending_event_join = """
        LEFT JOIN venue_command_events latest_command_event
          ON latest_command_event.command_id = vc.command_id
         AND latest_command_event.sequence_no = (
                SELECT sequence_no
                  FROM venue_command_events
                 WHERE command_id = vc.command_id
                 ORDER BY sequence_no DESC
                 LIMIT 1
             )
        """
        pending_event_select = (
            "latest_command_event.event_type AS latest_cancel_event_type, "
            "latest_command_event.payload_json AS latest_cancel_payload_json"
        )
    command_states = "'ACKED', 'POST_ACKED', 'PARTIAL'"
    if include_pending_cancels:
        command_states += ", 'CANCEL_PENDING'"
    rows = conn.execute(
        f"""
        WITH latest_facts AS (
            SELECT venue_order_id, state, matched_size,
                   ROW_NUMBER() OVER (
                       PARTITION BY venue_order_id ORDER BY local_sequence DESC
                   ) AS rn
            FROM venue_order_facts
        )
        SELECT vc.command_id, vc.venue_order_id, vc.token_id, vc.market_id,
               vc.created_at, {q_version_expr} AS q_version, lf.state AS fact_state, lf.matched_size,
               {snapshot_id_select}, {snapshot_min_order_select}, {submit_payload_select},
               vc.side AS command_side, vc.state AS command_state,
               {pending_event_select}
        FROM venue_commands vc
        JOIN latest_facts lf
          ON lf.venue_order_id = vc.venue_order_id AND lf.rn = 1
        {snapshot_join}
        {submit_payload_join}
        {pending_event_join}
        WHERE vc.intent_kind = 'ENTRY'
          AND upper(vc.side) = 'BUY'
          AND vc.venue_order_id IS NOT NULL
          AND vc.venue_order_id != ''
          AND vc.state IN ({command_states})
          AND lf.state IN ({placeholders})
        """,
        OPEN_REST_FACT_STATES,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            item = dict(row)
        else:
            item = {
                "command_id": row[0],
                "venue_order_id": row[1],
                "token_id": row[2],
                "market_id": row[3],
                "created_at": row[4],
                "q_version": row[5],
                "fact_state": row[6],
                "matched_size": row[7],
                "snapshot_id": row[8],
                "min_order_size": row[9],
                "submit_payload_json": row[10],
                "command_side": row[11],
                "command_state": row[12],
                "latest_cancel_event_type": row[13],
                "latest_cancel_payload_json": row[14],
            }
        command_state = str(item.get("command_state") or "").strip().upper()
        pending_cancel = False
        if command_state == "CANCEL_PENDING":
            try:
                pending_payload = json.loads(
                    str(item.get("latest_cancel_payload_json") or "{}")
                )
            except (TypeError, ValueError):
                pending_payload = None
            pending_cancel = (
                include_pending_cancels
                and str(item.get("latest_cancel_event_type") or "").strip().upper()
                == "CANCEL_REQUESTED"
                and isinstance(pending_payload, dict)
                and str(pending_payload.get("venue_order_id") or "").strip()
                == str(item.get("venue_order_id") or "").strip()
                and pending_payload.get("batch") is True
            )
            if not pending_cancel:
                continue
        if include_pending_cancels:
            item["command_state"] = command_state
            item["pending_cancel"] = pending_cancel
        else:
            item.pop("command_state", None)
        item.pop("latest_cancel_event_type", None)
        item.pop("latest_cancel_payload_json", None)
        source_details = _decision_source_details_from_submit_payload(item.get("submit_payload_json"))
        q_authority = _decision_q_authority_from_details(source_details)
        if q_authority:
            item["q_version_authority"] = q_authority
        if not item.get("q_version"):
            recovered = _decision_q_version_from_details(source_details)
            if recovered:
                item["q_version"] = recovered
                item["q_version_source"] = "submit_requested_decision_source"
                item["q_version_authority"] = "forecast_entry_primary"
        item.pop("submit_payload_json", None)
        out.append(item)
    return out


def resolve_order_families(
    entries: Iterable[Mapping[str, Any]],
    trade_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
) -> dict[str, FamilyKey | None]:
    """Resolve each order through its own submission snapshot, never a latest alias.

    The order's immutable ``snapshot_id`` binds its token to one condition;
    ``market_events`` binds that condition to exactly one city/date/metric
    family. HIGH and LOW for the same city/date are different families, so an
    ambiguous condition resolves to ``None`` (protective cancel), never to a
    guessed metric.
    """
    out: dict[str, FamilyKey | None] = {}
    for entry in entries:
        command_id = str(entry.get("command_id") or "")
        token_id = str(entry.get("token_id") or "")
        snapshot_id = str(entry.get("snapshot_id") or "")
        try:
            row = trade_conn.execute(
                "SELECT condition_id, yes_token_id, no_token_id "
                "FROM executable_market_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if row is None or not token_id or token_id not in (str(row[1]), str(row[2])):
                out[command_id] = None
                continue
            families = {
                tuple(str(value or "").strip() for value in family_row)
                for family_row in forecasts_conn.execute(
                    "SELECT DISTINCT city, target_date, temperature_metric "
                    "FROM market_events WHERE condition_id = ?",
                    (str(row[0]),),
                ).fetchall()
            }
        except sqlite3.Error:
            out[command_id] = None
            continue
        family = next(iter(families)) if len(families) == 1 else None
        out[command_id] = (
            family
            if family is not None and all(family) and family[2] in {"high", "low"}
            else None
        )
    return out


def _merge_cancel_proposals(
    proposals_by_lane: Iterable[tuple[str, Iterable[dict[str, Any]]]],
    families_by_command: dict[str, FamilyKey | None],
) -> list[dict[str, Any]]:
    """Deduplicate commands without discarding any lane's reason evidence."""

    merged: dict[str, dict[str, Any]] = {}
    for lane, proposals in proposals_by_lane:
        for proposal in proposals:
            command_id = str(proposal["command_id"])
            family = proposal.get("family")
            if family and families_by_command.get(command_id) is None:
                families_by_command[command_id] = tuple(family)
            current = merged.get(command_id)
            if current is None:
                current = dict(proposal)
                current["cancel_detail_by_lane"] = {
                    lane: proposal.get("cancel_detail")
                }
                merged[command_id] = current
                continue
            reasons = {
                reason
                for value in (
                    current.get("cancel_reason"),
                    proposal.get("cancel_reason"),
                )
                for reason in str(value or "").split("+")
                if reason
            }
            current["cancel_reason"] = "+".join(sorted(reasons))
            current["cancel_detail_by_lane"][lane] = proposal.get("cancel_detail")
    return list(merged.values())


# ---------------------------------------------------------------------------
# Standing ENTRY valuation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StandingEntryValuation:
    """One open ENTRY rest's disposition and the authority that produced it."""

    command_id: str
    venue_order_id: str
    token_id: str
    family: FamilyKey | None
    action: str  # KEEP | CANCEL | DEFER
    reason: str
    evidence: Mapping[str, Any]


# One valuation pass may hold its trade read snapshot and consult the
# correction resolver no longer than this; an expired pass cancels nothing it
# could not value (DEFER), and the next pass values on fresher inputs.
STANDING_ENTRY_PASS_BUDGET_SECONDS = 60.0


def _deferred(entry: Mapping[str, Any], family: FamilyKey | None, reason: str) -> StandingEntryValuation:
    """Authority this process has not loaded yet: no decision, no venue action."""

    return StandingEntryValuation(
        command_id=str(entry.get("command_id") or ""),
        venue_order_id=str(entry.get("venue_order_id") or ""),
        token_id=str(entry.get("token_id") or ""),
        family=family,
        action="DEFER",
        reason=reason,
        evidence={"authority_valid": None, "reason": reason},
    )


def _authority_pending_reason(exc: BaseException | None = None) -> str | None:
    """Name authority that is not loaded yet; every other unknown is None.

    Only two states qualify: the allocator never published in this process
    (an ``allocator_not_configured`` denial before the first publish), and the
    canonical fit corpus never installed by its builder. A stale or cleared
    allocator, or a missing fit once a corpus is installed, is lost authority
    and fails closed.
    """

    from src.calibration.market_anchored_live_fit import canonical_entry_fit_corpus_pending
    from src.risk_allocator import AllocationDenied, global_allocator_ever_published

    if (
        isinstance(exc, AllocationDenied)
        and exc.decision.reason == "allocator_not_configured"
        and not global_allocator_ever_published()
    ):
        return "ENTRY_REST_AUTHORITY_PENDING:allocator_not_published"
    if canonical_entry_fit_corpus_pending():
        return "ENTRY_REST_AUTHORITY_PENDING:fit_corpus_not_installed"
    return None


def _protective(entry: Mapping[str, Any], family: FamilyKey | None, reason: str) -> StandingEntryValuation:
    """Missing or blocked authority cancels: it never licenses further fills."""

    return StandingEntryValuation(
        command_id=str(entry.get("command_id") or ""),
        venue_order_id=str(entry.get("venue_order_id") or ""),
        token_id=str(entry.get("token_id") or ""),
        family=family,
        action="CANCEL",
        reason=reason,
        evidence={"authority_valid": False, "reason": reason},
    )


@dataclass(frozen=True)
class OwnCommandCapital:
    """One open ENTRY command's own capital, read with the wealth witness.

    ``at_risk_micro``: this command's cash the witness holds back (its open
    reservation and unsettled outgoing deduction). ``position`` is the
    runtime projection the command's fills land on, if any.
    """

    command_id: str
    token_id: str
    size: Decimal
    price: Decimal
    filled_shares: Decimal
    at_risk_micro: int
    position: Any | None


def _own_command_capital(
    trade_conn: sqlite3.Connection,
    rest: Mapping[str, Any],
    *,
    positions: tuple[Any, ...],
) -> OwnCommandCapital:
    from src.state.collateral_ledger import _proven_filled_size

    command_id = str(rest["command_id"])
    at_risk = trade_conn.execute(
        "SELECT COALESCE((SELECT SUM(amount) FROM collateral_reservations "
        "WHERE command_id = ? AND reservation_type = 'PUSD_BUY' AND released_at IS NULL), 0) "
        "+ COALESCE((SELECT SUM(amount_micro) FROM collateral_unsettled_proceeds "
        "WHERE command_id = ? AND direction = 'OUTGOING_DEDUCTION' AND settled_at IS NULL), 0)",
        (command_id, command_id),
    ).fetchone()[0]
    filled = max(
        Decimal(str(rest.get("matched_size") or "0")),
        _proven_filled_size(trade_conn, command_id),
    )
    position_id = str(rest.get("position_id") or "")
    position = next(
        (
            p
            for p in positions
            if position_id
            and position_id
            in {str(getattr(p, "position_id", "") or ""), str(getattr(p, "trade_id", "") or "")}
        ),
        None,
    )
    return OwnCommandCapital(
        command_id=command_id,
        token_id=str(rest["token_id"]),
        size=Decimal(str(rest["size"])),
        price=Decimal(str(rest["price"])),
        filled_shares=filled,
        at_risk_micro=int(at_risk or 0),
        position=position,
    )


def _own_reservation_wealth(
    wealth: Any,
    own: OwnCommandCapital,
    *,
    obligation_rows: list[tuple],
    positions: tuple[Any, ...],
    native_holdings_micro: Mapping[str, int],
):
    """Current wealth as this order's own redecision sees it.

    The counterfactual is the order's unfilled remainder never placed: its
    cash returns to spendable exactly as a terminal cancel would release it
    (``filled_reservation_amount``, the conversion law of
    ``convert_reservation_on_fill``), and its entry obligation resolves. The
    obligation endowment is then recomputed by the same
    ``pending_entry_endowments_from_rows`` the wealth witness used, so the
    filled part is counted once: through the position projection the fill
    already moved, or, only when no projection carries it yet, as a pending
    endowment. Every other command's reservations and obligations stay
    exactly as the selector sees them. A valuation input only; it never
    writes a reservation or an order row.
    """
    from src.contracts.strategy_capital_allocation import StrategyCapitalAllocationWitness
    from src.engine.global_auction_universe import pending_entry_endowments_from_rows
    from src.solve.solver import PortfolioWealthWitness, portfolio_wealth_identity
    from src.state.collateral_ledger import filled_reservation_amount

    if own.size <= 0 or own.filled_shares < 0 or own.at_risk_micro < 0:
        raise ValueError("ENTRY_REST_OWN_CAPITAL_INVALID")
    base_pending, _identities, base_cost, _ids = pending_entry_endowments_from_rows(
        obligation_rows, positions=positions, native_holdings_micro=native_holdings_micro
    )
    uncertain = tuple(
        row for row in wealth.pending_entry_endowments_micro if row[0].startswith("position_claim:")
    )
    if tuple(sorted((*base_pending, *uncertain))) != tuple(wealth.pending_entry_endowments_micro):
        # The rows must reproduce the witness exactly: otherwise this view would
        # be built on a different ledger than the one the selector sizes on.
        raise ValueError("ENTRY_REST_OBLIGATION_ROWS_NOT_THE_WITNESS")
    cf_pending, _identities, cf_cost, _ids = pending_entry_endowments_from_rows(
        obligation_rows,
        positions=positions,
        native_holdings_micro=native_holdings_micro,
        resolved_command_ids=frozenset({own.command_id}),
    )
    own_position_micro = (
        int((Decimal(str(getattr(own.position, "shares", 0) or 0)) * _MICRO).to_integral_value())
        if own.position is not None
        else 0
    )
    filled_micro = int((own.filled_shares * _MICRO).to_integral_value(rounding=ROUND_FLOOR))
    # A fill no runtime projection carries yet is still owned exposure.
    unprojected = max(0, filled_micro - own_position_micro)
    pending = list(cf_pending)
    if unprojected:
        pending.append((own.command_id, own.token_id, unprojected))
    pending_tuple = tuple(sorted((*pending, *uncertain)))

    spent_micro = filled_reservation_amount(
        own.at_risk_micro, order_size=own.size, filled=own.filled_shares
    )
    credit = Decimal(own.at_risk_micro - spent_micro) / _MICRO
    commitments = dict(wealth.native_commitments_micro)
    base_own_cost = int(base_cost.get(own.command_id, 0))
    cf_own_cost = int(cf_cost.get(own.command_id, 0))
    if unprojected:
        cf_own_cost = int(
            (Decimal(unprojected) / _MICRO * own.price * _MICRO).to_integral_value()
        )
    removed_cost = base_own_cost - cf_own_cost
    if removed_cost < 0 or removed_cost > commitments.get(own.token_id, 0):
        raise ValueError("ENTRY_REST_OWN_COMMITMENT_NOT_IN_WEALTH")
    commitments[own.token_id] = commitments.get(own.token_id, 0) - removed_cost
    if credit > wealth.reservations_usd:
        raise ValueError("ENTRY_REST_OWN_RESERVATION_NOT_IN_WEALTH")

    allocation = wealth.strategy_capital_allocation
    config: dict[str, object] = {"mode": allocation.mode}
    if allocation.configured_value is not None:
        config["value"] = allocation.configured_value
    if allocation.configured_buy_commitment_limit_usd is not None:
        config["buy_commitment_limit_usd"] = allocation.configured_buy_commitment_limit_usd
    floor = wealth.wealth_floor_usd + credit
    spendable = wealth.spendable_cash_usd + credit
    committed = sum((Decimal(v) / _MICRO for v in commitments.values()), Decimal("0"))
    updated_allocation = StrategyCapitalAllocationWitness.build(
        capital_basis_usd=floor + committed,
        committed_capital_usd=committed,
        venue_spendable_cash_usd=spendable,
        allocation=config,
    )
    pending_delta = sum(int(r[2]) for r in pending_tuple) - sum(
        int(r[2]) for r in wealth.pending_entry_endowments_micro
    )
    position_set_hash = hashlib.sha256(
        json.dumps(
            [wealth.position_set_hash, own.command_id, str(credit), [list(r) for r in pending_tuple]],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    fields = dict(
        ledger_snapshot_id=wealth.ledger_snapshot_id,
        position_set_hash=position_set_hash,
        wealth_floor_usd=floor,
        wealth_ceiling_usd=wealth.wealth_ceiling_usd + credit + Decimal(pending_delta) / _MICRO,
        spendable_cash_usd=spendable,
        reservations_usd=wealth.reservations_usd - credit,
        collateral_authority=wealth.collateral_authority,
        captured_at_utc=wealth.captured_at_utc,
    )
    return PortfolioWealthWitness(
        **fields,
        strategy_capital_allocation=updated_allocation,
        max_age=wealth.max_age,
        witness_identity=portfolio_wealth_identity(
            **fields,
            strategy_capital_allocation_identity=updated_allocation.witness_identity,
        ),
        native_holdings_micro=wealth.native_holdings_micro,
        pending_entry_endowments_micro=pending_tuple,
        native_commitments_micro=tuple(
            sorted((token, amount) for token, amount in commitments.items() if amount)
        ),
    )


def _rest_candidate(
    entry: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    binding: Any,
    side: str,
    probability_witness: Any,
    capacity: Decimal,
    ledger_snapshot_id: str,
    now: datetime,
):
    """The open rest as the selector's own MAKER_REST BUY proposal at its limit.

    The economic curve is one level at the rest's own limit, ``capacity``
    shares deep (the open remainder: the order already exists, so no cash
    envelope re-sizes it), zero maker fee, its submission snapshot's tick and
    lot. The executable ask ladder is that snapshot's; it only satisfies the
    candidate's non-crossing shape and is never read by the scorer.

    The value is conditional on fill, so the maker witness is the certain-fill one
    (one outcome: probability 1, full fill at the limit). The common
    expected-growth law (``bind_score_capital_horizon``) then values the
    order over its capital horizon exactly as the selector values a filled
    maker proposal; the fill-model period is a candidate-shape field that a
    full-fill outcome never weights, not an order deadline.
    """
    from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
    from src.engine.event_reactor_adapter import _native_side_cost_curve_from_snapshot_row
    from src.solve.solver import (
        CurrentMakerFillWitness,
        GlobalSingleOrderCandidate,
        MakerFillOutcome,
        current_maker_fill_witness_identity,
        executable_curve_identity,
        maker_fill_candidate_binding_identity,
    )
    from src.strategy.live_inference.mode_consistent_ev import (
        MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    )

    price = Decimal(str(entry["price"]))
    command_id = str(entry["command_id"])
    asks = _native_side_cost_curve_from_snapshot_row(
        dict(snapshot), side=side, token_id=str(entry["token_id"])
    )
    proposal = ExecutableCostCurve(
        token_id=asks.token_id,
        side=asks.side,
        snapshot_id=asks.snapshot_id,
        book_hash=asks.book_hash,
        levels=(BookLevel(price=price, size=capacity),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=asks.min_tick,
        min_order_size=asks.min_order_size,
        quote_ttl=asks.quote_ttl,
    )
    deadline = float(MAKER_REST_ESCALATION_DEADLINE_MINUTES)
    asset_epoch_identity = f"standing_entry:{command_id}:{asks.snapshot_id}"
    witness_fields = dict(
        candidate_binding_identity=maker_fill_candidate_binding_identity(
            action="BUY",
            family_key=probability_witness.family_key,
            bin_id=binding.bin_id,
            condition_id=binding.condition_id,
            side=side,
            token_id=str(entry["token_id"]),
            ledger_snapshot_id=ledger_snapshot_id,
            position_id=None,
            held_shares=None,
            asset_epoch_identity=asset_epoch_identity,
            proposal_identity=executable_curve_identity(proposal),
        ),
        asset_epoch_identity=asset_epoch_identity,
        book_snapshot_id=proposal.snapshot_id,
        book_hash=proposal.book_hash,
        limit_price=price,
        rest_deadline_minutes=deadline,
        source_identity="standing_entry_conditional_on_fill",
        model_identity="certain_full_fill",
        sample_identity=command_id,
        training_cutoff_at_utc=now,
        issued_at_utc=now,
        valid_until_at_utc=now,
        outcomes=(
            MakerFillOutcome(
                probability=Decimal("1"),
                fill_fraction=Decimal("1"),
                proceeds_per_share_usd=-price,
            ),
        ),
    )
    witness = CurrentMakerFillWitness(
        witness_identity=current_maker_fill_witness_identity(**witness_fields),
        **witness_fields,
    )
    return GlobalSingleOrderCandidate(
        candidate_id="standing_entry:" + command_id,
        family_key=probability_witness.family_key,
        bin_id=binding.bin_id,
        condition_id=binding.condition_id,
        side=side,  # type: ignore[arg-type]
        token_id=str(entry["token_id"]),
        probability_witness_identity=probability_witness.witness_identity,
        book_snapshot_id=asks.snapshot_id,
        book_captured_at_utc=now,
        execution_curve_identity=executable_curve_identity(asks),
        ledger_snapshot_id=ledger_snapshot_id,
        executable_cost_curve=asks,
        resolution_identity=probability_witness.resolution_identity,
        neg_risk=bool(snapshot.get("neg_risk")),
        execution_mode="MAKER_REST",
        proposal_cost_curve=proposal,
        rest_deadline_minutes=deadline,
        fill_probability_source=witness.witness_identity,
        maker_fill_witness=witness,
        asset_epoch_identity=asset_epoch_identity,
    )


def value_standing_entry(
    entry: Mapping[str, Any],
    *,
    family: FamilyKey,
    snapshot: Mapping[str, Any],
    prepared: Any,
    wealth: Any,
    holdings_snapshot: Any,
    fractional_kelly_multiplier: Decimal,
    capital_limit_usd: Decimal,
    payoff_q_correction_resolver: Any,
    resolution_at: datetime | None,
    now: datetime,
) -> StandingEntryValuation:
    """Disposition of one open ENTRY rest from the selector's own BUY laws.

    The rest is valued as the order it is (``score_existing_buy_expected``):
    exactly its open remainder at its own limit, on the selector's
    posterior-mean expected objective, given the holding its own fills already
    created (counted once). In selector order: the market-anchored correction
    (``resolve_candidate_payoff_q_correction``), the post-calibration BUY
    refutation (``buy_probability_rejection``), the remainder's expected
    growth on the common axis (``bind_score_capital_horizon``) against the
    selector's Kelly holdings at that limit, then ``entry_rest_disposition``
    and the selector's per-token capital envelope. The lot floor and the cash
    envelope size only a fresh order; ``wealth`` already credits the rest's
    own unfilled reservation back.
    """
    from src.engine.global_batch_runtime import _prepared_candidate_payoff_q_lcb_caps
    from src.engine.global_single_order_auction import (
        _candidate_portfolio_endowment,
        buy_probability_rejection,
        day0_saturated_sides_by_family,
    )
    from src.solve.solver import (
        family_payoff_point_q,
        resolve_candidate_payoff_q_correction,
        score_existing_buy_expected,
    )

    probability_witness = prepared.probability_witness
    command_id = str(entry["command_id"])
    token_id = str(entry["token_id"])
    binding = next(
        (
            b
            for b in probability_witness.bindings
            if token_id in (str(b.yes_token_id or ""), str(b.no_token_id or ""))
        ),
        None,
    )
    if binding is None or str(binding.condition_id) != str(snapshot.get("condition_id") or ""):
        return _protective(entry, family, "ENTRY_REST_CURRENT_TOPOLOGY_MISMATCH")
    side = "YES" if token_id == str(binding.yes_token_id) else "NO"
    price = Decimal(str(entry["price"]))
    size = Decimal(str(entry["size"]))
    filled = Decimal(str(entry.get("matched_size") or "0"))
    remaining = size - filled
    if remaining <= 0:
        return _protective(entry, family, "ENTRY_REST_REMAINDER_NOT_POSITIVE")
    base_evidence: dict[str, Any] = {
        "authority_valid": True,
        "probability_witness_identity": probability_witness.witness_identity,
        "q_version": probability_witness.q_version,
        "posterior_identity_hash": probability_witness.posterior_identity_hash,
        "authority_certificate_hash": probability_witness.authority_certificate_hash,
        "submission_q_version": entry.get("q_version"),
        "wealth_witness_identity": wealth.witness_identity,
        "ledger_snapshot_id": wealth.ledger_snapshot_id,
        "limit_price": str(price),
        "filled_shares": str(filled),
        "open_remaining": str(remaining),
    }

    def valued(action: str, reason: str, evidence: Mapping[str, Any]) -> StandingEntryValuation:
        return StandingEntryValuation(
            command_id=command_id,
            venue_order_id=str(entry["venue_order_id"]),
            token_id=token_id,
            family=family,
            action=action,
            reason=reason,
            evidence={**base_evidence, **evidence},
        )

    # The proposal curve carries the remainder itself; the cash envelope is
    # a fresh order's bound, and the remainder's cash is already reserved.
    candidate = _rest_candidate(
        entry,
        snapshot=snapshot,
        binding=binding,
        side=side,
        probability_witness=probability_witness,
        capacity=remaining,
        ledger_snapshot_id=wealth.ledger_snapshot_id,
        now=now,
    )
    raw_q = family_payoff_point_q(probability_witness, bin_id=binding.bin_id, side=side)
    if raw_q is None:
        return _protective(entry, family, "ENTRY_REST_POINT_PROBABILITY_UNAVAILABLE")
    try:
        correction = resolve_candidate_payoff_q_correction(
            candidate,
            raw_q=raw_q,
            witness=probability_witness,
            resolver=payoff_q_correction_resolver,
            decision_at_utc=now,
        )
    except Exception as exc:  # noqa: BLE001 - a failed correction never authorizes raw q
        return _protective(entry, family, f"ENTRY_REST_CALIBRATED_Q_UNAVAILABLE:{exc}")
    q = correction.corrected_q if correction is not None else raw_q
    q_evidence = {
        "bin_id": binding.bin_id,
        "condition_id": binding.condition_id,
        "side": side,
        "raw_q": raw_q,
        "acting_q": q,
        "payoff_q_correction": (
            None
            if correction is None
            else {
                "type": type(correction).__name__,
                "p0": correction.p0,
                "param_hash": getattr(correction, "param_hash", None),
            }
        ),
    }
    refutation = buy_probability_rejection(
        candidate,
        q,
        saturated_sides=day0_saturated_sides_by_family(
            {probability_witness.family_key: prepared},
            {probability_witness.family_key: probability_witness},
        ),
        payoff_q_lcb_by_candidate=_prepared_candidate_payoff_q_lcb_caps({command_id: prepared}),
    )
    if refutation is not None:
        return valued("CANCEL", f"ENTRY_REST_BUY_REFUTED:{refutation}", q_evidence)
    endowment = _candidate_portfolio_endowment(
        candidate,
        probability_witness=probability_witness,
        holdings_snapshot=holdings_snapshot,
        wealth_witness=wealth,
    )
    try:
        existing = score_existing_buy_expected(
            candidate,
            shares=remaining,
            payoff_probability_mean=q,
            wealth_floor_usd=endowment.loss_wealth_floor_usd,
            wealth_ceiling_usd=endowment.win_wealth_floor_usd,
            fractional_kelly_multiplier=fractional_kelly_multiplier,
            current_token_shares=endowment.current_token_shares,
            probability_witness=probability_witness,
            resolution_at=resolution_at,
            decision_at_utc=now,
            action_mode="CONTINGENT_MAKER_REST_BUY",
        )
    except ValueError as exc:
        return _protective(entry, family, f"ENTRY_REST_CAPITAL_HORIZON_INVALID:{exc}")
    growth = existing.expected_growth
    gain = (
        float(growth.expected_delta_log_wealth)
        if growth is not None and existing.no_value_reason is None
        else 0.0
    )
    action, reason = entry_rest_disposition(
        held_shares=endowment.current_token_shares,
        open_remaining=remaining,
        full_kelly_target_shares=existing.full_kelly_target_shares,
        fractional_kelly_target_shares=existing.fractional_kelly_target_shares,
        legal_lot_shares=existing.legal_lot_shares,
        remainder_gain=gain,
    )
    if action == "CANCEL" and existing.no_value_reason:
        reason = f"{reason}:{existing.no_value_reason}"
    if action == "KEEP" and existing.cost_usd > capital_limit_usd:
        # The same per-token capital envelope a fresh order is sized inside;
        # it depends on the fills only through the order's total cost.
        action, reason = "CANCEL", "CURRENT_CAPITAL_LIMIT_EXCEEDED"
    return valued(
        action,
        reason,
        {
            **q_evidence,
            "current_token_shares": str(endowment.current_token_shares),
            "full_kelly_target_shares": str(existing.full_kelly_target_shares),
            "fractional_kelly_target_shares": str(existing.fractional_kelly_target_shares),
            "legal_lot_shares": str(existing.legal_lot_shares),
            "remainder_no_value_reason": existing.no_value_reason,
            "resolution_at_utc": None if resolution_at is None else resolution_at.isoformat(),
            "conditional_gain": gain,
            "expected_growth": None
            if growth is None
            else {
                "ruin_probability_reduction": growth.ruin_probability_reduction,
                "expected_delta_log_wealth": growth.expected_delta_log_wealth,
                "expected_ev_usd": growth.expected_ev_usd,
                "capital_lock_hours": growth.capital_lock_hours,
                "expected_capital_efficiency": growth.expected_capital_efficiency,
            },
            "fractional_kelly_multiplier": str(fractional_kelly_multiplier),
            "remainder_cost_usd": str(existing.cost_usd),
            "capital_limit_usd": str(capital_limit_usd),
        },
    )


@dataclass(frozen=True)
class FamilyOptimum:
    """The selector's own best fresh BUY for one family on the common axis."""

    candidate_id: str
    token_id: str
    execution_mode: str
    shares: Decimal
    limit_price: Decimal
    ruin_probability_reduction: float
    expected_delta_log_wealth: float
    fill_probability: float


def family_optimum_fresh_buy(
    trade_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    *,
    event: Any,
    prepared: Any,
    portfolio: Any,
    wealth: Any,
    fractional_kelly_multiplier: Decimal,
    capital_authority: Any,
    payoff_q_correction_resolver: Any,
    now: datetime,
) -> FamilyOptimum | None:
    """Run the selector itself on this one family and return its best BUY.

    ``select_prepared_global_auction`` on a one-family cut: the family's
    current book epoch from persisted projections only (no venue call), the
    selector's maker-fill witnesses, the same capital resolver, correction
    resolver, Kelly multiplier and wealth (the rest's own reservation already
    credited back), and the adapter's own module-level candidate laws: the
    active-order duplicate lock (so the rest's own token, which live cannot
    re-post while the rest is open, never competes), live strategy and Day0
    feasibility, and the same-token re-post law. Every SCORED/SELECTED BUY is
    on the selector's common growth axis; the best is returned by the
    selector's own ordering key. None when the cut cannot be built: no
    dominance is then proven, which never cancels a rest.
    """
    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
    from src.engine import event_reactor_adapter as adapter
    from src.engine import global_auction_universe as universe
    from src.engine import global_batch_runtime as runtime
    from src.engine.global_single_order_auction import select_prepared_global_auction
    from src.config import tier0_research_mode_enabled

    if tier0_research_mode_enabled():
        # Tier-0 actuates a flat one-lot stake through an adapter-local
        # capital closure; its fresh order is not this valuation's to rebuild.
        return None
    witness = prepared.probability_witness
    family_key = witness.family_key
    tokens = tuple(
        token
        for binding in witness.bindings
        for token in (binding.yes_token_id, binding.no_token_id)
        if token
    )
    projected = adapter._projected_global_books(
        trade_conn, tokens, checked_at=now, max_age=FRESHNESS_WINDOW_DEFAULT,
    )
    if projected is None:
        return None
    books, books_at = projected

    def _no_network(*_a, **_k):
        raise RuntimeError("STANDING_ENTRY_FAMILY_OPTIMUM_NO_VENUE_READ")

    try:
        epoch = universe.capture_current_global_book_epoch(
            trade_conn,
            probability_witnesses={family_key: witness},
            get_books=_no_network,
            clock=lambda: now,
            max_age=FRESHNESS_WINDOW_DEFAULT,
            prefetched_books=books,
            prefetched_at_utc=min(books_at, now),
        )
    except Exception:  # noqa: BLE001 - an unbuildable cut proves no dominance
        return None
    bound_family = runtime._bind_selection_holdings(
        {event.event_id: prepared}, portfolio_state=portfolio, wealth_witness=wealth,
    )
    bound_family, epoch = runtime._bind_current_maker_fill_witnesses(
        bound_family,
        book_epoch=epoch,
        wealth_witness=wealth,
        samples=runtime._load_current_maker_fill_samples(trade_conn, selection_cut_at_utc=now),
        issued_at_utc=now,
    )
    payload = adapter._payload(event)
    event_type = str(payload.get("event_type") or getattr(event, "event_type", "") or "").strip()
    metric = str(payload.get("metric") or payload.get("temperature_metric") or "").strip()
    truth_by_bin_side = {
        (str(bin_id), str(side).upper()): str(truth)
        for bin_id, side, truth in tuple(getattr(prepared, "day0_payoff_truth_by_bin_side", ()) or ())
    }
    revision = adapter._prepared_global_probability_semantics_revision(prepared, forecasts_conn)

    def candidate_policy(candidate: Any) -> str | None:
        if str(getattr(candidate, "action", "BUY") or "BUY").upper() == "SELL":
            # A held position's SELL/HOLD is the monitor's decision, not the
            # fresh BUY this comparison asks for.
            return "STANDING_ENTRY_FRESH_BUY_ONLY"
        duplicate = adapter._global_active_entry_duplicate_reason(
            candidate, trade_conn=trade_conn, live_cap_conn=trade_conn,
        )
        if duplicate is not None:
            return duplicate
        side = str(getattr(candidate, "side", "") or "").upper()
        bin_id = str(getattr(candidate, "bin_id", "") or "")
        day0_truth = truth_by_bin_side.get((bin_id, side))
        day0_reason = adapter._day0_unresolved_entry_probability_rejection_reason(
            day0_payoff_truth=day0_truth, probability_semantics_revision=revision,
        )
        if day0_reason is not None:
            return day0_reason
        repricing = adapter._day0_candidate_ask_repricing_rejection_reason(
            candidate, event_type=event_type, trade_conn=trade_conn,
            counts=adapter._DAY0_ASK_SELECTION_EVIDENCE,
        )
        if repricing is not None:
            return repricing
        try:
            strategy_key = adapter._event_bound_strategy_key(
                event_type=event_type, direction=f"buy_{side.lower()}", metric=metric,
                day0_payoff_truth=day0_truth, require_metric_live=True,
            )
        except ValueError as exc:
            return str(exc)
        return adapter._global_current_entry_feasibility_rejection_reason(
            candidate,
            strategy_key=strategy_key,
            probability_semantics_revision=revision,
            strategy_policy_conn=trade_conn,
            temperature_metric=metric,
        )

    def capital_limit(candidate: Any, gamma_market_id: str, market_event_id: str, _owner: str) -> Decimal:
        return Decimal(
            capital_authority.capacity_usd(
                market_id=str(gamma_market_id),
                event_id=str(market_event_id),
                resolution_window="default",
                correlation_key=family_key,
            )
        )

    scope = universe.current_global_auction_scope_from_events((event,), captured_at_utc=now)
    authorities = runtime._current_probability_authorities({family_key: witness})
    selected = select_prepared_global_auction(
        bound_family,
        selection_epoch_identity=f"standing_entry_family_optimum:{family_key}:{now.isoformat()}",
        selection_cut_at_utc=now,
        current_scope=scope,
        current_scope_identity_resolver=lambda: scope.scope_identity,
        venue_universe_identity=epoch.witness_identity,
        current_venue_universe_identity_resolver=lambda: epoch.witness_identity,
        universe_max_age=epoch.max_age,
        current_probability_resolver=authorities.get,
        current_execution_resolver=lambda c: epoch.execution_authority(c, checked_at_utc=now),
        current_wealth_identity_resolver=lambda: wealth.economic_identity,
        wealth_witness=wealth,
        capital_limit_usd=wealth.strategy_capital_allocation.remaining_buy_capacity_usd,
        fractional_kelly_multiplier=fractional_kelly_multiplier,
        decision_at_utc=now,
        book_epoch=epoch,
        family_joint_plan_cache={},
        current_capital_limit_resolver=capital_limit,
        candidate_policy_rejection_resolver=candidate_policy,
        selected_order_rejection_resolver=lambda s, at: adapter.global_selected_order_same_token_rejection(
            s, at, trade_conn=trade_conn,
        ),
        payoff_q_lcb_by_candidate=runtime._prepared_candidate_payoff_q_lcb_caps(bound_family),
        payoff_q_correction_resolver=payoff_q_correction_resolver,
    )
    scored = [
        row
        for row in tuple(getattr(selected.decision, "candidate_evaluations", ()) or ())
        if row.action == "BUY"
        and row.status in {"SCORED", "SELECTED"}
        and row.expected_growth is not None
    ]
    if not scored:
        return None
    best = min(
        scored,
        key=lambda row: (
            -float(row.expected_growth.ruin_probability_reduction),
            -float(row.expected_growth.expected_delta_log_wealth),
            -float(row.expected_growth.expected_capital_efficiency),
            row.cost_usd,
            row.candidate_id,
        ),
    )
    return FamilyOptimum(
        candidate_id=best.candidate_id,
        token_id=best.token_id,
        execution_mode=best.execution_mode,
        shares=best.shares,
        limit_price=best.limit_price,
        ruin_probability_reduction=float(best.expected_growth.ruin_probability_reduction),
        expected_delta_log_wealth=float(best.expected_growth.expected_delta_log_wealth),
        fill_probability=float(best.fill_probability),
    )


def family_optimum_dominates(
    rest: StandingEntryValuation, optimum: FamilyOptimum | None,
) -> bool:
    """Whether the family's fresh optimum beats keeping the rest.

    Same lexicographic key the selector ranks proposals by (ruin reduction,
    then expected growth). Both values sit on the common axis, and both are
    net of their own cost: the fresh proposal's growth already prices its
    fill probability through its maker witness (a taker fills by
    construction), while the rest's is conditional on fill, an upper bound on
    its realized value; cancelling the rest costs no fee. So a fresh optimum
    strictly above the rest's fill-conditional growth dominates it.
    """
    growth = rest.evidence.get("expected_growth") or {}
    if optimum is None or rest.action != "KEEP" or not growth:
        return False
    rest_key = (
        float(growth.get("ruin_probability_reduction") or 0.0),
        float(growth["expected_delta_log_wealth"]),
    )
    fresh_key = (optimum.ruin_probability_reduction, optimum.expected_delta_log_wealth)
    return fresh_key > rest_key


def _snapshot_row(trade_conn: sqlite3.Connection, snapshot_id: str) -> dict[str, Any] | None:
    cursor = trade_conn.execute(
        "SELECT * FROM executable_market_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip((c[0] for c in cursor.description or ()), row, strict=True))


def _entry_calibration_scope_resolver(
    metric_by_family_key: Mapping[str, str], forecasts_conn: sqlite3.Connection
):
    """The selector's ENTRY calibration fit scope (its owner metric per family)."""
    from src.engine.event_reactor_adapter import (
        _global_entry_calibration_fit_scope,
        _prepared_global_probability_semantics_revision,
    )

    def resolve(candidate: Any, prepared: Any) -> Any:
        return _global_entry_calibration_fit_scope(
            candidate,
            metric=metric_by_family_key.get(str(getattr(candidate, "family_key", ""))),
            raw_probability_revision=_prepared_global_probability_semantics_revision(
                prepared, forecasts_conn
            ),
        )

    return resolve


def _capture_standing_entry_values(
    trade_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    world_conn: sqlite3.Connection,
    entries: list[dict[str, Any]],
    *,
    families: Mapping[str, FamilyKey | None],
    clock: Callable[[], datetime],
    deadline_monotonic: float | None = None,
) -> tuple[datetime, list[StandingEntryValuation]]:
    """Read every input before any write: current scope, q, wealth, holdings.

    The decision instant is taken from ``clock`` after the trade read snapshot
    is established, so no fact this pass reads (collateral snapshot,
    posterior, readiness) can be stamped after it: a witness from the future
    is impossible by construction, not clamped. Returns that instant and one
    valuation per order.

    Reuses the selector's own readers and adapter: the current global scope
    (and its per-family resolution time), ``_prepare_current_global_probability_family``
    (the ENTRY q authority with HWM enforcement), ``current_portfolio_wealth_witness``,
    ``_bind_selection_holdings``, the runtime Kelly multiplier, one
    market-anchored correction resolver over every valued family (with
    ``_target_context_by_family`` on the selector's own payload reader) and
    the allocator's per-market capacity. Every trade read shares one read
    transaction, so the wealth witness, the obligation rows it was built from
    and each order's own capital are one ledger snapshot. A failed read for
    one order cancels that order protectively; it never aborts the others.
    """
    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
    from src.engine import event_reactor_adapter as adapter
    from src.engine import global_auction_universe as universe
    from src.engine import global_batch_runtime as runtime
    from src.engine.global_single_order_auction import single_position_capital_limit
    from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS
    from src.state.portfolio import load_runtime_open_portfolio
    from src.state.venue_command_repo import get_command

    order = [str(entry["command_id"]) for entry in entries]
    values: dict[str, StandingEntryValuation] = {}
    rests: list[dict[str, Any]] = []
    owns_txn = not trade_conn.in_transaction
    if owns_txn:
        trade_conn.execute("BEGIN")
    now = clock()
    try:
        # The first read pins this transaction's WAL snapshot; the decision
        # instant is taken after it (F1: never earlier than any fact read).
        trade_conn.execute("SELECT 1 FROM venue_commands LIMIT 1").fetchone()
        now = clock()
        if now.tzinfo is None:
            raise ValueError("STANDING_ENTRY_CLOCK_NAIVE")
        for entry in entries:
            command_id = str(entry["command_id"])
            try:
                command = get_command(trade_conn, command_id)
            except Exception as exc:  # noqa: BLE001 - an unreadable order cancels protectively
                values[command_id] = _protective(
                    entry, families.get(command_id), f"ENTRY_REST_COMMAND_UNREADABLE:{type(exc).__name__}"
                )
                continue
            if command is None:
                order.remove(command_id)
                continue
            rest = {**command, **entry}
            rests.append(rest)
            if families.get(command_id) is None:
                values[command_id] = _protective(rest, None, "ENTRY_REST_FAMILY_UNRESOLVED")
        live_families = {
            families[str(r["command_id"])] for r in rests if str(r["command_id"]) not in values
        }
        prepared_by_family: dict[FamilyKey, tuple[Any, Any]] = {}
        blocked: dict[FamilyKey, str] = {}
        resolution_at_by_key: Mapping[str, datetime] = {}
        if live_families:
            try:
                scope = universe.scan_current_global_auction_scope(
                    world_conn=world_conn,
                    forecasts_conn=forecasts_conn,
                    decision_at_utc=now,
                    restrict_to_families=tuple(sorted(live_families)),
                )
                resolution_at_by_key = dict(getattr(scope, "resolution_at_by_family", {}) or {})
                scope_events = tuple(scope.events)
            except Exception as exc:  # noqa: BLE001 - no current scope is no authority
                blocked = {
                    f: f"ENTRY_REST_CURRENT_SCOPE_UNAVAILABLE:{type(exc).__name__}" for f in live_families
                }
                scope_events = ()
            for event in scope_events:
                family = universe._event_family(event)
                if family not in live_families:
                    continue
                try:
                    prepared = adapter._prepare_current_global_probability_family(
                        event,
                        forecast_conn=forecasts_conn,
                        topology_conn=forecasts_conn,
                        observation_conn=world_conn,
                        decision_time=now,
                        max_age=FRESHNESS_WINDOW_DEFAULT,
                        allow_partial_deterministic=True,
                        allow_provisional_day0_replacement=True,
                        raw_input_hwm_conn=forecasts_conn,
                    )
                except Exception as exc:  # noqa: BLE001 - blocked q cancels protectively
                    blocked[family] = f"ENTRY_REST_PROBABILITY_BLOCKED:{type(exc).__name__}:{exc}"
                    continue
                if prepared is None:
                    blocked[family] = "ENTRY_REST_PROBABILITY_UNAVAILABLE"
                    continue
                try:
                    # Native token identity exactly as the selector's book
                    # epoch binds it before holdings, from persisted executable
                    # snapshots only (no Gamma/CLOB call in this pass).
                    witness = prepared.probability_witness
                    bound_witness = universe.bind_current_global_probability_tokens(
                        forecasts_conn,
                        probability_witnesses={witness.family_key: witness},
                        trade_conn=trade_conn,
                        checked_at_utc=now,
                    )[witness.family_key]
                    prepared = runtime._rebind_prepared_probability(prepared, bound_witness)
                except Exception as exc:  # noqa: BLE001 - unbound identity is no authority
                    blocked[family] = f"ENTRY_REST_TOKEN_IDENTITY_UNAVAILABLE:{type(exc).__name__}:{exc}"
                    continue
                prepared_by_family[family] = (event, prepared)
        for rest in rests:
            command_id = str(rest["command_id"])
            family = families.get(command_id)
            if command_id not in values and family not in prepared_by_family:
                values[command_id] = _protective(
                    rest, family, blocked.get(family, "ENTRY_REST_PROBABILITY_UNAVAILABLE")
                )
        active = [r for r in rests if str(r["command_id"]) not in values]
        if not active:
            return now, [values[c] for c in order]
        pending = _authority_pending_reason()
        if pending is not None:
            for rest in active:
                values[str(rest["command_id"])] = _deferred(
                    rest, families.get(str(rest["command_id"])), pending
                )
            return now, [values[c] for c in order]
        try:
            portfolio = load_runtime_open_portfolio(trade_conn)
            positions = tuple(getattr(portfolio, "positions", ()) or ())
            wealth = universe.current_portfolio_wealth_witness(
                trade_conn,
                decision_at_utc=now,
                max_age=timedelta(seconds=float(COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS)),
                portfolio_state=portfolio,
            )
            obligation_rows = universe.entry_obligation_rows(trade_conn)
            bounded_claims = dict(wealth.native_holdings_micro)
            for obligation_id, token, amount in wealth.pending_entry_endowments_micro:
                if obligation_id.startswith("position_claim:"):
                    bounded_claims[token] = bounded_claims.get(token, 0) + int(amount)
            multiplier = Decimal(str(adapter._runtime_kelly_multiplier()))
            from src.risk_allocator import snapshot_global_auction_capital_authority

            capital_authority = snapshot_global_auction_capital_authority()
            prepared_by_key = {
                prepared.probability_witness.family_key: prepared
                for _event, prepared in prepared_by_family.values()
            }
            # The selector's public resolver entry, outside any cut: no cut
            # memo exists here, so every scope is computed fully, and under the
            # daemon's corpus builder it only serves (never loads in-line).
            correction = runtime._market_anchored_correction_resolver(
                world_conn,
                trade_conn=trade_conn,
                forecast_conn=forecasts_conn,
                deadline_monotonic=deadline_monotonic,
                target_context_by_family=runtime._target_context_by_family(
                    {
                        prepared.probability_witness.family_key: event
                        for event, prepared in prepared_by_family.values()
                    },
                    payload_reader=adapter._payload,
                ),
                prepared_by_family=prepared_by_key,
                calibration_scope_resolver=_entry_calibration_scope_resolver(
                    {
                        prepared.probability_witness.family_key: family[2]
                        for family, (_event, prepared) in prepared_by_family.items()
                    },
                    forecasts_conn,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - no current wealth is no new exposure
            pending = _authority_pending_reason(exc)
            for rest in active:
                command_id = str(rest["command_id"])
                values[command_id] = (
                    _deferred(rest, families.get(command_id), pending)
                    if pending is not None
                    else _protective(
                        rest,
                        families.get(command_id),
                        f"ENTRY_REST_PORTFOLIO_AUTHORITY_INVALID:{type(exc).__name__}",
                    )
                )
            return now, [values[c] for c in order]
        for rest in active:
            command_id = str(rest["command_id"])
            family = families[command_id]
            event, prepared = prepared_by_family[family]
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                values[command_id] = _deferred(rest, family, "ENTRY_REST_PASS_DEADLINE")
                continue
            try:
                snapshot = _snapshot_row(trade_conn, str(rest.get("snapshot_id") or ""))
                if snapshot is None:
                    raise ValueError("ENTRY_REST_SUBMISSION_SNAPSHOT_MISSING")
                own = _own_command_capital(trade_conn, rest, positions=positions)
                rest["matched_size"] = str(own.filled_shares)
                own_wealth = _own_reservation_wealth(
                    wealth,
                    own,
                    obligation_rows=obligation_rows,
                    positions=positions,
                    native_holdings_micro=bounded_claims,
                )
                bound = runtime._bind_selection_holdings(
                    {event.event_id: prepared},
                    portfolio_state=portfolio,
                    wealth_witness=own_wealth,
                )[event.event_id]
                allocator_limit = capital_authority.capacity_usd(
                    market_id=str(snapshot.get("gamma_market_id") or ""),
                    event_id=str(snapshot.get("event_id") or ""),
                    correlation_key=prepared.probability_witness.family_key,
                )
                capital_limit = min(
                    own_wealth.strategy_capital_allocation.remaining_buy_capacity_usd,
                    single_position_capital_limit(
                        allocator_limit,
                        token_id=str(rest["token_id"]),
                        wealth_witness=own_wealth,
                    ),
                )
                valuation = value_standing_entry(
                    rest,
                    family=family,
                    snapshot=snapshot,
                    prepared=prepared,
                    wealth=own_wealth,
                    holdings_snapshot=bound.holdings_snapshot,
                    fractional_kelly_multiplier=multiplier,
                    capital_limit_usd=capital_limit,
                    payoff_q_correction_resolver=correction,
                    resolution_at=resolution_at_by_key.get(prepared.probability_witness.family_key),
                    now=now,
                )
                if valuation.action == "KEEP":
                    # Not dominated by the family's own fresh optimum, with
                    # the rest's reservation credited back as if cancelled.
                    try:
                        optimum = family_optimum_fresh_buy(
                            trade_conn,
                            forecasts_conn,
                            event=event,
                            prepared=prepared,
                            portfolio=portfolio,
                            wealth=own_wealth,
                            fractional_kelly_multiplier=multiplier,
                            capital_authority=capital_authority,
                            payoff_q_correction_resolver=correction,
                            now=now,
                        )
                    except Exception as exc:  # noqa: BLE001 - no fresh cut proves no dominance
                        logger.warning(
                            "standing ENTRY family optimum unavailable command=%s: %s: %s",
                            command_id, type(exc).__name__, exc,
                        )
                        optimum = None
                    evidence = {
                        **dict(valuation.evidence),
                        "family_optimum": None
                        if optimum is None
                        else {
                            "candidate_id": optimum.candidate_id,
                            "token_id": optimum.token_id,
                            "execution_mode": optimum.execution_mode,
                            "shares": str(optimum.shares),
                            "limit_price": str(optimum.limit_price),
                            "ruin_probability_reduction": optimum.ruin_probability_reduction,
                            "expected_delta_log_wealth": optimum.expected_delta_log_wealth,
                            "fill_probability": optimum.fill_probability,
                        },
                    }
                    valuation = replace(
                        valuation,
                        action="CANCEL" if family_optimum_dominates(valuation, optimum) else "KEEP",
                        reason=(
                            "FAMILY_OPTIMUM_DOMINATES"
                            if family_optimum_dominates(valuation, optimum)
                            else valuation.reason
                        ),
                        evidence=evidence,
                    )
                values[command_id] = valuation
            except Exception as exc:  # noqa: BLE001 - an unprovable valuation cancels protectively
                values[command_id] = _protective(
                    rest,
                    family,
                    f"ENTRY_REST_VALUE_AUTHORITY_INVALID:{type(exc).__name__}:{exc}",
                )
        return now, [values[c] for c in order]
    finally:
        if owns_txn and trade_conn.in_transaction:
            trade_conn.rollback()


def _authority_identity(valuation: StandingEntryValuation) -> str:
    """The valuation's economic authority, independent of the clock.

    Witness identities hash their capture time, so they change every tick;
    only the posterior's own identity and the economics it produced belong
    here. An unchanged KEEP then journals nothing.
    """
    stable = {
        key: valuation.evidence.get(key)
        for key in (
            "q_version",
            "posterior_identity_hash",
            "acting_q",
            "open_remaining",
            "limit_price",
            "authority_valid",
        )
    }
    return hashlib.sha256(
        json.dumps(
            [valuation.command_id, valuation.venue_order_id, valuation.action, valuation.reason, stable],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


# An unchanged KEEP is re-journaled at most once per window, so the journal
# read stays a bounded tail of the newest rows of this mode.
STANDING_ENTRY_REJOURNAL_WINDOW = timedelta(hours=24)


def read_journaled_identities(conn: sqlite3.Connection, *, now: datetime) -> dict[str, str]:
    """Latest journaled authority per command within the re-journal window.

    Read phase only (never inside the write lease): one ``timestamp`` index
    seek for the window start, then the ``(mode, rowid)`` index range of this
    mode from there. An unreadable journal reads as empty: every KEEP then
    journals once, which is safe.
    """
    try:
        start = conn.execute(
            "SELECT id FROM decision_log WHERE timestamp >= ? ORDER BY timestamp LIMIT 1",
            ((now - STANDING_ENTRY_REJOURNAL_WINDOW).isoformat(),),
        ).fetchone()
        if start is None:
            return {}
        rows = conn.execute(
            "SELECT json_extract(artifact_json, '$.command_id'), "
            "json_extract(artifact_json, '$.authority_identity') "
            "FROM decision_log WHERE mode = ? AND id >= ? ORDER BY id",
            (STANDING_ENTRY_DECISION_MODE, start[0]),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(command_id): str(identity) for command_id, identity in rows if command_id}


def persist_standing_entry_values(
    conn: sqlite3.Connection,
    valuations: Iterable[StandingEntryValuation],
    *,
    now: datetime,
    journaled: Mapping[str, str],
) -> int:
    """Journal each changed disposition's authority; return the rows written.

    One append-only ``decision_log`` row per changed authority, bound to the
    exact command and venue order id. The submission certificate,
    ``venue_commands.q_version`` and every reservation stay untouched. A KEEP
    whose authority equals ``journaled`` (read before the lease) writes
    nothing. The write lease holds only the inserts and the bounded
    retention walk; it reads no journal rows.
    """
    from src.execution.batch_order_submission import _cancel_journal_transaction
    from src.state.decision_chain import _inline_expire_decision_log

    artifacts = []
    for valuation in valuations:
        identity = _authority_identity(valuation)
        if valuation.action == "KEEP" and journaled.get(valuation.command_id) == identity:
            continue
        artifacts.append(
            json.dumps(
                {
                    "command_id": valuation.command_id,
                    "venue_order_id": valuation.venue_order_id,
                    "token_id": valuation.token_id,
                    "family": list(valuation.family) if valuation.family else None,
                    "action": valuation.action,
                    "reason": valuation.reason,
                    "authority_identity": identity,
                    "evidence": dict(valuation.evidence),
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
    if not artifacts:
        return 0
    stamp = now.isoformat()
    with _cancel_journal_transaction(conn, owner="standing_entry_value"):
        last_id = None
        for artifact in artifacts:
            last_id = conn.execute(
                "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (STANDING_ENTRY_DECISION_MODE, stamp, stamp, artifact, stamp),
            ).lastrowid
        _inline_expire_decision_log(conn, STANDING_ENTRY_DECISION_MODE, exclude_id=last_id)
    return len(artifacts)


def _family_scope_key(family: Iterable[object]) -> tuple[str, str, str]:
    city, target_date, metric = (str(value or "").strip() for value in family)
    return city.casefold(), target_date[:10], metric.lower()


def run_c3_staleness_cancel_cycle(
    trade_conn_ro: sqlite3.Connection,
    trade_conn_rw: sqlite3.Connection,
    forecasts_conn_ro: sqlite3.Connection,
    client: Any,
    *,
    world_conn_ro: sqlite3.Connection,
    clock: Callable[[], datetime] | None = None,
    rate_budget: Any = None,
    families: Iterable[Iterable[object]] | None = None,
    budget_seconds: float = STANDING_ENTRY_PASS_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Value open ENTRY rests and cancel only what current value rejects.

    ``clock`` supplies the decision instant; the capture reads it after its
    trade read snapshot is pinned, so the instant is never earlier than any
    fact the pass reads. ``budget_seconds`` bounds the read snapshot and the
    correction resolver; a rest left unvalued when it runs out is DEFERRED.

    ``families`` restricts the pass to the open rests of those families (a
    belief or Day0 wake for them); ``None`` is the full tick, which also
    retries pending cancels and runs the Day0 dead-bin lane.

    Read phase (no writes, INV-37): open rests, current probability, wealth,
    holdings, the journal's latest identities, Day0 classification. Write
    phase: journal changed authority (best effort), then one
    ``cancel_commands_batch`` over every CANCEL, Day0 and pending-retry
    command. A journal failure never blocks a cancel: the batch journals
    CANCEL_REQUESTED, with its ``cancel_reason``, before its SDK call. KEEP
    takes no venue action. A rate-budget denial defers (never drops); the
    next tick revalues the still-open order. ``confirmed_families`` are the
    families whose every cancelled command re-reads as CANCELLED.
    """
    from src.execution.batch_order_submission import cancel_commands_batch
    from src.execution.day0_hard_fact_exit import classify_day0_dead_bin_entry_cancels
    from src.state.venue_command_repo import get_command

    read_clock = clock or (lambda: datetime.now(UTC))
    deadline_monotonic = time.monotonic() + float(budget_seconds)
    at = read_clock()
    full_tick = families is None
    entries = find_open_entry_rests(trade_conn_ro, include_pending_cancels=full_tick)
    families_by_command = resolve_order_families(entries, trade_conn_ro, forecasts_conn_ro)
    if not full_tick:
        wanted = {_family_scope_key(family) for family in families or ()}
        entries = [
            e
            for e in entries
            if (family := families_by_command.get(str(e["command_id"]))) is not None
            and _family_scope_key(family) in wanted
        ]
    pending = [e for e in entries if e.get("pending_cancel")]
    active = [e for e in entries if not e.get("pending_cancel")]

    day0_cancel_set: list[dict[str, Any]] = []
    if active and full_tick:
        try:
            from src.config import runtime_cities_by_name

            day0_cancel_set = classify_day0_dead_bin_entry_cancels(
                active,
                trade_conn=trade_conn_ro,
                forecasts_conn=forecasts_conn_ro,
                cities_by_name=runtime_cities_by_name(),
                now=at,
            )
        except Exception as exc:  # noqa: BLE001 - one lane cannot suppress valuation
            logger.warning("C3 Day0 cancel classification failed: %s", exc)
    valuations: list[StandingEntryValuation] = []
    journaled_rows = 0
    if active:
        try:
            at, valuations = _capture_standing_entry_values(
                trade_conn_ro,
                forecasts_conn_ro,
                world_conn_ro,
                active,
                families=families_by_command,
                clock=read_clock,
                deadline_monotonic=deadline_monotonic,
            )
        except Exception as exc:  # noqa: BLE001 - an unvalued rest never keeps filling
            logger.warning("C3 standing valuation failed; cancelling protectively: %s", exc)
            valuations = [
                _protective(
                    e,
                    families_by_command.get(str(e["command_id"])),
                    f"ENTRY_REST_VALUATION_FAILED:{type(exc).__name__}",
                )
                for e in active
            ]
        journaled = read_journaled_identities(trade_conn_ro, now=at)
        try:
            journaled_rows = persist_standing_entry_values(
                trade_conn_rw,
                [v for v in valuations if v.action != "DEFER"],
                now=at,
                journaled=journaled,
            )
        except Exception as exc:  # noqa: BLE001 - bookkeeping never blocks a cancel
            logger.warning(
                "C3 standing valuation journal failed (cancels still go): %s: %s",
                type(exc).__name__,
                exc,
            )

    value_cancel_set = [
        {
            "command_id": v.command_id,
            "venue_order_id": v.venue_order_id,
            "family": v.family,
            "cancel_reason": v.reason,
            "cancel_action": "CANCEL_REPLACE",
            "cancel_detail": {"action": v.action, **dict(v.evidence)},
        }
        for v in valuations
        if v.action == "CANCEL"
    ]
    pending_cancel_set = [
        {
            "command_id": e["command_id"],
            "family": families_by_command.get(str(e["command_id"])),
            "cancel_reason": "CANCEL_PENDING_RETRY",
            "cancel_action": "CANCEL_REPLACE",
            "cancel_detail": {"trigger": "c3_pending_cancel_retry"},
        }
        for e in pending
    ]
    cancel_set = _merge_cancel_proposals(
        (
            ("pending_cancel", pending_cancel_set),
            ("value", value_cancel_set),
            ("day0", day0_cancel_set),
        ),
        families_by_command,
    )
    result: dict[str, Any] = {
        "scanned": len(entries),
        "valuations": valuations,
        "kept": sum(v.action == "KEEP" for v in valuations),
        "deferred": sum(v.action == "DEFER" for v in valuations),
        "journaled": journaled_rows,
        "cancel_set_size": len(cancel_set),
        "day0_cancel_set_size": len(day0_cancel_set),
        "outcomes": [],
        "confirmed_families": set(),
    }
    if not cancel_set:
        return result

    outcomes = cancel_commands_batch(
        trade_conn_rw,
        client,
        [str(e["command_id"]) for e in cancel_set],
        rate_budget=rate_budget,
        cancel_reasons={str(e["command_id"]): str(e.get("cancel_reason") or "") for e in cancel_set},
    )
    result["outcomes"] = outcomes
    confirmed: set[FamilyKey] = set()
    blocked: set[FamilyKey] = set()
    for outcome in outcomes:
        family = families_by_command.get(outcome.command_id)
        if outcome.status != "acked":
            error = str(outcome.error_message or "")
            logger.warning(
                "c3_staleness_cancel outcome command_id=%s status=%s reason=%s",
                outcome.command_id,
                outcome.status,
                error.split(":", 1)[0] if error else outcome.status,
            )
            if family:
                blocked.add(family)
            continue
        # Poll the journaled fact: redecision is gated on durable CANCELLED truth.
        command = get_command(trade_conn_rw, outcome.command_id)
        if command is None or str(command.get("state") or "").upper() != "CANCELLED":
            if family:
                blocked.add(family)
            continue
        if family:
            confirmed.add(family)
    result["confirmed_families"] = confirmed - blocked
    logger.info(
        "c3_staleness_cancel: scanned=%d kept=%d deferred=%d journaled=%d cancel_set=%d "
        "confirmed_families=%d",
        result["scanned"],
        result["kept"],
        result["deferred"],
        result["journaled"],
        result["cancel_set_size"],
        len(result["confirmed_families"]),
    )
    return result
