# Created: (pre-audit)
# Last reused or audited: 2026-06-08
# Authority basis: thepath/audit-realign Fitz #5 — the risk-block read path
#   (_get_risk_details) used a bare no-timeout sqlite3.connect (a WAL lock-loser
#   on the read that surfaces the risk block). It now carries the configured
#   busy_timeout. The risk level/details surfaced here are the SAME single
#   authority (get_current_level) the entry gate reads — no parallel risk lane.
"""Status summary: written every cycle. Zeus is not a black box.

Blueprint v2 §10: 5-section health snapshot.
Written to a derived live status file for Venus/OpenClaw to read.
"""

import json
import logging
import os
from datetime import datetime, timezone

from src.config import get_mode, state_path
from src.control.control_plane import (
    get_entries_pause_evidence,
    get_entries_pause_reason,
    get_entries_pause_source,
    get_edge_threshold_multiplier,
    is_entries_paused,
    recommended_autosafe_commands_from_status,
    recommended_commands_from_status,
    refresh_control_state,
    review_required_commands_from_status,
    strategy_gates,
)
from src.control.gate_decision import reason_refuted
from src.observability.calibration_serving_status import build_calibration_serving_status
from src.observability.price_evidence_report import (
    build_price_evidence_error_report,
    build_price_evidence_report,
)
from src.state.decision_chain import query_learning_surface_summary, query_lifecycle_funnel_report
from src.state.db import (
    get_trade_connection_with_world,
    query_execution_event_summary,
    query_position_current_status_view,
    query_strategy_health_snapshot,
)
from src.state.decision_chain import query_no_trade_cases
from src.state.lifecycle_manager import TERMINAL_STATES
from src.state.truth_files import annotate_truth_payload, read_truth_json

logger = logging.getLogger(__name__)

STATUS_PATH = state_path("status_summary.json")
LEGACY_POSITIONS_PATH = state_path("positions.json")
# P0c: "quarantined" retained explicitly in both sets below — it dropped out
# of the canonical TERMINAL_STATES when its fold widened to
# {QUARANTINED, SETTLED, VOIDED} (docs/rebuild/chain_mirror_state_model_2026-07-04.md
# §5), but a quarantined row is still inactive/no-new-entry for status display
# purposes until the chain-mirror reconciler resolves it.
_LEGACY_JSON_INACTIVE_POSITION_STATES = set(TERMINAL_STATES) | {
    "closed",
    "exited",
    "quarantined",
}
_OPEN_ENTRY_COMMAND_STATES = {"ACKED", "PARTIAL", "POST_ACKED", "SUBMITTED"}
_TERMINAL_ENTRY_COMMAND_STATES = {
    "CANCELLED",
    "CANCELED",
    "EXPIRED",
    "FILLED",
    "REJECTED",
    "SUBMIT_REJECTED",
}
_TERMINAL_ENTRY_ORDER_STATUSES = {
    "filled",
    "cancelled",
    "canceled",
    "expired",
    "rejected",
    "voided",
}
_TERMINAL_ENTRY_PHASES = set(TERMINAL_STATES) | {"quarantined"}
_TERMINAL_VENUE_ORDER_STATES = {
    "MATCHED",
    "FILLED",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
    "REJECTED",
}
_NONTERMINAL_VENUE_ORDER_STATES = {
    "LIVE",
    "PARTIAL",
    "PARTIALLY_MATCHED",
    "OPEN",
    "RESTING",
}
_TERMINAL_COMMAND_EVENT_TYPES = {
    "CANCEL_ACKED",
    "EXPIRED",
    "FILL_CONFIRMED",
    "SUBMIT_REJECTED",
}
_BUSINESS_CYCLE_KEYS_PRESERVED_ON_AUX_PULSE = {
    "mode",
    "started_at",
    "completed_at",
    "candidates",
    "candidates_evaluated",
    "processed",
    "proof_accepted",
    "final_intents_built",
    "final_execution_intents_built",
    "submit_attempts",
    "entry_submit_attempts",
    "entry_orders_submitted",
    "venue_acks",
    "venue_ack_count",
    "no_trades",
    "no_trade_count",
    "rejected",
    "retried",
    "dead_lettered",
    "rejection_reason_counts",
    "top_no_trade_reasons",
    "no_trade_reasons",
    "deterministic_rejections",
}
_BUSINESS_CYCLE_KEYS = _BUSINESS_CYCLE_KEYS_PRESERVED_ON_AUX_PULSE - {
    "mode",
    "started_at",
    "completed_at",
}


def _cycle_has_business_activity(cycle: dict | None) -> bool:
    if not isinstance(cycle, dict):
        return False
    mode = str(cycle.get("mode") or "")
    if mode and mode != "heartbeat_pulse":
        return True
    return any(key in cycle for key in _BUSINESS_CYCLE_KEYS)


def _atomic_write_status_payload(payload: dict) -> None:
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(STATUS_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, str(STATUS_PATH))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_cycle_pulse(cycle_summary: dict | None = None) -> None:
    """Update live progress plus the minimal DB-derived runtime read model."""

    generated_at = datetime.now(timezone.utc).isoformat()
    prior: dict = {}
    if STATUS_PATH.exists():
        try:
            with open(STATUS_PATH) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                prior = loaded
        except Exception:
            prior = {}
    status = dict(prior)
    status["process"] = {
        "pid": os.getpid(),
        "mode": get_mode(),
        "version": "zeus_v2",
    }
    if cycle_summary is not None:
        incoming_cycle = dict(cycle_summary)
        prior_cycle = prior.get("cycle") if isinstance(prior, dict) else None
        if (
            isinstance(prior_cycle, dict)
            and "candidates" in prior_cycle
            and "candidates" not in incoming_cycle
        ):
            merged_cycle = dict(prior_cycle)
            auxiliary_cycle = dict(incoming_cycle)
            for key in _BUSINESS_CYCLE_KEYS_PRESERVED_ON_AUX_PULSE:
                auxiliary_cycle.pop(key, None)
            merged_cycle.update(auxiliary_cycle)
            if incoming_cycle:
                merged_cycle["last_auxiliary_pulse"] = incoming_cycle
            status["cycle"] = merged_cycle
        else:
            status["cycle"] = incoming_cycle
    status["process"]["pulse_only"] = not _cycle_has_business_activity(status.get("cycle"))
    status["process"]["last_pulse_kind"] = (
        "business_cycle" if not status["process"]["pulse_only"] else "auxiliary_pulse"
    )
    minimal_refresh_ok = _refresh_minimal_runtime_read_model_for_status(status)
    try:
        status["execution_capability"] = _get_execution_capability_status()
    except Exception as exc:
        status["execution_capability"] = {
            "schema_version": 1,
            "authority": "derived_operator_visibility",
            "derived_only": True,
            "live_action_authorized": False,
            "entry": {
                "action": "entry",
                "status": "unavailable",
                "global_allow_submit": False,
                "live_action_authorized": False,
                "authority": "derived_operator_visibility",
                "components": [
                    _capability_component(
                        "execution_capability_pulse",
                        allowed=False,
                        reason="pulse_refresh_failed",
                        details={"error_type": type(exc).__name__, "error": str(exc)},
                    )
                ],
                "required_intent_components": [],
                "unavailable_components": ["execution_capability_pulse"],
            },
        }
    _refresh_current_open_entry_orders_for_status(status)
    if minimal_refresh_ok:
        _refresh_control_status_for_pulse(status)
        _refresh_pulse_infrastructure_status(status, cycle_summary)
        status["timestamp"] = generated_at
        # Freshness-contract bridge: release-gate / EDLI-stage readiness checks
        # read a canonical top-level freshness key (generated_at|updated_at|
        # observed_at|captured_at), not "timestamp". Emit generated_at at the top
        # level (same instant as timestamp) so a genuinely-fresh pulse is read as
        # fresh by the gate. This is the producer honoring the gate's contract,
        # NOT a relaxation of the freshness window.
        status["generated_at"] = generated_at
        status = annotate_truth_payload(status, STATUS_PATH, generated_at=generated_at, authority="VERIFIED")
    else:
        _preserve_prior_status_freshness_after_pulse_failure(status, prior)
    _atomic_write_status_payload(status)


def _refresh_control_status_for_pulse(status: dict) -> None:
    """Refresh cheap control-plane truth on pulse writes.

    ``write_cycle_pulse`` merges with the previous full status snapshot.  Control
    overrides are live operator state, so a pulse must not preserve stale
    entries_paused fields from that prior snapshot.
    """

    control = status.get("control")
    if not isinstance(control, dict):
        control = {}
        status["control"] = control
    try:
        current_strategy_gates = strategy_gates()
        control["entries_paused"] = is_entries_paused()
        control["entries_pause_source"] = get_entries_pause_source()
        control["entries_pause_reason"] = get_entries_pause_reason()
        control["entries_pause_evidence"] = get_entries_pause_evidence()
        control["edge_threshold_multiplier"] = get_edge_threshold_multiplier()
        control["strategy_gates"] = {k: v.to_dict() for k, v in current_strategy_gates.items()}
    except Exception as exc:  # noqa: BLE001 - status pulse must remain non-fatal
        control["status"] = "control_refresh_failed"
        control["refresh_error_type"] = type(exc).__name__
        control["refresh_error"] = str(exc)


def _preserve_prior_status_freshness_after_pulse_failure(status: dict, prior: dict) -> None:
    """Fail closed: a failed truth refresh must not mint a fresh status timestamp."""

    prior_timestamp = prior.get("timestamp") if isinstance(prior, dict) else None
    if prior_timestamp:
        status["timestamp"] = prior_timestamp
        # Keep generated_at (gate freshness key) in lockstep with timestamp so a
        # failed pulse refresh cannot mint a fresh generated_at the gate would
        # read as live. Fail-closed: inherit the prior (stale) instant.
        status["generated_at"] = prior_timestamp
    else:
        status.pop("timestamp", None)
        status.pop("generated_at", None)
    prior_truth = prior.get("truth") if isinstance(prior, dict) else None
    truth = dict(prior_truth) if isinstance(prior_truth, dict) else {}
    truth.setdefault("runtime_state", get_mode())
    truth.setdefault("source_path", str(STATUS_PATH))
    truth["authority"] = "UNVERIFIED"
    truth["stale_reason"] = "position_current_pulse_query_error"
    status["truth"] = truth


