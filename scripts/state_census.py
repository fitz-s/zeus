# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1H/phase.json
"""Zeus state census — read-only diagnostic across six classification axes.

Classifies every open position in zeus_trades.db across:
  1. position_truth    — open / review_required / closed
  2. redeem_truth      — data_unavailable / no_redeem_queued / redeem_intent_created / ...
  3. command_truth     — pending / in_flight / resolved / none
  4. fill_truth        — venue_confirmed_full / pending / partial / cancelled / settled / none
  5. quote_or_exit_truth — holding / exit_intent / sell_placed / sell_pending / sell_filled /
                           retry_pending / backoff_exhausted / unknown
  6. identity_truth    — live_bound / placeholder

CRITICAL INVARIANTS (T1H phase.json):
  - T1H-CENSUS-READ-ONLY: DB opened with sqlite3.connect("file:PATH?mode=ro", uri=True).
    Any INSERT/UPDATE/DELETE/CREATE/ALTER/DROP is refused by SQLite at the driver layer.
  - T1H-DATA-UNAVAILABLE-DISTINCT-FROM-NO-REDEEM: 0 rows in settlement_commands for a
    condition_id → redeem_truth="data_unavailable", NOT "no_redeem_queued".
  - T1H-DETECTS-PLACEHOLDER-IDENTITY: condition_id starts with "legacy:" OR
    question_id == "legacy-compat" → identity_truth="placeholder"; trade_id → anomalies.
  - T1H-DETECTS-CORRECTED-WITHOUT-FILL-AUTHORITY: corrected_executable_economics_eligible=True
    AND fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL → position_truth="review_required";
    trade_id → anomalies.

JSON output schema (written to --json-out path):
{
  "generated_at": "<ISO-8601>",
  "census_version": "T1H/v1",
  "db_path": "<path used>",
  "warning": "<optional — e.g. DB not found>",
  "positions": [
    {
      "trade_id": "...",
      "condition_id": "...",
      "position_truth": "open" | "review_required" | "closed",
      "redeem_truth": "data_unavailable" | "no_redeem_queued" | "redeem_intent_created" | ...,
      "command_truth": "none" | "pending" | "in_flight" | "resolved",
      "fill_truth": "none" | "venue_confirmed_full" | "venue_confirmed_partial" |
                   "optimistic_submitted" | "cancelled_remainder" | "settled" | "<other>",
      "quote_or_exit_truth": "holding" | "exit_intent" | "sell_placed" | "sell_pending" |
                             "sell_filled" | "retry_pending" | "backoff_exhausted" | "unknown",
      "identity_truth": "live_bound" | "placeholder" | "no_envelope"
    },
    ...
  ],
  "anomalies": [
    {"trade_id": "...", "axis": "identity_truth", "reason": "placeholder",
     "detail": "condition_id starts with 'legacy:'"},
    ...
  ],
  "summary": {
    "total_positions": <int>,
    "anomaly_count": <int>
  }
}
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import fill-authority constant from canonical source (do NOT redefine).
# T1H-DETECTS-CORRECTED-WITHOUT-FILL-AUTHORITY uses this exact constant.
# T2F-CENSUS-FILL-AUTHORITY-FAIL-CLOSED: ImportError exits non-zero rather
# than silently duplicating the string, removing the drift risk (T1H C-1 LOW).
# ---------------------------------------------------------------------------
try:
    from src.state.portfolio import FILL_AUTHORITY_VENUE_CONFIRMED_FULL
except ImportError as _fill_auth_import_err:
    print(
        "FATAL: authority constant unavailable; refusing to classify. "
        "Cannot import FILL_AUTHORITY_VENUE_CONFIRMED_FULL from src.state.portfolio. "
        f"Ensure the repo venv is active and src/ is on sys.path. ({_fill_auth_import_err})",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_db_path() -> Path:
    """Return the canonical zeus_trades.db path relative to this repo."""
    try:
        from src.state.db import _zeus_trade_db_path  # type: ignore[attr-defined]
        return _zeus_trade_db_path()
    except Exception:
        return _REPO_ROOT / "state" / "zeus_trades.db"


# ---------------------------------------------------------------------------
# Read-only connection helper (T1H-CENSUS-READ-ONLY)
# ---------------------------------------------------------------------------

def _open_read_only(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection in strict read-only URI mode.

    Uses file:PATH?mode=ro URI so SQLite itself refuses any write statement.
    Raises sqlite3.OperationalError("unable to open database file") when the
    DB does not exist (mode=ro never creates files).
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Axis 2: redeem_truth helpers
# ---------------------------------------------------------------------------

_REDEEM_STATE_MAP: dict[str, str] = {
    "REDEEM_INTENT_CREATED": "redeem_intent_created",
    "REDEEM_SUBMITTED": "redeem_submitted",
    "REDEEM_TX_HASHED": "redeem_tx_hashed",
    "REDEEM_CONFIRMED": "redeem_confirmed",
    "REDEEM_FAILED": "redeem_failed",
    "REDEEM_RETRYING": "redeem_retrying",
    "REDEEM_REVIEW_REQUIRED": "redeem_review_required",
}


def _classify_redeem_truth(conn: sqlite3.Connection, condition_id: str) -> str:
    """Classify the redeem state for a given condition_id.

    T1H-DATA-UNAVAILABLE-DISTINCT-FROM-NO-REDEEM: 0 rows → "data_unavailable",
    never "no_redeem_queued". "no_redeem_queued" is reserved for future use
    when the census can confirm the market is NOT yet settled (i.e. the absence
    is expected). Here we cannot distinguish expected vs unexpected absence, so
    we always return "data_unavailable" on 0 rows.
    """
    try:
        rows = conn.execute(
            "SELECT state FROM settlement_commands WHERE condition_id = ? "
            "ORDER BY requested_at DESC",
            (condition_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table does not exist in this DB.
        return "data_unavailable"

    if not rows:
        # T1H-DATA-UNAVAILABLE-DISTINCT-FROM-NO-REDEEM: MUST be "data_unavailable"
        return "data_unavailable"

    # Use the most-recent row's state.
    raw_state = str(rows[0]["state"]) if rows[0]["state"] is not None else ""
    return _REDEEM_STATE_MAP.get(raw_state, raw_state.lower() if raw_state else "data_unavailable")


# ---------------------------------------------------------------------------
# Axis 3: command_truth helpers
# ---------------------------------------------------------------------------

_IN_FLIGHT_COMMAND_STATES = frozenset({
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "CANCEL_PENDING",
    "CANCEL_REQUESTED",
    "CANCEL_ACKED",
})

_RESOLVED_COMMAND_STATES = frozenset({
    "ACK",
    "REJECTED",
    "CANCEL_CONFIRMED",
    "EXPIRED",
    "FAILED",
})

_PENDING_COMMAND_STATES = frozenset({
    "CREATED",
    "PENDING",
})


def _classify_command_truth(conn: sqlite3.Connection, trade_id: str) -> str:
    """Classify the venue command queue state for a trade_id."""
    try:
        rows = conn.execute(
            "SELECT state FROM venue_commands WHERE trade_id = ? "
            "ORDER BY created_at DESC",
            (trade_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return "none"

    if not rows:
        return "none"

    states = {str(r["state"]) for r in rows if r["state"] is not None}

    # Classify by most severe active state.
    if states & _IN_FLIGHT_COMMAND_STATES:
        return "in_flight"
    if states & _PENDING_COMMAND_STATES:
        return "pending"
    if states & _RESOLVED_COMMAND_STATES:
        return "resolved"
    return "none"


# ---------------------------------------------------------------------------
# Axis 5: quote_or_exit_truth helpers
# ---------------------------------------------------------------------------

_EXIT_STATE_MAP: dict[str, str] = {
    "": "holding",
    "exit_intent": "exit_intent",
    "sell_placed": "sell_placed",
    "sell_pending": "sell_pending",
    "sell_filled": "sell_filled",
    "retry_pending": "retry_pending",
    "backoff_exhausted": "backoff_exhausted",
}


def _classify_quote_or_exit_truth(exit_state: str) -> str:
    """Map position.exit_state to quote_or_exit_truth axis value."""
    return _EXIT_STATE_MAP.get(exit_state or "", "unknown")


# ---------------------------------------------------------------------------
# Axis 6: identity_truth helpers
# ---------------------------------------------------------------------------


def _classify_identity_truth(
    conn: sqlite3.Connection,
    trade_id: str,
    condition_id: str,
) -> tuple[str, str | None]:
    """Classify identity_truth axis; return (classification, anomaly_detail_or_None).

    T1H-DETECTS-PLACEHOLDER-IDENTITY: condition_id starts with "legacy:" OR
    question_id == "legacy-compat" → "placeholder".
    """
    # First check: condition_id prefix on the position itself.
    if condition_id and condition_id.startswith("legacy:"):
        return "placeholder", "condition_id starts with 'legacy:'"

    # Second check: look up envelope row(s) for this trade_id.
    try:
        rows = conn.execute(
            "SELECT condition_id, question_id FROM venue_submission_envelopes "
            "WHERE trade_ids_json LIKE ? "
            "ORDER BY captured_at DESC",
            (f'%"{trade_id}"%',),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table does not exist.
        return "no_envelope", None

    if not rows:
        return "no_envelope", None

    for row in rows:
        env_cid = str(row["condition_id"] or "")
        env_qid = str(row["question_id"] or "")
        if env_cid.startswith("legacy:"):
            return "placeholder", "condition_id starts with 'legacy:'"
        if env_qid == "legacy-compat":
            return "placeholder", "question_id == 'legacy-compat'"

    return "live_bound", None


# ---------------------------------------------------------------------------
# Active-positions query (read-only)
# ---------------------------------------------------------------------------

_INACTIVE_STATES = frozenset({
    "voided",
    "settled",
    "economically_closed",
    "quarantined",
    "admin_closed",
})


def _query_open_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return active position rows from the canonical DB projection.

    Falls back to an older positions table schema if the projection view is
    unavailable. Returns [] if neither table/view exists.
    """
    # Try canonical projection view first.
    for table in ("position_current", "positions_current", "positions"):
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            # Filter out terminal states.
            active = [
                dict(r)
                for r in rows
                if str(r["state"] if "state" in r.keys() else "") not in _INACTIVE_STATES
            ]
            return active
        except sqlite3.OperationalError:
            continue
    return []


