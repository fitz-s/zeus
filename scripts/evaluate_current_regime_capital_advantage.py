#!/usr/bin/env python3
# Lifecycle: created=2026-08-12; last_reviewed=2026-08-12; last_reused=2026-08-12
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
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
import time
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
        _summary_proof(summary)
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


def _summary_proof(summary: Mapping[str, object]) -> Mapping[str, object]:
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
        if proof.get(field) != summary.get(field):
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
            _summary_proof(summary)
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
    forecasts: sqlite3.Connection,
    *,
    decision_log_id: int,
    summary: Mapping[str, object],
) -> dict[str, object]:
    proof = _summary_proof(summary)
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
    evaluation = winner.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise ValueError("proof winner evaluation missing")
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
        receipt = GlobalAuctionReceiptRef.from_payload(raw_receipt)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("EDLI global receipt invalid") from exc
    if receipt.schema_version != 22:
        raise ValueError("EDLI global receipt is not schema 22")
    row = conn.execute(
        "SELECT mode,artifact_json FROM decision_log WHERE id=?",
        (receipt.decision_log_id,),
    ).fetchone()
    if row is None:
        raise ValueError("EDLI global receipt row missing")
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
        raise ValueError("EDLI global receipt selection revision mismatch")
    return receipt


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


def _build_verdict(
    *,
    receipt: dict[str, object],
    shadows: dict[str, dict[str, object]],
    live_curves: dict[str, dict[str, object]],
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
    exact_live = [
        row for row in live_curves.values()
        if row.get("selection_revision_bound") is True
        and row.get("status") != "capital_truth_degraded"
        and row.get("net_realized_pnl_usd") is not None
    ]
    if not exact_live or sum(
        float(row.get("net_realized_pnl_usd") or 0.0) for row in exact_live
    ) <= 0.0:
        failures.append("EXACT_REVISION_LIVE_NET_CAPITAL_GAIN_NOT_POSITIVE")
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
    finally:
        forecasts.close()
        trades.close()
    verdict, failures = _build_verdict(
        receipt=receipt,
        shadows=shadows,
        live_curves=live_curves,
    )
    return {
        "schema_version": 1,
        "artifact_role": "OBSERVATIONAL_EVIDENCE_NOT_ORDER_AUTHORITY",
        "evaluated_at": as_of.isoformat(),
        "verdict": verdict,
        "admission_eligible": verdict == "PASS",
        "failures": failures,
        "contract": {
            "minimum_independent_family_days": MIN_INDEPENDENT_FAMILY_DAYS,
            "delta_log_wealth_lcb95_must_exceed": 0.0,
            "live_net_realized_pnl_must_exceed_usd": 0.0,
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
            "failures": [f"CAPITAL_TRUTH_UNAVAILABLE:{type(exc).__name__}:{exc}"],
        }
    _atomic_write(args.artifact, artifact)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
