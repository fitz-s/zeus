"""Read-only held-position monitor cadence evidence.

This module is intentionally pure SELECT/in-memory classification.  It proves
whether live-money positions have fresh per-position ``MONITOR_REFRESHED``
events; it does not use projection timestamps and never writes runtime state.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from src.contracts.position_truth import (
    CURRENT_MONEY_RISK_CHAIN_STATES,
)


# Monitoring follows canonical capital, not venue order precision.  Any finite
# positive residual remains an obligation until settlement or reconciliation
# writes zero; the execution lane independently enforces its 0.01-share floor.
MONITOR_CADENCE_EXPOSURE_FLOOR = 0.0
MONITOR_CADENCE_FUTURE_TOLERANCE_SECONDS = 30.0
MONITOR_CADENCE_POSITION_PHASES = frozenset({"active", "day0_window", "pending_exit"})
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' retired
# from this set — the T5 schema migration has run and the DB CHECK no longer
# admits the literal, so a live row can never carry it. A disputed-entry
# position now keeps its TRUE phase (in MONITOR_CADENCE_POSITION_PHASES
# above) per REPLACEMENT PHASE LAW, so it is normally monitored rather than
# routing through this non-monitor bucket; 'voided' remains a genuine
# not-actively-monitored-but-still-has-residual-chain-risk case.
NON_MONITOR_CHAIN_RISK_PHASES = frozenset({"voided"})
EXIT_REDECISION_EVENT_TYPES = frozenset({"EXIT_ORDER_REJECTED", "EXIT_RETRY_RELEASED"})
EXIT_REDECISION_PHASES = frozenset({"day0_window", "pending_exit"})
CLOSED_MARKET_PENDING_SETTLEMENT_VALIDATIONS = frozenset(
    {
        "day0_hard_fact_bin_dead_closed_market",
        "market_closed_non_accepting_orders",
    }
)
REVIEW_MANAGED_REASONS = frozenset(
    {
        "entry_authority_chain_absence_conflict",
        "confirmed_entry_fill_token_absent_market_not_resolved",
    }
)


def collect_monitor_cadence_evidence(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    max_age_seconds: float | None = None,
    min_occurred_at: datetime | None = None,
    strict_future: bool = False,
    monitor_refreshed_only: bool = False,
    require_fresh_inputs: bool = False,
    sample_limit: int = 25,
) -> dict[str, Any]:
    """Return per-position monitor cadence evidence for current money risk.

    ``max_age_seconds`` is the normal health/preflight freshness window.
    ``min_occurred_at`` is the post-start restart proof floor.  When both are
    supplied, a position must satisfy both.  Future-dated monitor events are
    reported separately because they are clock/data faults, not stale cadence.
    ``strict_future`` rejects every event after ``now``; other consumers retain
    the concurrent-write tolerance.
    ``monitor_refreshed_only`` excludes non-monitor fallback authority from
    coverage without changing the default health/preflight behavior.
    ``require_fresh_inputs`` additionally requires the latest canonical monitor
    event to attest both current probability and held-side CLOB authority.  It
    is meaningful only with ``monitor_refreshed_only`` and prevents a fresh
    timestamp carrying stale inputs from falsely clearing recovery debt.
    """

    if require_fresh_inputs and not monitor_refreshed_only:
        raise ValueError(
            "MONITOR_CADENCE_FRESH_INPUTS_REQUIRE_MONITOR_REFRESHED_ONLY"
        )

    position_columns = _table_columns(conn, "position_current")
    event_columns = _table_columns(conn, "position_events")
    now_utc = _ensure_utc(now)
    monitored_rows = _monitor_cadence_position_rows(conn, position_columns, now_utc=now_utc)
    non_monitor_chain_risk_rows = _non_monitor_chain_risk_position_rows(
        conn,
        position_columns,
        now_utc=now_utc,
    )
    min_occurred_utc = _ensure_utc(min_occurred_at) if min_occurred_at else None
    stale_or_missing: list[dict[str, Any]] = []
    future_events: list[dict[str, Any]] = []
    settlement_recoverable: list[dict[str, Any]] = []
    review_managed: list[dict[str, Any]] = []
    fresh_count = 0
    for position in monitored_rows:
        if _position_is_terminal_subprecision_dust_held_to_settlement(position):
            settlement_recoverable.append(
                {
                    "position_id": position["position_id"],
                    "phase": position["phase"],
                    "chain_state": position["chain_state"],
                    "cadence_source": "PARTIAL_EXIT_REMAINDER_TERMINAL_RELEASED",
                    "closed_market_validation": "sell_share_precision_dust",
                    "restart_resolution": "settlement_harvester_or_chain_size_change",
                }
            )
            # The venue cannot represent a SELL below one share quantum.  Keep
            # the positive residual in the monitor identity, but do not make a
            # permanently unavailable book a restart debt after the terminal
            # partial-exit fact has removed every live order.
            continue
        if _position_is_min_order_dust_held_to_settlement(position):
            settlement_recoverable.append(
                {
                    "position_id": position["position_id"],
                    "phase": position["phase"],
                    "chain_state": position["chain_state"],
                    "cadence_source": "EXIT_ORDER_REJECTED",
                    "closed_market_validation": "snapshot_min_order_dust",
                    "restart_resolution": "settlement_harvester_or_chain_size_change",
                }
            )
            if not monitor_refreshed_only:
                fresh_count += 1
                continue
        monitor_event = _latest_monitor_refreshed_event(
            conn,
            str(position["position_id"]),
            event_columns,
        )
        occurred_at = None if monitor_event is None else str(monitor_event.get("occurred_at") or "")
        position_evidence = {
            "position_id": position["position_id"],
            "phase": position["phase"],
            "chain_state": position["chain_state"],
        }
        exit_event = _latest_exit_redecision_event(
            conn,
            str(position["position_id"]),
            event_columns,
        )
        review_event = _latest_review_required_event(
            conn,
            str(position["position_id"]),
            event_columns,
        )
        if not occurred_at:
            if monitor_refreshed_only:
                stale_or_missing.append(
                    {**position_evidence, "last_monitor_refreshed_at": None}
                )
                continue
            if _review_required_event_is_fresh(
                review_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
                review_managed.append(position_evidence.copy())
                continue
            if _exit_redecision_event_is_fresh(
                position,
                exit_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
                continue
            stale_or_missing.append(
                {**position_evidence, "last_monitor_refreshed_at": None}
            )
            continue
        position_evidence["last_monitor_refreshed_at"] = occurred_at
        occurred_dt = _parse_iso_utc(occurred_at)
        if occurred_dt is None:
            stale_or_missing.append(
                {**position_evidence, "issue": "timestamp_unparseable"}
            )
            continue
        age_seconds = (now_utc - occurred_dt).total_seconds()
        position_evidence["age_seconds"] = round(age_seconds, 1)
        if age_seconds < (
            0.0 if strict_future else -MONITOR_CADENCE_FUTURE_TOLERANCE_SECONDS
        ):
            future_events.append(position_evidence)
            continue
        if require_fresh_inputs:
            input_issue = _monitor_event_fresh_input_issue(monitor_event)
            if input_issue is not None:
                if _monitor_event_closed_market_pending_settlement(
                    position_evidence,
                    monitor_event,
                ):
                    settlement_recoverable.append(position_evidence.copy())
                else:
                    stale_or_missing.append(
                        {**position_evidence, "issue": input_issue}
                    )
                continue
        if age_seconds < 0.0:
            fresh_count += 1
        elif min_occurred_utc is not None and occurred_dt < min_occurred_utc:
            if monitor_refreshed_only:
                if _monitor_event_closed_market_pending_settlement(
                    position_evidence,
                    monitor_event,
                ):
                    settlement_recoverable.append(position_evidence.copy())
                else:
                    stale_or_missing.append(position_evidence)
            elif _review_required_event_is_fresh(
                review_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
                review_managed.append(position_evidence.copy())
            elif _exit_redecision_event_is_fresh(
                position,
                exit_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
            else:
                if _monitor_event_closed_market_pending_settlement(
                    position_evidence,
                    monitor_event,
                ):
                    settlement_recoverable.append(position_evidence.copy())
                else:
                    stale_or_missing.append(position_evidence)
        elif max_age_seconds is not None and age_seconds > float(max_age_seconds):
            if monitor_refreshed_only:
                if _monitor_event_closed_market_pending_settlement(
                    position_evidence,
                    monitor_event,
                ):
                    settlement_recoverable.append(position_evidence.copy())
                else:
                    stale_or_missing.append(position_evidence)
            elif _review_required_event_is_fresh(
                review_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
                review_managed.append(position_evidence.copy())
            elif _exit_redecision_event_is_fresh(
                position,
                exit_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
            else:
                if _monitor_event_closed_market_pending_settlement(
                    position_evidence,
                    monitor_event,
                ):
                    settlement_recoverable.append(position_evidence.copy())
                else:
                    stale_or_missing.append(position_evidence)
        else:
            fresh_count += 1
    open_count = len(monitored_rows)
    quote_only_stale = [
        item
        for item in stale_or_missing
        if item.get("issue") == "monitor_clob_stale"
    ]
    probability_only_stale = [
        item
        for item in stale_or_missing
        if item.get("issue") == "monitor_probability_stale"
    ]
    blocking_stale = [
        item
        for item in stale_or_missing
        if item.get("issue") != "monitor_clob_stale"
    ]
    return {
        "open_position_count": open_count,
        "monitored_position_count": open_count,
        "monitored_position_ids": sorted(
            str(row["position_id"]) for row in monitored_rows
        ),
        "fresh_position_count": fresh_count,
        "stale_or_missing_position_count": len(stale_or_missing),
        "stale_or_missing_positions": stale_or_missing[:sample_limit],
        # Keep strict stale evidence intact, but split the complete list so a
        # missing held-side quote cannot become global cadence debt. Counts are
        # deliberately computed before sampling; each sample is independently
        # bounded by sample_limit.
        "quote_only_stale_position_count": len(quote_only_stale),
        "quote_only_stale_positions": quote_only_stale[:sample_limit],
        # A post-boot monitor can prove that the new runtime saw current CLOB
        # truth while probability authority remains DATA_DEGRADED. Runtime
        # entry law still treats this as blocking for the exact family; restart
        # proof may classify it separately so it cannot become a global deploy
        # pause with no reset until an external provider recovers.
        "probability_only_stale_position_count": len(probability_only_stale),
        "probability_only_stale_positions": probability_only_stale[:sample_limit],
        "blocking_stale_position_count": len(blocking_stale),
        "blocking_stale_positions": blocking_stale[:sample_limit],
        "settlement_recoverable_position_count": len(settlement_recoverable),
        "settlement_recoverable_positions": settlement_recoverable[:sample_limit],
        "review_managed_position_count": len(review_managed),
        "review_managed_positions": review_managed[:sample_limit],
        "future_monitor_event_count": len(future_events),
        "future_monitor_events": future_events[:sample_limit],
        "non_monitor_chain_risk_position_count": len(non_monitor_chain_risk_rows),
        "non_monitor_chain_risk_positions": non_monitor_chain_risk_rows[:sample_limit],
        "non_monitor_chain_risk_role": "chain_reconciliation_not_monitor_cadence",
    }


def monitor_cadence_blocking_evidence(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Read blocking/quote-only stale groups with legacy evidence fallback.

    Older callers and test doubles expose only the strict stale count/list.
    Treat that shape as wholly blocking so the new quote-only classification
    cannot accidentally make an unknown evidence shape fail open.
    """

    stale_count = int(evidence.get("stale_or_missing_position_count") or 0)
    stale_positions = list(evidence.get("stale_or_missing_positions") or [])
    group_fields = {
        "blocking_stale_position_count",
        "blocking_stale_positions",
        "quote_only_stale_position_count",
        "quote_only_stale_positions",
    }
    if not group_fields.issubset(evidence):
        return {
            "blocking_stale_position_count": stale_count,
            "blocking_stale_positions": stale_positions,
            "quote_only_stale_position_count": 0,
            "quote_only_stale_positions": [],
        }
    return {
        "blocking_stale_position_count": int(
            evidence.get("blocking_stale_position_count") or 0
        ),
        "blocking_stale_positions": list(evidence["blocking_stale_positions"]),
        "quote_only_stale_position_count": int(
            evidence.get("quote_only_stale_position_count") or 0
        ),
        "quote_only_stale_positions": list(evidence["quote_only_stale_positions"]),
    }