# ---------------------------------------------------------------------------
# Core census logic
# ---------------------------------------------------------------------------


def run_census(db_path: Path) -> dict[str, Any]:
    """Run the full six-axis census against db_path.

    Returns the census dict (JSON-serialisable). On missing DB, returns a
    valid census with 0 positions and a top-level warning field.
    """
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result: dict[str, Any] = {
        "generated_at": generated_at,
        "census_version": "T1H/v1",
        "db_path": str(db_path),
        "positions": [],
        "anomalies": [],
        "summary": {"total_positions": 0, "anomaly_count": 0},
    }

    if not db_path.exists():
        result["warning"] = f"DB not found: {db_path} — census output is empty."
        return result

    try:
        conn = _open_read_only(db_path)
    except sqlite3.OperationalError as exc:
        result["warning"] = f"Could not open DB in read-only mode: {exc}"
        return result

    try:
        positions = _query_open_positions(conn)
        classified: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []

        for row in positions:
            trade_id = str(row.get("trade_id") or "")
            condition_id = str(row.get("condition_id") or "")
            fill_authority = str(row.get("fill_authority") or "none")
            corrected_eligible = bool(row.get("corrected_executable_economics_eligible") or False)
            exit_state = str(row.get("exit_state") or "")
            pos_state = str(row.get("state") or "")

            # --- Axis 1: position_truth ---
            if pos_state in _INACTIVE_STATES:
                position_truth = "closed"
            elif (
                corrected_eligible
                and fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL
            ):
                # T1H-DETECTS-CORRECTED-WITHOUT-FILL-AUTHORITY
                position_truth = "review_required"
                anomalies.append({
                    "trade_id": trade_id,
                    "axis": "position_truth",
                    "reason": "corrected_without_fill_authority",
                    "detail": (
                        f"corrected_executable_economics_eligible=True but "
                        f"fill_authority={fill_authority!r} (expected "
                        f"{FILL_AUTHORITY_VENUE_CONFIRMED_FULL!r})"
                    ),
                })
            else:
                position_truth = "open"

            # --- Axis 2: redeem_truth ---
            redeem_truth = _classify_redeem_truth(conn, condition_id)

            # --- Axis 3: command_truth ---
            command_truth = _classify_command_truth(conn, trade_id)

            # --- Axis 4: fill_truth ---
            fill_truth = fill_authority if fill_authority else "none"

            # --- Axis 5: quote_or_exit_truth ---
            quote_or_exit_truth = _classify_quote_or_exit_truth(exit_state)

            # --- Axis 6: identity_truth ---
            identity_truth, identity_detail = _classify_identity_truth(
                conn, trade_id, condition_id
            )
            if identity_truth == "placeholder" and identity_detail:
                # T1H-DETECTS-PLACEHOLDER-IDENTITY
                anomalies.append({
                    "trade_id": trade_id,
                    "axis": "identity_truth",
                    "reason": "placeholder",
                    "detail": identity_detail,
                })

            classified.append({
                "trade_id": trade_id,
                "condition_id": condition_id,
                "position_truth": position_truth,
                "redeem_truth": redeem_truth,
                "command_truth": command_truth,
                "fill_truth": fill_truth,
                "quote_or_exit_truth": quote_or_exit_truth,
                "identity_truth": identity_truth,
            })

        result["positions"] = classified
        result["anomalies"] = anomalies
        result["summary"] = {
            "total_positions": len(classified),
            "anomaly_count": len(anomalies),
        }

    finally:
        conn.close()

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zeus state census — read-only six-axis position classifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        required=True,
        help=(
            "Mandatory operator gesture confirming this is a read-only census run. "
            "The script refuses to run without this flag."
        ),
    )
    parser.add_argument(
        "--json-out",
        required=True,
        metavar="PATH",
        help="Path to write JSON census output.",
    )
    parser.add_argument(
        "--db",
        metavar="DB_PATH",
        default=None,
        help="Path to zeus_trades.db. Defaults to state/zeus_trades.db relative to repo root.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # --read-only is required=True in argparse, so we reach here only if set.
    # Belt-and-suspenders assertion for defensive clarity.
    assert args.read_only, "T1H-CENSUS-READ-ONLY: --read-only flag required."

    db_path = Path(args.db) if args.db else _default_db_path()
    out_path = Path(args.json_out)

    census = run_census(db_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(census, indent=2))

    if "warning" in census:
        print(f"WARNING: {census['warning']}", file=sys.stderr)

    print(
        f"Census written to {out_path} — "
        f"{census['summary']['total_positions']} positions, "
        f"{census['summary']['anomaly_count']} anomalies."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
