# Created: 2026-07-04
# Last reused/audited: 2026-07-04
# Authority basis: F1 hierarchical settlement-coverage calibrator wiring
#   (src/calibration/settlement_coverage_hierarchy.py + the money-path choke
#   point in src/engine/event_reactor_adapter.py::
#   _settlement_coverage_hierarchy_executable_pair /
#   _hierarchy_observations_all). Flag: feature_flags.
#   settlement_coverage_hierarchy_enabled (default False).
"""Wiring tests for the F1 hierarchical settlement-coverage calibrator.

Covers: flag-OFF byte-identical pass-through (zero DB reads), flag-ON enriched
observation build + hierarchy-licensed shrink reaching the executable pair,
and fail-closed QLCB_COVERAGE_AUTHORITY_FAULT propagation on a structural read
fault. Pure-estimator/hierarchy-selection tests live in
tests/calibration/test_settlement_coverage_hierarchy.py.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import src.engine.event_reactor_adapter as era
from src.state.schema.edli_no_submit_receipts_schema import ensure_table as ensure_receipts_table


def _world_conn_with_claims(claims: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = None
    ensure_receipts_table(conn)
    for i, c in enumerate(claims):
        receipt_json = json.dumps(
            {
                "city": c["city"],
                "metric": c["metric"],
                "target_date": c["target_date"],
                "condition_id": c["condition_id"],
                "bin_label": c["bin_label"],
                "strategy_key": c.get("strategy_key"),
            }
        )
        conn.execute(
            """
            INSERT INTO edli_no_submit_receipts (
                receipt_id, event_id, decision_time, direction,
                side_effect_status, q_live, q_live_raw,
                projection_hash, receipt_json, receipt_hash, created_at,
                schema_version
            ) VALUES (?, ?, ?, ?, 'NO_SUBMIT', ?, ?, 'proj', ?, 'hash', ?, 1)
            """,
            (
                f"receipt-{i}",
                f"event-{i}",
                c["created_at"],
                c["direction"],
                c["q_raw"],
                c.get("q_live_raw", c["q_raw"]),
                receipt_json,
                c["created_at"],
            ),
        )
    conn.commit()
    return conn


def _forecast_conn_with_settlements(settlements: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, temperature_metric TEXT, target_date TEXT,
            settlement_value REAL, settlement_unit TEXT, authority TEXT,
            recorded_at TEXT, settled_at TEXT
        )
        """
    )
    for s in settlements:
        conn.execute(
            "INSERT INTO settlement_outcomes "
            "(city, temperature_metric, target_date, settlement_value, settlement_unit, "
            "authority, recorded_at, settled_at) VALUES (?, ?, ?, ?, ?, 'VERIFIED', ?, ?)",
            (
                s["city"], s["metric"], s["target_date"], s["settlement_value"],
                s["settlement_unit"], s["created_at"], s["created_at"],
            ),
        )
    conn.commit()
    return conn


def _topology_conn_with_bins(bins: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE market_events (condition_id TEXT, range_low REAL, range_high REAL, range_label TEXT)"
    )
    for b in bins:
        conn.execute(
            "INSERT INTO market_events (condition_id, range_low, range_high, range_label) VALUES (?, ?, ?, ?)",
            (b["condition_id"], b["range_low"], b["range_high"], b["range_label"]),
        )
    conn.commit()
    return conn


def _claim(
    *,
    idx: int,
    city: str = "Singapore",
    metric: str = "high",
    day: int,
    q_raw: float,
    direction: str = "buy_no",
    strategy_key: str = "opening_inertia",
    bin_label: str = "Will the highest temperature in Singapore be 31C",
) -> dict:
    return {
        "city": city,
        "metric": metric,
        "target_date": f"2026-05-{day:02d}",
        "condition_id": f"cond-{city}-{idx}",
        "bin_label": f"{bin_label} on May {day}?",
        "direction": direction,
        "q_raw": q_raw,
        "strategy_key": strategy_key,
        "created_at": f"2026-05-{day:02d}T12:00:00+00:00",
    }


@pytest.fixture()
def flag_off(monkeypatch):
    monkeypatch.setitem(era.settings["feature_flags"], "settlement_coverage_hierarchy_enabled", False)