def monitor_restart_blocking_evidence(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Narrow restart blocking without weakening runtime family admission.

    A recent MONITOR_REFRESHED event with a fresh held-side CLOB but degraded
    probability proves the restarted process evaluated that position. It does
    not authorize BUY/SELL and remains part of ordinary cadence blocking. Only
    restart/deploy proof subtracts this exact typed subset; unknown or legacy
    evidence shapes remain wholly blocking.
    """

    strict = monitor_cadence_blocking_evidence(evidence)
    strict_count = int(strict["blocking_stale_position_count"])
    probability_fields = {
        "probability_only_stale_position_count",
        "probability_only_stale_positions",
    }
    probability_count = 0
    probability_positions: list[Any] = []
    if probability_fields.issubset(evidence):
        candidate_count = int(
            evidence.get("probability_only_stale_position_count") or 0
        )
        candidate_positions = list(
            evidence.get("probability_only_stale_positions") or []
        )
        sample_is_typed = all(
            isinstance(item, Mapping)
            and bool(str(item.get("position_id") or ""))
            and item.get("issue") == "monitor_probability_stale"
            for item in candidate_positions
        )
        if (
            0 <= candidate_count <= strict_count
            and sample_is_typed
            and (candidate_count == 0 or candidate_positions)
        ):
            probability_count = candidate_count
            probability_positions = candidate_positions
    probability_ids = {
        str(item.get("position_id") or "")
        for item in probability_positions
        if isinstance(item, Mapping)
    }
    restart_blocking_positions = [
        item
        for item in strict["blocking_stale_positions"]
        if not (
            isinstance(item, Mapping)
            and str(item.get("position_id") or "") in probability_ids
            and item.get("issue") == "monitor_probability_stale"
        )
    ]
    return {
        **strict,
        "probability_only_stale_position_count": probability_count,
        "probability_only_stale_positions": probability_positions,
        "restart_blocking_stale_position_count": strict_count - probability_count,
        "restart_blocking_stale_positions": restart_blocking_positions,
    }


def latest_complete_global_auction_receipt(
    conn: sqlite3.Connection,
    *,
    completed_not_before: datetime,
    require_held_coverage_count: int = 0,
    require_held_position_ids: tuple[str, ...] = (),
) -> tuple[int, int, int] | None:
    """Return a complete current-cut auction proving held redecision coverage."""

    try:
        rows = conn.execute(
            """
            SELECT id, mode, started_at, completed_at, artifact_json
              FROM decision_log
             WHERE mode IN (
                'global_single_order_auction',
                'global_single_order_auction_delta',
                'global_single_order_auction_duplicate'
             )
             ORDER BY id DESC
             LIMIT 8
            """
        ).fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        try:
            from src.contracts.global_auction_receipt import (
                GLOBAL_AUCTION_RECEIPT_SCHEMA_VERSION,
                assert_global_auction_summary_integrity,
            )

            artifact = json.loads(row["artifact_json"] or "{}")
            summary = artifact.get("summary") or {}
            required_position_ids = frozenset(require_held_position_ids)
            if required_position_ids:
                if summary.get("schema_version") != GLOBAL_AUCTION_RECEIPT_SCHEMA_VERSION:
                    continue
                assert_global_auction_summary_integrity(summary)
                row_completed_at = _parse_iso_utc(row["completed_at"])
                artifact_completed_at = _parse_iso_utc(artifact.get("completed_at"))
                decision_at = _parse_iso_utc(summary.get("decision_at_utc"))
                if (
                    row_completed_at is None
                    or artifact_completed_at is None
                    or decision_at is None
                    or row_completed_at != artifact_completed_at
                    or row_completed_at != decision_at
                ):
                    continue
                completed_at = row_completed_at
            else:
                completed_at = _parse_iso_utc(
                    artifact.get("completed_at")
                    or row["completed_at"]
                    or row["started_at"]
                )
            candidate_count = int(summary.get("candidate_evaluation_count") or 0)
            scope_count = int(summary.get("full_scope_family_count") or 0)
            held_expected_count = int(summary.get("held_position_expected_count") or 0)
            held_accounted_count = int(
                summary.get("held_position_evaluated_count") or 0
            ) + int(summary.get("held_position_excluded_count") or 0)
            if required_position_ids:
                from src.control.live_health import (
                    _current_global_auction_holding_payload,
                )

                holding_payload = _current_global_auction_holding_payload(
                    conn,
                    summary,
                )
                receipt_position_ids = tuple(
                    str(item.get("position_id") or "").strip()
                    for item in holding_payload
                )
                if (
                    any(not position_id for position_id in receipt_position_ids)
                    or len(set(receipt_position_ids)) != len(receipt_position_ids)
                    # The receipt may contain an additional position that is
                    # no longer a current monitor obligation (for example an
                    # unexecutable dust remainder). Every current obligation
                    # must still be present; a newly opened or omitted current
                    # position therefore keeps the restart guard closed.
                    or not required_position_ids.issubset(receipt_position_ids)
                    or len(receipt_position_ids) != held_expected_count
                    or sum(
                        item.get("status") == "EVALUATED"
                        for item in holding_payload
                    )
                    != int(summary.get("held_position_evaluated_count") or 0)
                    or sum(
                        item.get("status") == "EXCLUDED"
                        for item in holding_payload
                    )
                    != int(summary.get("held_position_excluded_count") or 0)
                ):
                    continue
        except Exception:  # noqa: BLE001 - malformed receipts fail closed.
            continue
        if (
            completed_at is not None
            and completed_at >= completed_not_before
            and summary.get("candidate_coverage_complete") is True
            and summary.get("scope_family_coverage_complete") is True
            and candidate_count > 0
            and scope_count > 0
            and (
                require_held_coverage_count <= 0
                or (
                    summary.get("held_position_coverage_complete") is True
                    and held_expected_count >= require_held_coverage_count
                    and held_accounted_count >= held_expected_count
                )
            )
        ):
            return int(row["id"]), candidate_count, scope_count
    return None


def collect_monitor_restart_proof(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    completed_not_before: datetime,
    max_age_seconds: float | None = None,
    sample_limit: int = 5,
) -> dict[str, Any]:
    """Prove current held cadence and exact auction coverage in one DB snapshot."""

    owns_transaction = not bool(getattr(conn, "in_transaction", False))
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        monitor = collect_monitor_cadence_evidence(
            conn,
            now=now,
            min_occurred_at=completed_not_before,
            max_age_seconds=max_age_seconds,
            strict_future=True,
            monitor_refreshed_only=True,
            require_fresh_inputs=True,
            sample_limit=sample_limit,
        )
        groups = monitor_restart_blocking_evidence(monitor)
        open_count = int(monitor.get("open_position_count") or 0)
        held_ids = tuple(
            str(value or "").strip()
            for value in monitor.get("monitored_position_ids", ())
        )
        identity_complete = (
            len(held_ids) == open_count
            and all(held_ids)
            and len(set(held_ids)) == len(held_ids)
        )
        quote_only_count = int(groups["quote_only_stale_position_count"])
        receipt = None
        if quote_only_count > 0 and identity_complete:
            receipt = latest_complete_global_auction_receipt(
                conn,
                completed_not_before=completed_not_before,
                require_held_coverage_count=open_count,
                require_held_position_ids=held_ids,
            )
        green = (
            identity_complete
            and int(monitor.get("future_monitor_event_count") or 0) == 0
            and int(groups["restart_blocking_stale_position_count"]) == 0
            and (quote_only_count == 0 or receipt is not None)
        )
        return {
            **monitor,
            **groups,
            "complete_held_auction_receipt": receipt,
            "monitor_scope_identity": held_ids,
            "green": green,
        }
    finally:
        if owns_transaction and bool(getattr(conn, "in_transaction", False)):
            conn.rollback()


def count_current_monitor_obligations(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> int:
    """Strictly count positive exposures governed by monitor cadence law.

    Zero is authority only when the canonical schema and every exposure field
    that could prove a monitored row are present and finite. Unknown data raises
    so callers retain fail-closed monitor priority.
    """

    _ensure_utc(now)
    columns = _table_columns(conn, "position_current")
    required = {"position_id", "phase", "shares", "chain_shares"}
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError(
            "MONITOR_OBLIGATION_SCHEMA_INCOMPLETE:" + ",".join(missing)
        )
    invalid_phase = conn.execute(
        """
        SELECT position_id
          FROM position_current
         WHERE phase IS NULL OR TRIM(phase) = ''
         LIMIT 1
        """
    ).fetchone()
    if invalid_phase is not None:
        raise RuntimeError("MONITOR_OBLIGATION_PHASE_UNKNOWN")

    phases = tuple(sorted(MONITOR_CADENCE_POSITION_PHASES))
    placeholders = ",".join("?" for _ in phases)
    rows = conn.execute(
        f"""
        SELECT position_id, shares, chain_shares
          FROM position_current
         WHERE phase IN ({placeholders})
        """,
        phases,
    ).fetchall()
    obligation_count = 0
    for row in rows:
        position_id = str(row["position_id"] or "").strip()
        if not position_id:
            raise RuntimeError("MONITOR_OBLIGATION_POSITION_ID_UNKNOWN")
        exposures: list[float | None] = []
        for field in ("shares", "chain_shares"):
            raw = row[field]
            try:
                value = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None and (not math.isfinite(value) or value < 0.0):
                value = None
            exposures.append(value)
        if any(
            value is not None and value > MONITOR_CADENCE_EXPOSURE_FLOOR
            for value in exposures
        ):
            obligation_count += 1
            continue
        if any(value is None for value in exposures):
            raise RuntimeError(
                f"MONITOR_OBLIGATION_EXPOSURE_UNKNOWN:{position_id}"
            )
    return obligation_count


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _monitor_cadence_position_rows(
    conn: sqlite3.Connection,
    position_columns: set[str],
    *,
    now_utc: datetime,
) -> list[dict[str, object]]:
    if "position_id" not in position_columns:
        return []
    optional_selects = []
    for column in (
        "phase",
        "shares",
        "chain_shares",
        "chain_state",
        "order_status",
        "exit_reason",
        "target_date",
    ):
        optional_selects.append(column if column in position_columns else f"NULL AS {column}")
    rows = conn.execute(
        f"""
        SELECT position_id, {", ".join(optional_selects)}
          FROM position_current
        """
    ).fetchall()
    monitored: list[dict[str, object]] = []
    for row in rows:
        position_id = str(row["position_id"] or "")
        phase = str(row["phase"] or "").strip().lower()
        chain_state = str(row["chain_state"] or "").strip()
        order_status = str(row["order_status"] or "").strip().lower()
        shares = _finite_nonnegative_float_or_none(row["shares"])
        chain_shares = _finite_nonnegative_float_or_none(row["chain_shares"])
        # SCOPE: every positive canonical exposure in a monitored lifecycle
        # phase. DRAIN: settlement/reconciliation writes zero or a terminal
        # phase. RESET: any later positive residual restores the obligation.
        exposure_positive = (
            shares is not None and shares > MONITOR_CADENCE_EXPOSURE_FLOOR
        ) or (
            chain_shares is not None
            and chain_shares > MONITOR_CADENCE_EXPOSURE_FLOOR
        )
        exposure_unknown = shares is None or chain_shares is None
        if _position_requires_monitor_cadence(
            phase=phase,
            chain_state=chain_state,
            exposure_positive=exposure_positive or exposure_unknown,
            target_date=row["target_date"],
            now_utc=now_utc,
        ):
            monitored.append(
                {
                    "position_id": position_id,
                    "phase": phase,
                    "chain_state": chain_state,
                    "order_status": order_status,
                    "exit_reason": str(row["exit_reason"] or "").strip(),
                    "shares": shares,
                    "chain_shares": chain_shares,
                    "exposure_unknown": exposure_unknown,
                }
            )
    return monitored


_MIN_ORDER_DUST_RE = re.compile(
    r"\[DUST:\s*executable_snapshot_gate:\s*size\s+"
    r"(?P<size>[0-9]+(?:\.[0-9]+)?)\s+is below snapshot min_order_size\s+"
    r"(?P<minimum>[0-9]+(?:\.[0-9]+)?)\s*\]"
)

_SELL_SHARE_QUANTUM = Decimal("0.01")


def _position_is_terminal_subprecision_dust_held_to_settlement(
    position: dict[str, object],
) -> bool:
    """Recognize a terminal partial-exit remainder that cannot form a SELL."""

    if str(position.get("order_status") or "") != "filled":
        return False
    if (
        str(position.get("exit_reason") or "")
        != "PARTIAL_EXIT_REMAINDER_TERMINAL_RELEASED"
    ):
        return False
    if bool(position.get("exposure_unknown")):
        return False
    exposure = max(
        Decimal(str(position.get("shares") or 0)),
        Decimal(str(position.get("chain_shares") or 0)),
    )
    return Decimal("0") < exposure < _SELL_SHARE_QUANTUM


def _position_is_min_order_dust_held_to_settlement(position: dict[str, object]) -> bool:
    """Recognize the exact exit-lifecycle proof that no venue SELL is feasible."""

    if str(position.get("phase") or "") != "pending_exit":
        return False
    if str(position.get("order_status") or "") != "backoff_exhausted":
        return False
    match = _MIN_ORDER_DUST_RE.search(str(position.get("exit_reason") or ""))
    if match is None:
        return False
    exposure = max(
        Decimal(str(position.get("shares") or 0)),
        Decimal(str(position.get("chain_shares") or 0)),
    )
    cited_size = Decimal(match.group("size"))
    minimum = Decimal(match.group("minimum"))
    return (
        exposure > 0
        and minimum > 0
        and abs(exposure - cited_size) <= Decimal("0.000001")
        and cited_size < minimum
    )


def _position_requires_monitor_cadence(
    *,
    phase: str,
    chain_state: str,
    exposure_positive: bool,
    target_date: object = None,
    now_utc: datetime | None = None,
) -> bool:
    if not exposure_positive:
        return False
    if not phase:
        return True
    if phase in MONITOR_CADENCE_POSITION_PHASES:
        return True
    return False


def _non_monitor_chain_risk_position_rows(
    conn: sqlite3.Connection,
    position_columns: set[str],
    *,
    now_utc: datetime,
) -> list[dict[str, object]]:
    if "position_id" not in position_columns:
        return []
    optional_selects = []
    for column in ("phase", "shares", "chain_shares", "chain_state", "target_date"):
        optional_selects.append(column if column in position_columns else f"NULL AS {column}")
    rows = conn.execute(
        f"""
        SELECT position_id, {", ".join(optional_selects)}
          FROM position_current
        """
    ).fetchall()
    chain_risk_rows: list[dict[str, object]] = []
    for row in rows:
        phase = str(row["phase"] or "").strip().lower()
        chain_state = str(row["chain_state"] or "").strip()
        chain_shares = _float_or_zero(row["chain_shares"])
        if phase not in NON_MONITOR_CHAIN_RISK_PHASES:
            continue
        if chain_shares <= MONITOR_CADENCE_EXPOSURE_FLOOR:
            continue
        if chain_state not in CURRENT_MONEY_RISK_CHAIN_STATES:
            continue
        if _position_requires_monitor_cadence(
            phase=phase,
            chain_state=chain_state,
            exposure_positive=True,
            target_date=row["target_date"],
            now_utc=now_utc,
        ):
            continue
        chain_risk_rows.append(
            {
                "position_id": str(row["position_id"] or ""),
                "phase": phase,
                "chain_state": chain_state,
                "shares": _float_or_zero(row["shares"]),
                "chain_shares": chain_shares,
                "target_date": str(row["target_date"] or ""),
            }
        )
    return chain_risk_rows


def _latest_monitor_refreshed_event(
    conn: sqlite3.Connection,
    position_id: str,
    event_columns: set[str],
) -> dict[str, str] | None:
    order_by = (
        "sequence_no DESC"
        if "sequence_no" in event_columns
        else "datetime(occurred_at) DESC"
    )
    payload_select = "payload_json" if "payload_json" in event_columns else "NULL AS payload_json"
    row = conn.execute(
        f"""
        SELECT occurred_at, {payload_select}
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'MONITOR_REFRESHED'
         ORDER BY {order_by}
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "occurred_at": str(row["occurred_at"] or ""),
        "payload_json": str(row["payload_json"] or ""),
    }


