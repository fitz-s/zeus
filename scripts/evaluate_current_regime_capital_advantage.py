#!/usr/bin/env python3
# Lifecycle: created=2026-08-12; last_reviewed=2026-09-01; last_reused=2026-09-01
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
import fcntl
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
from typing import Callable, Mapping, Sequence

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
from src.contracts.venue_submission_envelope import (  # noqa: E402
    LIVE_ORDER_MAX_UNIT_PRICE,
    LIVE_ORDER_MIN_UNIT_PRICE,
)
from src.events.day0_authority import (  # noqa: E402
    DAY0_PROBABILITY_SEMANTICS_REVISION,
)
from src.riskguard import riskguard as rg  # noqa: E402
from src.state.db import (  # noqa: E402
    get_forecasts_connection_read_only,
    get_trade_connection_read_only,
    get_world_connection_read_only,
)
from src.state.fill_dedup import (  # noqa: E402
    canonical_trade_fact_cte,
    economic_trade_fact_cte,
)
from src.types.market import Bin  # noqa: E402
from src.data.replacement_forecast_cycle_policy import (  # noqa: E402
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS,
    STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
)

MIN_INDEPENDENT_TARGET_DATES = 30
MIN_EXACT_LIVE_REALIZED_POSITIONS = 30
GLOBAL_HOLD_RECEIPT_SCAN_ROWS = 5_000
WINDOW_DAYS = 35.0
CURRENT_CAPITAL_TRUTH_MAX_AGE_SECONDS = 180.0
PORTFOLIO_OBSERVATION_MAX_POINTS = 2_048
GLOBAL_AUCTION_RECEIPT_MODES = (
    "global_single_order_auction",
    "global_single_order_auction_delta",
    "global_single_order_auction_duplicate",
)
PROOF_ROLE = "SIDE_EFFECT_FREE_CAPITAL_COUNTERFACTUAL"
CONSERVATIVE_ONE_SIDED_T95_DF29 = 1.699
CURRENT_PROBABILITY_SEMANTICS = frozenset(
    {DAY0_PROBABILITY_SEMANTICS_REVISION}
).union(
    LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS
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
        != "SETTLEMENT_LOCKED_BUY"
        or float(evaluation.get("fill_probability") or 0.0) != 1.0
        or str(evaluation.get("fill_probability_source") or "").strip()
        != "immediate_taker"
    ):
        raise ValueError("proof winner taker execution certificate invalid")
    decision_at = _parse_aware(proof.get("decision_at_utc"))
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
    shares = _decimal(winner.get("shares"), "winner_shares")
    cost = _decimal(winner.get("cost_usd"), "winner_cost")
    tolerance = Decimal("0.000001")
    if (
        loss_payoff >= 0
        or win_payoff <= 0
        or shares <= 0
        or loss_before <= 0
        or win_before <= 0
        or abs(cost + loss_payoff) > tolerance
        or abs((win_payoff - loss_payoff) - shares) > tolerance
    ):
        raise ValueError("proof winner after-cost terminal wealth inconsistent")
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
    payoff = win_payoff if token_won else loss_payoff
    wealth_after = win_wealth if token_won else loss_wealth
    wealth_before = win_before if token_won else loss_before
    delta_log = math.log(float(wealth_after / wealth_before))
    if not math.isfinite(delta_log):
        raise ValueError("proof winner realized delta-log wealth invalid")
    return {
        "decision_log_id": decision_log_id,
        "proof_counterfactual_sha256": str(
            summary["proof_counterfactual_sha256"]
        ),
        "family": [city, target_date, metric],
        "independence_key": target_date,
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


def _proof_registry_entry(
    trades: sqlite3.Connection,
    forecasts: sqlite3.Connection,
    *,
    decision_log_id: int,
    summary: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Return one causal proof ref and its settlement grade when available."""

    try:
        sample = _realized_proof_sample(
            trades,
            forecasts,
            decision_log_id=decision_log_id,
            summary=summary,
        )
    except ValueError as exc:
        if str(exc) != "unique VERIFIED settlement unavailable":
            raise
        proof = _summary_proof(trades, decision_log_id, summary)
        winner = proof.get("winner")
        if not isinstance(winner, Mapping):
            raise ValueError("proof winner unavailable") from exc
        target_date = str(winner.get("target_date") or "").strip()
        decision_at = _parse_aware(proof.get("decision_at_utc"))
        if not target_date:
            raise ValueError("proof target date unavailable") from exc
        sample = None
    else:
        target_date = str(sample["independence_key"])
        decision_at = _parse_aware(sample["decision_at_utc"])
    ref = {
        "decision_log_id": decision_log_id,
        "proof_counterfactual_sha256": str(
            summary["proof_counterfactual_sha256"]
        ),
        "independence_key": target_date,
        "decision_at_utc": decision_at.isoformat(),
    }
    return ref, sample


def _settled_global_counterfactual_evidence(
    trades: sqlite3.Connection,
    forecasts: sqlite3.Connection,
    *,
    as_of: datetime,
    prior_proof_registry: Sequence[Mapping[str, object]] = (),
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
    registry_by_target_date: dict[str, dict[str, object]] = {}
    rejection_counts: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    def admit(
        *,
        decision_log_id: int,
        summary: Mapping[str, object],
        expected_ref: Mapping[str, object] | None = None,
    ) -> None:
        ref, sample = _proof_registry_entry(
            trades,
            forecasts,
            decision_log_id=decision_log_id,
            summary=summary,
        )
        if expected_ref is not None and any(
            str(expected_ref.get(field) or "") != str(ref[field])
            for field in (
                "decision_log_id",
                "proof_counterfactual_sha256",
                "independence_key",
                "decision_at_utc",
            )
        ):
            raise ValueError("retained proof registry identity mismatch")
        decision_at = _parse_aware(ref["decision_at_utc"])
        if not _parse_aware(cutoff) <= decision_at <= as_of:
            raise ValueError("proof decision outside current evidence window")
        target_date = str(ref["independence_key"])
        if target_date in registry_by_target_date:
            reject("duplicate_target_date")
            return
        registry_by_target_date[target_date] = ref
        if sample is not None:
            samples.append(sample)

    def retained_order(item: Mapping[str, object]) -> int:
        try:
            return int(item.get("decision_log_id") or 0)
        except (TypeError, ValueError):
            return sys.maxsize

    for raw_ref in sorted(prior_proof_registry, key=retained_order):
        try:
            if not isinstance(raw_ref, Mapping):
                raise ValueError("retained proof registry row invalid")
            decision_log_id = int(raw_ref.get("decision_log_id") or 0)
            row = trades.execute(
                "SELECT mode,artifact_json FROM decision_log WHERE id=?",
                (decision_log_id,),
            ).fetchone()
            if (
                row is None
                or str(row["mode"] or "") not in GLOBAL_AUCTION_RECEIPT_MODES
            ):
                raise ValueError("retained proof receipt unavailable")
            artifact = json.loads(str(row["artifact_json"] or ""))
            summary = artifact["summary"]
            if not isinstance(summary, Mapping):
                raise ValueError("retained proof summary invalid")
            admit(
                decision_log_id=decision_log_id,
                summary=summary,
                expected_ref=raw_ref,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            reject(str(exc) or type(exc).__name__)

    for row in rows:
        try:
            artifact = json.loads(str(row["artifact_json"] or ""))
            summary = artifact["summary"]
            if not isinstance(summary, Mapping):
                raise ValueError("proof summary invalid")
            admit(
                decision_log_id=int(row["id"]),
                summary=summary,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            reject(str(exc) or type(exc).__name__)
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
        "independent_target_date_count": len(samples),
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
        "minimum_sample_gate": MIN_INDEPENDENT_TARGET_DATES,
        "proof_registry_role": (
            "CURRENT_WINDOW_FIRST_ELIGIBLE_TARGET_DATE_PROOF_"
            "REVALIDATED_FROM_CANONICAL_RECEIPT"
        ),
        "proof_registry_target_date_count": len(registry_by_target_date),
        "proof_registry": [
            registry_by_target_date[target_date]
            for target_date in sorted(registry_by_target_date)
        ],
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
    events_conn: sqlite3.Connection | None = None,
) -> GlobalAuctionReceiptRef:
    event_source = events_conn or conn
    rows = event_source.execute(
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
    *,
    events_conn: sqlite3.Connection | None = None,
) -> dict[str, object]:
    exact_rows: list[dict[str, object]] = []
    unbound_reasons: dict[str, int] = {}
    for raw in tuple(curve.get("curve") or ()):
        row = dict(raw)
        position_id = str(row.get("position_id") or "").strip()
        commands = conn.execute(
            "SELECT DISTINCT command_id,decision_id FROM venue_commands "
            "WHERE position_id=? AND intent_kind='ENTRY' ORDER BY decision_id",
            (position_id,),
        ).fetchall()
        try:
            if not commands:
                raise ValueError("entry command missing")
            command_receipts = [
                (
                    str(command[0] or ""),
                    str(command[1] or ""),
                    _command_global_receipt(
                    conn,
                        execution_command_id=str(command[1] or ""),
                        events_conn=events_conn,
                    ),
                )
                for command in commands
            ]
        except ValueError as exc:
            reason = str(exc)
            unbound_reasons[reason] = unbound_reasons.get(reason, 0) + 1
            continue
        receipts = [item[2] for item in command_receipts]
        first_receipt = min(receipts, key=lambda item: item.decision_log_id)
        epoch_identities = sorted(
            {receipt.selection_epoch_identity for receipt in receipts}
        )
        bindings = [
            {
                "venue_command_id": command_id,
                "execution_command_id": execution_command_id,
                "global_auction_decision_log_id": receipt.decision_log_id,
                "global_auction_receipt_hash": receipt.receipt_hash,
                "global_selection_epoch_identity": (
                    receipt.selection_epoch_identity
                ),
            }
            for command_id, execution_command_id, receipt in command_receipts
        ]
        exact_rows.append(
            {
                **row,
                "global_selection_revision": (
                    CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                ),
                "global_auction_decision_log_id": (
                    first_receipt.decision_log_id
                ),
                "global_auction_receipt_hash": (
                    first_receipt.receipt_hash
                    if len(bindings) == 1
                    else None
                ),
                "global_selection_epoch_identity": (
                    epoch_identities[0]
                    if len(epoch_identities) == 1
                    else None
                ),
                "global_auction_receipt_count": len(bindings),
                "global_selection_epoch_identities": epoch_identities,
                "global_auction_receipts": bindings,
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


def _banded_bid_liquidation(
    depth_json: object,
    shares: object,
) -> dict[str, object]:
    """Value the immediately sellable prefix under the live unit-price law."""

    try:
        quantity = float(shares)
        raw_bids = json.loads(str(depth_json or ""))["bids"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"status": "BOOK_DEPTH_UNAVAILABLE"}
    if (
        not math.isfinite(quantity)
        or quantity <= 0.0
        or not isinstance(raw_bids, list)
    ):
        return {"status": "POSITION_SIZE_INVALID"}
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
            return {"status": "BOOK_DEPTH_INVALID"}
        if (
            not math.isfinite(price)
            or not math.isfinite(size)
            or not 0.0 < price < 1.0
            or size <= 0.0
        ):
            return {"status": "BOOK_DEPTH_INVALID"}
        levels.append((price, size))
    if not levels:
        return {"status": "NO_BID_DEPTH"}
    levels.sort(reverse=True)
    best_bid = Decimal(str(levels[0][0]))
    if not LIVE_ORDER_MIN_UNIT_PRICE <= best_bid <= LIVE_ORDER_MAX_UNIT_PRICE:
        return {
            "status": "BEST_BID_OUTSIDE_LIVE_SUBMIT_BAND",
            "best_bid": float(best_bid),
            "executable_prefix_shares": 0.0,
            "executable_prefix_gross_usd": 0.0,
            "full_position_executable": False,
        }
    remaining = quantity
    proceeds = 0.0
    filled = 0.0
    for price, available in levels:
        if Decimal(str(price)) < LIVE_ORDER_MIN_UNIT_PRICE:
            break
        taken = min(remaining, available)
        proceeds += taken * price
        filled += taken
        remaining -= taken
        if remaining <= 1e-9:
            break
    full = remaining <= 1e-9
    return {
        "status": "FULL_POSITION_EXECUTABLE" if full else "PREFIX_ONLY_EXECUTABLE",
        "best_bid": float(best_bid),
        "executable_prefix_shares": round(filled, 9),
        "executable_prefix_gross_usd": round(proceeds, 9),
        "full_position_executable": full,
        "full_position_vwap": round(proceeds / quantity, 9) if full else None,
    }


def _order_capital_ledger(
    conn: sqlite3.Connection,
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Reproduce every current-window venue command and its exact cash flow."""

    cutoff = as_of - timedelta(days=WINDOW_DAYS)
    canonical_facts: dict[str, list[dict[str, object]]] = {}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='venue_trade_facts'"
    ).fetchone():
        source_scope = (
            "JOIN venue_commands scope ON scope.command_id=fact.command_id "
            "WHERE datetime(scope.created_at)>=datetime(?) "
            "AND datetime(scope.created_at)<=datetime(?) "
            "AND datetime(fact.observed_at)<=datetime(?)"
        )
        trade_rows = conn.execute(
            f"WITH {canonical_trade_fact_cte(source_clause_sql=source_scope)},"
            f"{economic_trade_fact_cte()} "
            "SELECT command_id,trade_id,filled_size,fill_price,state,execution_ts "
            "FROM economic_trade_fact "
            "WHERE UPPER(COALESCE(state,'')) IN ('MATCHED','MINED','CONFIRMED') "
            "AND CAST(COALESCE(filled_size,'0') AS REAL)>0 "
            "AND CAST(COALESCE(fill_price,'0') AS REAL)>0 "
            "ORDER BY command_id,execution_ts,trade_id",
            (cutoff.isoformat(), as_of.isoformat(), as_of.isoformat()),
        ).fetchall()
        for row in trade_rows:
            command_id = str(row["command_id"] or "").strip()
            canonical_facts.setdefault(command_id, []).append(
                {
                    "fact_id": str(row["trade_id"] or ""),
                    "fill_price": row["fill_price"],
                    "fill_shares": row["filled_size"],
                    "filled_at": str(row["execution_ts"] or ""),
                    "terminal_exec_status": str(row["state"] or ""),
                }
            )
    rows = conn.execute(
        "SELECT vc.command_id,vc.position_id,vc.decision_id,vc.intent_kind,"
        "vc.side,vc.size,vc.price,vc.state,vc.created_at,vc.updated_at,"
        "vc.envelope_id,vc.venue_order_id,vse.outcome_label,vse.post_only,"
        "vse.fee_details_json,"
        "ef.intent_id AS fact_id,ef.order_role,ef.fill_price,ef.shares AS fill_shares,"
        "ef.filled_at,ef.terminal_exec_status "
        "FROM venue_commands AS vc "
        "LEFT JOIN venue_submission_envelopes AS vse "
        "ON vse.envelope_id=vc.envelope_id "
        "LEFT JOIN execution_fact AS ef ON ef.command_id=vc.command_id "
        "AND (ef.filled_at IS NULL OR datetime(ef.filled_at)<=datetime(?)) "
        "WHERE datetime(vc.created_at)>=datetime(?) "
        "AND datetime(vc.created_at)<=datetime(?) "
        "ORDER BY datetime(vc.created_at),vc.command_id,datetime(ef.filled_at),ef.intent_id",
        (as_of.isoformat(), cutoff.isoformat(), as_of.isoformat()),
    ).fetchall()
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        command_id = str(row["command_id"] or "").strip()
        item = grouped.setdefault(
            command_id,
            {
                "command_id": command_id,
                "position_id": str(row["position_id"] or ""),
                "decision_id": str(row["decision_id"] or ""),
                "venue_order_id": str(row["venue_order_id"] or ""),
                "intent_kind": str(row["intent_kind"] or ""),
                "side": str(row["side"] or ""),
                "outcome_label": str(row["outcome_label"] or ""),
                "state": str(row["state"] or ""),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "requested_shares": row["size"],
                "requested_unit_price": row["price"],
                "requested_notional_usd": round(
                    float(row["size"] or 0.0) * float(row["price"] or 0.0),
                    9,
                ),
                "post_only": row["post_only"],
                "fee_details_json": row["fee_details_json"],
                "facts": [],
            },
        )
        if row["fact_id"] is not None:
            item["facts"].append(
                {
                    "fact_id": str(row["fact_id"]),
                    "order_role": str(row["order_role"] or "").lower(),
                    "fill_price": row["fill_price"],
                    "fill_shares": row["fill_shares"],
                    "filled_at": str(row["filled_at"] or ""),
                    "terminal_exec_status": str(
                        row["terminal_exec_status"] or ""
                    ),
                    "post_only": row["post_only"],
                    "fee_details_json": row["fee_details_json"],
                }
            )

    terminal_no_fill_states = {
        "CANCELLED",
        "EXPIRED",
        "REJECTED",
        "SUBMIT_REJECTED",
    }
    orders: list[dict[str, object]] = []
    incomplete_reasons: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    intent_state_counts: dict[str, int] = {}
    entry_outflow = 0.0
    exit_inflow = 0.0
    total_fees = 0.0
    realized_exit_accounting_pnl = 0.0
    gain_truth_incomplete = 0
    for item in grouped.values():
        execution_facts = tuple(item.pop("facts"))
        command_id = str(item["command_id"])
        authoritative_facts = tuple(canonical_facts.get(command_id, ()))
        facts = authoritative_facts or execution_facts
        if authoritative_facts:
            fill_truth_source = "CANONICAL_ECONOMIC_VENUE_TRADE_FACT"
        elif execution_facts:
            fill_truth_source = "EXECUTION_FACT"
        else:
            fill_truth_source = "NO_FILL_FACT"
        post_only = item.pop("post_only")
        fee_details_json = item.pop("fee_details_json")
        state = str(item["state"])
        intent = str(item["intent_kind"])
        state_counts[state] = state_counts.get(state, 0) + 1
        intent_state = f"{intent}:{state}"
        intent_state_counts[intent_state] = intent_state_counts.get(intent_state, 0) + 1
        reasons: list[str] = []
        gross = 0.0
        fees = 0.0
        filled_shares = 0.0
        fill_times: list[str] = []
        expected_role = intent.lower()
        for fact in facts:
            fact_role = str(fact.get("order_role") or expected_role)
            if fact_role != expected_role:
                reasons.append("ORDER_ROLE_MISMATCH")
                continue
            fee = rg._submission_schedule_fee_usd(
                post_only=post_only,
                fee_details_json=fee_details_json,
                fill_price=fact["fill_price"],
                shares=fact["fill_shares"],
            )
            try:
                price = float(fact["fill_price"])
                shares = float(fact["fill_shares"])
            except (TypeError, ValueError):
                reasons.append("FILL_VALUES_INVALID")
                continue
            if (
                fee is None
                or not all(math.isfinite(value) for value in (price, shares))
                or not 0.0 < price < 1.0
                or shares <= 0.0
            ):
                reasons.append("FILL_OR_FEE_UNAVAILABLE")
                continue
            gross += price * shares
            fees += fee
            filled_shares += shares
            if fact["filled_at"]:
                fill_times.append(str(fact["filled_at"]))
        if state == "FILLED" and not facts:
            reasons.append("FILLED_COMMAND_EXECUTION_FACT_MISSING")
        if not facts and state not in terminal_no_fill_states and state != "FILLED":
            reasons.append("NONTERMINAL_COMMAND_WITHOUT_CAPITAL_FACT")
        reasons = sorted(set(reasons))
        for reason in reasons:
            incomplete_reasons[reason] = incomplete_reasons.get(reason, 0) + 1
        if reasons:
            cash_flow = None
            effect = "CAPITAL_TRUTH_DEGRADED"
        elif not facts:
            cash_flow = 0.0
            effect = "ZERO_CAPITAL_EFFECT_NO_FILL"
        elif intent == "ENTRY":
            cash_flow = -(gross + fees)
            entry_outflow += -cash_flow
            total_fees += fees
            effect = "CAPITAL_COMMITTED_BY_FILL"
        elif intent == "EXIT":
            cash_flow = gross - fees
            exit_inflow += cash_flow
            total_fees += fees
            effect = "CAPITAL_RELEASED_BY_FILL"
        else:
            cash_flow = None
            effect = "CAPITAL_TRUTH_DEGRADED"
            reason = "INTENT_KIND_UNSUPPORTED"
            incomplete_reasons[reason] = incomplete_reasons.get(reason, 0) + 1
            reasons.append(reason)
        if not facts:
            realized_pnl = 0.0 if not reasons else None
            gain_status = (
                "ZERO_REALIZED_GAIN_NO_FILL"
                if realized_pnl is not None
                else "GAIN_TRUTH_DEGRADED"
            )
        elif intent == "ENTRY":
            realized_pnl = None
            gain_status = "ENTRY_ACQUISITION_NOT_REALIZED_GAIN"
        elif intent == "EXIT":
            raw_exit_events = conn.execute(
                "SELECT payload_json FROM position_events "
                "WHERE position_id=? AND event_type='EXIT_ORDER_FILLED' "
                "AND command_id=? AND datetime(occurred_at)<=datetime(?) "
                "ORDER BY sequence_no LIMIT 2",
                (
                    str(item["position_id"]),
                    str(item["command_id"]),
                    as_of.isoformat(),
                ),
            ).fetchall()
            exit_events: list[Mapping[str, object]] = []
            for event in raw_exit_events:
                try:
                    payload = json.loads(str(event["payload_json"] or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                exit_events.append(payload)
            raw_pnl = None
            if len(exit_events) == 1:
                raw_pnl = exit_events[0].get("pnl")
                if raw_pnl is None:
                    raw_pnl = exit_events[0].get("realized_pnl_usd")
            gain_status = "EXIT_ACCOUNTING_GAIN_UNAVAILABLE"
            if raw_pnl is None and str(item["venue_order_id"]):
                raw_partial_events = conn.execute(
                    "SELECT payload_json FROM position_events "
                    "WHERE position_id=? "
                    "AND caused_by IN "
                    "('partial_exit_fill','partial_exit_economics_repair') "
                    "AND datetime(occurred_at)<=datetime(?) "
                    "AND (command_id=? OR lower(COALESCE(order_id,''))=lower(?) "
                    "OR json_extract(payload_json,'$.command_id')=? "
                    "OR lower(COALESCE(json_extract(payload_json,'$.venue_order_id'),''))"
                    "=lower(?)) ORDER BY sequence_no,event_id",
                    (
                        str(item["position_id"]),
                        as_of.isoformat(),
                        command_id,
                        str(item["venue_order_id"]),
                        command_id,
                        str(item["venue_order_id"]),
                    ),
                ).fetchall()
                partial_deltas: list[float] = []
                partial_complete = bool(raw_partial_events)
                for event in raw_partial_events:
                    try:
                        payload = json.loads(str(event["payload_json"] or ""))
                        delta = float(payload["realized_pnl_delta_usd"])
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ):
                        partial_complete = False
                        continue
                    if not math.isfinite(delta):
                        partial_complete = False
                        continue
                    partial_deltas.append(delta)
                if partial_complete:
                    raw_pnl = sum(partial_deltas)
                    gain_status = "PARTIAL_EXIT_ACCOUNTING_GAIN_AFTER_EXIT_FEE"
                elif raw_partial_events:
                    gain_status = "LEGACY_PARTIAL_EXIT_GAIN_UNAVAILABLE"
            try:
                realized_pnl = float(raw_pnl) - fees
            except (TypeError, ValueError):
                realized_pnl = None
            if realized_pnl is None or not math.isfinite(realized_pnl):
                realized_pnl = None
            else:
                realized_exit_accounting_pnl += realized_pnl
                if gain_status != "PARTIAL_EXIT_ACCOUNTING_GAIN_AFTER_EXIT_FEE":
                    gain_status = "EXIT_ACCOUNTING_GAIN_AFTER_EXIT_FEE"
        else:
            realized_pnl = None
            gain_status = "GAIN_TRUTH_DEGRADED"
        if gain_status in {
            "GAIN_TRUTH_DEGRADED",
            "EXIT_ACCOUNTING_GAIN_UNAVAILABLE",
            "LEGACY_PARTIAL_EXIT_GAIN_UNAVAILABLE",
        }:
            gain_truth_incomplete += 1
        orders.append(
            {
                **item,
                "fill_fact_count": len(facts),
                "canonical_trade_fact_count": len(authoritative_facts),
                "execution_fact_count": len(execution_facts),
                "fill_truth_source": fill_truth_source,
                "filled_shares": round(filled_shares, 9),
                "filled_gross_notional_usd": round(gross, 9),
                "fee_usd": round(fees, 9) if not reasons else None,
                "after_cost_cash_flow_usd": (
                    round(cash_flow, 9) if cash_flow is not None else None
                ),
                "first_filled_at": min(fill_times) if fill_times else None,
                "last_filled_at": max(fill_times) if fill_times else None,
                "capital_effect": effect,
                "capital_truth_complete": not reasons,
                "capital_truth_failures": reasons,
                "realized_accounting_gain_after_exit_fee_usd": (
                    round(realized_pnl, 9) if realized_pnl is not None else None
                ),
                "gain_status": gain_status,
                "settlement_graded_gain": False,
            }
        )
    return {
        "artifact_role": "EVERY_VENUE_COMMAND_AFTER_COST_CASH_FLOW_LEDGER",
        "window_days": WINDOW_DAYS,
        "window_start_utc": cutoff.isoformat(),
        "evaluated_at": as_of.isoformat(),
        "command_count": len(orders),
        "capital_affecting_command_count": sum(
            float(row["filled_shares"] or 0.0) > 0.0 for row in orders
        ),
        "capital_truth_complete": not incomplete_reasons,
        "capital_truth_incomplete_command_count": sum(
            not row["capital_truth_complete"] for row in orders
        ),
        "incomplete_reasons": dict(sorted(incomplete_reasons.items())),
        "state_counts": dict(sorted(state_counts.items())),
        "intent_state_counts": dict(sorted(intent_state_counts.items())),
        "filled_entry_after_cost_outflow_usd": round(entry_outflow, 9),
        "filled_exit_after_cost_inflow_usd": round(exit_inflow, 9),
        "submission_schedule_fee_usd": round(total_fees, 9),
        "realized_exit_accounting_gain_after_exit_fee_usd": round(
            realized_exit_accounting_pnl,
            9,
        ),
        "gain_truth_incomplete_command_count": gain_truth_incomplete,
        "net_venue_cash_flow_usd_not_profit": round(
            exit_inflow - entry_outflow,
            9,
        ),
        "warning": (
            "ORDER CASH FLOW IS NOT PROFIT; EXIT ACCOUNTING GAIN BECOMES "
            "OUTCOME-CORRECT ONLY THROUGH THE SEPARATE VERIFIED SETTLEMENT GRADE"
        ),
        "orders": orders,
    }


def _current_total_portfolio_capital(
    conn: sqlite3.Connection,
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Bracket total capital from Chain cash and exact current held tokens."""

    collateral = conn.execute(
        "SELECT id,pusd_balance_micro,reserved_pusd_for_buys_micro,captured_at,"
        "authority_tier FROM collateral_ledger_snapshots "
        "WHERE datetime(captured_at)<=datetime(?) "
        "ORDER BY datetime(captured_at) DESC,id DESC LIMIT 1",
        (as_of.isoformat(),),
    ).fetchone()
    if collateral is None:
        return {"ready": False, "reason": "CHAIN_COLLATERAL_SNAPSHOT_MISSING"}
    try:
        collateral_at = _parse_aware(collateral["captured_at"])
        collateral_age = (as_of - collateral_at).total_seconds()
        chain_cash = int(collateral["pusd_balance_micro"]) / 1_000_000.0
        snapshot_reserved = (
            int(collateral["reserved_pusd_for_buys_micro"]) / 1_000_000.0
        )
    except (TypeError, ValueError):
        return {"ready": False, "reason": "CHAIN_COLLATERAL_SNAPSHOT_INVALID"}
    reservation_row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM collateral_reservations "
        "WHERE released_at IS NULL AND reservation_type='PUSD_BUY'"
    ).fetchone()
    unsettled_row = conn.execute(
        "SELECT COALESCE(SUM(amount_micro),0) FROM collateral_unsettled_proceeds "
        "WHERE settled_at IS NULL"
    ).fetchone()
    active_reserved = int(reservation_row[0] or 0) / 1_000_000.0
    unsettled = int(unsettled_row[0] or 0) / 1_000_000.0
    reservation_match = math.isclose(
        snapshot_reserved,
        active_reserved,
        abs_tol=1e-6,
    )
    collateral_ready = bool(
        str(collateral["authority_tier"] or "") == "CHAIN"
        and 0.0 <= collateral_age <= CURRENT_CAPITAL_TRUTH_MAX_AGE_SECONDS
        and reservation_match
    )

    positions = conn.execute(
        "SELECT position_id,phase,city,target_date,temperature_metric,direction,"
        "chain_state,chain_shares,token_id,no_token_id FROM position_current "
        "WHERE phase IN ('active','day0_window','pending_exit') "
        "ORDER BY position_id"
    ).fetchall()
    position_rows: list[dict[str, object]] = []
    position_truth_failures: list[str] = []
    payoff_ceiling = 0.0
    executable_prefix_value = 0.0
    executable_prefix_shares = 0.0
    book_status_counts: dict[str, int] = {}
    for row in positions:
        position_id = str(row["position_id"] or "")
        direction = str(row["direction"] or "")
        try:
            shares = float(row["chain_shares"])
        except (TypeError, ValueError):
            shares = float("nan")
        raw_token = row["no_token_id"] if direction == "buy_no" else row["token_id"]
        selected_token = str(raw_token or "").strip()
        base = {
            "position_id": position_id,
            "phase": str(row["phase"] or ""),
            "city": str(row["city"] or ""),
            "target_date": str(row["target_date"] or ""),
            "metric": str(row["temperature_metric"] or ""),
            "direction": direction,
            "selected_token_id": selected_token,
            "chain_shares": shares if math.isfinite(shares) else None,
        }
        if (
            str(row["chain_state"] or "") == "synced"
            and math.isfinite(shares)
            and shares == 0.0
        ):
            status = "ZERO_CHAIN_SHARES_NO_CAPITAL"
            position_rows.append(
                {
                    **base,
                    "valuation_status": status,
                    "executable_prefix_shares": 0.0,
                    "executable_prefix_gross_usd_before_exit_fee": 0.0,
                    "full_position_executable": True,
                    "full_position_vwap": None,
                }
            )
            book_status_counts[status] = book_status_counts.get(status, 0) + 1
            continue
        if (
            str(row["chain_state"] or "") != "synced"
            or not math.isfinite(shares)
            or shares < 0.0
            or not selected_token
        ):
            status = "POSITION_CHAIN_TRUTH_INVALID"
            position_truth_failures.append(position_id)
            position_rows.append({**base, "valuation_status": status})
            book_status_counts[status] = book_status_counts.get(status, 0) + 1
            continue
        payoff_ceiling += shares
        book = conn.execute(
            "SELECT quote_seen_at,depth_before_json FROM execution_feasibility_latest "
            "WHERE token_id=? AND direction=?",
            (selected_token, direction),
        ).fetchone()
        if book is not None:
            try:
                latest_quote_at = _parse_aware(book["quote_seen_at"])
            except (TypeError, ValueError):
                latest_quote_at = None
            if latest_quote_at is None or latest_quote_at > as_of:
                book = conn.execute(
                    "SELECT quote_seen_at,depth_before_json "
                    "FROM execution_feasibility_evidence "
                    "WHERE token_id=? AND direction=? "
                    "AND datetime(quote_seen_at)<=datetime(?) "
                    "ORDER BY datetime(quote_seen_at) DESC,rowid DESC LIMIT 1",
                    (selected_token, direction, as_of.isoformat()),
                ).fetchone()
        if book is None:
            liquidation = {"status": "CURRENT_BOOK_MISSING"}
            quote_at = None
            quote_age = None
        else:
            try:
                quote_dt = _parse_aware(book["quote_seen_at"])
                quote_age = (as_of - quote_dt).total_seconds()
                quote_at = quote_dt.isoformat()
            except (TypeError, ValueError):
                quote_age = None
                quote_at = None
            if (
                quote_age is None
                or quote_age < 0.0
                or quote_age > CURRENT_CAPITAL_TRUTH_MAX_AGE_SECONDS
            ):
                liquidation = {"status": "CURRENT_BOOK_STALE"}
            else:
                liquidation = _banded_bid_liquidation(
                    book["depth_before_json"],
                    shares,
                )
        status = str(liquidation["status"])
        prefix_shares = float(liquidation.get("executable_prefix_shares") or 0.0)
        prefix_value = float(
            liquidation.get("executable_prefix_gross_usd") or 0.0
        )
        executable_prefix_shares += prefix_shares
        executable_prefix_value += prefix_value
        book_status_counts[status] = book_status_counts.get(status, 0) + 1
        position_rows.append(
            {
                **base,
                "quote_seen_at": quote_at,
                "quote_age_seconds": (
                    round(quote_age, 6) if quote_age is not None else None
                ),
                "valuation_status": status,
                "best_bid": liquidation.get("best_bid"),
                "executable_prefix_shares": round(prefix_shares, 9),
                "executable_prefix_gross_usd_before_exit_fee": round(
                    prefix_value,
                    9,
                ),
                "full_position_executable": bool(
                    liquidation.get("full_position_executable")
                ),
                "full_position_vwap": liquidation.get("full_position_vwap"),
            }
        )
    terminal_floor = chain_cash + unsettled
    executable_gross = terminal_floor + executable_prefix_value
    terminal_ceiling = terminal_floor + payoff_ceiling
    identity_payload = {
        "collateral_snapshot_id": int(collateral["id"]),
        "chain_cash_usd": round(chain_cash, 9),
        "active_reservations_usd": round(active_reserved, 9),
        "unsettled_proceeds_usd": round(unsettled, 9),
        "positions": [
            {
                key: value
                for key, value in row.items()
                if key != "quote_age_seconds"
            }
            for row in position_rows
        ],
    }
    return {
        "artifact_role": "TOTAL_PORTFOLIO_CHAIN_CASH_PLUS_EXECUTABLE_HELD_VALUE",
        "ready": bool(collateral_ready and not position_truth_failures),
        "evaluated_at": as_of.isoformat(),
        "collateral_snapshot_id": int(collateral["id"]),
        "collateral_captured_at": collateral_at.isoformat(),
        "collateral_age_seconds": round(collateral_age, 6),
        "collateral_authority": str(collateral["authority_tier"] or ""),
        "chain_cash_usd": round(chain_cash, 9),
        "active_buy_reservations_usd": round(active_reserved, 9),
        "snapshot_buy_reservations_usd": round(snapshot_reserved, 9),
        "reservation_ledger_matches_snapshot": reservation_match,
        "spendable_cash_usd": round(chain_cash - active_reserved, 9),
        "unsettled_exit_proceeds_usd": round(unsettled, 9),
        "open_position_count": len(position_rows),
        "capital_bearing_position_count": sum(
            float(row.get("chain_shares") or 0.0) > 0.0 for row in position_rows
        ),
        "open_contract_shares_binary_payoff_ceiling_usd": round(
            payoff_ceiling,
            9,
        ),
        "immediately_executable_prefix_shares": round(
            executable_prefix_shares,
            9,
        ),
        "immediately_executable_gross_value_before_exit_fee_usd": round(
            executable_prefix_value,
            9,
        ),
        "total_portfolio_terminal_floor_usd": round(terminal_floor, 9),
        "total_portfolio_current_executable_gross_usd_before_exit_fee": round(
            executable_gross,
            9,
        ),
        "total_portfolio_binary_payoff_ceiling_usd": round(
            terminal_ceiling,
            9,
        ),
        "full_position_liquidation_coverage_complete": all(
            row.get("full_position_executable") is True for row in position_rows
        ),
        "book_status_counts": dict(sorted(book_status_counts.items())),
        "position_truth_failures": position_truth_failures,
        "observation_identity": hashlib.sha256(
            _canonical_json_bytes(identity_payload)
        ).hexdigest(),
        "positions": position_rows,
        "warning": (
            "EXECUTABLE_GROSS_VALUE_IS_DECISION_TIME_DEPTH_BEFORE_HYPOTHETICAL_"
            "EXIT_FEES; TERMINAL_FLOOR_AND_CEILING_ARE PAYOFF BOUNDS, NOT PNL"
        ),
    }


def _portfolio_observation_curve(
    current: Mapping[str, object],
    *,
    prior: Sequence[Mapping[str, object]],
    as_of: datetime,
) -> dict[str, object]:
    cutoff = as_of - timedelta(days=WINDOW_DAYS)
    retained: list[dict[str, object]] = []
    for raw in prior:
        try:
            observed = _parse_aware(raw.get("evaluated_at"))
        except (TypeError, ValueError):
            continue
        if not cutoff <= observed <= as_of:
            continue
        if not str(raw.get("observation_identity") or "").strip():
            continue
        retained.append(dict(raw))
    point_fields = (
        "evaluated_at",
        "observation_identity",
        "ready",
        "chain_cash_usd",
        "active_buy_reservations_usd",
        "unsettled_exit_proceeds_usd",
        "open_position_count",
        "capital_bearing_position_count",
        "open_contract_shares_binary_payoff_ceiling_usd",
        "immediately_executable_gross_value_before_exit_fee_usd",
        "total_portfolio_terminal_floor_usd",
        "total_portfolio_current_executable_gross_usd_before_exit_fee",
        "total_portfolio_binary_payoff_ceiling_usd",
        "full_position_liquidation_coverage_complete",
        "book_status_counts",
    )
    current_point = {field: current.get(field) for field in point_fields}
    if (
        current.get("observation_identity")
        and (
            not retained
            or retained[-1].get("observation_identity")
            != current.get("observation_identity")
        )
    ):
        retained.append(current_point)
    retained = retained[-PORTFOLIO_OBSERVATION_MAX_POINTS:]
    latest_delta = None
    if len(retained) >= 2:
        previous = retained[-2]
        latest = retained[-1]
        latest_delta = {
            "from_evaluated_at": previous.get("evaluated_at"),
            "to_evaluated_at": latest.get("evaluated_at"),
            "chain_cash_delta_usd": round(
                float(latest.get("chain_cash_usd") or 0.0)
                - float(previous.get("chain_cash_usd") or 0.0),
                9,
            ),
            "current_executable_gross_capital_delta_usd": round(
                float(
                    latest.get(
                        "total_portfolio_current_executable_gross_usd_before_exit_fee"
                    )
                    or 0.0
                )
                - float(
                    previous.get(
                        "total_portfolio_current_executable_gross_usd_before_exit_fee"
                    )
                    or 0.0
                ),
                9,
            ),
            "binary_payoff_ceiling_delta_usd": round(
                float(
                    latest.get("total_portfolio_binary_payoff_ceiling_usd") or 0.0
                )
                - float(
                    previous.get("total_portfolio_binary_payoff_ceiling_usd")
                    or 0.0
                ),
                9,
            ),
            "external_flow_adjusted": False,
        }
    return {
        "artifact_role": "TOTAL_PORTFOLIO_OBSERVATION_TRAJECTORY",
        "observation_count": len(retained),
        "latest_delta": latest_delta,
        "external_flow_adjusted": False,
        "profit_proof_eligible": False,
        "warning": (
            "TRAJECTORY_TRACKS TOTAL PORTFOLIO, NOT CASH; WITHOUT EXTERNAL-FLOW "
            "ATTRIBUTION IT CANNOT BY ITSELF PROVE TRADING PROFIT"
        ),
        "curve": retained,
    }


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
    counterfactuals: dict[str, dict[str, object]],
) -> tuple[str, list[str]]:
    failures: list[str] = []
    if receipt.get("ready") is not True:
        failures.append("CURRENT_GLOBAL_SELECTION_RECEIPT_UNPROVEN")
    independent = sum(
        int(row.get("independent_target_date_count") or 0)
        for row in counterfactuals.values()
        if row.get("global_selection_revision_bound") is True
    )
    if independent < MIN_INDEPENDENT_TARGET_DATES:
        failures.append("INSUFFICIENT_CURRENT_REGIME_SETTLED_TARGET_DATES")
    lcbs = [
        row.get("delta_log_wealth_lcb95")
        for row in counterfactuals.values()
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
    counterfactuals: dict[str, dict[str, object]],
    live_curves: dict[str, dict[str, object]],
) -> tuple[str, list[str]]:
    _, failures = _build_counterfactual_admission_verdict(
        receipt=receipt,
        counterfactuals=counterfactuals,
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


def _order_ledger_proof_failures(
    ledger: Mapping[str, object],
) -> list[str]:
    failures: list[str] = []
    if ledger.get("capital_truth_complete") is not True:
        failures.append("ORDER_CAPITAL_LEDGER_INCOMPLETE")
    if int(ledger.get("gain_truth_incomplete_command_count") or 0) > 0:
        failures.append("ORDER_GAIN_LEDGER_INCOMPLETE")
    return failures


def evaluate(
    *,
    world_path: Path,
    forecasts_path: Path,
    trades_path: Path,
    as_of: datetime,
    prior_proof_registry: Sequence[Mapping[str, object]] = (),
    prior_portfolio_observations: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    trades = _read_only(
        trades_path,
        frozenset(
            {
                "decision_log",
                "venue_commands",
                "venue_submission_envelopes",
                "venue_trade_facts",
                "execution_fact",
                "execution_feasibility_evidence",
                "position_events",
                "position_current",
                "collateral_ledger_snapshots",
                "collateral_reservations",
                "collateral_unsettled_proceeds",
                "execution_feasibility_latest",
            }
        ),
        connection_factory=get_trade_connection_read_only,
    )
    forecasts = _read_only(
        forecasts_path,
        frozenset({"settlement_outcomes", "market_events"}),
        connection_factory=get_forecasts_connection_read_only,
    )
    world = _read_only(
        world_path,
        frozenset({"edli_live_order_events"}),
        connection_factory=get_world_connection_read_only,
    )
    for connection in (trades, forecasts, world):
        connection.execute("BEGIN")
        connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()

    try:
        receipt = _latest_proof_receipt_coverage(trades)
        counterfactuals = {
            "combined_current_global_selection": (
                _settled_global_counterfactual_evidence(
                    trades,
                    forecasts,
                    as_of=as_of,
                    prior_proof_registry=prior_proof_registry,
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
                _bind_live_curve_to_global_revision(
                    trades,
                    curve,
                    events_conn=world,
                )
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
        order_capital_ledger = _order_capital_ledger(trades, as_of=as_of)
        total_portfolio_capital = _current_total_portfolio_capital(
            trades,
            as_of=as_of,
        )
        total_portfolio_trajectory = _portfolio_observation_curve(
            total_portfolio_capital,
            prior=prior_portfolio_observations,
            as_of=as_of,
        )
    finally:
        world.close()
        forecasts.close()
        trades.close()
    verdict, failures = _build_verdict(
        receipt=receipt,
        counterfactuals=counterfactuals,
        live_curves=live_curves,
    )
    admission_verdict, admission_failures = (
        _build_counterfactual_admission_verdict(
            receipt=receipt,
            counterfactuals=counterfactuals,
        )
    )
    order_ledger_failures = _order_ledger_proof_failures(order_capital_ledger)
    if order_ledger_failures:
        failures.extend(order_ledger_failures)
        verdict = "FAIL"
    if total_portfolio_capital.get("ready") is not True:
        failures.append("CURRENT_TOTAL_PORTFOLIO_CAPITAL_TRUTH_DEGRADED")
        verdict = "FAIL"
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
            "minimum_independent_target_dates": MIN_INDEPENDENT_TARGET_DATES,
            "minimum_exact_live_realized_positions": (
                MIN_EXACT_LIVE_REALIZED_POSITIONS
            ),
            "delta_log_wealth_lcb95_must_exceed": 0.0,
            "live_capital_weighted_return_must_exceed": 0.0,
            "absolute_small_dollar_pnl_is_not_advantage_proof": True,
            "every_venue_command_must_have_atomic_capital_disposition": True,
            "every_realized_exit_must_have_atomic_gain_disposition": True,
            "total_portfolio_not_cash_is_required": True,
            "global_selection_revision": (
                CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "probability_semantics_revisions": {
                "day0_nowcast_entry": DAY0_PROBABILITY_SEMANTICS_REVISION,
                "forecast_qkernel_entry": sorted(
                    LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS
                ),
            },
        },
        "database_paths": {
            "world": str(world_path.resolve()),
            "forecasts": str(forecasts_path.resolve()),
            "trades": str(trades_path.resolve()),
        },
        "latest_global_receipt": receipt,
        "settled_counterfactuals": counterfactuals,
        "live_realized_capital": live_curves,
        "globally_selected_exit_quality": selected_exit_quality,
        "globally_compared_hold_settlement_quality": held_settlement_quality,
        "per_order_capital_ledger": order_capital_ledger,
        "current_total_portfolio_capital": total_portfolio_capital,
        "total_portfolio_capital_trajectory": total_portfolio_trajectory,
    }


def _atomic_write(path: Path, payload: dict[str, object]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                existing_at = _parse_aware(existing.get("evaluated_at"))
                candidate_at = _parse_aware(payload.get("evaluated_at"))
            except (
                AttributeError,
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                existing_at = None
                candidate_at = None
            if (
                existing_at is not None
                and candidate_at is not None
                and existing_at > candidate_at
            ):
                return False
        temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    return True


def _prior_proof_registry(path: Path) -> tuple[Mapping[str, object], ...]:
    """Load only refs; every row is revalidated against canonical DB truth."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        registry = payload["settled_counterfactuals"][
            "combined_current_global_selection"
        ]["proof_registry"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(registry, list) or any(
        not isinstance(row, Mapping) for row in registry
    ):
        return ()
    return tuple(registry)


def _prior_portfolio_observations(
    path: Path,
) -> tuple[Mapping[str, object], ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        curve = payload["total_portfolio_capital_trajectory"]["curve"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(curve, list) or any(
        not isinstance(row, Mapping) for row in curve
    ):
        return ()
    return tuple(curve)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--forecasts", type=Path, required=True)
    parser.add_argument("--world", type=Path)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    world = args.world or args.trades.with_name("zeus-world.db")
    as_of = datetime.now(timezone.utc)
    prior_proof_registry = _prior_proof_registry(args.artifact)
    prior_portfolio_observations = _prior_portfolio_observations(args.artifact)
    try:
        artifact = evaluate(
            world_path=world,
            forecasts_path=args.forecasts,
            trades_path=args.trades,
            as_of=as_of,
            prior_proof_registry=prior_proof_registry,
            prior_portfolio_observations=prior_portfolio_observations,
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
    if not _atomic_write(args.artifact, artifact):
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
