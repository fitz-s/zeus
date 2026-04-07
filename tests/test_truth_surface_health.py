"""Truth-surface health tests — antibodies for Venus sensing audit findings.

These tests verify cross-surface invariants that Venus detected as broken.
They run against the live Zeus database, not test fixtures, because the
invariants they check are about production state integrity.
"""

import re
from datetime import date, datetime, timezone

import pytest

from src.state.db import get_connection, query_portfolio_loader_view


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


class TestPortfolioTruthSource:
    """AB-003: canonical truth path must never silently degrade to fallback."""

    def test_portfolio_truth_source_is_canonical(self):
        """Portfolio loader must return status 'ok', not 'CANONICAL_AUTHORITY_UNAVAILABLE'.

        If this fails, position_current projections are older than
        position_events_legacy — canonical truth is NOT available.
        This is a structural failure, not soft staleness.
        """
        conn = get_connection()
        result = query_portfolio_loader_view(conn)
        status = result.get("status", "unknown")
        assert status == "ok", (
            f"portfolio_truth_source is '{status}', not 'ok'. "
            f"Stale trade IDs: {result.get('stale_trade_ids', [])}. "
            f"This means position_current is behind position_events_legacy."
        )

    def test_portfolio_loader_has_positions(self):
        """Portfolio loader must return at least one position when ok."""
        conn = get_connection()
        result = query_portfolio_loader_view(conn)
        if result.get("status") == "ok":
            positions = result.get("positions", [])
            assert len(positions) > 0, "Status is ok but zero positions returned"


class TestGhostPositions:
    """Entered trade_decisions with expired target_dates are ghost positions."""

    def test_no_ghost_positions(self):
        """No trade_decisions with status=entered should have target_date in the past.

        Ghost positions indicate the decision-to-position materialization gap:
        a decision was entered but never reached durable position truth or was
        never voided/settled after expiry.
        """
        conn = get_connection()
        today = date.today()
        rows = conn.execute(
            "SELECT trade_id, bin_label FROM trade_decisions WHERE status='entered'"
        ).fetchall()

        ghosts = []
        for row in rows:
            trade_id = row["trade_id"]
            bin_label = row["bin_label"] or ""
            date_m = re.search(r"on (\w+ \d+)\?", bin_label)
            if date_m:
                try:
                    target = datetime.strptime(
                        date_m.group(1) + ", 2026", "%B %d, %Y"
                    ).date()
                    if target < today:
                        ghosts.append(trade_id)
                except ValueError:
                    pass

        assert len(ghosts) == 0, (
            f"{len(ghosts)} ghost positions found (entered decisions with expired target_date): "
            f"{ghosts[:10]}{'...' if len(ghosts) > 10 else ''}"
        )


class TestSettlementFreshness:
    """Settlement lifecycle must keep pace with the trading cycle."""

    def test_settlement_freshness(self):
        """Latest settlement activity must be within 48h.

        Checks decision_log settlement artifacts and calibration_pairs,
        not the deprecated legacy settlements table.
        """
        conn = get_connection()
        max_settled = conn.execute(
            "SELECT MAX(timestamp) FROM decision_log WHERE mode = 'settlement'"
        ).fetchone()[0]

        max_cal_target = conn.execute(
            "SELECT MAX(target_date) FROM calibration_pairs"
        ).fetchone()[0]

        assert max_settled is not None or max_cal_target is not None, (
            "No settlement activity found in decision_log or calibration_pairs"
        )

        if max_settled:
            dt = _parse_iso(str(max_settled))
            if dt:
                age_hours = (_now_utc() - dt).total_seconds() / 3600
                assert age_hours <= 48, (
                    f"Settlement is {age_hours:.1f}h stale (threshold: 48h). "
                    f"Latest: {max_settled}"
                )
