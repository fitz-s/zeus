# Created: 2026-07-03
# Last reused or audited: 2026-10-01
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#   (SCH-W1.2-ORDER-STATE) §"C3" (cancel-set goes out through the existing CANCEL intent);
#   standing ENTRY keep-by-value law (operator, 2026-09-30): an open ENTRY rest keeps
#   working toward its current fractional-Kelly target; age and posterior identity are
#   revaluation triggers, never cancellation authority.
"""C3: value every open ENTRY rest -> KEEP / RESIZE / CANCEL -> reconciled re-solve.

Every recurring tick revalues each open ENTRY rest with the selector's own BUY
sizer (``solver._score_global_single_order_buy_expected``) at the rest's own
limit, on current probability, wealth and holdings. ``entry_rest_disposition``
turns that valuation into one action:

- KEEP: the authority this valuation used is journaled as an append-only
  ``decision_log`` row bound to the same venue order id; no venue call. The
  submission certificate and ``venue_commands.q_version`` are never rewritten.
- RESIZE: the venue client has no amend, so a resize is a persisted cancel
  (``cancel_commands_batch``), confirmed terminal reconciliation, then a fresh
  redecision for the family.
- CANCEL: the same persisted batch cancel. Unavailable or blocked probability
  authority cancels protectively; it never licenses further fills.

All reads finish before the TRADE write lease (INV-37). Day0 dead-bin/anomaly
classification is a separate, unconditional protective lane merged before the
single batch cancel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Iterable, Mapping

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
    action: str  # KEEP | RESIZE | CANCEL
    reason: str
    evidence: Mapping[str, Any]


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


def _own_reservation_wealth(
    wealth: Any,
    *,
    command_id: str,
    token_id: str,
    reservation_micro: int,
    filled_shares: Decimal,
    remaining_cost_usd: Decimal,
):
    """Current wealth as seen by this order's own redecision.

    The selector's holdings minus this order's own unfilled remainder: the
    remainder's reserved cash (at most its limit cost; a filled part's cash is
    already spent) returns to spendable cash and leaves BUY capital
    commitments, and the order's pending obligation (full size) is replaced by
    its filled part only. Other commands' reservations and obligations stay
    exactly as the selector sees them. A valuation input only — it never
    writes a reservation or an order row.
    """
    from src.contracts.strategy_capital_allocation import StrategyCapitalAllocationWitness
    from src.solve.solver import PortfolioWealthWitness, portfolio_wealth_identity

    if reservation_micro < 0 or remaining_cost_usd < 0:
        raise ValueError("ENTRY_REST_OWN_RESERVATION_INVALID")
    credit_micro = min(
        int(reservation_micro),
        int((remaining_cost_usd * _MICRO).to_integral_value(rounding=ROUND_FLOOR)),
    )
    if credit_micro > int(wealth.reservations_usd * _MICRO):
        raise ValueError("ENTRY_REST_OWN_RESERVATION_NOT_IN_WEALTH")
    credit = Decimal(credit_micro) / _MICRO
    commitments = dict(wealth.native_commitments_micro)
    if credit_micro > commitments.get(token_id, 0):
        raise ValueError("ENTRY_REST_OWN_COMMITMENT_NOT_IN_WEALTH")
    commitments[token_id] = commitments.get(token_id, 0) - credit_micro
    own = tuple(row for row in wealth.pending_entry_endowments_micro if row[0] == command_id)
    if len(own) > 1:
        raise ValueError("ENTRY_REST_OWN_OBLIGATION_AMBIGUOUS")
    filled_micro = int((filled_shares * _MICRO).to_integral_value(rounding=ROUND_FLOOR))
    pending = tuple(row for row in wealth.pending_entry_endowments_micro if row[0] != command_id)
    if own and filled_micro > 0:
        pending += ((command_id, token_id, min(filled_micro, int(own[0][2]))),)
    removed = sum(int(row[2]) for row in own) - sum(
        int(row[2]) for row in pending if row[0] == command_id
    )
    allocation = wealth.strategy_capital_allocation
    config: dict[str, object] = {"mode": allocation.mode}
    if allocation.configured_value is not None:
        config["value"] = allocation.configured_value
    if allocation.configured_buy_commitment_limit_usd is not None:
        config["buy_commitment_limit_usd"] = allocation.configured_buy_commitment_limit_usd
    floor = wealth.wealth_floor_usd + credit
    committed = allocation.committed_capital_usd - credit
    spendable = wealth.spendable_cash_usd + credit
    updated_allocation = StrategyCapitalAllocationWitness.build(
        capital_basis_usd=floor + committed,
        committed_capital_usd=committed,
        venue_spendable_cash_usd=spendable,
        allocation=config,
    )
    position_set_hash = hashlib.sha256(
        json.dumps(
            [wealth.position_set_hash, command_id, str(credit), sorted(pending)],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    fields = dict(
        ledger_snapshot_id=wealth.ledger_snapshot_id,
        position_set_hash=position_set_hash,
        wealth_floor_usd=floor,
        wealth_ceiling_usd=wealth.wealth_ceiling_usd + credit - Decimal(removed) / _MICRO,
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
        pending_entry_endowments_micro=pending,
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

    The economic curve is one level: the rest's own limit, the selector's own
    proposal capacity (``maker_buy_capacity`` of current cash), zero maker fee,
    its submission snapshot's tick and lot. The open remainder does not bound
    R*: it is compared with R* afterwards. The executable ask
    ladder is that snapshot's; it only satisfies the candidate's non-crossing
    shape and is never read by the BUY sizer. The fill-model period is a
    candidate-shape field the BUY sizer does not read either; it is not an
    order deadline. Fill probability is not an input: R* is conditional on fill.
    """
    from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
    from src.engine.event_reactor_adapter import _native_side_cost_curve_from_snapshot_row
    from src.solve.solver import GlobalSingleOrderCandidate, executable_curve_identity
    from src.strategy.live_inference.mode_consistent_ev import (
        MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    )

    price = Decimal(str(entry["price"]))
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
    return GlobalSingleOrderCandidate(
        candidate_id="standing_entry:" + str(entry["command_id"]),
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
        rest_deadline_minutes=float(MAKER_REST_ESCALATION_DEADLINE_MINUTES),
        fill_probability_source="standing_entry_conditional_on_fill",
    )


