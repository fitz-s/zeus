#!/usr/bin/env python3
# Lifecycle: created=2026-08-12; last_reviewed=2026-08-13; last_reused=2026-08-13
# Purpose: Grade exact current selection/probability revisions on causal capital outcomes.
# Reuse: Run read-only against canonical WORLD/FORECAST/TRADES DBs; output is evidence, not authority.
"""Fail-closed evaluator for current-regime capital advantage.

Old profit, model scores, marks, and mixed-revision fills cannot satisfy this
contract.  The evaluator reports the narrowest missing proof line and writes a
deterministic evidence artifact; it never mutates canonical state or submits an
order.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.global_batch_runtime import (  # noqa: E402
    CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
)
from src.contracts.global_auction_receipt import (  # noqa: E402
    GlobalAuctionReceiptRef,
    assert_global_auction_receipt_artifact,
    assert_global_auction_summary_integrity,
)
from src.events.day0_authority import (  # noqa: E402
    DAY0_PROBABILITY_SEMANTICS_REVISION,
)
from src.riskguard import riskguard as rg  # noqa: E402
from src.state.db import (  # noqa: E402
    get_forecasts_connection_read_only,
    get_trade_connection_read_only,
)
from src.types.market import Bin  # noqa: E402
from src.data.replacement_forecast_cycle_policy import (  # noqa: E402
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
)

MIN_INDEPENDENT_FAMILY_DAYS = 30
MIN_EXACT_LIVE_REALIZED_POSITIONS = 30
GLOBAL_HOLD_RECEIPT_SCAN_ROWS = 5_000
WINDOW_DAYS = 35.0
GLOBAL_AUCTION_RECEIPT_MODES = (
    "global_single_order_auction",
    "global_single_order_auction_delta",
    "global_single_order_auction_duplicate",
)
PROOF_ROLE = "SIDE_EFFECT_FREE_CAPITAL_COUNTERFACTUAL"
CONSERVATIVE_ONE_SIDED_T95_DF29 = 1.699
CURRENT_PROBABILITY_SEMANTICS = frozenset(
    {
        DAY0_PROBABILITY_SEMANTICS_REVISION,
        CURRENT_EVIDENCE_SEMANTICS_REVISION,
    }
)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_aware(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp is not timezone-aware")
    return parsed


def _decimal(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal: {field}") from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite decimal: {field}")
    return parsed


def _read_only(
    path: Path,
    required_tables: frozenset[str],
    *,
    connection_factory: Callable[[], sqlite3.Connection],
) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 4096:
        raise ValueError(f"canonical DB missing or placeholder: {resolved}")
    conn = connection_factory()
    database_rows = conn.execute("PRAGMA database_list").fetchall()
    main_paths = [
        Path(str(row[2])).resolve()
        for row in database_rows
        if str(row[1]) == "main" and str(row[2]).strip()
    ]
    if main_paths != [resolved]:
        conn.close()
        raise ValueError(
            f"configured canonical DB path mismatch: expected={resolved}:actual={main_paths}"
        )
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(required_tables.difference(tables))
    if missing:
        conn.close()
        raise ValueError(
            f"canonical DB schema mismatch: {resolved}:missing={','.join(missing)}"
        )
    return conn


def _receipt_revision_coverage(conn: sqlite3.Connection) -> dict[str, object]:
    row = conn.execute(
        "SELECT id,completed_at,artifact_json FROM decision_log "
        "WHERE mode IN (?,?,?) AND completed_at IS NOT NULL "
        "AND id > (SELECT COALESCE(MAX(id),0)-10000 FROM decision_log) "
        "AND instr(artifact_json, '\"proof_counterfactual\"') > 0 "
        "ORDER BY id DESC LIMIT 1",
        GLOBAL_AUCTION_RECEIPT_MODES,
    ).fetchone()
    if row is None:
        return {"ready": False, "reason": "global_auction_receipt_missing"}
    try:
        artifact = json.loads(str(row["artifact_json"] or ""))
        summary = artifact["summary"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"ready": False, "reason": "global_auction_receipt_invalid"}
    exact_revision = (
        summary.get("global_selection_revision")
        == CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    )
    wealth = summary.get("portfolio_wealth")
    wealth_ready = isinstance(wealth, dict) and all(
        str(wealth.get(field) or "").strip()
        for field in (
            "ledger_snapshot_id",
            "position_set_hash",
            "wealth_floor_usd",
            "wealth_ceiling_usd",
            "spendable_cash_usd",
            "reservations_usd",
            "collateral_authority",
        )
    )
    coverage_ready = all(
        summary.get(field) is True
        for field in (
            "scope_family_coverage_complete",
            "candidate_coverage_complete",
            "held_position_coverage_complete",
            "book_capture_freshness_complete",
        )
    )
    try:
        _summary_proof(conn, int(row["id"]), summary)
        proof_ready = True
    except (KeyError, TypeError, ValueError):
        proof_ready = False
    return {
        "ready": bool(
            exact_revision and wealth_ready and coverage_ready and proof_ready
        ),
        "decision_log_id": int(row["id"]),
        "completed_at": str(row["completed_at"]),
        "observed_selection_revision": summary.get(
            "global_selection_revision"
        ),
        "expected_selection_revision": (
            CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ),
        "selection_revision_ready": exact_revision,
        "portfolio_wealth_ready": wealth_ready,
        "coverage_ready": coverage_ready,
        "proof_counterfactual_ready": proof_ready,
    }


def _audit_context_for_summary(
    conn: sqlite3.Connection,
    decision_log_id: int,
    summary: Mapping[str, object],
    *,
    seen: frozenset[int] = frozenset(),
) -> dict[str, object]:
    """Rehydrate and verify compact schema-22 audit context from its chain."""

    if decision_log_id in seen or len(seen) >= 16:
        raise ValueError("audit context reference cycle/depth invalid")
    expected_sha = str(summary.get("audit_context_sha256") or "")
    if len(expected_sha) != 64:
        raise ValueError("audit context hash unavailable")

    inline = summary.get("audit_context_zlib_b64")
    if inline is not None:
        try:
            raw = zlib.decompress(base64.b64decode(str(inline), validate=True))
            context = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError, zlib.error) as exc:
            raise ValueError("audit context inline payload invalid") from exc
        if (
            not isinstance(context, dict)
            or hashlib.sha256(_canonical_json_bytes(context)).hexdigest()
            != expected_sha
        ):
            raise ValueError("audit context inline hash mismatch")
        return context

    if summary.get("audit_context_reference_decision_log_id") is not None:
        base_id = int(summary["audit_context_reference_decision_log_id"])
        base_mode = str(summary.get("audit_context_reference_mode") or "")
        base_receipt_hash = str(
            summary.get("audit_context_reference_receipt_hash") or ""
        )
        base_sha = str(summary.get("audit_context_reference_sha256") or "")
        if base_sha != expected_sha:
            raise ValueError("audit context exact reference hash mismatch")
        delta = None
    elif summary.get("audit_context_base_decision_log_id") is not None:
        base_id = int(summary["audit_context_base_decision_log_id"])
        base_mode = str(summary.get("audit_context_base_mode") or "")
        base_receipt_hash = str(
            summary.get("audit_context_base_receipt_hash") or ""
        )
        base_sha = str(summary.get("audit_context_base_sha256") or "")
        try:
            delta_raw = zlib.decompress(
                base64.b64decode(
                    str(summary["audit_context_delta_zlib_b64"]),
                    validate=True,
                )
            )
            delta = json.loads(delta_raw)
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            zlib.error,
        ) as exc:
            raise ValueError("audit context delta payload invalid") from exc
        if (
            not isinstance(delta, Mapping)
            or hashlib.sha256(_canonical_json_bytes(delta)).hexdigest()
            != str(summary.get("audit_context_delta_sha256") or "")
        ):
            raise ValueError("audit context delta hash mismatch")
    else:
        raise ValueError("audit context payload/reference unavailable")

    row = conn.execute(
        "SELECT mode,artifact_json FROM decision_log WHERE id=?",
        (base_id,),
    ).fetchone()
    if row is None or str(row[0]) != base_mode:
        raise ValueError("audit context base row unavailable")
    try:
        base_summary = json.loads(str(row[1] or ""))["summary"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("audit context base summary invalid") from exc
    if (
        not isinstance(base_summary, Mapping)
        or str(base_summary.get("receipt_hash") or "") != base_receipt_hash
        or str(base_summary.get("audit_context_sha256") or "") != base_sha
    ):
        raise ValueError("audit context base identity mismatch")
    base = _audit_context_for_summary(
        conn,
        base_id,
        base_summary,
        seen=seen | {decision_log_id},
    )
    if delta is None:
        return base
    replacements = delta.get("replacements")
    removed = delta.get("removed_keys")
    if not isinstance(replacements, Mapping) or not isinstance(removed, list):
        raise ValueError("audit context delta shape invalid")
    context = dict(base)
    for key in removed:
        context.pop(str(key), None)
    context.update((str(key), value) for key, value in replacements.items())
    if hashlib.sha256(_canonical_json_bytes(context)).hexdigest() != expected_sha:
        raise ValueError("audit context reconstructed hash mismatch")
    return context


def _summary_proof(
    conn: sqlite3.Connection,
    decision_log_id: int,
    summary: Mapping[str, object],
) -> Mapping[str, object]:
    assert_global_auction_summary_integrity(summary)
    if summary.get("schema_version") != 22:
        raise ValueError("proof receipt is not schema 22")
    if summary.get("global_selection_revision") != (
        CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    ):
        raise ValueError("proof receipt selection revision mismatch")
    if not all(
        summary.get(field) is True
        for field in (
            "scope_family_coverage_complete",
            "candidate_coverage_complete",
            "held_position_coverage_complete",
            "book_capture_freshness_complete",
        )
    ):
        raise ValueError("proof receipt coverage incomplete")
    proof = summary.get("proof_counterfactual")
    if not isinstance(proof, Mapping):
        raise ValueError("proof counterfactual missing")
    if hashlib.sha256(_canonical_json_bytes(proof)).hexdigest() != str(
        summary.get("proof_counterfactual_sha256") or ""
    ):
        raise ValueError("proof counterfactual hash mismatch")
    if (
        proof.get("role") != PROOF_ROLE
        or proof.get("venue_actuation_available") is not False
        or proof.get("venue_side_effect_free") is not True
        or proof.get("venue_submit_count_before")
        != proof.get("venue_submit_count_after")
        or proof.get("global_selection_revision")
        != CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    ):
        raise ValueError("proof counterfactual side-effect contract invalid")
    audit_context = _audit_context_for_summary(
        conn,
        decision_log_id,
        summary,
    )
    for field in (
        "selection_epoch_identity",
        "selection_cut_at_utc",
        "decision_at_utc",
        "probability_manifest",
        "full_scope_identity",
        "book_epoch_identity",
        "wealth_witness_identity",
        "wealth_economic_identity",
    ):
        expected = (
            audit_context.get(field)
            if field in audit_context
            else summary.get(field)
        )
        if proof.get(field) != expected:
            raise ValueError(f"proof counterfactual cut mismatch: {field}")
    if (
        int(proof.get("candidate_input_count") or -1) <= 0
        or proof.get("candidate_input_count")
        != proof.get("candidate_evaluation_count")
    ):
        raise ValueError("proof counterfactual candidate coverage incomplete")
    return proof


def _latest_proof_receipt_coverage(
    conn: sqlite3.Connection,
) -> dict[str, object]:
    rows = conn.execute(
        "SELECT id,completed_at,artifact_json FROM decision_log "
        "WHERE mode IN (?,?,?) AND completed_at IS NOT NULL "
        "ORDER BY id DESC LIMIT 256",
        GLOBAL_AUCTION_RECEIPT_MODES,
    ).fetchall()
    if not rows:
        return {"ready": False, "reason": "global_auction_receipt_missing"}
    latest_invalid: dict[str, object] | None = None
    for row in rows:
        try:
            artifact = json.loads(str(row["artifact_json"] or ""))
            summary = artifact["summary"]
            _summary_proof(conn, int(row["id"]), summary)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if latest_invalid is None:
                latest_invalid = {
                    "ready": False,
                    "decision_log_id": int(row["id"]),
                    "completed_at": str(row["completed_at"]),
                    "reason": str(exc) or type(exc).__name__,
                }
            continue
        return {
            "ready": True,
            "decision_log_id": int(row["id"]),
            "completed_at": str(row["completed_at"]),
            "observed_selection_revision": summary.get(
                "global_selection_revision"
            ),
            "expected_selection_revision": (
                CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "selection_revision_ready": True,
            "portfolio_wealth_ready": True,
            "coverage_ready": True,
            "proof_counterfactual_ready": True,
        }
    return latest_invalid or {
        "ready": False,
        "reason": "global_auction_proof_receipt_missing",
    }


def _verified_settlement(
    forecasts: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    decision_at: datetime,
) -> sqlite3.Row:
    rows = forecasts.execute(
        "SELECT settlement_id,settlement_value,settlement_unit,settled_at,"
        "recorded_at,authority FROM settlement_outcomes "
        "WHERE city=? AND target_date=? AND temperature_metric=?",
        (city, target_date, metric),
    ).fetchall()
    if len(rows) != 1 or str(rows[0]["authority"]) != "VERIFIED":
        raise ValueError("unique VERIFIED settlement unavailable")
    row = rows[0]
    if row["settlement_value"] is None or not str(
        row["settlement_unit"] or ""
    ).strip():
        raise ValueError("VERIFIED settlement value/unit incomplete")
    if not (
        decision_at < _parse_aware(row["settled_at"])
        and decision_at < _parse_aware(row["recorded_at"])
    ):
        raise ValueError("settlement is not strictly after decision")
    return row


def _condition_resolved_yes(
    forecasts: sqlite3.Connection,
    *,
    condition_id: str,
    city: str,
    target_date: str,
    metric: str,
    settlement_value: Decimal,
    settlement_unit: str,
) -> bool:
    rows = forecasts.execute(
        "SELECT city,target_date,temperature_metric,range_low,range_high "
        "FROM market_events WHERE condition_id=?",
        (condition_id,),
    ).fetchall()
    matching = [
        row
        for row in rows
        if (
            str(row["city"]) == city
            and str(row["target_date"]) == target_date
            and str(row["temperature_metric"]).lower() == metric
        )
    ]
    if len(matching) != 1:
        raise ValueError("unique condition settlement geometry unavailable")
    low = (
        _decimal(matching[0]["range_low"], "range_low")
        if matching[0]["range_low"] is not None
        else None
    )
    high = (
        _decimal(matching[0]["range_high"], "range_high")
        if matching[0]["range_high"] is not None
        else None
    )
    if low is None and high is None:
        raise ValueError("condition settlement geometry empty")
    unit = str(settlement_unit or "").strip().upper()
    if unit not in {"F", "C"}:
        raise ValueError("condition settlement unit invalid")
    try:
        market_bin = Bin(
            low=float(low) if low is not None else None,
            high=float(high) if high is not None else None,
            unit=unit,
        )
    except ValueError as exc:
        raise ValueError("condition settlement geometry invalid") from exc
    return market_bin.contains(float(settlement_value))


def _realized_proof_sample(
    trades: sqlite3.Connection,
    forecasts: sqlite3.Connection,
    *,
    decision_log_id: int,
    summary: Mapping[str, object],
) -> dict[str, object]:
    proof = _summary_proof(trades, decision_log_id, summary)
    winner = proof.get("winner")
    if not isinstance(winner, Mapping) or winner.get("action") != "BUY":
        raise ValueError("proof winner is not a statistical BUY")
    city = str(winner.get("city") or "").strip()
    target_date = str(winner.get("target_date") or "").strip()
    metric = str(winner.get("metric") or "").strip().lower()
    condition_id = str(winner.get("condition_id") or "").strip()
    side = str(winner.get("side") or "").strip().upper()
    semantics = str(
        winner.get("probability_semantics_revision") or ""
    ).strip()
    if (
        not all((city, target_date, metric, condition_id))
        or metric not in {"high", "low"}
        or side not in {"YES", "NO"}
        or semantics not in CURRENT_PROBABILITY_SEMANTICS
    ):
        raise ValueError("proof winner identity/semantics invalid")
    evaluation = winner.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise ValueError("proof winner evaluation missing")
    execution_mode = str(winner.get("execution_mode") or "").strip().upper()
    if execution_mode != "TAKER_LIMIT":
        raise ValueError("proof winner lacks immediate full-fill execution proof")
    if (
        str(evaluation.get("execution_mode") or "").strip().upper()
        != execution_mode
        or str(evaluation.get("capital_action_mode") or "").strip().upper()
        != "IMMEDIATE_TAKER_BUY"
    ):
        raise ValueError("proof winner taker execution certificate invalid")
    decision_at = _parse_aware(proof.get("decision_at_utc"))
    settlement = _verified_settlement(
        forecasts,
        city=city,
        target_date=target_date,
        metric=metric,
        decision_at=decision_at,
    )
    condition_yes = _condition_resolved_yes(
        forecasts,
        condition_id=condition_id,
        city=city,
        target_date=target_date,
        metric=metric,
        settlement_value=_decimal(
            settlement["settlement_value"], "settlement_value"
        ),
        settlement_unit=str(settlement["settlement_unit"]),
    )
    token_won = condition_yes if side == "YES" else not condition_yes
    expected_growth = evaluation.get("expected_growth")
    terminal = evaluation.get("expected_terminal_wealth")
    if (
        evaluation.get("status") != "SELECTED"
        or evaluation.get("action") != "BUY"
        or str(evaluation.get("candidate_id") or "")
        != str(winner.get("candidate_id") or "")
        or not isinstance(expected_growth, Mapping)
        or expected_growth.get("probability_basis")
        != "POSTERIOR_PREDICTIVE_MEAN"
        or not isinstance(terminal, Mapping)
        or terminal.get("probability_basis")
        != "POSTERIOR_PREDICTIVE_MEAN"
    ):
        raise ValueError("proof winner expected-growth certificate invalid")
    loss_payoff = _decimal(terminal.get("loss_payoff_usd"), "loss_payoff")
    win_payoff = _decimal(terminal.get("win_payoff_usd"), "win_payoff")
    loss_wealth = _decimal(
        terminal.get("wealth_after_loss_usd"), "wealth_after_loss"
    )
    win_wealth = _decimal(
        terminal.get("wealth_after_win_usd"), "wealth_after_win"
    )
    loss_before = loss_wealth - loss_payoff
    win_before = win_wealth - win_payoff
    tolerance = Decimal("0.000001")
    if (
        loss_payoff >= 0
        or win_payoff <= 0
        or loss_before <= 0
        or win_before <= 0
        or abs(loss_before - win_before) > tolerance
        or abs(_decimal(winner.get("cost_usd"), "winner_cost") + loss_payoff)
        > tolerance
    ):
        raise ValueError("proof winner after-cost terminal wealth inconsistent")
    payoff = win_payoff if token_won else loss_payoff
    wealth_after = win_wealth if token_won else loss_wealth
    delta_log = math.log(float(wealth_after / loss_before))
    if not math.isfinite(delta_log):
        raise ValueError("proof winner realized delta-log wealth invalid")
    return {
        "decision_log_id": decision_log_id,
        "proof_counterfactual_sha256": str(
            summary["proof_counterfactual_sha256"]
        ),
        "family_day": [city, target_date, metric],
        "condition_id": condition_id,
        "side": side,
        "execution_mode": execution_mode,
        "probability_semantics_revision": semantics,
        "decision_at_utc": decision_at.isoformat(),
        "settlement_id": int(settlement["settlement_id"]),
        "settlement_value": str(settlement["settlement_value"]),
        "settlement_unit": str(settlement["settlement_unit"]),
        "token_won": token_won,
        "capital_committed_usd": str(winner.get("cost_usd")),
        "realized_after_cost_payoff_usd": str(payoff),
        "realized_delta_log_wealth": delta_log,
    }


def _settled_global_counterfactual_evidence(
    trades: sqlite3.Connection,
    forecasts: sqlite3.Connection,
    *,
    as_of: datetime,
) -> dict[str, object]:
    cutoff = (as_of - timedelta(days=WINDOW_DAYS)).isoformat()
    rows = trades.execute(
        "SELECT id,artifact_json FROM decision_log "
        "WHERE mode IN (?,?,?) AND completed_at>=? AND completed_at<=? "
        "AND id > (SELECT COALESCE(MAX(id),0)-10000 FROM decision_log) "
        "AND instr(artifact_json, '\"proof_counterfactual\"') > 0 "
        "ORDER BY id ASC",
        (*GLOBAL_AUCTION_RECEIPT_MODES, cutoff, as_of.isoformat()),
    ).fetchall()
    samples: list[dict[str, object]] = []
    seen_family_days: set[tuple[str, str, str]] = set()
    rejection_counts: dict[str, int] = {}
    for row in rows:
        try:
            artifact = json.loads(str(row["artifact_json"] or ""))
            summary = artifact["summary"]
            sample = _realized_proof_sample(
                trades,
                forecasts,
                decision_log_id=int(row["id"]),
                summary=summary,
            )
            family_day = tuple(str(value) for value in sample["family_day"])
            if family_day in seen_family_days:
                rejection_counts["duplicate_family_day"] = (
                    rejection_counts.get("duplicate_family_day", 0) + 1
                )
                continue
            seen_family_days.add(family_day)
            samples.append(sample)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            reason = str(exc) or type(exc).__name__
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    values = [float(row["realized_delta_log_wealth"]) for row in samples]
    mean = statistics.fmean(values) if values else None
    if len(values) >= 2:
        standard_error = statistics.stdev(values) / math.sqrt(len(values))
        lcb95 = mean - CONSERVATIVE_ONE_SIDED_T95_DF29 * standard_error
    else:
        standard_error = None
        lcb95 = None
    return {
        "global_selection_revision_bound": True,
        "global_selection_revision": CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
        "independent_family_day_count": len(samples),
        "settled_row_count": len(samples),
        "realized_after_cost_pnl_usd": str(
            sum(
                (
                    Decimal(str(row["realized_after_cost_payoff_usd"]))
                    for row in samples
                ),
                Decimal("0"),
            )
        ),
        "mean_delta_log_wealth": mean,
        "delta_log_wealth_standard_error": standard_error,
        "delta_log_wealth_lcb95": lcb95,
        "lcb_method": "one-sided Student-t; conservative critical=1.699 (df=29 floor)",
        "minimum_sample_gate": MIN_INDEPENDENT_FAMILY_DAYS,
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "samples": samples,
    }


def _realized_curve_with_deadline(
    conn: sqlite3.Connection,
    *,
    evaluator: Callable[..., dict[str, object]],
    as_of: datetime,
    deadline_seconds: float = 20.0,
) -> dict[str, object]:
    deadline = time.monotonic() + deadline_seconds

    def interrupt_when_expired() -> int:
        return int(time.monotonic() >= deadline)

    conn.set_progress_handler(interrupt_when_expired, 5_000)
    try:
        return evaluator(conn, window_days=WINDOW_DAYS, as_of=as_of)
    except sqlite3.OperationalError as exc:
        if "interrupt" not in str(exc).lower():
            raise
        return {
            "status": "capital_truth_degraded",
            "reason": "read_deadline_exceeded",
            "net_realized_pnl_usd": None,
        }
    finally:
        conn.set_progress_handler(None, 0)


def _validated_global_receipt(
    conn: sqlite3.Connection,
    raw_receipt: object,
    *,
    source: str,
) -> GlobalAuctionReceiptRef:
    try:
        receipt = GlobalAuctionReceiptRef.from_payload(raw_receipt)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} global receipt invalid") from exc
    if receipt.schema_version != 22:
        raise ValueError(f"{source} global receipt is not schema 22")
    row = conn.execute(
        "SELECT mode,artifact_json FROM decision_log WHERE id=?",
        (receipt.decision_log_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"{source} global receipt row missing")
    assert_global_auction_receipt_artifact(
        expected=receipt,
        decision_log_id=receipt.decision_log_id,
        decision_log_mode=str(row[0]),
        artifact_json=row[1],
    )
    artifact = json.loads(str(row[1]))
    if artifact["summary"].get("global_selection_revision") != (
        CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    ):
        raise ValueError(f"{source} global receipt selection revision mismatch")
    return receipt


def _command_global_receipt(
    conn: sqlite3.Connection,
    *,
    execution_command_id: str,
) -> GlobalAuctionReceiptRef:
    rows = conn.execute(
        "SELECT pre.payload_json FROM edli_live_order_events AS cmd "
        "JOIN edli_live_order_events AS pre "
        "ON pre.aggregate_id=cmd.aggregate_id "
        "AND pre.event_type='PreSubmitRevalidated' "
        "WHERE cmd.event_type='ExecutionCommandCreated' "
        "AND json_extract(cmd.payload_json,'$.execution_command_id')=? "
        "LIMIT 2",
        (execution_command_id,),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("unique EDLI pre-submit receipt unavailable")
    try:
        payload = json.loads(str(rows[0][0] or ""))
        raw_receipt = payload.get("global_auction_receipt")
        if raw_receipt is None:
            economics = payload.get("qkernel_execution_economics")
            raw_receipt = (
                economics.get("global_auction_receipt")
                if isinstance(economics, Mapping)
                else None
            )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("EDLI global receipt invalid") from exc
    return _validated_global_receipt(
        conn,
        raw_receipt,
        source="EDLI",
    )


def _bind_live_curve_to_global_revision(
    conn: sqlite3.Connection,
    curve: Mapping[str, object],
) -> dict[str, object]:
    exact_rows: list[dict[str, object]] = []
    unbound_reasons: dict[str, int] = {}
    for raw in tuple(curve.get("curve") or ()):
        row = dict(raw)
        position_id = str(row.get("position_id") or "").strip()
        commands = conn.execute(
            "SELECT DISTINCT decision_id FROM venue_commands "
            "WHERE position_id=? AND intent_kind='ENTRY' ORDER BY decision_id",
            (position_id,),
        ).fetchall()
        try:
            if not commands:
                raise ValueError("entry command missing")
            receipts = {
                _command_global_receipt(
                    conn,
                    execution_command_id=str(command[0] or ""),
                )
                for command in commands
            }
            if len(
                {receipt.selection_epoch_identity for receipt in receipts}
            ) != 1:
                raise ValueError("entry commands span selection epochs")
        except ValueError as exc:
            reason = str(exc)
            unbound_reasons[reason] = unbound_reasons.get(reason, 0) + 1
            continue
        receipt = min(receipts, key=lambda item: item.decision_log_id)
        exact_rows.append(
            {
                **row,
                "global_selection_revision": (
                    CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                ),
                "global_auction_decision_log_id": receipt.decision_log_id,
                "global_auction_receipt_hash": receipt.receipt_hash,
                "global_selection_epoch_identity": (
                    receipt.selection_epoch_identity
                ),
            }
        )
    net_pnl = sum(
        float(row.get("net_realized_pnl_usd") or 0.0) for row in exact_rows
    )
    capital = sum(
        float(row.get("capital_committed_usd") or 0.0) for row in exact_rows
    )
    return {
        "status": (
            "positive"
            if exact_rows and net_pnl > 0.0
            else "nonpositive"
            if exact_rows
            else "awaiting_exact_selection_revision_fills"
        ),
        "selection_revision_bound": True,
        "global_selection_revision": (
            CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ),
        "realized_position_count": len(exact_rows),
        "unbound_current_semantics_position_count": (
            len(tuple(curve.get("curve") or ())) - len(exact_rows)
        ),
        "unbound_reasons": dict(sorted(unbound_reasons.items())),
        "realized_capital_committed_usd": round(capital, 6),
        "net_realized_pnl_usd": round(net_pnl, 6),
        "return_on_realized_capital": (
            round(net_pnl / capital, 6) if capital > 0.0 else None
        ),
        "curve": exact_rows,
        "all_current_probability_semantics": dict(curve),
    }


def _executable_bid_vwap(depth_json: object, shares: object) -> float | None:
    """Return the bid-ladder VWAP for the full sold size, or fail closed."""

    try:
        quantity = float(shares)
        depth = json.loads(str(depth_json or ""))
        raw_bids = depth["bids"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not math.isfinite(quantity) or quantity <= 0.0 or not isinstance(raw_bids, list):
        return None
    levels: list[tuple[float, float]] = []
    for raw in raw_bids:
        try:
            if isinstance(raw, Mapping):
                price = float(raw["price"])
                size = float(raw["size"])
            else:
                price = float(raw[0])
                size = float(raw[1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if (
            not math.isfinite(price)
            or not math.isfinite(size)
            or not 0.0 < price < 1.0
            or size <= 0.0
        ):
            return None
        levels.append((price, size))
    remaining = quantity
    proceeds = 0.0
    for price, available in sorted(levels, reverse=True):
        filled = min(remaining, available)
        proceeds += filled * price
        remaining -= filled
        if remaining <= 1e-9:
            return proceeds / quantity
    return None


def _globally_selected_exit_quality(
    conn: sqlite3.Connection,
    forecasts: sqlite3.Connection,
    curves: Mapping[str, Mapping[str, object]],
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Grade exact schema-22 EXITs against HOLD and later executable bids.

    Entry-to-exit PnL is accounting, not an EXIT quality verdict.  EXIT only
    becomes settlement-graded when the held token's binary payoff is VERIFIED.
    Post-exit bid observations are size-executable lower bounds on the attainable
    peak; they are never promoted to complete peak proof without a continuity
    contract.
    """

    candidates: dict[str, dict[str, object]] = {}
    for strategy, curve in curves.items():
        for raw in tuple(curve.get("curve") or ()):
            row = dict(raw)
            if row.get("close_type") != "EXIT_ORDER_FILLED":
                continue
            position_id = str(row.get("position_id") or "").strip()
            if position_id:
                candidates[position_id] = {**row, "strategy": strategy}

    exact_rows: list[dict[str, object]] = []
    rejection_counts: dict[str, int] = {}
    for position_id, row in sorted(candidates.items()):
        try:
            realized_at = _parse_aware(row.get("realized_at"))
            fills = conn.execute(
                "SELECT command_id,payload_json FROM position_events "
                "WHERE position_id=? AND event_type='EXIT_ORDER_FILLED' "
                "AND occurred_at=? ORDER BY sequence_no DESC LIMIT 2",
                (position_id, realized_at.isoformat()),
            ).fetchall()
            if len(fills) != 1 or not str(fills[0][0] or "").strip():
                raise ValueError("unique exit fill command unavailable")
            command_id = str(fills[0][0])
            command = conn.execute(
                "SELECT created_at,state,token_id,envelope_id FROM venue_commands "
                "WHERE command_id=? AND position_id=? AND intent_kind='EXIT'",
                (command_id, position_id),
            ).fetchone()
            if command is None or str(command[1]) != "FILLED":
                raise ValueError("filled exit venue command unavailable")
            intents = conn.execute(
                "SELECT occurred_at,payload_json FROM position_events "
                "WHERE position_id=? AND event_type='EXIT_INTENT' "
                "AND datetime(occurred_at)<=datetime(?) "
                "AND datetime(occurred_at)<=datetime(?) "
                "ORDER BY sequence_no DESC LIMIT 1",
                (position_id, command[0], realized_at.isoformat()),
            ).fetchall()
            if len(intents) != 1:
                raise ValueError("exit intent unavailable")
            payload = json.loads(str(intents[0][1] or ""))
            certificate = payload.get("exit_intent_capital_certificate")
            if not isinstance(certificate, Mapping):
                raise ValueError("exit capital certificate unavailable")
            if (
                certificate.get("action") != "SELL"
                or str(certificate.get("position_id") or "") != position_id
            ):
                raise ValueError("exit capital certificate identity mismatch")
            receipt = _validated_global_receipt(
                conn,
                certificate.get("global_auction_receipt"),
                source="EXIT_INTENT",
            )
            position = conn.execute(
                "SELECT city,target_date,temperature_metric,condition_id,direction,"
                "entry_price,shares,cost_basis_usd FROM position_current "
                "WHERE position_id=?",
                (position_id,),
            ).fetchone()
            if position is None:
                raise ValueError("exit position truth unavailable")
            fills = conn.execute(
                "SELECT ef.fill_price,ef.shares,ef.filled_at,ef.terminal_exec_status,"
                "vse.post_only,vse.fee_details_json,vse.outcome_label "
                "FROM execution_fact AS ef "
                "JOIN venue_submission_envelopes AS vse ON vse.envelope_id=? "
                "WHERE ef.command_id=? AND ef.order_role='exit' "
                "AND lower(COALESCE(ef.terminal_exec_status,'')) "
                "IN ('filled','confirmed','partial')",
                (command[3], command_id),
            ).fetchall()
            if len(fills) != 1:
                raise ValueError("unique exact exit execution fact unavailable")
            fill = fills[0]
            fill_price = float(fill[0])
            fill_shares = float(fill[1])
            fill_at = _parse_aware(fill[2])
            exit_fee = rg._submission_schedule_fee_usd(
                post_only=fill[4],
                fee_details_json=fill[5],
                fill_price=fill_price,
                shares=fill_shares,
            )
            if exit_fee is None:
                raise ValueError("exact exit fee unavailable")
            outcome_label = str(fill[6] or "").strip().upper()
            if outcome_label not in {"YES", "NO"}:
                raise ValueError("sold token outcome unavailable")
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            reason = str(exc) or type(exc).__name__
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        city = str(position[0] or "").strip()
        target_date = str(position[1] or "").strip()
        metric = str(position[2] or "").strip().lower()
        condition_id = str(position[3] or "").strip()
        settlement_status = "awaiting_unique_verified_settlement"
        hold_payoff_usd: float | None = None
        exit_vs_hold_usd: float | None = None
        try:
            settlement = _verified_settlement(
                forecasts,
                city=city,
                target_date=target_date,
                metric=metric,
                decision_at=fill_at,
            )
            condition_yes = _condition_resolved_yes(
                forecasts,
                condition_id=condition_id,
                city=city,
                target_date=target_date,
                metric=metric,
                settlement_value=_decimal(
                    settlement["settlement_value"], "settlement_value"
                ),
                settlement_unit=str(settlement["settlement_unit"]),
            )
            sold_token_won = condition_yes if outcome_label == "YES" else not condition_yes
            hold_payoff_usd = fill_shares if sold_token_won else 0.0
            exit_vs_hold_usd = fill_price * fill_shares - exit_fee - hold_payoff_usd
            settlement_status = "verified_binary_payoff"
        except (TypeError, ValueError):
            pass

        path_rows = conn.execute(
            "SELECT quote_seen_at,depth_before_json FROM execution_feasibility_evidence "
            "WHERE token_id=? AND datetime(quote_seen_at)>datetime(?) "
            "AND datetime(quote_seen_at)<=datetime(?) "
            "AND depth_before_json IS NOT NULL AND depth_before_json!='' "
            "ORDER BY datetime(quote_seen_at),rowid",
            (str(command[2]), fill_at.isoformat(), as_of.isoformat()),
        ).fetchall()
        executable_path = [
            (str(path[0]), vwap)
            for path in path_rows
            if (vwap := _executable_bid_vwap(path[1], fill_shares)) is not None
        ]
        observed_peak = (
            max(vwap for _, vwap in executable_path) if executable_path else None
        )
        entry_price = float(position[5]) if position[5] is not None else None
        cost_basis = float(position[7]) if position[7] is not None else None
        accounting_pnl = (
            fill_price * fill_shares - exit_fee - cost_basis
            if cost_basis is not None
            else row.get("net_realized_pnl_usd")
        )
        exact_rows.append(
            {
                "position_id": position_id,
                "strategy": row.get("strategy"),
                "city": city,
                "target_date": target_date,
                "metric": metric,
                "condition_id": condition_id,
                "direction": str(position[4] or ""),
                "sold_outcome": outcome_label,
                "command_id": command_id,
                "filled_at": fill_at.isoformat(),
                "filled_shares": fill_shares,
                "fill_price": fill_price,
                "exit_fee_usd": round(exit_fee, 6),
                "entry_price": entry_price,
                "cost_basis_usd": cost_basis,
                "entry_to_exit_accounting_pnl_usd": (
                    round(float(accounting_pnl), 6)
                    if accounting_pnl is not None
                    else None
                ),
                "settlement_status": settlement_status,
                "hold_to_binary_payoff_usd": (
                    round(hold_payoff_usd, 6)
                    if hold_payoff_usd is not None
                    else None
                ),
                "exit_vs_hold_incremental_usd": (
                    round(exit_vs_hold_usd, 6)
                    if exit_vs_hold_usd is not None
                    else None
                ),
                "post_exit_executable_depth_observations": len(executable_path),
                "observed_post_exit_peak_executable_bid_vwap": observed_peak,
                "observed_peak_miss_usd_lower_bound": (
                    round(max(0.0, observed_peak - fill_price) * fill_shares, 6)
                    if observed_peak is not None
                    else None
                ),
                "peak_proof_status": (
                    "OBSERVED_PATH_LOWER_BOUND_NOT_COMPLETE_PEAK_PROOF"
                    if executable_path
                    else "UNPROVEN_NO_POST_EXIT_EXECUTABLE_DEPTH"
                ),
                "global_auction_decision_log_id": receipt.decision_log_id,
                "global_auction_receipt_hash": receipt.receipt_hash,
                "global_selection_epoch_identity": receipt.selection_epoch_identity,
            }
        )

    graded = [
        row for row in exact_rows
        if row.get("exit_vs_hold_incremental_usd") is not None
    ]
    exit_vs_hold = sum(
        float(row["exit_vs_hold_incremental_usd"]) for row in graded
    )
    return {
        "artifact_role": "EXIT_VS_HOLD_SETTLEMENT_GRADE_AND_POST_EXIT_PATH_AUDIT",
        "contributes_to_admission": False,
        "status": (
            "settlement_graded_positive"
            if graded and len(graded) == len(exact_rows) and exit_vs_hold > 0.0
            else "settlement_graded_nonpositive"
            if graded and len(graded) == len(exact_rows)
            else "awaiting_verified_settlement"
            if exact_rows
            else "awaiting_exact_global_exit_fills"
        ),
        "global_selection_revision": CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
        "exact_global_exit_fill_count": len(exact_rows),
        "settlement_graded_exit_count": len(graded),
        "exit_vs_hold_incremental_usd": (
            round(exit_vs_hold, 6) if graded else None
        ),
        "complete_peak_proof_count": 0,
        "proof_warning": (
            "ENTRY_TO_EXIT_PNL_IS_ACCOUNTING_ONLY; EXIT_CORRECTNESS_REQUIRES_"
            "VERIFIED_BINARY_HOLD_PAYOFF; OBSERVED_BIDS_ARE_ONLY_A_PEAK_LOWER_BOUND"
        ),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "curve": exact_rows,
    }