def _pulse_only_summary(surface: str) -> dict:
    return {
        "status": "not_refreshed_by_cycle_pulse",
        "authority": "derived_operator_visibility",
        "pulse_only": True,
        "surface": surface,
    }


def _check_armed_live_no_submit_receipts(
    *,
    status: dict,
    cycle: dict,
    window_seconds: int,
) -> bool:
    """Return True when the system is armed-live with no recent submit receipt.

    armed_live is True when:
      - the global entry gate (global_allow_submit) is True (entry is open), AND
      - the current cycle produced at least one submit-admissible intent
        (final_intents_built > 0 in the cycle summary).

    recent_submit_receipt is True when at least one SUBMIT_REQUESTED or
    SUBMIT_ACKED row exists in venue_command_events with occurred_at within
    the last ``window_seconds`` seconds.

    Returns True (→ append to consistency_issues) only when armed_live AND
    zero recent submit receipts.  Detection-only: this function never blocks
    or gates an order.
    """
    from datetime import datetime, timezone

    # --- armed_live: gate open AND intents were built ---
    entry_cap = (
        status.get("execution_capability", {}).get("entry", {})
        if isinstance(status.get("execution_capability"), dict)
        else {}
    )
    global_allow_submit = bool(entry_cap.get("global_allow_submit", False))
    final_intents_built = int(cycle.get("final_intents_built", 0) or 0)
    armed_live = global_allow_submit and final_intents_built > 0

    if not armed_live:
        return False

    # --- recent_submit_receipt: query venue_command_events ---
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc.timestamp() - window_seconds
    # ISO-8601 threshold string for SQL comparison (SQLite string comparison
    # works correctly for ISO-8601 UTC timestamps in the same timezone).
    import datetime as _dt
    threshold_iso = _dt.datetime.fromtimestamp(window_start, tz=_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    try:
        conn = get_trade_connection_with_world()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM venue_command_events
                WHERE event_type IN ('SUBMIT_REQUESTED', 'SUBMIT_ACKED')
                  AND occurred_at >= ?
                LIMIT 1
                """,
                (threshold_iso,),
            ).fetchone()
            recent_submit_receipt = row is not None
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        # B6 (2026-06-20): FAIL CLOSED. An unreadable receipt table under armed-live
        # must NOT suppress the dead-submit RED. The prior `return False` let the
        # detector be silenced by its OWN query failure (a false-green): armed-live
        # could avoid RED simply because venue_command_events was unreadable. Treat an
        # unreadable query as "cannot confirm a recent submit receipt" -> surface the
        # consistency issue -> RED. Detection-only; this never blocks an order.
        logger.warning(
            "armed_live submit-receipt DB query UNREADABLE under armed-live "
            "(surfacing as a consistency issue, NOT suppressing): %s", exc
        )
        return True

    return not recent_submit_receipt


def _refresh_pulse_infrastructure_status(status: dict, cycle_summary: dict | None) -> None:
    """Recompute infrastructure truth for the surfaces a cycle pulse refreshes.

    ``write_cycle_pulse`` intentionally avoids the full read model, so it must
    not carry stale full-status failures forward under a fresh timestamp.
    """

    status["learning"] = _pulse_only_summary("learning")
    status["calibration_serving"] = _pulse_only_summary("calibration_serving")
    status["lifecycle_funnel"] = _pulse_only_summary("lifecycle_funnel")
    status["price_evidence"] = _pulse_only_summary("price_evidence")
    status["no_trade"] = _pulse_only_summary("no_trade")

    cycle = cycle_summary if isinstance(cycle_summary, dict) else {}
    consistency_issues: list[str] = []
    if bool(cycle.get("failed", False)):
        consistency_issues.append("cycle_failed")

    current_open_entry_orders = (
        status.get("execution", {}).get("current_open_entry_orders")
        if isinstance(status.get("execution"), dict)
        else None
    )
    if isinstance(current_open_entry_orders, dict) and current_open_entry_orders.get("status") == "query_error":
        consistency_issues.append("current_open_entry_orders_query_error")

    # B6 (2026-06-20): armed-live + zero submit receipts → RED (dead-submit detection).
    # Detects the 2026-06-06 silent dead-submit mode: the entry gate is open AND
    # the cycle produced submit-admissible intents, but no SUBMIT_REQUESTED/
    # SUBMIT_ACKED event landed in venue_command_events in the last 30 minutes.
    # DETECTION ONLY — never blocks an order; no throttle; no gate object.
    _ARMED_LIVE_SUBMIT_RECEIPT_WINDOW_SECONDS = 1800  # 30 min
    try:
        _armed_live = _check_armed_live_no_submit_receipts(
            status=status,
            cycle=cycle,
            window_seconds=_ARMED_LIVE_SUBMIT_RECEIPT_WINDOW_SECONDS,
        )
        if _armed_live:
            consistency_issues.append("armed_live_no_recent_submit_receipts")
    except Exception as _exc:  # noqa: BLE001 - armed-live check must never break the pulse
        logger.warning("armed_live submit-receipt check failed (non-fatal): %s", _exc)

    risk = status.setdefault("risk", {})
    if not isinstance(risk, dict):
        risk = {}
        status["risk"] = risk
    try:
        risk["level"] = _get_risk_level()
        risk["riskguard_level"] = risk["level"]
        risk["details"] = _get_risk_details()
    except Exception as exc:  # noqa: BLE001 - status pulse must remain non-fatal
        risk["details"] = {
            "status": "riskguard_status_refresh_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    observed_risk_level = str(
        cycle.get("risk_level")
        or risk.get("level")
        or risk.get("riskguard_level")
        or ""
    )
    if observed_risk_level:
        risk.setdefault("level", observed_risk_level)
        risk.setdefault("riskguard_level", risk.get("level", observed_risk_level))
    risk["consistency_check"] = {
        "ok": not consistency_issues,
        "issues": consistency_issues,
        "cycle_risk_level": str(cycle.get("risk_level") or "") or None,
        "scope": "cycle_pulse",
    }
    risk["infrastructure_level"] = "RED" if consistency_issues else "GREEN"
    risk["infrastructure_issues"] = list(consistency_issues)
    risk["infrastructure_scope"] = "cycle_pulse"

    # AB3 (2026-06-16): lane-liveness surfacing. The cycle summary is reachable
    # HERE (the venue heartbeat-keeper process has no access to it), so this is
    # the minimal existing channel to surface lane health onto the pulse that
    # live_health.py reads. The check emits a logger.warning naming any
    # decision/telemetry lane that had write failures this cycle. OBSERVABILITY
    # ONLY — it does NOT feed consistency_issues / infrastructure_level (a dead
    # telemetry lane must not gate or block trading; operator law: no throttles).
    try:
        from src.control.heartbeat_supervisor import data_lane_health_check

        lane_verdict = data_lane_health_check(
            lane_write_failures=cycle.get("lane_write_failures"),
            decision_lane_writes=cycle.get("decision_lane_writes"),
            expected_lanes=None,  # quiet lanes are NOT assumed dead without an explicit expectation
        )
        status["data_lane_liveness"] = lane_verdict
    except Exception as exc:  # noqa: BLE001 - liveness surfacing must never break the pulse
        logger.warning("data lane liveness check failed (non-fatal): %s", exc)


def _refresh_minimal_runtime_read_model_for_status(status: dict) -> bool:
    """Refresh portfolio/runtime slices that make top-level freshness meaningful."""

    conn = None
    try:
        conn = get_trade_connection_with_world()
        position_view = query_position_current_status_view(conn)
    except Exception as exc:
        logger.warning(
            "status_summary: minimal runtime read-model pulse refresh failed: %s",
            exc,
            exc_info=True,
        )
        risk = status.setdefault("risk", {})
        if isinstance(risk, dict):
            risk["infrastructure_level"] = "RED"
            issues = risk.setdefault("infrastructure_issues", [])
            if isinstance(issues, list) and "position_current_pulse_query_error" not in issues:
                issues.append("position_current_pulse_query_error")
        status["portfolio"] = {
            "status": "query_error",
            "truth_authority": "UNVERIFIED",
            "refresh_error": "position_current_pulse_query_error",
            "open_positions": None,
            "total_exposure_usd": None,
            "unrealized_pnl": None,
            "positions": [],
            "heat_pct": None,
        }
        status["runtime"] = {
            "pulse_refreshed": False,
            "pulse_refresh_error": "position_current_pulse_query_error",
        }
        status["strategy"] = {
            "status": "query_error",
            "refresh_error": "position_current_pulse_query_error",
        }
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    portfolio = status.setdefault("portfolio", {})
    if not isinstance(portfolio, dict):
        portfolio = {}
        status["portfolio"] = portfolio
    portfolio["open_positions"] = int(position_view.get("open_positions", 0))
    portfolio["total_exposure_usd"] = round(
        float(position_view.get("total_exposure_usd", 0.0) or 0.0),
        2,
    )
    portfolio["unrealized_pnl"] = round(float(position_view.get("unrealized_pnl", 0.0) or 0.0), 2)
    portfolio["positions"] = list(position_view.get("positions", []))
    effective_bankroll = portfolio.get("effective_bankroll") or portfolio.get("bankroll")
    try:
        effective_bankroll_f = float(effective_bankroll)
    except (TypeError, ValueError):
        effective_bankroll_f = 0.0
    portfolio["heat_pct"] = (
        round((float(portfolio["total_exposure_usd"]) / effective_bankroll_f) * 100, 1)
        if effective_bankroll_f > 0
        else 0.0
    )
    status["runtime"] = {
        "chain_state_counts": dict(position_view.get("chain_state_counts", {})),
        "exit_state_counts": dict(position_view.get("exit_state_counts", {})),
        "unverified_entries": int(position_view.get("unverified_entries", 0)),
        "day0_positions": int(position_view.get("day0_positions", 0)),
        "pulse_refreshed": True,
    }
    prior_strategy = status.get("strategy") if isinstance(status.get("strategy"), dict) else {}
    strategy = {}
    status["strategy"] = strategy
    open_by_strategy: dict[str, dict[str, float]] = {}
    for position in position_view.get("positions", []):
        if not isinstance(position, dict):
            continue
        strategy_key = str(position.get("strategy") or "unclassified")
        bucket = open_by_strategy.setdefault(
            strategy_key,
            {"open_positions": 0.0, "open_exposure_usd": 0.0, "unrealized_pnl": 0.0},
        )
        bucket["open_positions"] += 1
        bucket["open_exposure_usd"] += float(
            position.get("effective_cost_basis_usd")
            if position.get("effective_cost_basis_usd") is not None
            else position.get("size_usd", 0.0)
            or 0.0
        )
        bucket["unrealized_pnl"] += float(position.get("unrealized_pnl", 0.0) or 0.0)
    for strategy_key, open_metrics in open_by_strategy.items():
        prior_bucket = prior_strategy.get(strategy_key) if isinstance(prior_strategy, dict) else {}
        realized_pnl = (
            float(prior_bucket.get("realized_pnl", 0.0) or 0.0)
            if isinstance(prior_bucket, dict)
            else 0.0
        )
        strategy[strategy_key] = {
            "open_positions": int(open_metrics["open_positions"]),
            "open_exposure_usd": round(open_metrics["open_exposure_usd"], 2),
            "unrealized_pnl": round(open_metrics["unrealized_pnl"], 2),
            "realized_pnl": round(realized_pnl, 2),
            "total_pnl": round(realized_pnl + round(open_metrics["unrealized_pnl"], 2), 2),
        }
    return True


def _refresh_current_open_entry_orders_for_status(status: dict) -> None:
    """Refresh the cheap order-truth slice when a pulse marks status fresh."""

    conn = None
    try:
        conn = get_trade_connection_with_world()
        current_open_entry_orders = _query_current_open_entry_orders(conn)
        terminal_command_venue_fact_conflicts = (
            _query_terminal_entry_command_venue_fact_conflicts(conn)
        )
    except Exception as exc:
        logger.warning(
            "status_summary: current_open_entry_orders pulse refresh failed: %s",
            exc,
            exc_info=True,
        )
        prior_execution = status.get("execution") if isinstance(status.get("execution"), dict) else {}
        prior_open_orders = (
            prior_execution.get("current_open_entry_orders")
            if isinstance(prior_execution.get("current_open_entry_orders"), dict)
            else _query_current_open_entry_orders(None)
        )
        current_open_entry_orders = {
            **prior_open_orders,
            "status": "query_error",
        }
        terminal_command_venue_fact_conflicts = _terminal_entry_command_conflict_empty()
        terminal_command_venue_fact_conflicts["status"] = "query_error"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    status["execution"] = {
        "pulse_only": True,
        "current_open_entry_orders": current_open_entry_orders,
        "terminal_command_venue_fact_conflicts": terminal_command_venue_fact_conflicts,
    }


def _enum_text(value, default: str) -> str:
    if value in (None, ""):
        return default
    return str(getattr(value, "value", value))


def _round_money_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_status_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status_time_not_before(left, right) -> bool:
    left_dt = _parse_status_time(left)
    right_dt = _parse_status_time(right)
    if left_dt is None or right_dt is None:
        return False
    return left_dt >= right_dt


def _payload_mapping(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cancel_ack_payload_confirms_terminal_cancel(payload, *, venue_order_id: str) -> bool:
    payload_map = _payload_mapping(payload)
    payload_order_id = str(payload_map.get("venue_order_id") or "")
    if payload_order_id and payload_order_id != str(venue_order_id or ""):
        return False
    cancel_outcome = payload_map.get("cancel_outcome")
    cancel_outcome = cancel_outcome if isinstance(cancel_outcome, dict) else {}
    status = str(cancel_outcome.get("status") or "").strip().upper()
    return status in {"CANCELED", "CANCELLED", "CANCEL_CONFIRMED"}


def _terminal_event_supersedes_nonterminal_fact(row) -> bool:
    event_type = str(row["terminal_event_type"] or "").upper()
    if event_type not in _TERMINAL_COMMAND_EVENT_TYPES:
        return False
    if not _status_time_not_before(row["terminal_event_at"], row["venue_observed_at"]):
        return False
    if event_type == "CANCEL_ACKED":
        return _cancel_ack_payload_confirms_terminal_cancel(
            row["terminal_event_payload_json"],
            venue_order_id=str(row["venue_order_id"] or ""),
        )
    return True


def _get_risk_level() -> str:
    """Read actual RiskGuard level instead of hardcoding GREEN."""
    try:
        from src.riskguard.riskguard import get_current_level
        return get_current_level().value
    except Exception:
        return "UNKNOWN"


def _get_risk_details() -> dict:
    try:
        import os
        import sqlite3

        # CATEGORY ANTIBODY (Fitz #5): this risk-block read path used a bare
        # sqlite3.connect() with no timeout, giving it the bare-connect default
        # wait budget (0 ms on builds where it is unset) — a guaranteed lock-loser
        # on the read that surfaces the risk block. Route it through the SAME
        # configured busy_timeout (ZEUS_DB_BUSY_TIMEOUT_MS, default 30 s) as the
        # canonical factory so a transient WAL writer makes this read WAIT, not
        # raise. Read-only single-DB query — no INV-37 / txn-semantic change.
        _busy_ms = int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
        conn = sqlite3.connect(
            str(state_path("risk_state.db")), timeout=_busy_ms / 1000.0
        )
        conn.execute("PRAGMA busy_timeout = %d" % _busy_ms)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT details_json FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None or not row["details_json"]:
            return {}
        details = json.loads(row["details_json"])
        return details if isinstance(details, dict) else {}
    except Exception:
        return {}


_V2_TABLES = (
    "platt_models",
    "calibration_pairs",
    "ensemble_snapshots",
    "historical_forecasts",
    "settlement_outcomes",
)

# Canonical schema preference lives in v2_table_schema_preference.py so this
# module and calibration_serving_status share a single source of truth (PR
# #210 review: drifting copies caused the 2026-05-19 false-BLOCKED outage).
from src.observability.v2_table_schema_preference import (
    V2_TABLE_SCHEMA_PREFERENCE as _V2_ROW_COUNT_SCHEMA_PREFERENCE,
)


def _quote_sql_identifier(identifier: str) -> str:
    text = str(identifier or "")
    if not text or text[0].isdigit() or not text.replace("_", "").isalnum():
        raise ValueError(f"unsafe sqlite identifier: {identifier!r}")
    return f'"{text}"'


def _attached_schema_names(conn) -> set[str]:
    try:
        return {
            str(row[1])
            for row in conn.execute("PRAGMA database_list").fetchall()
            if len(row) > 1 and row[1]
        }
    except Exception:
        return {"main"}


def _table_exists(conn, schema: str, table: str) -> bool:
    try:
        schema_sql = _quote_sql_identifier(schema)
        row = conn.execute(
            f"SELECT 1 FROM {schema_sql}.sqlite_master "
            "WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _query_current_open_entry_orders(conn) -> dict:
    """Derived operator view of currently open entry orders in DB truth."""

    empty = {
        "status": "skipped_no_connection",
        "authority": "derived_operator_visibility",
        "source": "venue_commands+position_current+venue_order_facts+venue_command_events",
        "count": 0,
        "pending_entry_count": 0,
        "by_strategy": {},
        "orders": [],
    }
    if conn is None:
        return empty
    if not _table_exists(conn, "main", "venue_commands"):
        return {**empty, "status": "missing_venue_commands"}

    has_position_current = _table_exists(conn, "main", "position_current")
    has_order_facts = _table_exists(conn, "main", "venue_order_facts")
    pc_select = (
        "pc.position_id, pc.phase, pc.order_status, pc.city, pc.target_date, pc.strategy_key"
        if has_position_current
        else (
            "vc.position_id, NULL AS phase, NULL AS order_status, NULL AS city, "
            "NULL AS target_date, NULL AS strategy_key"
        )
    )
    pc_join = "LEFT JOIN position_current pc ON pc.position_id = vc.position_id" if has_position_current else ""
    if has_order_facts:
        fact_select = (
            "lof.state AS venue_state, lof.remaining_size, lof.matched_size, "
            "lof.observed_at AS venue_observed_at"
        )
        fact_join = """
            LEFT JOIN venue_order_facts lof
              ON lof.fact_id = (
                    SELECT latest.fact_id
                      FROM venue_order_facts latest
                     WHERE latest.venue_order_id = vc.venue_order_id
                     ORDER BY latest.local_sequence DESC,
                              latest.observed_at DESC,
                              latest.ingested_at DESC,
                              latest.fact_id DESC
                     LIMIT 1
                 )
        """
    else:
        fact_select = (
            "NULL AS venue_state, NULL AS remaining_size, NULL AS matched_size, "
            "NULL AS venue_observed_at"
        )
        fact_join = ""

    rows = conn.execute(
        f"""
        SELECT vc.command_id, vc.venue_order_id, vc.state AS command_state,
               vc.side, vc.size AS submitted_size, vc.price AS submitted_price,
               vc.updated_at, {pc_select}, {fact_select}
          FROM venue_commands vc
          {pc_join}
          {fact_join}
         WHERE upper(COALESCE(vc.intent_kind, '')) = 'ENTRY'
           AND vc.venue_order_id IS NOT NULL
           AND trim(vc.venue_order_id) != ''
           AND upper(COALESCE(vc.state, '')) IN ({",".join("?" for _ in _OPEN_ENTRY_COMMAND_STATES)})
         ORDER BY vc.updated_at DESC, vc.created_at DESC, vc.command_id
        """,
        tuple(sorted(_OPEN_ENTRY_COMMAND_STATES)),
    ).fetchall()

    orders: list[dict] = []
    by_strategy: dict[str, int] = {}
    pending_entry_count = 0
    for row in rows:
        phase = str(row["phase"] or "")
        order_status = str(row["order_status"] or "")
        venue_state = str(row["venue_state"] or "")
        if phase.lower() in _TERMINAL_ENTRY_PHASES:
            continue
        if order_status.lower() in _TERMINAL_ENTRY_ORDER_STATUSES:
            continue
        if venue_state.upper() in _TERMINAL_VENUE_ORDER_STATES:
            continue
        strategy_key = str(row["strategy_key"] or "unclassified")
        by_strategy[strategy_key] = by_strategy.get(strategy_key, 0) + 1
        if phase == "pending_entry":
            pending_entry_count += 1
        orders.append(
            {
                "command_id": str(row["command_id"] or ""),
                "venue_order_id": str(row["venue_order_id"] or ""),
                "position_id": str(row["position_id"] or ""),
                "city": str(row["city"] or ""),
                "target_date": str(row["target_date"] or ""),
                "strategy_key": strategy_key,
                "phase": phase,
                "order_status": order_status,
                "command_state": str(row["command_state"] or ""),
                "venue_state": venue_state or "UNKNOWN",
                "side": str(row["side"] or ""),
                "submitted_price": _float_or_none(row["submitted_price"]),
                "submitted_size": _float_or_none(row["submitted_size"]),
                "remaining_size": _float_or_none(row["remaining_size"]),
                "matched_size": _float_or_none(row["matched_size"]),
                "updated_at": str(row["updated_at"] or ""),
                "venue_observed_at": str(row["venue_observed_at"] or ""),
            }
        )

    return {
        **empty,
        "status": "ok",
        "count": len(orders),
        "pending_entry_count": pending_entry_count,
        "by_strategy": by_strategy,
        "orders": orders,
    }


def _terminal_entry_command_conflict_empty() -> dict:
    return {
        "status": "skipped_no_connection",
        "authority": "derived_operator_visibility",
        "source": "venue_commands+position_current+venue_order_facts+venue_command_events",
        "description": (
            "entry commands locally terminal while latest stored venue fact "
            "is still nonterminal"
        ),
        "count": 0,
        "superseded_by_terminal_event_count": 0,
        "by_command_state": {},
        "by_venue_state": {},
        "by_position_phase": {},
        "orders": [],
    }


def _query_terminal_entry_command_venue_fact_conflicts(conn) -> dict:
    """Surface local-terminal entry commands lacking terminal venue-fact proof."""

    empty = _terminal_entry_command_conflict_empty()
    if conn is None:
        return empty
    if not _table_exists(conn, "main", "venue_commands"):
        return {**empty, "status": "missing_venue_commands"}
    if not _table_exists(conn, "main", "venue_order_facts"):
        return {**empty, "status": "missing_venue_order_facts"}

    has_position_current = _table_exists(conn, "main", "position_current")
    has_command_events = _table_exists(conn, "main", "venue_command_events")
    pc_select = (
        "pc.position_id, pc.phase, pc.order_status, pc.chain_state, pc.city, "
        "pc.target_date, pc.strategy_key"
        if has_position_current
        else (
            "vc.position_id, NULL AS phase, NULL AS order_status, NULL AS chain_state, "
            "NULL AS city, NULL AS target_date, NULL AS strategy_key"
        )
    )
    pc_join = "LEFT JOIN position_current pc ON pc.position_id = vc.position_id" if has_position_current else ""
    event_select = (
        "NULL AS terminal_event_type, NULL AS terminal_event_at, "
        "NULL AS terminal_event_state_after, NULL AS terminal_event_payload_json"
    )
    event_join = ""
    event_params: tuple[str, ...] = ()
    if has_command_events:
        event_select = (
            "lte.event_type AS terminal_event_type, "
            "lte.occurred_at AS terminal_event_at, "
            "lte.state_after AS terminal_event_state_after, "
            "lte.payload_json AS terminal_event_payload_json"
        )
        event_join = """
            LEFT JOIN venue_command_events lte
              ON lte.event_id = (
                    SELECT latest.event_id
                      FROM venue_command_events latest
                     WHERE latest.command_id = vc.command_id
                       AND latest.event_type IN ({terminal_event_placeholders})
                     ORDER BY latest.sequence_no DESC,
                              latest.occurred_at DESC,
                              latest.event_id DESC
                     LIMIT 1
                 )
        """.format(
            terminal_event_placeholders=",".join("?" for _ in _TERMINAL_COMMAND_EVENT_TYPES)
        )
        event_params = tuple(sorted(_TERMINAL_COMMAND_EVENT_TYPES))

    rows = conn.execute(
        f"""
        SELECT vc.command_id, vc.venue_order_id, vc.state AS command_state,
               vc.side, vc.size AS submitted_size, vc.price AS submitted_price,
               vc.updated_at, {pc_select},
               lof.state AS venue_state, lof.remaining_size, lof.matched_size,
               lof.observed_at AS venue_observed_at,
               {event_select}
          FROM venue_commands vc
          JOIN venue_order_facts lof
            ON lof.fact_id = (
                  SELECT latest.fact_id
                    FROM venue_order_facts latest
                   WHERE latest.venue_order_id = vc.venue_order_id
                   ORDER BY latest.local_sequence DESC,
                            latest.observed_at DESC,
                            latest.ingested_at DESC,
                            latest.fact_id DESC
                   LIMIT 1
               )
          {event_join}
          {pc_join}
         WHERE upper(COALESCE(vc.intent_kind, '')) = 'ENTRY'
           AND vc.venue_order_id IS NOT NULL
           AND trim(vc.venue_order_id) != ''
           AND upper(COALESCE(vc.state, '')) IN ({",".join("?" for _ in _TERMINAL_ENTRY_COMMAND_STATES)})
           AND upper(COALESCE(lof.state, '')) IN ({",".join("?" for _ in _NONTERMINAL_VENUE_ORDER_STATES)})
         ORDER BY vc.updated_at DESC, vc.created_at DESC, vc.command_id
        """,
        event_params
        + tuple(sorted(_TERMINAL_ENTRY_COMMAND_STATES))
        + tuple(sorted(_NONTERMINAL_VENUE_ORDER_STATES)),
    ).fetchall()

    by_command_state: dict[str, int] = {}
    by_venue_state: dict[str, int] = {}
    by_position_phase: dict[str, int] = {}
    superseded_by_terminal_event_count = 0
    orders: list[dict] = []
    for row in rows:
        if _terminal_event_supersedes_nonterminal_fact(row):
            superseded_by_terminal_event_count += 1
            continue
        command_state = str(row["command_state"] or "UNKNOWN").upper()
        venue_state = str(row["venue_state"] or "UNKNOWN").upper()
        phase = str(row["phase"] or "unknown")
        by_command_state[command_state] = by_command_state.get(command_state, 0) + 1
        by_venue_state[venue_state] = by_venue_state.get(venue_state, 0) + 1
        by_position_phase[phase] = by_position_phase.get(phase, 0) + 1
        orders.append(
            {
                "command_id": str(row["command_id"] or ""),
                "venue_order_id": str(row["venue_order_id"] or ""),
                "position_id": str(row["position_id"] or ""),
                "city": str(row["city"] or ""),
                "target_date": str(row["target_date"] or ""),
                "strategy_key": str(row["strategy_key"] or "unclassified"),
                "phase": phase,
                "order_status": str(row["order_status"] or ""),
                "chain_state": str(row["chain_state"] or ""),
                "command_state": command_state,
                "venue_state": venue_state,
                "side": str(row["side"] or ""),
                "submitted_price": _float_or_none(row["submitted_price"]),
                "submitted_size": _float_or_none(row["submitted_size"]),
                "remaining_size": _float_or_none(row["remaining_size"]),
                "matched_size": _float_or_none(row["matched_size"]),
                "updated_at": str(row["updated_at"] or ""),
                "venue_observed_at": str(row["venue_observed_at"] or ""),
                "terminal_event_type": str(row["terminal_event_type"] or ""),
                "terminal_event_at": str(row["terminal_event_at"] or ""),
                "terminal_event_state_after": str(row["terminal_event_state_after"] or ""),
            }
        )

    return {
        **empty,
        "status": "ok",
        "count": len(orders),
        "superseded_by_terminal_event_count": superseded_by_terminal_event_count,
        "by_command_state": by_command_state,
        "by_venue_state": by_venue_state,
        "by_position_phase": by_position_phase,
        "orders": orders,
    }


def _bounded_table_row_count(conn, schema: str, table: str) -> int:
    """Return a cheap row-count signal without full-table COUNT(*) scans.

    These values are derived operator telemetry. For normal rowid tables,
    MAX(rowid) is an O(log n) high-water count signal and avoids scanning
    large world tables every cycle. If a future table has no rowid, fall back
    to a bounded non-empty sentinel rather than blocking status writes.
    """
    schema_sql = _quote_sql_identifier(schema)
    table_sql = _quote_sql_identifier(table)
    try:
        row = conn.execute(f"SELECT MAX(rowid) FROM {schema_sql}.{table_sql}").fetchone()
        return max(0, int(row[0])) if row and row[0] is not None else 0
    except Exception:
        row = conn.execute(f"SELECT 1 FROM {schema_sql}.{table_sql} LIMIT 1").fetchone()
        return 1 if row else 0


def _float_or_zero(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _legacy_position_state(row: dict) -> str:
    return str(row.get("phase") or row.get("state") or "").strip().lower()


def _is_nonterminal_legacy_position(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if not (row.get("trade_id") or row.get("market_id") or row.get("condition_id")):
        return False
    state = _legacy_position_state(row)
    return state not in _LEGACY_JSON_INACTIVE_POSITION_STATES


def _legacy_positions_artifact_summary(position_view: dict) -> dict:
    """Summarize legacy positions.json without promoting it to portfolio truth."""
    path = LEGACY_POSITIONS_PATH
    summary = {
        "path": str(path),
        "exists": path.exists(),
        "authority": "legacy_json_derived_observability_only",
        "canonical_truth_source": "position_current",
        "canonical_db_status": str(position_view.get("status") or "unknown"),
        "canonical_db_open_positions": int(position_view.get("open_positions", 0) or 0),
        "status": "missing",
        "active_positions": 0,
        "active_cost_basis_usd": 0.0,
        "conflicts": [],
        "sample_positions": [],
    }
    if not path.exists():
        return summary

    try:
        stat = path.stat()
        summary["mtime"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        data, truth = read_truth_json(path)
    except Exception as exc:
        summary["status"] = "unreadable"
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        return summary

    summary["deprecated"] = bool(truth.get("deprecated", False))
    summary["generated_at"] = truth.get("generated_at")
    summary["stale_age_seconds"] = truth.get("stale_age_seconds")
    positions = data.get("positions", []) if isinstance(data, dict) else []
    if not isinstance(positions, list):
        summary["status"] = "malformed"
        summary["error"] = "positions is not a list"
        return summary

    active_positions = [
        row for row in positions
        if _is_nonterminal_legacy_position(row)
    ]
    summary["active_positions"] = len(active_positions)
    summary["active_cost_basis_usd"] = round(
        sum(_float_or_zero(row.get("cost_basis_usd") or row.get("size_usd")) for row in active_positions),
        4,
    )
    target_dates = sorted({
        str(row.get("target_date"))
        for row in active_positions
        if row.get("target_date")
    })
    if target_dates:
        summary["target_dates"] = target_dates
        summary["oldest_target_date"] = target_dates[0]
    chain_state_counts: dict[str, int] = {}
    for row in active_positions:
        chain_state = str(row.get("chain_state") or "unknown")
        chain_state_counts[chain_state] = chain_state_counts.get(chain_state, 0) + 1
    if chain_state_counts:
        summary["chain_state_counts"] = chain_state_counts
    summary["sample_positions"] = [
        {
            "trade_id": row.get("trade_id"),
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "bin_label": row.get("bin_label"),
            "direction": row.get("direction"),
            "state": _legacy_position_state(row) or None,
            "strategy_key": row.get("strategy_key") or row.get("strategy"),
            "chain_state": row.get("chain_state"),
            "cost_basis_usd": round(_float_or_zero(row.get("cost_basis_usd") or row.get("size_usd")), 4),
        }
        for row in active_positions[:10]
    ]
    if active_positions:
        summary["status"] = "active_legacy_positions"
    else:
        summary["status"] = "empty"

    canonical_status = str(position_view.get("status") or "").strip().lower()
    canonical_open = int(position_view.get("open_positions", 0) or 0)
    if canonical_status in {"ok", "empty"} and canonical_open == 0 and active_positions:
        summary["conflicts"].append("canonical_empty_legacy_active_positions")
        summary["status"] = "conflict"
    if summary.get("deprecated") is True and active_positions:
        summary["conflicts"].append("deprecated_legacy_file_has_active_positions")
    return summary


def _get_row_counts(conn) -> dict[str, int]:
    """Query row counts for the 5 v2 tables.

    S5 R11 P10B: meta-immune-system sensor. Returns 0 for tables that don't
    exist yet (Golden Window: v2 tables are empty until data lift). Missing
    table → 0, not an error, so this never blocks status writes.
    """
    counts: dict[str, int] = {}
    attached_schemas = _attached_schema_names(conn)
    for table in _V2_TABLES:
        counts[table] = 0
        for schema in _V2_ROW_COUNT_SCHEMA_PREFERENCE.get(table, ("main",)):
            if schema not in attached_schemas or not _table_exists(conn, schema, table):
                continue
            try:
                counts[table] = _bounded_table_row_count(conn, schema, table)
            except Exception:
                counts[table] = 0
            break
    return counts


def _capability_component(
    component: str,
    *,
    allowed: bool | None,
    reason: str,
    details: dict | None = None,
) -> dict:
    return {
        "component": component,
        "allowed": allowed,
        "reason": reason,
        "details": dict(details or {}),
    }


def _safe_component(component: str, loader) -> dict:
    try:
        return loader()
    except Exception as exc:
        return _capability_component(
            component,
            allowed=False,
            reason="summary_unavailable",
            details={
                "error_type": type(exc).__name__,
                "error": str(exc),
                # Sentinel fields so downstream component builders (e.g. _collateral_component)
                # can distinguish "loader failed / DB locked" from "genuinely unconfigured".
                "configured": False,
                "authority_tier": "DEGRADED",
                "loader_failed": True,
            },
        )


def _propagate_loader_failure(payload: dict, details: dict) -> dict:
    """Copy _safe_component sentinels into a component's details dict.

    When _safe_component caught a loader exception, the payload it returned
    carries loader_failed/error_type/error sentinels. Component builders
    that construct their own details dict would otherwise drop these,
    making loader failures invisible to operators.
    """
    if isinstance(payload, dict) and payload.get("loader_failed"):
        details["loader_failed"] = True
        if "error_type" in payload:
            details["error_type"] = payload["error_type"]
        if "error" in payload:
            details["error"] = payload["error"]
    return details


def _cutover_summary() -> dict:
    from src.control.cutover_guard import summary

    return summary()


def _heartbeat_summary() -> dict:
    from src.control.heartbeat_supervisor import summary

    return summary()


def _ws_gap_summary() -> dict:
    from src.control.ws_gap_guard import summary

    return summary()


def _risk_allocator_summary() -> dict:
    from src.risk_allocator import summary

    return summary()


def _collateral_summary() -> dict:
    from src.state.collateral_ledger import get_global_ledger

    ledger = get_global_ledger()
    if ledger is None:
        return {
            "configured": False,
            "authority_tier": "UNCONFIGURED",
            "reason": "collateral_ledger_unconfigured",
        }
    snapshot = ledger.snapshot()
    payload = snapshot.to_dict()
    payload["configured"] = True
    payload["reason"] = "ok" if payload.get("authority_tier") != "DEGRADED" else "collateral_snapshot_degraded"
    return payload


def _cutover_component(action: str, payload: dict) -> dict:
    key = "redemption" if action == "redeem" else action
    decision = payload.get(key, {}) if isinstance(payload, dict) else {}
    allow_key = {
        "entry": "allow_submit",
        "exit": "allow_submit",
        "cancel": "allow_cancel",
        "redeem": "allow_redemption",
    }[action]
    allowed = bool(decision.get(allow_key, False))
    details = {
        "state": payload.get("state"),
        "allow_key": allow_key,
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "cutover_guard",
        allowed=allowed,
        reason=str(decision.get("block_reason") or ("allowed" if allowed else "blocked")),
        details=details,
    )


def _heartbeat_component(payload: dict, *, order_type: str = "GTC") -> dict:
    entry = payload.get("entry", {}) if isinstance(payload, dict) else {}
    allowed = bool(entry.get("allow_submit", False))
    details = {
        "health": payload.get("health"),
        "order_type": order_type,
        "required_order_types": list(entry.get("required_order_types", []) or []),
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "heartbeat_supervisor",
        allowed=allowed,
        reason="allowed" if allowed else str(payload.get("last_error") or payload.get("health") or "blocked"),
        details=details,
    )


def _ws_gap_component(payload: dict, *, current_executor_blocks_exit: bool = False) -> dict:
    entry = payload.get("entry", {}) if isinstance(payload, dict) else {}
    allowed = bool(entry.get("allow_submit", False))
    details = {
        "connected": payload.get("connected"),
        "subscription_state": payload.get("subscription_state"),
        "m5_reconcile_required": payload.get("m5_reconcile_required"),
        "current_executor_blocks_exit": current_executor_blocks_exit,
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "ws_gap_guard",
        allowed=allowed,
        reason="allowed" if allowed else str(payload.get("gap_reason") or payload.get("subscription_state") or "blocked"),
        details=details,
    )


def _risk_allocator_entry_component(payload: dict) -> dict:
    entry = payload.get("entry", {}) if isinstance(payload, dict) else {}
    allowed = bool(entry.get("allow_submit", False))
    details = {
        "configured": bool(payload.get("configured", False)),
        "kill_switch_reason": payload.get("kill_switch_reason"),
        "reduce_only": bool(payload.get("reduce_only", False)),
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "risk_allocator_global",
        allowed=allowed,
        reason=str(entry.get("reason") or ("allowed" if allowed else "blocked")),
        details=details,
    )


def _risk_allocator_reduce_only_component(payload: dict) -> dict:
    configured = bool(payload.get("configured", False)) if isinstance(payload, dict) else False
    kill_reason = payload.get("kill_switch_reason") if isinstance(payload, dict) else "summary_unavailable"
    allowed = configured and not kill_reason
    details = {
        "configured": configured,
        "reduce_only_submit": True,
        "reduce_only_mode": bool(payload.get("reduce_only", False)) if isinstance(payload, dict) else False,
    }
    _propagate_loader_failure(payload, details)
    return _capability_component(
        "risk_allocator_global",
        allowed=allowed,
        reason=str(kill_reason or "allowed"),
        details=details,
    )


def _collateral_component(payload: dict, *, collateral: str) -> dict:
    configured = bool(payload.get("configured", False)) if isinstance(payload, dict) else False
    authority_tier = str(payload.get("authority_tier") or "UNKNOWN")
    allowed = configured and authority_tier != "DEGRADED"
    details: dict = {
        "collateral": collateral,
        "configured": configured,
        "authority_tier": authority_tier,
        "captured_at": payload.get("captured_at"),
    }
    # Propagate loader-failure fields set by _safe_component so operators can
    # distinguish "DB locked at load time" from "genuinely unconfigured".
    if isinstance(payload, dict) and payload.get("loader_failed"):
        details["loader_failed"] = True
        if "error_type" in payload:
            details["error_type"] = payload["error_type"]
        if "error" in payload:
            details["error"] = payload["error"]
    return _capability_component(
        "collateral_ledger_global",
        allowed=allowed,
        reason=str(payload.get("reason") or ("allowed" if allowed else "blocked")),
        details=details,
    )


def _requires_intent_component(component: str, reason: str, *, details: dict | None = None) -> dict:
    return _capability_component(component, allowed=None, reason=reason, details=details)


def _action_capability(
    action: str,
    *,
    gate_key: str,
    components: list[dict],
    required_intent_components: list[dict] | None = None,
) -> dict:
    unresolved = list(required_intent_components or [])
    unavailable = [c for c in components if c.get("allowed") is False]
    status = "unavailable" if unavailable else ("requires_intent" if unresolved else "ready")
    return {
        "action": action,
        "status": status,
        gate_key: not unavailable,
        "live_action_authorized": False,
        "authority": "derived_operator_visibility",
        "components": components,
        "required_intent_components": unresolved,
        "unavailable_components": [str(c.get("component")) for c in unavailable],
    }


def _get_execution_capability_status() -> dict:
    """Derived operator matrix for execution gates.

    This is not a decision surface. It summarizes global gate readiness and
    explicitly leaves per-intent facts (snapshot freshness, exact notional,
    token inventory, replacement-sell policy) unresolved.
    """

    cutover = _safe_component(
        "cutover_guard",
        lambda: _capability_component("cutover_guard_summary", allowed=True, reason="loaded", details=_cutover_summary()),
    )["details"]
    heartbeat = _safe_component(
        "heartbeat_supervisor",
        lambda: _capability_component("heartbeat_summary", allowed=True, reason="loaded", details=_heartbeat_summary()),
    )["details"]
    ws_gap = _safe_component(
        "ws_gap_guard",
        lambda: _capability_component("ws_gap_summary", allowed=True, reason="loaded", details=_ws_gap_summary()),
    )["details"]
    risk = _safe_component(
        "risk_allocator",
        lambda: _capability_component("risk_allocator_summary", allowed=True, reason="loaded", details=_risk_allocator_summary()),
    )["details"]
    collateral = _safe_component(
        "collateral_ledger",
        lambda: _capability_component("collateral_summary", allowed=True, reason="loaded", details=_collateral_summary()),
    )["details"]

    entry = _action_capability(
        "entry",
        gate_key="global_allow_submit",
        components=[
            _cutover_component("entry", cutover),
            _heartbeat_component(heartbeat),
            _ws_gap_component(ws_gap),
            _risk_allocator_entry_component(risk),
            _collateral_component(collateral, collateral="pUSD"),
        ],
        required_intent_components=[
            _requires_intent_component("risk_allocator_capacity", "requires_market_notional_and_family"),
            _requires_intent_component("collateral_buy_amount", "requires_order_size_and_limit_price"),
            _requires_intent_component("executable_snapshot_gate", "requires_snapshot_id_price_size_and_token"),
        ],
    )
    exit_ = _action_capability(
        "exit",
        gate_key="global_allow_submit",
        components=[
            _cutover_component("exit", cutover),
            _heartbeat_component(heartbeat),
            _ws_gap_component(ws_gap, current_executor_blocks_exit=True),
            _risk_allocator_reduce_only_component(risk),
            _collateral_component(collateral, collateral="CTF"),
        ],
        required_intent_components=[
            _requires_intent_component("collateral_sell_inventory", "requires_token_id_and_shares"),
            _requires_intent_component("replacement_sell_guard", "requires_current_order_and_replace_context"),
            _requires_intent_component("executable_snapshot_gate", "requires_snapshot_id_price_size_and_token"),
        ],
    )
    cancel = _action_capability(
        "cancel",
        gate_key="global_allow_cancel",
        components=[_cutover_component("cancel", cutover)],
        required_intent_components=[
            _requires_intent_component("cancel_command_identity", "requires_command_id_and_venue_order_id"),
            _requires_intent_component("venue_order_cancelability", "requires_current_order_state"),
        ],
    )
    redeem = _action_capability(
        "redeem",
        gate_key="global_allow_redeem",
        components=[_cutover_component("redeem", cutover)],
        required_intent_components=[
            _requires_intent_component("payout_asset_fx_classification", "requires_redeem_command_payout_asset"),
        ],
    )
    return {
        "schema_version": 1,
        "authority": "derived_operator_visibility",
        "derived_only": True,
        "live_action_authorized": False,
        "entry": entry,
        "exit": exit_,
        "cancel": cancel,
        "redeem": redeem,
    }


def write_status(cycle_summary: dict = None) -> None:
    """Write 5-section health snapshot."""
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        refresh_control_state()
    except Exception as exc:  # noqa: BLE001 - status write must surface, not crash
        logger.error("control_state_refresh_failed_before_status_write: %s", exc, exc_info=True)
    risk_details = _get_risk_details()
    riskguard_level = _get_risk_level()
    cycle_summary_from_prior = cycle_summary is None
    if cycle_summary is None and STATUS_PATH.exists():
        try:
            with open(STATUS_PATH) as f:
                prior = json.load(f)
            cycle_summary = prior.get("cycle", {})
        except Exception:
            cycle_summary = {}
    recommended_strategy_gates = set(risk_details.get("recommended_strategy_gates", []) or [])
    recommended_strategy_gate_reasons = {
        str(strategy): list(reasons)
        for strategy, reasons in (risk_details.get("recommended_strategy_gate_reasons", {}) or {}).items()
        if isinstance(reasons, list)
    }
    current_entries_paused = is_entries_paused()
    current_entries_pause_reason = get_entries_pause_reason()
    current_entries_pause_evidence = get_entries_pause_evidence()
    if cycle_summary_from_prior:
        cycle_summary = dict(cycle_summary or {})
        if current_entries_paused:
            cycle_summary["entries_paused"] = True
            cycle_summary["entries_pause_reason"] = current_entries_pause_reason
            cycle_summary["entries_pause_evidence"] = current_entries_pause_evidence
            cycle_summary["entries_blocked_reason"] = "entries_paused"
        else:
            cycle_summary.pop("entries_paused", None)
            cycle_summary.pop("entries_pause_reason", None)
            cycle_summary.pop("entries_pause_evidence", None)
            if cycle_summary.get("entries_blocked_reason") == "entries_paused":
                cycle_summary.pop("entries_blocked_reason", None)
    current_strategy_gates = strategy_gates()
    recommended_but_not_gated = sorted(
        strategy for strategy in recommended_strategy_gates
        if not (d := current_strategy_gates.get(strategy)) or d.enabled
    )
    gated_but_not_recommended = sorted(
        strategy for strategy, decision in current_strategy_gates.items()
        if not decision.enabled and strategy not in recommended_strategy_gates
    )
    review_required_gate_recommendations = [
        {
            "command": "set_strategy_gate",
            "strategy": strategy,
            "enabled": True,
            "note": f"recommended_by=reason_refuted:{decision.reason_code.value}",
        }
        for strategy, decision in current_strategy_gates.items()
        if not decision.enabled and reason_refuted(decision, current_data={})
    ]
    recommended_controls = list(risk_details.get("recommended_controls", []))
    recommended_control_reasons = {
        str(control): list(reasons)
        for control, reasons in (risk_details.get("recommended_control_reasons", {}) or {}).items()
        if isinstance(reasons, list)
    }
    recommended_controls_not_applied: list[str] = []
    if "tighten_risk" in recommended_controls and get_edge_threshold_multiplier() <= 1.0:
        recommended_controls_not_applied.append("tighten_risk")
    if "review_strategy_gates" in recommended_controls and recommended_but_not_gated:
        recommended_controls_not_applied.append("review_strategy_gates")
    conn = None
    current_open_entry_orders = _query_current_open_entry_orders(None)
    terminal_command_venue_fact_conflicts = _terminal_entry_command_conflict_empty()
    try:
        conn = get_trade_connection_with_world()
        position_view = query_position_current_status_view(conn)
        strategy_health = query_strategy_health_snapshot(conn, now=generated_at)
        current_open_entry_orders = _query_current_open_entry_orders(conn)
        terminal_command_venue_fact_conflicts = (
            _query_terminal_entry_command_venue_fact_conflicts(conn)
        )
    except Exception:
        position_view = {
            "status": "query_error",
            "positions": [],
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "strategy_open_counts": {},
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        }
        strategy_health = {
            "status": "query_error",
            "by_strategy": {},
            "stale_strategy_keys": [],
        }
        current_open_entry_orders = {
            **current_open_entry_orders,
            "status": "query_error",
        }
        terminal_command_venue_fact_conflicts = {
            **terminal_command_venue_fact_conflicts,
            "status": "query_error",
        }

    # S5 R11 P10B: v2 row-count sensor
    v2_row_counts: dict[str, int] = {}
    try:
        if conn is not None:
            v2_row_counts = _get_row_counts(conn)
    except Exception:
        pass

    strategy_summary: dict[str, dict] = {}
    strategy_open_counts = position_view.get("strategy_open_counts", {})
    for name, row in (strategy_health.get("by_strategy", {}) or {}).items():
        bucket = strategy_summary.setdefault(
            name,
            {
                "open_positions": int(strategy_open_counts.get(name, 0)),
                "open_exposure_usd": round(float(row.get("open_exposure_usd") or 0.0), 2),
                "realized_pnl": round(float(row.get("realized_pnl_30d") or 0.0), 2),
                "unrealized_pnl": round(float(row.get("unrealized_pnl") or 0.0), 2),
            },
        )
        bucket["total_pnl"] = round(bucket["realized_pnl"] + bucket["unrealized_pnl"], 2)
        bucket["settlement_count"] = int(row.get("settled_trades_30d") or 0)
        bucket["settlement_pnl"] = round(float(row.get("realized_pnl_30d") or 0.0), 2)
        bucket["settlement_accuracy"] = row.get("win_rate_30d")
        bucket["settlement_source"] = "strategy_health"
        bucket["settlement_window"] = "30d"
        bucket["fill_rate_14d"] = row.get("fill_rate_14d")
        bucket["execution_decay_flag"] = bool(row.get("execution_decay_flag", 0))
        bucket["edge_compression_flag"] = bool(row.get("edge_compression_flag", 0))
    for name, open_count in strategy_open_counts.items():
        bucket = strategy_summary.setdefault(
            name,
            {
                "open_positions": int(open_count),
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "settlement_count": 0,
                "settlement_pnl": 0.0,
                "settlement_accuracy": None,
                "settlement_source": "strategy_health",
                "settlement_window": "30d",
            },
        )
        bucket["open_positions"] = int(open_count)
    for name, bucket in strategy_summary.items():
        _gate = current_strategy_gates.get(name)
        bucket["gated"] = _gate is not None and not _gate.enabled
        bucket["recommended_gate"] = name in recommended_strategy_gates
        bucket["recommended_gate_reasons"] = list(recommended_strategy_gate_reasons.get(name, []))

    status = {
        "timestamp": generated_at,
        "generated_at": generated_at,
        "process": {
            "pid": os.getpid(),
            "mode": get_mode(),
            "version": "zeus_v2",
        },
        "control": {
            "entries_paused": current_entries_paused,
            "entries_pause_source": get_entries_pause_source(),
            "entries_pause_reason": current_entries_pause_reason,
            "entries_pause_evidence": current_entries_pause_evidence,
            "edge_threshold_multiplier": get_edge_threshold_multiplier(),
            "strategy_gates": {k: v.to_dict() for k, v in current_strategy_gates.items()},
            "recommended_controls": recommended_controls,
            "recommended_control_reasons": recommended_control_reasons,
            "recommended_strategy_gates": risk_details.get("recommended_strategy_gates", []),
            "recommended_strategy_gate_reasons": recommended_strategy_gate_reasons,
            "recommended_but_not_gated": recommended_but_not_gated,
            "gated_but_not_recommended": gated_but_not_recommended,
            "recommended_controls_not_applied": recommended_controls_not_applied,
            "review_required_gate_recommendations": review_required_gate_recommendations,
        },
        "risk": {
            "level": riskguard_level,
            "riskguard_level": riskguard_level,
            "details": risk_details,
        },
        "portfolio": {
            "open_positions": int(position_view.get("open_positions", 0)),
            "total_exposure_usd": round(float(position_view.get("total_exposure_usd", 0.0) or 0.0), 2),
            "heat_pct": 0.0,
            "initial_bankroll": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": round(float(position_view.get("unrealized_pnl", 0.0) or 0.0), 2),
            "total_pnl": 0.0,
            "effective_bankroll": 0.0,
            "bankroll": 0.0,
            "positions": list(position_view.get("positions", [])),
        },
        "runtime": {
            "chain_state_counts": dict(position_view.get("chain_state_counts", {})),
            "exit_state_counts": dict(position_view.get("exit_state_counts", {})),
            "unverified_entries": int(position_view.get("unverified_entries", 0)),
            "day0_positions": int(position_view.get("day0_positions", 0)),
        },
        "strategy": strategy_summary,
        "execution": {
            "fdr_family_size": int((cycle_summary or {}).get("fdr_family_size", 0)),
            "fdr_family_scan_unavailable": bool((cycle_summary or {}).get("fdr_family_scan_unavailable", False)),
        },
        "execution_capability": _get_execution_capability_status(),
        "calibration_serving": {},
        "price_evidence": {},
        "learning": {},
        "lifecycle_funnel": {},
        "no_trade": {},
        "cycle": cycle_summary or {},
        # S5 R11 P10B: v2 row-count observability sensor
        "v2_row_counts": v2_row_counts,
        # S5 R11 P10B: dual-track scaffold claim (True since P9C closed the main line)
        "dual_track_scaffold_claimed": True,
        "discrepancy_flags": [],
    }
    legacy_positions_artifact = _legacy_positions_artifact_summary(position_view)
    status["portfolio"]["legacy_artifact"] = legacy_positions_artifact
    status["control"]["recommended_auto_commands"] = recommended_autosafe_commands_from_status(status)
    status["control"]["review_required_commands"] = review_required_commands_from_status(status)
    status["control"]["recommended_commands"] = recommended_commands_from_status(
        status,
        include_review_required=True,
    )
    risk_effective_bankroll = _round_money_or_none(risk_details.get("effective_bankroll"))
    risk_initial_bankroll = _round_money_or_none(risk_details.get("initial_bankroll"))
    realized_pnl = risk_details.get("realized_pnl")
    unrealized_pnl = risk_details.get("unrealized_pnl")
    total_pnl = risk_details.get("total_pnl")
    bankroll_truth = risk_details.get("bankroll_truth") if isinstance(risk_details.get("bankroll_truth"), dict) else {}
    risk_bankroll_truth_source = risk_details.get("bankroll_truth_source") or bankroll_truth.get("source")
    risk_bankroll_truth_authority = bankroll_truth.get("authority")
    risk_bankroll_provenance_ok = (
        risk_effective_bankroll is not None
        and risk_initial_bankroll is not None
        and str(risk_bankroll_truth_source or "") == "polymarket_wallet"
        and str(bankroll_truth.get("source") or "") == "polymarket_wallet"
        and str(risk_bankroll_truth_authority or "") == "canonical"
    )
    effective_bankroll = risk_effective_bankroll if risk_bankroll_provenance_ok else None
    initial_bankroll = risk_initial_bankroll if risk_bankroll_provenance_ok else None
    bankroll_truth_source = risk_bankroll_truth_source if risk_bankroll_provenance_ok else None
    bankroll_truth_authority = risk_bankroll_truth_authority if risk_bankroll_provenance_ok else None
    bankroll_truth_status = "present" if risk_bankroll_provenance_ok else "missing"
    bankroll_derivation = "riskguard_effective_bankroll" if risk_bankroll_provenance_ok else None
    bankroll_resolution_source = None
    bankroll_rejected_source = None
    if risk_effective_bankroll is not None and not risk_bankroll_provenance_ok:
        bankroll_rejected_source = "riskguard_unproven"
    if realized_pnl is None:
        realized_pnl = round(
            sum(float(bucket.get("realized_pnl", 0.0) or 0.0) for bucket in strategy_summary.values()),
            2,
        )
    if unrealized_pnl is None:
        unrealized_pnl = status["portfolio"]["unrealized_pnl"]
    if total_pnl is None:
        total_pnl = round(float(realized_pnl or 0.0) + float(unrealized_pnl or 0.0), 2)
    if initial_bankroll is None:
        initial_bankroll = _round_money_or_none((cycle_summary or {}).get("wallet_balance_usd"))
        if initial_bankroll is not None:
            bankroll_truth_source = "cycle_summary.wallet_balance_usd"
            bankroll_truth_authority = "runtime_summary"
            bankroll_resolution_source = "cycle_summary.wallet_balance_usd"
    if initial_bankroll is None:
        # Removed 2026-05-04: previously fell back to retired config-literal
        # capital. Now query the on-chain wallet via bankroll_provider;
        # if the provider has no usable value (None / wallet unreachable + no
        # cache), leave initial_bankroll as None so downstream renders it as
        # null + flags DATA_DEGRADED rather than smuggling a config literal.
        try:
            from src.runtime.bankroll_provider import current as _bankroll_current
            _record = _bankroll_current()
        except Exception:
            _record = None
        if _record is not None:
            initial_bankroll = round(float(_record.value_usd), 2)
            bankroll_truth_source = str(getattr(_record, "source", None) or "polymarket_wallet")
            bankroll_truth_authority = str(getattr(_record, "authority", None) or "canonical")
            bankroll_resolution_source = "bankroll_provider"
        else:
            bankroll_resolution_source = "bankroll_provider_unavailable"
    if effective_bankroll is None:
        if initial_bankroll is not None:
            # Definition A: status bankroll preserves wallet-equity identity.
            # PnL is report analytics; folding it into bankroll would recreate
            # the legacy wallet+PnL synthetic equity object.
            effective_bankroll = round(float(initial_bankroll), 2)
            bankroll_truth_status = "present"
            bankroll_derivation = "wallet_equity_no_pnl"
        else:
            effective_bankroll = None
            bankroll_truth_status = "missing"
            bankroll_derivation = "missing_wallet_truth"
    status["portfolio"]["realized_pnl"] = round(float(realized_pnl or 0.0), 2)
    status["portfolio"]["unrealized_pnl"] = round(float(unrealized_pnl or 0.0), 2)
    status["portfolio"]["total_pnl"] = round(float(total_pnl or 0.0), 2)
    status["portfolio"]["effective_bankroll"] = _round_money_or_none(effective_bankroll)
    status["portfolio"]["bankroll"] = _round_money_or_none(effective_bankroll)
    status["portfolio"]["initial_bankroll"] = _round_money_or_none(initial_bankroll)
    status["portfolio"]["bankroll_object_identity"] = "wallet_equity"
    status["portfolio"]["bankroll_truth_status"] = bankroll_truth_status
    status["portfolio"]["bankroll_truth_source"] = bankroll_truth_source or "unknown"
    status["portfolio"]["bankroll_truth_authority"] = bankroll_truth_authority or "unknown"
    status["portfolio"]["effective_bankroll_derivation"] = bankroll_derivation
    if bankroll_rejected_source is not None:
        status["portfolio"]["bankroll_rejected_source"] = bankroll_rejected_source
    if effective_bankroll is not None and float(effective_bankroll) > 0:
        status["portfolio"]["heat_pct"] = round(
            (float(status["portfolio"]["total_exposure_usd"]) / float(effective_bankroll)) * 100,
            1,
        )
    current_regime_started_at = str(
        ((risk_details.get("strategy_tracker_accounting") or {}).get("current_regime_started_at")) or ""
    )
    try:
        execution_summary = query_execution_event_summary(
            conn,
            not_before=current_regime_started_at or None,
        )
        if not isinstance(execution_summary, dict):
            execution_summary = {}
        execution_summary["current_open_entry_orders"] = current_open_entry_orders
        execution_summary["terminal_command_venue_fact_conflicts"] = (
            terminal_command_venue_fact_conflicts
        )
        status["execution"] = execution_summary
    except Exception:
        status["execution"] = {
            "error": "execution_summary_unavailable",
            "current_open_entry_orders": current_open_entry_orders,
            "terminal_command_venue_fact_conflicts": terminal_command_venue_fact_conflicts,
        }

    try:
        status["learning"] = query_learning_surface_summary(
            conn,
            not_before=current_regime_started_at or None,
        )
    except Exception:
        status["learning"] = {"error": "learning_summary_unavailable"}

    try:
        status["calibration_serving"] = build_calibration_serving_status(conn)
    except Exception:
        status["calibration_serving"] = {
            "status": "query_error",
            "authority": "derived_operator_visibility",
            "error": "calibration_serving_summary_unavailable",
        }

    try:
        status["lifecycle_funnel"] = query_lifecycle_funnel_report(
            conn,
            not_before=current_regime_started_at or None,
        )
    except Exception:
        status["lifecycle_funnel"] = {
            "status": "query_error",
            "authority": "derived_operator_visibility",
            "error": "lifecycle_funnel_summary_unavailable",
        }

    try:
        status["price_evidence"] = build_price_evidence_report(conn)
    except Exception:
        status["price_evidence"] = build_price_evidence_error_report(
            "status_summary",
            "price_evidence_summary_unavailable",
        )

    try:
        recent_no_trades = query_no_trade_cases(conn, hours=24)
        stage_counts: dict[str, int] = {}
        for case in recent_no_trades:
            stage = str(case.get("rejection_stage") or "unknown")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        status["no_trade"] = {
            "recent_stage_counts": stage_counts,
        }
    except Exception:
        status["no_trade"] = {"error": "no_trade_summary_unavailable"}
    if conn is not None:
        conn.close()

    # S5 R11 P10B: discrepancy flag — claim=True AND any v2 table has 0 rows
    if status.get("dual_track_scaffold_claimed") and v2_row_counts:
        empty_v2 = [t for t, c in v2_row_counts.items() if c == 0]
        if empty_v2:
            status["discrepancy_flags"].append("v2_empty_despite_closure_claim")

    consistency_issues: list[str] = []
    cycle_risk_level = str((cycle_summary or {}).get("risk_level") or "")
    if cycle_risk_level and cycle_risk_level != riskguard_level:
        consistency_issues.append(
            f"cycle_risk_level_mismatch:{cycle_risk_level}->{riskguard_level}"
        )
    if bool((cycle_summary or {}).get("failed", False)):
        consistency_issues.append("cycle_failed")
    if status.get("execution", {}).get("error"):
        consistency_issues.append("execution_summary_unavailable")
    terminal_command_conflicts = (
        (status.get("execution", {}) or {}).get("terminal_command_venue_fact_conflicts", {})
        if isinstance(status.get("execution"), dict)
        else {}
    )
    if (
        isinstance(terminal_command_conflicts, dict)
        and str(terminal_command_conflicts.get("status") or "") == "ok"
        and int(terminal_command_conflicts.get("count", 0) or 0) > 0
    ):
        consistency_issues.append(
            "terminal_entry_command_venue_fact_conflicts:"
            f"{int(terminal_command_conflicts.get('count', 0) or 0)}"
        )
    if status.get("learning", {}).get("error"):
        consistency_issues.append("learning_summary_unavailable")
    calibration_serving_status = str((status.get("calibration_serving", {}) or {}).get("status") or "")
    if calibration_serving_status == "query_error":
        consistency_issues.append("calibration_serving_summary_unavailable")
    elif calibration_serving_status == "partial":
        consistency_issues.append("calibration_serving_summary_partial")
    lifecycle_funnel_status = str((status.get("lifecycle_funnel", {}) or {}).get("status") or "")
    if lifecycle_funnel_status == "query_error":
        consistency_issues.append("lifecycle_funnel_summary_unavailable")
    elif lifecycle_funnel_status == "partial":
        consistency_issues.append("lifecycle_funnel_summary_partial")
    price_evidence_status = str((status.get("price_evidence", {}) or {}).get("status") or "")
    if price_evidence_status == "query_error":
        consistency_issues.append("price_evidence_summary_unavailable")
    elif price_evidence_status == "partial":
        consistency_issues.append("price_evidence_summary_partial")
    if status.get("no_trade", {}).get("error"):
        consistency_issues.append("no_trade_summary_unavailable")
    monitor_chain_missing = int((cycle_summary or {}).get("monitor_chain_missing", 0) or 0)
    if monitor_chain_missing > 0:
        consistency_issues.append(f"cycle_monitor_chain_missing:{monitor_chain_missing}")
    if position_view.get("status") != "ok":
        consistency_issues.append(f"position_current_{position_view.get('status')}")
    if "canonical_empty_legacy_active_positions" in set(legacy_positions_artifact.get("conflicts", [])):
        consistency_issues.append("legacy_positions_json_conflicts_with_canonical_empty")
    strategy_health_status = str(strategy_health.get("status") or "")
    if strategy_health_status not in {"fresh"}:
        consistency_issues.append(f"strategy_health_{strategy_health_status or 'unknown'}")
    if status["portfolio"].get("bankroll_truth_status") == "missing":
        consistency_issues.append("bankroll_truth_missing")
    if status["portfolio"].get("bankroll_rejected_source"):
        consistency_issues.append(f"bankroll_rejected_{status['portfolio']['bankroll_rejected_source']}")

    status["risk"]["consistency_check"] = {
        "ok": not consistency_issues,
        "issues": consistency_issues,
        "cycle_risk_level": cycle_risk_level or None,
    }
    # K4: infrastructure / data-availability issues are a SEPARATE dimension from
    # trading risk. Previously any consistency_issue escalated risk.level to RED,
    # which meant cold-start states like strategy_health_empty or
    # cycle_risk_level_mismatch produced false-RED alerts indistinguishable from
    # real trading halts. risk.level now reflects RiskGuard's six trading
    # dimensions only. infrastructure_level reflects observability/data-health.
    # Downstream consumers (Venus supervisor, daily review, Discord alerts) must
    # read both fields and treat them as orthogonal signals.
    if not consistency_issues:
        infrastructure_level = "GREEN"
    else:
        # Hard infrastructure failures escalate to RED because they mean the
        # observability layer cannot be trusted; soft cold-start or
        # availability states stay YELLOW so they do not page as emergencies.
        _HARD_INFRASTRUCTURE_FAILURE_PREFIXES = (
            "cycle_failed",
            "execution_summary_unavailable",
            "learning_summary_unavailable",
            "no_trade_summary_unavailable",
            "cycle_monitor_chain_missing",
            "legacy_positions_json_conflicts_with_canonical_empty",
            "position_current_missing_table",
            "position_current_query_error",
        )
        if any(
            issue.startswith(prefix)
            for issue in consistency_issues
            for prefix in _HARD_INFRASTRUCTURE_FAILURE_PREFIXES
        ):
            infrastructure_level = "RED"
        else:
            infrastructure_level = "YELLOW"
    status["risk"]["infrastructure_level"] = infrastructure_level
    status["risk"]["infrastructure_issues"] = list(consistency_issues)

    learning_by_strategy = (status.get("learning", {}) or {}).get("by_strategy", {}) or {}
    for name, learning_bucket in learning_by_strategy.items():
        _lgate = current_strategy_gates.get(name)
        bucket = strategy_summary.setdefault(
            name,
            {
                "open_positions": 0,
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "gated": _lgate is not None and not _lgate.enabled,
                "recommended_gate": name in recommended_strategy_gates,
                "recommended_gate_reasons": list(recommended_strategy_gate_reasons.get(name, [])),
                "settlement_count": 0,
                "settlement_pnl": 0.0,
                "settlement_accuracy": None,
                "settlement_source": "strategy_health",
                "settlement_window": "30d",
            },
        )
        bucket.setdefault("settlement_count", 0)
        bucket.setdefault("settlement_pnl", 0.0)
        bucket.setdefault("settlement_accuracy", None)
        bucket.setdefault("settlement_source", "strategy_health")
        bucket.setdefault("settlement_window", "30d")
        bucket["learning_settlement_count"] = learning_bucket.get("settlement_count", 0)
        bucket["learning_settlement_pnl"] = learning_bucket.get("settlement_pnl", 0.0)
        bucket["learning_settlement_accuracy"] = learning_bucket.get("settlement_accuracy")
        bucket["learning_settlement_source"] = "learning_surface"
        bucket["learning_settlement_window"] = (
            "current_regime" if current_regime_started_at else "learning_surface_default"
        )
        bucket["no_trade_count"] = learning_bucket.get("no_trade_count", 0)
        bucket["no_trade_stage_counts"] = dict(learning_bucket.get("no_trade_stage_counts", {}) or {})
        bucket["entry_attempted"] = learning_bucket.get("entry_attempted", 0)
        bucket["entry_filled"] = learning_bucket.get("entry_filled", 0)
        bucket["entry_rejected"] = learning_bucket.get("entry_rejected", 0)
    status = annotate_truth_payload(status, STATUS_PATH, generated_at=generated_at, authority="VERIFIED")
    status["truth"]["db_primary_inputs"] = {
        "position_current": str(position_view.get("status") or "unknown"),
        "strategy_health": strategy_health_status or "unknown",
    }
    compatibility_inputs: dict[str, object] = {}
    if current_regime_started_at:
        compatibility_inputs["strategy_tracker_current_regime_started_at"] = current_regime_started_at
    if bankroll_resolution_source is not None:
        # Removed 2026-05-04: the previous config-cap fallback label is gone.
        # Live truth now flows from bankroll_provider.current(); when it returns None the field stays
        # null and DATA_DEGRADED surfaces upstream — no config-literal smuggle.
        compatibility_inputs["bankroll_resolution_source"] = bankroll_resolution_source
    if bankroll_rejected_source is not None:
        compatibility_inputs["bankroll_rejected_source"] = bankroll_rejected_source
    if compatibility_inputs:
        status["truth"]["compatibility_inputs"] = compatibility_inputs

    # Atomic write
    _atomic_write_status_payload(status)