@pytest.fixture()
def flag_on(monkeypatch):
    monkeypatch.setitem(era.settings["feature_flags"], "settlement_coverage_hierarchy_enabled", True)


class TestFlagOffByteIdentical:
    def test_flag_off_pass_through_zero_db_reads(self, flag_off, monkeypatch):
        def _boom():
            raise AssertionError("must not read world.db when flag is OFF")

        monkeypatch.setattr("src.state.db.get_world_connection_read_only", _boom)
        pair = era._settlement_coverage_hierarchy_executable_pair(
            event_type="FORECAST_SNAPSHOT_READY",
            city="Singapore",
            metric="high",
            bin_label="Will the highest temperature in Singapore be 31C on May 31?",
            direction="buy_no",
            q_raw=0.84,
            q_lcb_raw=0.80,
            forecast_conn=sqlite3.connect(":memory:"),
            topology_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        assert pair.q_exec == pytest.approx(0.84)
        assert pair.q_lcb_exec == pytest.approx(0.80)
        assert pair.level is None
        assert pair.status == "INSUFFICIENT_DATA"


class TestFlagOnWiring:
    def test_unlicensed_strategy_cohort_shrinks_executable_pair(self, flag_on, monkeypatch):
        # 36 historical claims: settlement_capture buy_no at q_raw=0.844, 16
        # winning (the audit anchor) -- spread across many cities so the
        # exact-cell scope for the QUERY (a fresh city "Manila") stays thin and
        # only the Level-1 STRATEGY_BUCKET cohort can license the shrink.
        # event_type=DAY0_EXTREME_UPDATED + direction=buy_no is the ONLY
        # _event_bound_strategy_key combination that resolves to a CANONICAL
        # strategy ("settlement_capture") -- the qkernel forecast lane resolves
        # to "forecast_qkernel_entry", which is NOT in CANONICAL_STRATEGY_KEYS
        # and correctly canonicalizes to UNKNOWN (by design, see
        # src/calibration/settlement_coverage_hierarchy.py).
        # All six MUST be real configured cities with unit=C (config/cities.json)
        # so _bin_from_market_event resolves a real settlement unit -- an
        # unconfigured city name falls back to unit="F" and grade_receipt then
        # raises UnitMismatchError against the "C" settlement, silently
        # dropping the observation (by design -- not a test bug to route around).
        cities = ["Singapore", "Tokyo", "Jakarta", "Beijing", "Ankara", "Auckland"]
        claims = []
        settlements = []
        bins = []
        for i in range(36):
            city = cities[i % len(cities)]
            day = (i % 28) + 1
            claims.append(
                _claim(
                    idx=i, city=city, day=day, q_raw=0.844, direction="buy_no",
                    strategy_key="settlement_capture",
                )
            )
            won = i < 16  # 16/36 wins -- the audit anchor
            # Bin: exact 31C. Settle IN the bin -> buy_no LOSES; settle OUT -> buy_no WINS.
            settlement_value = 31.0 if not won else 25.0
            settlements.append(
                {
                    "city": city, "metric": "high", "target_date": f"2026-05-{day:02d}",
                    "settlement_value": settlement_value, "settlement_unit": "C",
                    "created_at": f"2026-05-{day:02d}T18:00:00+00:00",
                }
            )
            bins.append(
                {"condition_id": f"cond-{city}-{i}", "range_low": 31.0, "range_high": 31.0, "range_label": "31C"}
            )

        world_conn = _world_conn_with_claims(claims)
        forecast_conn = _forecast_conn_with_settlements(settlements)
        topology_conn = _topology_conn_with_bins(bins)
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)

        pair = era._settlement_coverage_hierarchy_executable_pair(
            event_type="DAY0_EXTREME_UPDATED",
            city="Manila",
            metric="high",
            bin_label="Will the highest temperature in Manila be 31C on Jun 1?",
            direction="buy_no",
            q_raw=0.84,
            q_lcb_raw=0.80,
            forecast_conn=forecast_conn,
            topology_conn=topology_conn,
            decision_time=datetime(2026, 6, 5, tzinfo=timezone.utc),
        )
        assert pair.status == "UNLICENSED"
        assert pair.level in ("STRATEGY_BUCKET", "CROSS_STRATEGY", "GLOBAL")
        assert pair.q_exec == pytest.approx(0.446, abs=0.02)
        assert pair.q_exec < pair.q_raw
        assert pair.q_lcb_exec < pair.q_lcb_raw
        assert pair.q_raw == pytest.approx(0.84)
        assert pair.q_lcb_raw == pytest.approx(0.80)
        assert pair.n >= 30
        assert pair.estimator == "jeffreys_v1"

    def test_thin_history_is_insufficient_data_no_op(self, flag_on, monkeypatch):
        world_conn = _world_conn_with_claims([])
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)
        pair = era._settlement_coverage_hierarchy_executable_pair(
            event_type="FORECAST_SNAPSHOT_READY",
            city="Singapore",
            metric="high",
            bin_label="Will the highest temperature in Singapore be 31C on May 31?",
            direction="buy_no",
            q_raw=0.84,
            q_lcb_raw=0.80,
            forecast_conn=sqlite3.connect(":memory:"),
            topology_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        assert pair.status == "INSUFFICIENT_DATA"
        assert pair.level is None
        assert pair.q_exec == pytest.approx(0.84)
        assert pair.q_lcb_exec == pytest.approx(0.80)


class TestFailClosed:
    def test_world_db_connect_fault_raises_authority_fault(self, flag_on, monkeypatch):
        def _boom():
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr("src.state.db.get_world_connection_read_only", _boom)
        with pytest.raises(ValueError, match="QLCB_COVERAGE_AUTHORITY_FAULT"):
            era._settlement_coverage_hierarchy_executable_pair(
                event_type="FORECAST_SNAPSHOT_READY",
                city="Singapore",
                metric="high",
                bin_label="Will the highest temperature in Singapore be 31C on May 31?",
                direction="buy_no",
                q_raw=0.84,
                q_lcb_raw=0.80,
                forecast_conn=sqlite3.connect(":memory:"),
                topology_conn=sqlite3.connect(":memory:"),
                decision_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

    def test_settlement_query_fault_raises_authority_fault(self, flag_on, monkeypatch):
        claims = [_claim(idx=0, city="Singapore", day=1, q_raw=0.84)]
        world_conn = _world_conn_with_claims(claims)
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)
        # forecast_conn with NO settlement_outcomes table -> query raises.
        broken_forecast_conn = sqlite3.connect(":memory:")
        with pytest.raises(ValueError, match="QLCB_COVERAGE_AUTHORITY_FAULT"):
            era._settlement_coverage_hierarchy_executable_pair(
                event_type="FORECAST_SNAPSHOT_READY",
                city="Singapore",
                metric="high",
                bin_label="Will the highest temperature in Singapore be 31C on May 31?",
                direction="buy_no",
                q_raw=0.84,
                q_lcb_raw=0.80,
                forecast_conn=broken_forecast_conn,
                topology_conn=sqlite3.connect(":memory:"),
                decision_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )


class TestDedup:
    def test_two_receipts_same_claim_one_day_count_once(self, flag_on, monkeypatch):
        # Two fills of the SAME market-side claim on one day (same condition_id,
        # city, metric, band, direction, strategy) must count once in the
        # observation stream feeding the hierarchy.
        claims = [
            _claim(idx=0, city="Singapore", day=1, q_raw=0.80, direction="buy_no"),
            _claim(idx=0, city="Singapore", day=1, q_raw=0.83, direction="buy_no"),
        ]
        # Force the SAME condition_id across both (two fills of one claim).
        claims[1]["condition_id"] = claims[0]["condition_id"]
        world_conn = _world_conn_with_claims(claims)
        forecast_conn = _forecast_conn_with_settlements(
            [{
                "city": "Singapore", "metric": "high", "target_date": "2026-05-01",
                "settlement_value": 25.0, "settlement_unit": "C",
                "created_at": "2026-05-01T18:00:00+00:00",
            }]
        )
        topology_conn = _topology_conn_with_bins(
            [{"condition_id": claims[0]["condition_id"], "range_low": 31.0, "range_high": 31.0, "range_label": "31C"}]
        )
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)
        obs = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        assert len(obs) == 1