def _held_to_binary_settlement_quality(
    conn: sqlite3.Connection,
    forecasts: sqlite3.Connection,
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Grade schema-22 globally compared HOLD decisions at binary settlement."""

    latest_by_position: dict[str, dict[str, object]] = {}
    rejection_counts: dict[str, int] = {}
    cutoff = (as_of - timedelta(days=WINDOW_DAYS)).isoformat()
    max_decision_log_id = int(
        conn.execute("SELECT COALESCE(MAX(id),0) FROM decision_log").fetchone()[0]
    )
    minimum_decision_log_id = max(
        0,
        max_decision_log_id - GLOBAL_HOLD_RECEIPT_SCAN_ROWS,
    )
    rows = conn.execute(
        "SELECT id,mode,artifact_json FROM decision_log "
        "WHERE id>=? AND timestamp>=? "
        "AND mode IN (?,?,?) "
        "AND json_extract(artifact_json,'$.summary.schema_version')=22 "
        "AND json_extract(artifact_json,'$.summary.global_selection_revision')=? "
        "AND json_extract(artifact_json,'$.summary.holding_auction_coverage_zlib_b64') "
        "IS NOT NULL ORDER BY id",
        (
            minimum_decision_log_id,
            cutoff,
            *GLOBAL_AUCTION_RECEIPT_MODES,
            CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
        ),
    ).fetchall()
    for decision_log_id, mode, artifact_json in rows:
        try:
            artifact = json.loads(str(artifact_json or ""))
            summary = artifact["summary"]
            if not isinstance(summary, Mapping):
                raise ValueError("global summary unavailable")
            assert_global_auction_summary_integrity(summary)
            compressed = base64.b64decode(
                str(summary["holding_auction_coverage_zlib_b64"]),
                validate=True,
            )
            raw = zlib.decompress(compressed)
            if hashlib.sha256(raw).hexdigest() != str(
                summary["holding_auction_coverage_sha256"]
            ):
                raise ValueError("holding coverage hash mismatch")
            coverage = json.loads(raw)
            if not isinstance(coverage, list):
                raise ValueError("holding coverage is not a list")
            winner_candidate_id = str(
                summary.get("winner_candidate_id") or ""
            ).strip()
            for item in coverage:
                if not isinstance(item, Mapping) or item.get("status") != "EVALUATED":
                    continue
                position_id = str(item.get("position_id") or "").strip()
                candidate_ids = {
                    str(value or "").strip()
                    for value in item.get("candidate_ids") or ()
                    if str(value or "").strip()
                }
                decision_at = _parse_aware(item.get("decision_at_utc"))
                if not position_id or not candidate_ids:
                    raise ValueError("evaluated holding identity incomplete")
                if winner_candidate_id in candidate_ids:
                    continue
                prior = latest_by_position.get(position_id)
                if prior is None or decision_at > prior["decision_at"]:
                    latest_by_position[position_id] = {
                        "decision_at": decision_at,
                        "decision_log_id": int(decision_log_id),
                        "decision_log_mode": str(mode),
                        "receipt_hash": str(summary.get("receipt_hash") or ""),
                        "selection_epoch_identity": str(
                            summary.get("selection_epoch_identity") or ""
                        ),
                        "held_shares": float(item.get("held_shares")),
                        "side": str(item.get("side") or "").strip().upper(),
                        "condition_id": str(
                            item.get("condition_id") or ""
                        ).strip(),
                        "candidate_ids": sorted(candidate_ids),
                    }
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            zlib.error,
        ) as exc:
            reason = str(exc) or type(exc).__name__
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    graded: list[dict[str, object]] = []
    awaiting = 0
    for position_id, hold in latest_by_position.items():
        position = conn.execute(
            "SELECT phase,city,target_date,temperature_metric,condition_id,shares,"
            "settled_at FROM position_current WHERE position_id=?",
            (position_id,),
        ).fetchone()
        if position is None or str(position[0]) != "settled" or not position[6]:
            awaiting += 1
            continue
        decision_at = hold["decision_at"]
        settled_at = _parse_aware(position[6])
        if not decision_at < settled_at:
            continue
        if conn.execute(
            "SELECT 1 FROM position_events WHERE position_id=? "
            "AND event_type='EXIT_ORDER_FILLED' AND datetime(occurred_at)>datetime(?) "
            "LIMIT 1",
            (position_id, decision_at.isoformat()),
        ).fetchone():
            continue
        shares = float(position[5])
        if (
            not math.isfinite(shares)
            or shares <= 0.0
            or abs(shares - float(hold["held_shares"])) > 1e-6
        ):
            continue
        city = str(position[1] or "").strip()
        target_date = str(position[2] or "").strip()
        metric = str(position[3] or "").strip().lower()
        condition_id = str(position[4] or "").strip()
        if condition_id != hold["condition_id"] or hold["side"] not in {"YES", "NO"}:
            continue
        try:
            settlement = _verified_settlement(
                forecasts,
                city=city,
                target_date=target_date,
                metric=metric,
                decision_at=decision_at,
            )
            condition_yes = _condition_resolved_yes(
                forecasts,
                condition_id=condition_id,
                city=city,
                target_date=target_date,
                metric=metric,
                settlement_value=_decimal(
                    settlement["settlement_value"], "settlement_value"
                ),
                settlement_unit=str(settlement["settlement_unit"]),
            )
        except (TypeError, ValueError):
            continue
        token_won = condition_yes if hold["side"] == "YES" else not condition_yes
        graded.append(
            {
                "position_id": position_id,
                "city": city,
                "target_date": target_date,
                "metric": metric,
                "condition_id": condition_id,
                "held_outcome": hold["side"],
                "held_shares": shares,
                "hold_decision_at": decision_at.isoformat(),
                "settled_at": settled_at.isoformat(),
                "binary_payoff": 1 if token_won else 0,
                "settlement_payoff_usd": round(shares if token_won else 0.0, 6),
                "result": "HELD_TO_ONE" if token_won else "HELD_TO_ZERO",
                "global_auction_decision_log_id": hold["decision_log_id"],
                "global_auction_receipt_hash": hold["receipt_hash"],
                "global_selection_epoch_identity": hold[
                    "selection_epoch_identity"
                ],
                "evaluated_sell_candidate_ids": hold["candidate_ids"],
            }
        )
    wins = sum(row["result"] == "HELD_TO_ONE" for row in graded)
    return {
        "artifact_role": "GLOBAL_HOLD_DECISION_TO_VERIFIED_BINARY_SETTLEMENT",
        "contributes_to_admission": False,
        "status": "graded" if graded else "awaiting_exact_settled_hold_decisions",
        "global_selection_revision": CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
        "settlement_graded_hold_count": len(graded),
        "held_to_one_count": wins,
        "held_to_zero_count": len(graded) - wins,
        "held_to_one_rate": round(wins / len(graded), 6) if graded else None,
        "awaiting_settlement_position_count": awaiting,
        "receipt_scan_row_limit": GLOBAL_HOLD_RECEIPT_SCAN_ROWS,
        "minimum_scanned_decision_log_id": minimum_decision_log_id,
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "curve": sorted(graded, key=lambda row: (row["settled_at"], row["position_id"])),
    }


def _build_counterfactual_admission_verdict(
    *,
    receipt: dict[str, object],
    shadows: dict[str, dict[str, object]],
) -> tuple[str, list[str]]:
    failures: list[str] = []
    if receipt.get("ready") is not True:
        failures.append("CURRENT_GLOBAL_SELECTION_RECEIPT_UNPROVEN")
    independent = sum(
        int(row.get("independent_family_day_count") or 0)
        for row in shadows.values()
        if row.get("global_selection_revision_bound") is True
    )
    if independent < MIN_INDEPENDENT_FAMILY_DAYS:
        failures.append("INSUFFICIENT_CURRENT_REGIME_SETTLED_FAMILY_DAYS")
    lcbs = [
        row.get("delta_log_wealth_lcb95")
        for row in shadows.values()
        if row.get("global_selection_revision_bound") is True
    ]
    if not lcbs or any(
        value is None or not math.isfinite(float(value)) or float(value) <= 0.0
        for value in lcbs
    ):
        failures.append("AFTER_COST_DELTA_LOG_WEALTH_LCB_NOT_POSITIVE")
    return ("PASS" if not failures else "FAIL", failures)


def _build_verdict(
    *,
    receipt: dict[str, object],
    shadows: dict[str, dict[str, object]],
    live_curves: dict[str, dict[str, object]],
) -> tuple[str, list[str]]:
    _, failures = _build_counterfactual_admission_verdict(
        receipt=receipt,
        shadows=shadows,
    )
    exact_live = [
        row for row in live_curves.values()
        if row.get("selection_revision_bound") is True
        and row.get("status") != "capital_truth_degraded"
        and row.get("net_realized_pnl_usd") is not None
    ]
    realized_count = sum(
        int(row.get("realized_position_count") or 0) for row in exact_live
    )
    if realized_count < MIN_EXACT_LIVE_REALIZED_POSITIONS:
        failures.append("INSUFFICIENT_EXACT_REVISION_LIVE_REALIZED_POSITIONS")
    live_net_pnl = sum(
        float(row.get("net_realized_pnl_usd") or 0.0) for row in exact_live
    )
    live_capital = sum(
        float(row.get("realized_capital_committed_usd") or 0.0)
        for row in exact_live
    )
    if live_capital <= 0.0 or live_net_pnl / live_capital <= 0.0:
        failures.append("EXACT_REVISION_LIVE_CAPITAL_WEIGHTED_RETURN_NOT_POSITIVE")
    return ("PASS" if not failures else "FAIL", failures)


def evaluate(
    *,
    world_path: Path,
    forecasts_path: Path,
    trades_path: Path,
    as_of: datetime,
) -> dict[str, object]:
    trades = _read_only(
        trades_path,
        frozenset(
            {
                "decision_log",
                "venue_commands",
                "venue_submission_envelopes",
                "execution_fact",
                "execution_feasibility_evidence",
                "position_events",
                "position_current",
            }
        ),
        connection_factory=get_trade_connection_read_only,
    )
    forecasts = _read_only(
        forecasts_path,
        frozenset({"settlement_outcomes", "market_events"}),
        connection_factory=get_forecasts_connection_read_only,
    )

    try:
        receipt = _latest_proof_receipt_coverage(trades)
        shadows = {
            "combined_current_global_selection": (
                _settled_global_counterfactual_evidence(
                    trades,
                    forecasts,
                    as_of=as_of,
                )
            )
        }
        raw_live_curves = {
            "day0_nowcast_entry": _realized_curve_with_deadline(
                    trades,
                    evaluator=rg._day0_live_realized_capital_curve,
                    as_of=as_of,
                ),
            "forecast_qkernel_entry": _realized_curve_with_deadline(
                    trades,
                    evaluator=rg._qkernel_live_realized_capital_curve,
                    as_of=as_of,
                ),
        }
        live_curves = {
            strategy: (
                _bind_live_curve_to_global_revision(trades, curve)
                if curve.get("status") != "capital_truth_degraded"
                else {**curve, "selection_revision_bound": False}
            )
            for strategy, curve in raw_live_curves.items()
        }
        selected_exit_quality = _globally_selected_exit_quality(
            trades,
            forecasts,
            raw_live_curves,
            as_of=as_of,
        )
        held_settlement_quality = _held_to_binary_settlement_quality(
            trades,
            forecasts,
            as_of=as_of,
        )
    finally:
        forecasts.close()
        trades.close()
    verdict, failures = _build_verdict(
        receipt=receipt,
        shadows=shadows,
        live_curves=live_curves,
    )
    admission_verdict, admission_failures = (
        _build_counterfactual_admission_verdict(
            receipt=receipt,
            shadows=shadows,
        )
    )
    return {
        "schema_version": 1,
        "artifact_role": "OBSERVATIONAL_EVIDENCE_NOT_ORDER_AUTHORITY",
        "evaluated_at": as_of.isoformat(),
        "verdict": verdict,
        "admission_eligible": admission_verdict == "PASS",
        "admission_verdict": admission_verdict,
        "admission_failures": admission_failures,
        "failures": failures,
        "contract": {
            "admission_requires_live_realized_positions": False,
            "goal_completion_requires_live_realized_positions": True,
            "minimum_independent_family_days": MIN_INDEPENDENT_FAMILY_DAYS,
            "minimum_exact_live_realized_positions": (
                MIN_EXACT_LIVE_REALIZED_POSITIONS
            ),
            "delta_log_wealth_lcb95_must_exceed": 0.0,
            "live_capital_weighted_return_must_exceed": 0.0,
            "absolute_small_dollar_pnl_is_not_advantage_proof": True,
            "global_selection_revision": (
                CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "probability_semantics_revisions": {
                "day0_nowcast_entry": DAY0_PROBABILITY_SEMANTICS_REVISION,
                "forecast_qkernel_entry": CURRENT_EVIDENCE_SEMANTICS_REVISION,
            },
        },
        "database_paths": {
            "world": str(world_path.resolve()),
            "forecasts": str(forecasts_path.resolve()),
            "trades": str(trades_path.resolve()),
        },
        "latest_global_receipt": receipt,
        "settled_counterfactuals": shadows,
        "live_realized_capital": live_curves,
        "globally_selected_exit_quality": selected_exit_quality,
        "globally_compared_hold_settlement_quality": held_settlement_quality,
    }


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--forecasts", type=Path, required=True)
    parser.add_argument("--world", type=Path)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    world = args.world or args.trades.with_name("zeus-world.db")
    as_of = datetime.now(timezone.utc)
    try:
        artifact = evaluate(
            world_path=world,
            forecasts_path=args.forecasts,
            trades_path=args.trades,
            as_of=as_of,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        artifact = {
            "schema_version": 1,
            "artifact_role": "OBSERVATIONAL_EVIDENCE_NOT_ORDER_AUTHORITY",
            "evaluated_at": as_of.isoformat(),
            "verdict": "FAIL",
            "admission_eligible": False,
            "admission_verdict": "FAIL",
            "admission_failures": [
                f"CAPITAL_TRUTH_UNAVAILABLE:{type(exc).__name__}:{exc}"
            ],
            "failures": [f"CAPITAL_TRUTH_UNAVAILABLE:{type(exc).__name__}:{exc}"],
        }
    _atomic_write(args.artifact, artifact)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