def value_standing_entry(
    entry: Mapping[str, Any],
    *,
    family: FamilyKey,
    snapshot: Mapping[str, Any],
    probability_witness: Any,
    wealth: Any,
    holdings_snapshot: Any,
    fractional_kelly_multiplier: Decimal,
    capital_limit_usd: Decimal,
    payoff_q_correction_resolver: Any,
    now: datetime,
) -> StandingEntryValuation:
    """Disposition of one open ENTRY rest from the selector's own BUY sizer.

    R* is ``_score_global_single_order_buy_expected`` at the rest's own limit:
    the same posterior-mean q (after the same market-anchored correction), the
    same Kelly multiplier, wealth floor/ceiling, held shares and capital limit
    the selector would use. Holdings include this order's filled part; its own
    unfilled reservation is credited back (``wealth`` is already that view).
    """
    from src.engine.global_single_order_auction import _candidate_portfolio_endowment
    from src.solve.solver import (
        _score_global_single_order_buy_expected,
        family_payoff_point_q,
        family_payoff_q_samples,
        maker_buy_capacity,
        resolve_candidate_payoff_q_correction,
    )

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
    minimum = Decimal(str(snapshot["min_order_size"]))
    if remaining <= 0:
        return _protective(entry, family, "ENTRY_REST_REMAINDER_NOT_POSITIVE")
    capacity = maker_buy_capacity(wealth.spendable_cash_usd, price)
    if capacity < minimum:
        # The selector offers no maker proposal below one lot of current cash:
        # the target is below a legal lot. This is a value outcome, not a fault.
        action, reason = entry_rest_disposition(
            open_remaining=remaining,
            target_remaining=Decimal("0"),
            minimum_order_size=minimum,
            conditional_gain=0.0,
        )
        return StandingEntryValuation(
            command_id=command_id,
            venue_order_id=str(entry["venue_order_id"]),
            token_id=token_id,
            family=family,
            action=action,
            reason=f"{reason}:MAKER_CASH_CAPACITY_BELOW_LOT",
            evidence={
                "authority_valid": True,
                "probability_witness_identity": probability_witness.witness_identity,
                "limit_price": str(price),
                "open_remaining": str(remaining),
                "minimum_order_size": str(minimum),
                "spendable_cash_usd": str(wealth.spendable_cash_usd),
                "target_remaining": "0",
            },
        )
    candidate = _rest_candidate(
        entry,
        snapshot=snapshot,
        binding=binding,
        side=side,
        probability_witness=probability_witness,
        capacity=capacity,
        ledger_snapshot_id=wealth.ledger_snapshot_id,
        now=now,
    )
    raw_q = family_payoff_point_q(probability_witness, bin_id=binding.bin_id, side=side)
    samples = family_payoff_q_samples(probability_witness, bin_id=binding.bin_id, side=side)
    if raw_q is None or samples is None:
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
    endowment = _candidate_portfolio_endowment(
        candidate,
        probability_witness=probability_witness,
        holdings_snapshot=holdings_snapshot,
        wealth_witness=wealth,
    )
    score = _score_global_single_order_buy_expected(
        candidate,
        payoff_probability_mean=q,
        sample_count=int(samples.size),
        band_alpha=float(probability_witness.band_alpha),
        wealth_floor_usd=endowment.loss_wealth_floor_usd,
        wealth_ceiling_usd=endowment.win_wealth_floor_usd,
        spendable_cash_usd=wealth.spendable_cash_usd,
        capital_limit_usd=capital_limit_usd,
        fractional_kelly_multiplier=fractional_kelly_multiplier,
        current_token_shares=endowment.current_token_shares,
    )
    target = score.shares if score.candidate is not None else Decimal("0")
    gain = (
        float(score.expected_terminal_wealth.expected_delta_log_wealth)
        if score.candidate is not None and score.expected_terminal_wealth is not None
        else 0.0
    )
    action, reason = entry_rest_disposition(
        open_remaining=remaining,
        target_remaining=target,
        minimum_order_size=minimum,
        conditional_gain=gain,
    )
    if action == "CANCEL" and score.no_trade_reason:
        reason = f"{reason}:{score.no_trade_reason}"
    evidence = {
        "authority_valid": True,
        "probability_witness_identity": probability_witness.witness_identity,
        "q_version": probability_witness.q_version,
        "posterior_identity_hash": probability_witness.posterior_identity_hash,
        "authority_certificate_hash": probability_witness.authority_certificate_hash,
        "submission_q_version": entry.get("q_version"),
        "wealth_witness_identity": wealth.witness_identity,
        "ledger_snapshot_id": wealth.ledger_snapshot_id,
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
        "limit_price": str(price),
        "proposal_capacity_shares": str(capacity),
        "filled_shares": str(filled),
        "open_remaining": str(remaining),
        "minimum_order_size": str(minimum),
        "current_token_shares": str(endowment.current_token_shares),
        "full_kelly_target_shares": str(score.full_kelly_target_shares),
        "fractional_kelly_target_shares": str(score.fractional_kelly_target_shares),
        "target_remaining": str(target),
        "sizing_no_trade_reason": score.no_trade_reason,
        "conditional_gain": gain,
        "fractional_kelly_multiplier": str(fractional_kelly_multiplier),
        "capital_limit_usd": str(capital_limit_usd),
        "resize_semantics": "PERSISTED_CANCEL_THEN_TERMINAL_RECONCILIATION_THEN_REDECISION",
    }
    return StandingEntryValuation(
        command_id=command_id,
        venue_order_id=str(entry["venue_order_id"]),
        token_id=token_id,
        family=family,
        action=action,
        reason=reason,
        evidence=evidence,
    )


