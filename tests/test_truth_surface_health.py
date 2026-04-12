"""Truth-surface health tests — antibodies for Venus sensing audit findings.

These tests verify cross-surface invariants that Venus detected as broken.
They run against the live Zeus database, not test fixtures, because the
invariants they check are about production state integrity.
"""

import re
from datetime import date, datetime, timezone

import pytest

from src.state.db import get_connection, init_schema, query_portfolio_loader_view


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

    @pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
    def test_portfolio_truth_source_is_canonical(self):
        """Portfolio loader must return status 'ok' or 'partial_stale'.

        If this fails, position_current projections are missing or broken.
        'partial_stale' is acceptable when some legacy positions have newer
        events — those are excluded per-position while the rest are served.
        """
        conn = get_connection()
        result = query_portfolio_loader_view(conn)
        status = result.get("status", "unknown")
        assert status in ("ok", "partial_stale"), (
            f"portfolio_truth_source is '{status}', not 'ok'/'partial_stale'. "
            f"Stale trade IDs: {result.get('stale_trade_ids', [])}. "
            f"This means position_current is behind position_events_legacy."
        )

    def test_portfolio_loader_has_positions(self):
        """Portfolio loader must return at least one position when ok."""
        conn = get_connection()
        result = query_portfolio_loader_view(conn)
        if result.get("status") in ("ok", "partial_stale"):
            positions = result.get("positions", [])
            assert len(positions) > 0, "Status is ok but zero positions returned"


class TestGhostPositions:
    """Entered trade_decisions with expired target_dates are ghost positions."""

    @pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
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

    @pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
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


@pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
def test_portfolio_loader_ignores_same_phase_legacy_entry_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "paper")
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at
        ) VALUES (
            'trade-1', 'active', 'trade-1', 'm1', 'NYC', 'US-Northeast', '2099-04-01', '39-40°F',
            'buy_yes', 'F', 5.0, 14.29, 5.0, 0.35, 0.6,
            'snap-1', 'ens_member_counting', 'center_buy', 'center_buy', 'opening_hunt',
            'unknown', '', 'filled', '2099-04-01T11:45:45.242001+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'ORDER_FILLED', 'trade-1', 'entered', '', 'snap-1',
            'NYC', '2099-04-01', 'm1', '39-40°F', 'buy_yes', 'center_buy', 'center_buy',
            'test', '{}', '2099-04-01T11:45:45.242861+00:00', 'paper'
        )
        """
    )
    conn.commit()

    result = query_portfolio_loader_view(conn)
    conn.close()

    assert result["status"] == "ok"
    assert [row["trade_id"] for row in result["positions"]] == ["trade-1"]


@pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
def test_portfolio_loader_marks_semantic_exit_shadow_as_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "paper")
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at
        ) VALUES (
            'shadow-trade', 'day0_window', 'shadow-trade', 'm1', 'Dallas', 'US-South', '2099-04-07', '76-77°F',
            'buy_no', 'F', 1.18, 1.28, 1.18, 0.92, 0.55,
            'snap-1', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', '', 'filled', '2099-04-07T10:58:44.847407+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'POSITION_EXIT_RECORDED', 'shadow-trade', 'economically_closed', '', 'snap-1',
            'Dallas', '2099-04-07', 'm1', '76-77°F', 'buy_no', 'opening_inertia', 'opening_inertia',
            'test', '{\"pnl\":0.05}', '2099-04-07T11:14:30.687958+00:00', 'paper'
        )
        """
    )
    conn.commit()

    result = query_portfolio_loader_view(conn)
    conn.close()

    assert result["status"] == "stale_legacy_fallback"
    assert result["stale_trade_ids"] == ["shadow-trade"]


@pytest.mark.skip(reason="P9/Phase2: legacy position_events_legacy or backfill eliminated")
def test_portfolio_loader_keeps_older_semantic_advance_stale_even_if_newer_shadow_event_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "paper")
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at
        ) VALUES (
            'pending-trade', 'pending_entry', 'pending-trade', 'm1', 'NYC', 'US-Northeast', '2099-04-01', '39-40°F',
            'buy_yes', 'F', 5.0, 14.29, 5.0, 0.35, 0.6,
            'snap-1', 'ens_member_counting', 'center_buy', 'center_buy', 'opening_hunt',
            'unknown', '', 'filled', '2099-04-01T11:45:45.242001+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'POSITION_ENTRY_RECORDED', 'pending-trade', 'entered', '', 'snap-1',
            'NYC', '2099-04-01', 'm1', '39-40°F', 'buy_yes', 'center_buy', 'center_buy',
            'test', '{}', '2099-04-01T11:45:46.000000+00:00', 'paper'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            event_type, runtime_trade_id, position_state, order_id, decision_snapshot_id,
            city, target_date, market_id, bin_label, direction, strategy, edge_source,
            source, details_json, timestamp, env
        ) VALUES (
            'ORDER_FILLED', 'pending-trade', 'entered', '', 'snap-1',
            'NYC', '2099-04-01', 'm1', '39-40°F', 'buy_yes', 'center_buy', 'center_buy',
            'test', '{}', '2099-04-01T11:45:47.000000+00:00', 'paper'
        )
        """
    )
    conn.commit()

    result = query_portfolio_loader_view(conn)
    conn.close()

    assert result["status"] == "stale_legacy_fallback"
    assert result["stale_trade_ids"] == ["pending-trade"]
