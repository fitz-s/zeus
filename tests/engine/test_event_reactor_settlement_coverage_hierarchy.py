# Created: 2026-07-04
# Last reused/audited: 2026-07-23
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
        # A locked selected-side Day0 payoff is the only input that resolves to
        # settlement_capture; direction alone must never select that cohort. The
        # qkernel forecast lane resolves
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
            day0_payoff_truth="locked",
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
        monkeypatch.setattr(
            "src.state.db.get_trade_connection_read_only",
            lambda: (_ for _ in ()).throw(RuntimeError("no trades DB in this test")),
        )
        obs = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        assert len(obs) == 1


# ---------------------------------------------------------------------------
# Prerequisite #2 (docs/evidence/capital_efficiency_2026_07_19/highq_overconfidence.md
# Sec 5): _hierarchy_observations_all ALSO ingests entered-position walk-forward
# outcomes from position_current (zeus_trades.db), which carry real canonical
# strategy_key -- the only thing that lets Levels 1-3 ever engage.
# ---------------------------------------------------------------------------


def _position_row(
    *,
    idx: int,
    city: str = "Singapore",
    metric: str = "high",
    day: int,
    q_raw: float,
    direction: str = "buy_no",
    strategy_key: str = "opening_inertia",
    entry_method: str = "ens_member_counting",
    phase: str = "settled",
    bin_label: str = "Will the highest temperature in Singapore be 31C",
    condition_id: str | None = None,
) -> dict:
    return {
        "position_id": f"pos-{city}-{idx}",
        "phase": phase,
        "city": city,
        "metric": metric,
        "target_date": f"2026-05-{day:02d}",
        "condition_id": condition_id or f"pos-cond-{city}-{idx}",
        "bin_label": f"{bin_label} on May {day}?",
        "direction": direction,
        "strategy_key": strategy_key,
        "p_posterior": q_raw,
        "entry_method": entry_method,
        "updated_at": f"2026-05-{day:02d}T20:00:00+00:00",
    }