def _monitor_event_closed_market_pending_settlement(
    position_evidence: dict[str, Any],
    monitor_event: dict[str, str] | None,
) -> bool:
    if monitor_event is None:
        return False
    try:
        payload = json.loads(monitor_event.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    validations_raw = payload.get("applied_validations")
    validations = {str(item) for item in validations_raw} if isinstance(validations_raw, list) else set()
    matched = sorted(validations & CLOSED_MARKET_PENDING_SETTLEMENT_VALIDATIONS)
    canonical_closed_hold = (
        payload.get("semantic_event") == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
        and payload.get("hold_reason") == "MARKET_CLOSED_AWAITING_SETTLEMENT"
        and payload.get("exit_order_submitted") is False
        and payload.get("exit_failure") is False
        and "MARKET_CLOSED_AWAITING_SETTLEMENT" in validations
    )
    if not canonical_closed_hold and not matched:
        return False
    closed_market_validation = (
        "MARKET_CLOSED_AWAITING_SETTLEMENT"
        if canonical_closed_hold
        else matched[0]
    )
    position_evidence.update(
        {
            "cadence_source": "MONITOR_REFRESHED_CLOSED_MARKET_PENDING_SETTLEMENT",
            "closed_market_validation": closed_market_validation,
            "restart_resolution": "settlement_harvester_or_market_reopen_recovery",
        }
    )
    return True


def _monitor_event_fresh_input_issue(
    monitor_event: dict[str, str] | None,
) -> str | None:
    """Return why a current monitor event lacks redecision authority."""

    if monitor_event is None:
        return "monitor_payload_missing"
    try:
        payload = json.loads(monitor_event.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return "monitor_payload_unparseable"
    if not isinstance(payload, dict):
        return "monitor_payload_invalid"

    validations_raw = payload.get("applied_validations")
    validations = (
        {str(item) for item in validations_raw}
        if isinstance(validations_raw, list)
        else set()
    )
    if "global_auction_completion_request_failed" in validations:
        return "monitor_exit_completion_unavailable"

    def _is_true(value: object) -> bool:
        return value is True or (type(value) is int and value == 1)

    probability_flag = _is_true(payload.get("last_monitor_prob_is_fresh"))
    quote_flag = _is_true(payload.get("last_monitor_market_price_is_fresh"))

    def _is_unit_interval(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and 0.0 <= float(value) <= 1.0
        )

    probability_fresh = probability_flag and _is_unit_interval(
        payload.get("last_monitor_prob")
    )
    quote_fresh = quote_flag and _is_unit_interval(
        payload.get("last_monitor_market_price")
    )
    structural_win = (
        probability_fresh
        and payload.get("last_monitor_prob") == 1.0
        and payload.get("selected_method") == "day0_absorbing_hard_fact"
        and "day0_hard_fact_structural_win_quote_bypassed" in validations
    )
    if structural_win:
        return None
    if probability_fresh and quote_fresh:
        if payload.get("exit_decision_available") is False:
            return "monitor_exit_decision_unavailable"
        return None
    if probability_flag and not probability_fresh:
        return "monitor_probability_value_invalid"
    if quote_flag and not quote_fresh:
        return "monitor_market_price_value_invalid"
    if not probability_fresh and not quote_fresh:
        return "monitor_probability_and_clob_stale"
    if not probability_fresh:
        return "monitor_probability_stale"
    return "monitor_clob_stale"


def _latest_exit_redecision_event(
    conn: sqlite3.Connection,
    position_id: str,
    event_columns: set[str],
) -> tuple[str, str] | None:
    if "event_type" not in event_columns or "occurred_at" not in event_columns:
        return None
    order_by = "datetime(occurred_at) DESC"
    if "sequence_no" in event_columns:
        order_by += ", sequence_no DESC"
    placeholders = ", ".join("?" for _ in EXIT_REDECISION_EVENT_TYPES)
    row = conn.execute(
        f"""
        SELECT event_type, occurred_at
          FROM position_events
         WHERE position_id = ?
           AND event_type IN ({placeholders})
         ORDER BY {order_by}
         LIMIT 1
        """,
        (position_id, *tuple(sorted(EXIT_REDECISION_EVENT_TYPES))),
    ).fetchone()
    if row is None:
        return None
    return str(row["event_type"] or ""), str(row["occurred_at"] or "")


def _latest_review_required_event(
    conn: sqlite3.Connection,
    position_id: str,
    event_columns: set[str],
) -> dict[str, str] | None:
    if not {"event_type", "occurred_at", "payload_json"}.issubset(event_columns):
        return None
    order_by = "datetime(occurred_at) DESC"
    if "sequence_no" in event_columns:
        order_by += ", sequence_no DESC"
    row = conn.execute(
        f"""
        SELECT occurred_at, payload_json
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'REVIEW_REQUIRED'
         ORDER BY {order_by}
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "occurred_at": str(row["occurred_at"] or ""),
        "payload_json": str(row["payload_json"] or ""),
    }


def _review_required_event_is_fresh(
    review_event: dict[str, str] | None,
    *,
    now_utc: datetime,
    max_age_seconds: float | None,
    min_occurred_utc: datetime | None,
    position_evidence: dict[str, Any],
    future_events: list[dict[str, Any]],
) -> bool:
    if review_event is None:
        return False
    try:
        payload = json.loads(review_event.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    reason = str(payload.get("reason") or "")
    if reason not in REVIEW_MANAGED_REASONS:
        return False
    exact_conflict = (
        reason == "entry_authority_chain_absence_conflict"
        and payload.get("review_state") == "unresolved"
        and payload.get("source") == "chain_reconciliation"
    )
    exact_mirror = (
        reason == "confirmed_entry_fill_token_absent_market_not_resolved"
        and payload.get("chain_mirror_classification") == "review_open_absent"
        and payload.get("reconciler") == "chain_mirror"
    )
    if not exact_conflict and not exact_mirror:
        return False
    occurred_at = str(review_event.get("occurred_at") or "")
    occurred_dt = _parse_iso_utc(occurred_at)
    if occurred_dt is None:
        return False
    age_seconds = (now_utc - occurred_dt).total_seconds()
    enriched = {
        **position_evidence,
        "cadence_source": "REVIEW_REQUIRED",
        "latest_review_required_at": occurred_at,
        "review_reason": reason,
        "review_age_seconds": round(age_seconds, 1),
    }
    if age_seconds < 0.0:
        future_events.append(enriched)
        return False
    if min_occurred_utc is not None and occurred_dt < min_occurred_utc:
        return False
    if max_age_seconds is not None and age_seconds > float(max_age_seconds):
        return False
    position_evidence.update(enriched)
    return True


def _exit_redecision_event_is_fresh(
    position: dict[str, object],
    exit_event: tuple[str, str] | None,
    *,
    now_utc: datetime,
    max_age_seconds: float | None,
    min_occurred_utc: datetime | None,
    position_evidence: dict[str, Any],
    future_events: list[dict[str, Any]],
) -> bool:
    phase = str(position.get("phase") or "").strip().lower()
    order_status = str(position.get("order_status") or "").strip().lower()
    exit_reason = str(position.get("exit_reason") or "").strip()
    if phase not in EXIT_REDECISION_PHASES:
        return False
    if not exit_reason and order_status not in {"retry_pending", "exit_intent"}:
        return False
    if exit_event is None:
        return False
    event_type, occurred_at = exit_event
    occurred_dt = _parse_iso_utc(occurred_at)
    enriched = {
        **position_evidence,
        "cadence_source": event_type,
        "latest_exit_redecision_at": occurred_at,
    }
    if occurred_dt is None:
        return False
    age_seconds = (now_utc - occurred_dt).total_seconds()
    enriched["exit_redecision_age_seconds"] = round(age_seconds, 1)
    if age_seconds < 0.0:
        future_events.append(enriched)
        return False
    if min_occurred_utc is not None and occurred_dt < min_occurred_utc:
        return False
    if max_age_seconds is not None and age_seconds > float(max_age_seconds):
        return False
    position_evidence.update(enriched)
    return True


def _parse_iso_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _float_or_zero(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _finite_nonnegative_float_or_none(value: object) -> float | None:
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if parsed is None or not math.isfinite(parsed) or parsed < 0.0:
        return None
    return parsed