def _snapshot_row(trade_conn: sqlite3.Connection, snapshot_id: str) -> dict[str, Any] | None:
    cursor = trade_conn.execute(
        "SELECT * FROM executable_market_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip((c[0] for c in cursor.description or ()), row, strict=True))


def _capture_standing_entry_values(
    trade_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    world_conn: sqlite3.Connection,
    entries: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[StandingEntryValuation]:
    """Read every input before any write: current scope, q, wealth, holdings.

    Reuses the selector's own readers and adapter: the current global scope,
    ``_prepare_current_global_probability_family`` (the ENTRY q authority with
    HWM enforcement), ``current_portfolio_wealth_witness``,
    ``_bind_selection_holdings``, the runtime Kelly multiplier, the
    market-anchored correction and the allocator's per-market capacity.
    """
    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
    from src.engine import event_reactor_adapter as adapter
    from src.engine import global_auction_universe as universe
    from src.engine import global_batch_runtime as runtime
    from src.engine.global_single_order_auction import single_position_capital_limit
    from src.state.collateral_ledger import COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS, _proven_filled_size
    from src.state.portfolio import load_runtime_open_portfolio
    from src.state.venue_command_repo import get_command

    families = resolve_order_families(entries, trade_conn, forecasts_conn)
    rests: list[dict[str, Any]] = []
    values: dict[str, StandingEntryValuation] = {}
    for entry in entries:
        command_id = str(entry["command_id"])
        command = get_command(trade_conn, command_id)
        if command is None:
            continue
        rest = {**command, **entry}
        rest["matched_size"] = str(
            max(
                Decimal(str(entry.get("matched_size") or "0")),
                _proven_filled_size(trade_conn, command_id),
            )
        )
        rests.append(rest)
        if families.get(command_id) is None:
            values[command_id] = _protective(rest, None, "ENTRY_REST_FAMILY_UNRESOLVED")
    live_families = {f for f in families.values() if f}
    prepared_by_family: dict[FamilyKey, tuple[str, Any, Any]] = {}
    blocked: dict[FamilyKey, str] = {}
    if live_families:
        try:
            scope = universe.scan_current_global_auction_scope(
                world_conn=world_conn,
                forecasts_conn=forecasts_conn,
                decision_at_utc=now,
                restrict_to_families=tuple(sorted(live_families)),
            )
        except Exception as exc:  # noqa: BLE001 - no current scope is no authority
            blocked = {f: f"ENTRY_REST_CURRENT_SCOPE_UNAVAILABLE:{type(exc).__name__}" for f in live_families}
        else:
            for event in scope.events:
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
                prepared_by_family[family] = (event, prepared, payload_family_context(event))
    active = [r for r in rests if str(r["command_id"]) not in values]
    for rest in active:
        family = families[str(rest["command_id"])]
        if family not in prepared_by_family:
            values[str(rest["command_id"])] = _protective(
                rest, family, blocked.get(family, "ENTRY_REST_PROBABILITY_UNAVAILABLE")
            )
    active = [r for r in active if str(r["command_id"]) not in values]
    if not active:
        return [values[str(r["command_id"])] for r in rests if str(r["command_id"]) in values]
    try:
        portfolio = load_runtime_open_portfolio(trade_conn)
        wealth = universe.current_portfolio_wealth_witness(
            trade_conn,
            decision_at_utc=now,
            max_age=timedelta(seconds=float(COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS)),
            portfolio_state=portfolio,
        )
        multiplier = Decimal(str(adapter._runtime_kelly_multiplier()))
        from src.risk_allocator import snapshot_global_auction_capital_authority

        capital_authority = snapshot_global_auction_capital_authority()
    except Exception as exc:  # noqa: BLE001 - no current wealth is no new exposure
        for rest in active:
            values[str(rest["command_id"])] = _protective(
                rest,
                families[str(rest["command_id"])],
                f"ENTRY_REST_PORTFOLIO_AUTHORITY_INVALID:{type(exc).__name__}",
            )
        return [values[str(r["command_id"])] for r in rests]
    target_context = {
        prepared.probability_witness.family_key: context
        for _event, prepared, context in prepared_by_family.values()
        if context is not None
    }
    for rest in active:
        command_id = str(rest["command_id"])
        family = families[command_id]
        event, prepared, _context = prepared_by_family[family]
        try:
            snapshot = _snapshot_row(trade_conn, str(rest.get("snapshot_id") or ""))
            if snapshot is None:
                raise ValueError("ENTRY_REST_SUBMISSION_SNAPSHOT_MISSING")
            reservation = trade_conn.execute(
                "SELECT amount FROM collateral_reservations "
                "WHERE command_id = ? AND reservation_type = 'PUSD_BUY' AND released_at IS NULL",
                (command_id,),
            ).fetchone()
            filled = Decimal(str(rest["matched_size"]))
            own_wealth = _own_reservation_wealth(
                wealth,
                command_id=command_id,
                token_id=str(rest["token_id"]),
                reservation_micro=int(reservation[0]) if reservation is not None else 0,
                filled_shares=filled,
                remaining_cost_usd=max(Decimal("0"), Decimal(str(rest["size"])) - filled)
                * Decimal(str(rest["price"])),
            )
            bound = runtime._bind_selection_holdings(
                {event.event_id: prepared},
                portfolio_state=portfolio,
                wealth_witness=own_wealth,
            )[event.event_id]
            correction = runtime._market_anchored_correction_resolver(
                world_conn,
                trade_conn=trade_conn,
                forecast_conn=forecasts_conn,
                target_context_by_family=target_context,
                prepared_by_family={prepared.probability_witness.family_key: prepared},
                calibration_scope_resolver=_entry_calibration_scope_resolver(family, forecasts_conn),
            )
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
            values[command_id] = value_standing_entry(
                rest,
                family=family,
                snapshot=snapshot,
                probability_witness=prepared.probability_witness,
                wealth=own_wealth,
                holdings_snapshot=bound.holdings_snapshot,
                fractional_kelly_multiplier=multiplier,
                capital_limit_usd=capital_limit,
                payoff_q_correction_resolver=correction,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - an unprovable valuation cancels protectively
            values[command_id] = _protective(
                rest,
                family,
                f"ENTRY_REST_VALUE_AUTHORITY_INVALID:{type(exc).__name__}:{exc}",
            )
    return [values[str(r["command_id"])] for r in rests]


def payload_family_context(event: Any) -> tuple[str, Any] | None:
    """(city, target date) from the event payload, as the selector reads it."""
    from datetime import date

    try:
        payload = json.loads(str(event.payload_json or "{}"))
        city = str(payload.get("city") or "")
        target_date = date.fromisoformat(str(payload.get("target_date") or "")[:10])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return (city, target_date) if city else None


def _entry_calibration_scope_resolver(family: FamilyKey, forecasts_conn: sqlite3.Connection):
    """The selector's ENTRY calibration fit scope for this family's metric."""
    from src.engine.event_reactor_adapter import (
        _global_entry_calibration_fit_scope,
        _prepared_global_probability_semantics_revision,
    )

    def resolve(candidate: Any, prepared: Any) -> Any:
        return _global_entry_calibration_fit_scope(
            candidate,
            metric=family[2],
            raw_probability_revision=_prepared_global_probability_semantics_revision(
                prepared, forecasts_conn
            ),
        )

    return resolve


def _authority_identity(valuation: StandingEntryValuation) -> str:
    stable = {
        key: valuation.evidence.get(key)
        for key in (
            "probability_witness_identity",
            "acting_q",
            "target_remaining",
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


def persist_standing_entry_values(
    conn: sqlite3.Connection,
    valuations: Iterable[StandingEntryValuation],
    *,
    now: datetime,
) -> list[StandingEntryValuation]:
    """Journal each disposition's authority before any venue action.

    One append-only ``decision_log`` row per changed authority, bound to the
    exact command and venue order id. The submission certificate,
    ``venue_commands.q_version`` and every reservation stay untouched. A KEEP
    whose authority equals the command's latest journaled authority writes
    nothing (read from the journal itself, so a restart does not re-journal).
    Returns the valuations whose command is still the same open order.
    """
    from src.execution.batch_order_submission import _cancel_journal_transaction
    from src.state.decision_chain import _inline_expire_decision_log
    from src.state.venue_command_repo import get_command

    current: list[StandingEntryValuation] = []
    with _cancel_journal_transaction(conn, owner="standing_entry_value"):
        last_id = None
        for valuation in valuations:
            command = get_command(conn, valuation.command_id)
            if (
                command is None
                or str(command.get("venue_order_id") or "") != valuation.venue_order_id
                or str(command.get("token_id") or "") != valuation.token_id
                or str(command.get("state") or "") not in {"ACKED", "POST_ACKED", "PARTIAL"}
            ):
                continue
            current.append(valuation)
            identity = _authority_identity(valuation)
            if valuation.action == "KEEP" and _latest_journaled_identity(
                conn, valuation.command_id
            ) == identity:
                continue
            artifact = {
                "command_id": valuation.command_id,
                "venue_order_id": valuation.venue_order_id,
                "token_id": valuation.token_id,
                "family": list(valuation.family) if valuation.family else None,
                "action": valuation.action,
                "reason": valuation.reason,
                "authority_identity": identity,
                "evidence": dict(valuation.evidence),
            }
            cursor = conn.execute(
                "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    STANDING_ENTRY_DECISION_MODE,
                    now.isoformat(),
                    now.isoformat(),
                    json.dumps(artifact, sort_keys=True, separators=(",", ":"), default=str),
                    now.isoformat(),
                ),
            )
            last_id = cursor.lastrowid
        if last_id is not None:
            _inline_expire_decision_log(conn, STANDING_ENTRY_DECISION_MODE, exclude_id=last_id)
    return current


def _latest_journaled_identity(conn: sqlite3.Connection, command_id: str) -> str | None:
    row = conn.execute(
        "SELECT json_extract(artifact_json, '$.authority_identity') FROM decision_log "
        "WHERE mode = ? AND json_extract(artifact_json, '$.command_id') = ? "
        "ORDER BY id DESC LIMIT 1",
        (STANDING_ENTRY_DECISION_MODE, command_id),
    ).fetchone()
    return None if row is None else row[0]


def run_c3_staleness_cancel_cycle(
    trade_conn_ro: sqlite3.Connection,
    trade_conn_rw: sqlite3.Connection,
    forecasts_conn_ro: sqlite3.Connection,
    client: Any,
    *,
    world_conn_ro: sqlite3.Connection,
    now: datetime | None = None,
    rate_budget: Any = None,
) -> dict[str, Any]:
    """Value every open ENTRY rest and cancel only what current value rejects.

    Read phase (no writes, INV-37): open rests, current probability, wealth,
    holdings, Day0 dead-bin classification. Write phase: journal every
    valuation's authority, then one ``cancel_commands_batch`` over CANCEL,
    RESIZE, Day0 and pending-retry commands. KEEP takes no venue action.

    ``cancel_commands_batch`` persists CANCEL_REQUESTED before its SDK call and
    defers (never drops) on a rate-budget denial; the next tick revalues the
    still-open order. Returns ``confirmed_families``: families whose every
    cancelled command re-reads as CANCELLED, the gate for fresh redecision.
    """
    from src.execution.batch_order_submission import cancel_commands_batch
    from src.execution.day0_hard_fact_exit import classify_day0_dead_bin_entry_cancels
    from src.state.venue_command_repo import get_command

    at = now or datetime.now(UTC)
    entries = find_open_entry_rests(trade_conn_ro, include_pending_cancels=True)
    pending = [e for e in entries if e.get("pending_cancel")]
    active = [e for e in entries if not e.get("pending_cancel")]
    families_by_command = resolve_order_families(entries, trade_conn_ro, forecasts_conn_ro)

    day0_cancel_set: list[dict[str, Any]] = []
    if active:
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
    if active:
        valuations = persist_standing_entry_values(
            trade_conn_rw,
            _capture_standing_entry_values(
                trade_conn_ro, forecasts_conn_ro, world_conn_ro, active, now=at
            ),
            now=at,
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
        if v.action in {"CANCEL", "RESIZE"}
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
        "resized": sum(v.action == "RESIZE" for v in valuations),
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
        "c3_staleness_cancel: scanned=%d kept=%d resized=%d cancel_set=%d confirmed_families=%d",
        result["scanned"],
        result["kept"],
        result["resized"],
        result["cancel_set_size"],
        len(result["confirmed_families"]),
    )
    return result