def _trade_conn_with_positions(positions: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            temperature_metric TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            strategy_key TEXT,
            p_posterior REAL,
            entry_method TEXT,
            condition_id TEXT,
            updated_at TEXT
        )
        """
    )
    for p in positions:
        conn.execute(
            "INSERT INTO position_current (position_id, phase, city, temperature_metric, "
            "target_date, bin_label, direction, strategy_key, p_posterior, entry_method, "
            "condition_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["position_id"], p["phase"], p["city"], p["metric"], p["target_date"],
                p["bin_label"], p["direction"], p["strategy_key"], p["p_posterior"],
                p["entry_method"], p["condition_id"], p["updated_at"],
            ),
        )
    conn.commit()
    return conn


class TestPositionClaimsWalkForward:
    def test_settled_position_enters_pool_with_real_strategy_key(self, flag_on, monkeypatch):
        pos = _position_row(idx=0, city="Singapore", day=1, q_raw=0.86, strategy_key="opening_inertia")
        world_conn = _world_conn_with_claims([])
        trade_conn = _trade_conn_with_positions([pos])
        forecast_conn = _forecast_conn_with_settlements(
            [{
                "city": "Singapore", "metric": "high", "target_date": "2026-05-01",
                "settlement_value": 25.0, "settlement_unit": "C",
                "created_at": "2026-05-01T22:00:00+00:00",
            }]
        )
        topology_conn = _topology_conn_with_bins(
            [{"condition_id": pos["condition_id"], "range_low": 31.0, "range_high": 31.0, "range_label": "31C"}]
        )
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)
        monkeypatch.setattr("src.state.db.get_trade_connection_read_only", lambda: trade_conn)

        obs = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        assert len(obs) == 1
        o = obs[0]
        assert o.strategy_key == "opening_inertia"
        assert o.q_raw == pytest.approx(0.86)
        assert o.settlement_time == "2026-05-01T22:00:00+00:00"

    def test_walk_forward_cutoff_applies_to_position_sourced_observation(self, flag_on, monkeypatch):
        pos = _position_row(idx=0, city="Singapore", day=1, q_raw=0.86)
        world_conn = _world_conn_with_claims([])
        trade_conn = _trade_conn_with_positions([pos])
        settled_at = "2026-05-01T22:00:00+00:00"
        forecast_conn = _forecast_conn_with_settlements(
            [{
                "city": "Singapore", "metric": "high", "target_date": "2026-05-01",
                "settlement_value": 25.0, "settlement_unit": "C",
                "created_at": settled_at,
            }]
        )
        topology_conn = _topology_conn_with_bins(
            [{"condition_id": pos["condition_id"], "range_low": 31.0, "range_high": 31.0, "range_label": "31C"}]
        )
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)
        monkeypatch.setattr("src.state.db.get_trade_connection_read_only", lambda: trade_conn)

        obs = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        from src.calibration.settlement_coverage_hierarchy import filter_observations_prefix

        # A decision strictly BEFORE the settlement finalized must not see it.
        assert filter_observations_prefix(obs, "2026-05-01T21:00:00+00:00") == []
        # A decision strictly AFTER must.
        assert len(filter_observations_prefix(obs, "2026-05-02T00:00:00+00:00")) == 1

    def test_chain_only_reconciliation_entry_method_excluded(self, flag_on, monkeypatch):
        # Foreign co-trading position -- no Zeus decision evidence behind it
        # (exclusion precedent: src/riskguard/riskguard.py:1095).
        foreign = _position_row(
            idx=0, city="Singapore", day=1, q_raw=0.86,
            entry_method="chain_only_reconciliation", condition_id="cond-foreign",
        )
        genuine = _position_row(
            idx=1, city="Singapore", day=2, q_raw=0.87,
            entry_method="ens_member_counting", condition_id="cond-genuine",
        )
        world_conn = _world_conn_with_claims([])
        trade_conn = _trade_conn_with_positions([foreign, genuine])
        forecast_conn = _forecast_conn_with_settlements(
            [
                {
                    "city": "Singapore", "metric": "high", "target_date": "2026-05-01",
                    "settlement_value": 25.0, "settlement_unit": "C",
                    "created_at": "2026-05-01T22:00:00+00:00",
                },
                {
                    "city": "Singapore", "metric": "high", "target_date": "2026-05-02",
                    "settlement_value": 25.0, "settlement_unit": "C",
                    "created_at": "2026-05-02T22:00:00+00:00",
                },
            ]
        )
        topology_conn = _topology_conn_with_bins(
            [
                {"condition_id": "cond-foreign", "range_low": 31.0, "range_high": 31.0, "range_label": "31C"},
                {"condition_id": "cond-genuine", "range_low": 31.0, "range_high": 31.0, "range_label": "31C"},
            ]
        )
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)
        monkeypatch.setattr("src.state.db.get_trade_connection_read_only", lambda: trade_conn)

        obs = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        assert len(obs) == 1
        assert obs[0].condition_or_market_id == "cond-genuine"

    def test_trades_db_unavailable_receipt_pool_unaffected(self, flag_on, monkeypatch):
        # add-data-must-never-block-serving: a structural fault opening the
        # trades DB must not affect the receipt-only pool at all.
        claims = [_claim(idx=0, city="Singapore", day=1, q_raw=0.84)]
        world_conn = _world_conn_with_claims(claims)
        forecast_conn = _forecast_conn_with_settlements(
            [{
                "city": "Singapore", "metric": "high", "target_date": "2026-05-01",
                "settlement_value": 25.0, "settlement_unit": "C",
                "created_at": "2026-05-01T18:00:00+00:00",
            }]
        )
        topology_conn = _topology_conn_with_bins(
            [{"condition_id": "cond-Singapore-0", "range_low": 31.0, "range_high": 31.0, "range_label": "31C"}]
        )
        monkeypatch.setattr("src.state.db.get_world_connection_read_only", lambda: world_conn)

        def _boom():
            raise RuntimeError("trades DB unreachable in this runtime")

        monkeypatch.setattr("src.state.db.get_trade_connection_read_only", _boom)

        obs = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        assert len(obs) == 1
        assert obs[0].condition_or_market_id == "cond-Singapore-0"
        assert obs[0].q_raw == pytest.approx(0.84)

    def test_merged_pool_licenses_a_level_that_receipts_alone_leave_insufficient(
        self, flag_on, monkeypatch
    ):
        # Receipts NEVER carry strategy_key (fix-spec finding) -- Level 1
        # STRATEGY_BUCKET can therefore never fire from receipts alone. Once
        # entered-position claims (real canonical strategy_key) are merged in,
        # the same query licenses/unlicenses at Level 1.
        from src.calibration.settlement_coverage_hierarchy import hierarchical_coverage_check

        cities = ["Singapore", "Tokyo", "Jakarta", "Beijing", "Ankara", "Auckland"]
        positions, settlements, bins = [], [], []
        for i in range(32):
            city = cities[i % len(cities)]
            day = (i % 28) + 1
            positions.append(
                _position_row(
                    idx=i, city=city, day=day, q_raw=0.86, direction="buy_no",
                    strategy_key="opening_inertia",
                )
            )
            won = i < 20  # 20/32 wins
            settlement_value = 31.0 if not won else 25.0
            settlements.append({
                "city": city, "metric": "high", "target_date": f"2026-05-{day:02d}",
                "settlement_value": settlement_value, "settlement_unit": "C",
                "created_at": f"2026-05-{day:02d}T18:00:00+00:00",
            })
            bins.append({
                "condition_id": positions[-1]["condition_id"],
                "range_low": 31.0, "range_high": 31.0, "range_label": "31C",
            })

        forecast_conn = _forecast_conn_with_settlements(settlements)
        topology_conn = _topology_conn_with_bins(bins)
        # Each call to _hierarchy_observations_all CLOSES the world/trade
        # connections it opens -- give each invocation a fresh connection
        # rather than reusing one across the before/after calls below.
        monkeypatch.setattr(
            "src.state.db.get_world_connection_read_only",
            lambda: _world_conn_with_claims([]),
        )

        query_kwargs = dict(
            city="Manila", metric="high", band_template="Will the highest temperature in Manila be 31C",
            direction="buy_no", strategy_key="opening_inertia", q_raw=0.86, q_lcb_raw=0.82,
        )

        # BEFORE: no trades DB -> receipts-only (empty) pool -> INSUFFICIENT_DATA.
        monkeypatch.setattr(
            "src.state.db.get_trade_connection_read_only",
            lambda: (_ for _ in ()).throw(RuntimeError("no trades DB")),
        )
        obs_before = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        pair_before = hierarchical_coverage_check(observations=obs_before, **query_kwargs)
        assert pair_before.status == "INSUFFICIENT_DATA"
        assert pair_before.level is None

        # AFTER: entered-position claims merged in -> Level 1 STRATEGY_BUCKET engages.
        monkeypatch.setattr(
            "src.state.db.get_trade_connection_read_only",
            lambda: _trade_conn_with_positions(positions),
        )
        obs_after = era._hierarchy_observations_all(
            forecast_conn=forecast_conn, topology_conn=topology_conn,
            coverage_cache=None, fail_closed_on_fault=True,
        )
        pair_after = hierarchical_coverage_check(observations=obs_after, **query_kwargs)
        assert pair_after.status != "INSUFFICIENT_DATA"
        assert pair_after.level == "STRATEGY_BUCKET"
        assert pair_after.n >= 30
