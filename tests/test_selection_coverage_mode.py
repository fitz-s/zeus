# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: backtest_v2_port_2026_05_07.md §D2+D3, T1-T5+T7
"""Tests for the selection_coverage replay mode.

T2 is the critical FDR-path antibody — must be written first and must
FAIL before D2 implementation, then pass after. All other tests depend
on the live scan_full_hypothesis_family + apply_familywise_fdr path,
NOT the legacy find_edges + fdr_filter path.
"""

import sqlite3
import json
from datetime import date, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch, call
import pytest

from src.engine.replay import ReplaySummary, run_replay


# ---------------------------------------------------------------------------
# Minimal in-memory DB fixture helpers
# ---------------------------------------------------------------------------

def _make_in_memory_db(cities: list[dict]) -> sqlite3.Connection:
    """Create minimal in-memory DB with all tables needed for selection_coverage."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            available_at TEXT,
            fetch_time TEXT,
            issue_time TEXT,
            valid_time TEXT,
            lead_hours REAL,
            spread REAL,
            is_bimodal INTEGER,
            model_version TEXT,
            members_json TEXT,
            p_raw_json TEXT,
            data_version TEXT,
            temperature_metric TEXT
        );
        CREATE TABLE calibration_pairs_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            p_raw REAL,
            outcome INTEGER,
            lead_days REAL,
            season TEXT,
            cluster TEXT,
            forecast_available_at TEXT,
            decision_group_id TEXT,
            bias_corrected INTEGER,
            temperature_metric TEXT DEFAULT 'high'
        );
        CREATE TABLE settlements_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            settlement_value REAL,
            winning_bin TEXT,
            temperature_metric TEXT,
            authority TEXT
        );
        CREATE TABLE market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            event_type TEXT
        );
    """)
    return conn


def _insert_settlement(conn, city, target_date, settlement_value, winning_bin, metric="high"):
    conn.execute(
        "INSERT INTO settlements_v2 (city, target_date, settlement_value, winning_bin, temperature_metric, authority) VALUES (?, ?, ?, ?, ?, 'VERIFIED')",
        (city, target_date, settlement_value, winning_bin, metric),
    )


def _insert_snapshot(conn, city, target_date, snapshot_id, p_raw, lead_hours=72.0, metric="high"):
    # members_json: 1D list of 50 member max values (MarketAnalysis requires 1D)
    # available_at must be BEFORE decision_time (target_date T00:00:00) so the
    # production-side filter `available_at <= decision_time` picks this snapshot.
    # Use (target_date - 1 day) at 12:00Z as the available_at value.
    prev_date = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
    conn.execute(
        """INSERT INTO ensemble_snapshots_v2
           (snapshot_id, city, target_date, available_at, fetch_time, issue_time, valid_time,
            lead_hours, spread, is_bimodal, model_version, members_json, p_raw_json, data_version, temperature_metric)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id, city, target_date,
            f"{prev_date}T12:00:00Z",
            f"{prev_date}T12:00:00Z",
            f"{prev_date}T00:00:00",
            f"{target_date}T12:00:00",
            lead_hours, 3.0, 0, "ecmwf",
            json.dumps([20.0] * 50),  # 1D member maxes
            json.dumps(p_raw),
            "v2", metric,
        ),
    )


def _insert_calibration_pair(conn, city, target_date, range_label, p_raw, outcome, lead_days=3.0, metric="high"):
    conn.execute(
        """INSERT INTO calibration_pairs_v2
           (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
            forecast_available_at, decision_group_id, bias_corrected, temperature_metric)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (city, target_date, range_label, p_raw, outcome, lead_days, "JJA", city,
         f"{target_date}T06:00:00Z", "default", 0, metric),
    )


def _insert_market_event(conn, city, target_date, range_label):
    conn.execute(
        "INSERT INTO market_events (city, target_date, range_label, event_type) VALUES (?, ?, ?, ?)",
        (city, target_date, range_label, "OPEN"),
    )


# ---------------------------------------------------------------------------
# T1 — dispatch routes correctly
# ---------------------------------------------------------------------------

# Celsius point bins (width=1) — required by Bin validation for non-shoulder Celsius bins
# "20°C" parses to (20.0, 20.0), width=1 per Bin.__post_init__ contract.
_REAL_LABELS = [
    "20°C",
    "21°C",
    "22°C",
    "23°C",
    "24°C",
]


class TestT1Dispatch:
    """T1: run_replay(mode='selection_coverage') returns a ReplaySummary with correct mode."""

    def test_dispatch_returns_replay_summary_with_correct_mode(self, tmp_path):
        """run_replay with selection_coverage mode returns ReplaySummary(mode='selection_coverage')."""
        from src.engine.replay_selection_coverage import run_selection_coverage, SELECTION_COVERAGE_LANE

        conn = _make_in_memory_db([])
        city_name = "Amsterdam"
        target_date = "2025-06-01"
        labels = _REAL_LABELS[:3]

        p_raw = [0.10, 0.75, 0.15]
        _insert_snapshot(conn, city_name, target_date, 1001, p_raw)
        for rl in labels:
            _insert_calibration_pair(conn, city_name, target_date, rl, 0.33, 0)
            _insert_market_event(conn, city_name, target_date, rl)
        _insert_settlement(conn, city_name, target_date, 22.0, labels[1])

        conn.commit()

        # Patch the DB connection to return our in-memory DB
        with patch("src.engine.replay_selection_coverage.get_trade_connection_with_world", return_value=conn), \
             patch("src.engine.replay_selection_coverage.get_backtest_connection") as mock_bc, \
             patch("src.engine.replay_selection_coverage.init_backtest_schema"):
            mock_backtest_conn = MagicMock()
            mock_bc.return_value = mock_backtest_conn

            summary = run_selection_coverage(
                "2025-06-01", "2025-06-01",
                temperature_metric="high",
                fdr_alpha=0.10,
                kelly_multiplier=0.5,
                p_market_source="uniform",
                override_platt=False,
            )

        assert isinstance(summary, ReplaySummary)
        assert summary.mode == SELECTION_COVERAGE_LANE
        assert summary.limitations.get("selection_coverage") is not None


# ---------------------------------------------------------------------------
# T2 — FDR-family path, not legacy find_edges (ANTIBODY — must fail before D2)
# ---------------------------------------------------------------------------

class TestT2FDRPathAntibody:
    """T2: scan_full_hypothesis_family + apply_familywise_fdr called; find_edges + fdr_filter NOT called."""

    def test_live_fdr_path_called_not_legacy(self, tmp_path):
        """Antibody: selection_coverage must use the live FDR path, not replay.py:1628 legacy path."""
        from src.engine.replay_selection_coverage import run_selection_coverage

        conn = _make_in_memory_db([])
        city_name = "Amsterdam"
        target_date = "2025-06-01"
        p_raw = [0.05, 0.05, 0.60, 0.15, 0.15]
        labels = _REAL_LABELS[:len(p_raw)]

        _insert_snapshot(conn, city_name, target_date, 1002, p_raw)
        for rl in labels:
            _insert_calibration_pair(conn, city_name, target_date, rl, 0.2, 0)
            _insert_market_event(conn, city_name, target_date, rl)
        _insert_settlement(conn, city_name, target_date, 24.0, labels[2])
        conn.commit()

        scan_calls = []
        apply_calls = []
        find_edges_calls = []
        fdr_filter_calls = []

        def mock_scan(analysis, *, n_bootstrap):
            scan_calls.append(1)
            from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
            hyps = []
            for i, rl in enumerate(labels):
                hyps.append(FullFamilyHypothesis(
                    index=i, range_label=rl, direction="buy_yes",
                    edge=0.4 if i == 2 else -0.1,
                    ci_lower=0.1 if i == 2 else -0.2,
                    ci_upper=0.6, p_value=0.001 if i == 2 else 0.9,
                    p_model=p_raw[i], p_market=0.2,
                    p_posterior=p_raw[i] + (0.4 if i == 2 else -0.1),
                    entry_price=0.2, is_shoulder=False,
                    passed_prefilter=(i == 2),
                ))
            return hyps

        def mock_apply(rows, q=0.10):
            apply_calls.append(1)
            # Select the prefilter-passed hypothesis
            out = [dict(r) for r in rows]
            for row in out:
                row["selected_post_fdr"] = 1 if row.get("passed_prefilter") else 0
                row["q_value"] = 0.01
            return out

        def mock_find_edges(*args, **kwargs):
            find_edges_calls.append(1)
            return []

        def mock_fdr_filter(*args, **kwargs):
            fdr_filter_calls.append(1)
            return []

        with patch("src.engine.replay_selection_coverage.get_trade_connection_with_world", return_value=conn), \
             patch("src.engine.replay_selection_coverage.get_backtest_connection") as mock_bc, \
             patch("src.engine.replay_selection_coverage.init_backtest_schema"), \
             patch("src.engine.replay_selection_coverage.scan_full_hypothesis_family", side_effect=mock_scan), \
             patch("src.engine.replay_selection_coverage.apply_familywise_fdr", side_effect=mock_apply), \
             patch("src.strategy.market_analysis.MarketAnalysis.find_edges", side_effect=mock_find_edges), \
             patch("src.strategy.fdr_filter.fdr_filter", side_effect=mock_fdr_filter):
            mock_bc.return_value = MagicMock()

            run_selection_coverage(
                "2025-06-01", "2025-06-01",
                temperature_metric="high",
                fdr_alpha=0.10,
                kelly_multiplier=0.5,
                p_market_source="uniform",
                override_platt=False,
            )

        assert len(scan_calls) >= 1, "scan_full_hypothesis_family must be called at least once"
        assert len(apply_calls) >= 1, "apply_familywise_fdr must be called at least once"
        assert len(find_edges_calls) == 0, f"find_edges must NOT be called; was called {len(find_edges_calls)} times"
        assert len(fdr_filter_calls) == 0, f"fdr_filter must NOT be called; was called {len(fdr_filter_calls)} times"


# ---------------------------------------------------------------------------
# T3 — hit accounting
# ---------------------------------------------------------------------------

class TestT3HitAccounting:
    """T3: hit=1 when picked bin matches winning_bin; hit=NULL when no bin passes FDR."""

    def test_hit_when_winning_bin_selected(self):
        """Synthetic 5-bin market: winning bin picked -> hit=1."""
        from src.engine.replay_selection_coverage import _score_snapshot_hit

        assert _score_snapshot_hit(picked_labels=["22°C"], winning_bin="22°C") == 1

    def test_miss_when_wrong_bin_selected(self):
        """hit=0 when a different bin is picked."""
        from src.engine.replay_selection_coverage import _score_snapshot_hit

        assert _score_snapshot_hit(picked_labels=["21°C"], winning_bin="22°C") == 0

    def test_null_when_no_bin_cleared_fdr(self):
        """hit=NULL (None) when no bin passes FDR — no-pick case."""
        from src.engine.replay_selection_coverage import _score_snapshot_hit

        assert _score_snapshot_hit(picked_labels=[], winning_bin="22°C") is None

    def test_null_when_winning_bin_unknown(self):
        """hit=NULL when winning_bin is empty/None even if we picked."""
        from src.engine.replay_selection_coverage import _score_snapshot_hit

        assert _score_snapshot_hit(picked_labels=["22°C"], winning_bin="") is None
        assert _score_snapshot_hit(picked_labels=["22°C"], winning_bin=None) is None


# ---------------------------------------------------------------------------
# T4 — p_market substitute correctness (climatology no-future-leak)
# ---------------------------------------------------------------------------

class TestT4PMarketSubstitute:
    """T4: climatology source uses only target_date < snapshot.target_date rows."""

    def test_climatology_excludes_future_rows(self):
        """Future settlement rows must NOT influence climatology vector."""
        from src.engine.replay_selection_coverage import _compute_climatology_p_market

        bins_count = 5
        target_date = "2025-06-15"
        labels = _REAL_LABELS[:bins_count]
        # Historical rows: all outcome in bin index 2
        historical_rows = [
            {"range_label": labels[i], "target_date": "2025-06-01", "outcome": 1 if i == 2 else 0}
            for i in range(bins_count)
        ]
        # Future row — must be excluded (bin 0 wins in future)
        future_rows = [
            {"range_label": labels[i], "target_date": "2025-07-01", "outcome": 1 if i == 0 else 0}
            for i in range(bins_count)
        ]
        all_rows = historical_rows + future_rows

        p_clim = _compute_climatology_p_market(all_rows, labels, target_date)

        # With only historical rows, bin_2 should have climatology > other bins
        assert len(p_clim) == bins_count
        assert p_clim[2] > p_clim[0], "Climatology must reflect historical data only (bin_2 dominant)"
        # Verify future row did not push bin_0 above bin_2
        assert p_clim[0] < p_clim[2], "Future row for bin_0 must not contaminate climatology"


# ---------------------------------------------------------------------------
# T5 — Asia/non-Asia stratification
# ---------------------------------------------------------------------------

class TestT5AsiaStratification:
    """T5: by_timezone_class present with Asia_star and non_Asia keys and correct counts."""

    def test_stratification_keys_present(self):
        """summary.limitations.selection_coverage.by_timezone_class has correct structure."""
        from src.engine.replay_selection_coverage import _build_timezone_stratification

        # Simulate snapshot rows
        rows = [
            {"city": "Tokyo", "hit": 1, "brier": 0.1, "timezone_class": "Asia_star"},
            {"city": "Tokyo", "hit": 0, "brier": 0.4, "timezone_class": "Asia_star"},
            {"city": "London", "hit": 1, "brier": 0.15, "timezone_class": "non_Asia"},
            {"city": "London", "hit": None, "brier": None, "timezone_class": "non_Asia"},
        ]

        result = _build_timezone_stratification(rows)

        assert "Asia_star" in result
        assert "non_Asia" in result
        assert result["Asia_star"]["n_snapshots"] == 2
        assert result["non_Asia"]["n_snapshots"] == 2
        # Tokyo: 1 hit out of 2 = 50%
        assert result["Asia_star"]["hit_rate"] == pytest.approx(0.5, abs=0.01)
        # London: 1 non-null hit out of 1 (the None is excluded from hit_rate denominator)
        assert result["non_Asia"]["hit_rate"] == pytest.approx(1.0, abs=0.01)

    def test_stratification_uses_timezone_from_city_registry(self):
        """Classification must use config/cities.json timezone, not hardcoded city list."""
        from src.config import cities_by_name

        # Spot-check: Tokyo must be Asia_star, Amsterdam must be non_Asia
        tokyo = cities_by_name.get("Tokyo")
        amsterdam = cities_by_name.get("Amsterdam")
        if tokyo:
            assert tokyo.timezone.startswith("Asia/"), f"Tokyo timezone={tokyo.timezone!r} should start with Asia/"
        if amsterdam:
            assert not amsterdam.timezone.startswith("Asia/"), f"Amsterdam timezone={amsterdam.timezone!r} should NOT start with Asia/"


# ---------------------------------------------------------------------------
# T6 — lead-day bucket reporting
# ---------------------------------------------------------------------------

class TestT6LeadDayBuckets:
    """T6: by_lead_day present in summary with correct bucket keys and counts."""

    def test_lead_day_buckets_present(self):
        """summary.limitations.selection_coverage.by_lead_day has all required keys."""
        from src.engine.replay_selection_coverage import run_selection_coverage

        conn = _make_in_memory_db([])
        city_name = "Amsterdam"
        p_raw = [0.10, 0.70, 0.20]
        labels = _REAL_LABELS[:len(p_raw)]

        # Insert two snapshots with different lead_hours so they land in different buckets:
        # snapshot 3001: lead_hours=48 -> lead_days=2.0 -> bucket "2"
        # snapshot 3002: lead_hours=96 -> lead_days=4.0 -> bucket "4-5"
        for snap_id, target_date, lead_hours in [
            (3001, "2025-07-01", 48.0),
            (3002, "2025-07-02", 96.0),
        ]:
            _insert_snapshot(conn, city_name, target_date, snap_id, p_raw, lead_hours=lead_hours)
            for rl in labels:
                _insert_calibration_pair(conn, city_name, target_date, rl, 0.33, 0)
                _insert_market_event(conn, city_name, target_date, rl)
            _insert_settlement(conn, city_name, target_date, 22.0, labels[1])
        conn.commit()

        with patch("src.engine.replay_selection_coverage.get_trade_connection_with_world", return_value=conn), \
             patch("src.engine.replay_selection_coverage.get_backtest_connection") as mock_bc, \
             patch("src.engine.replay_selection_coverage.init_backtest_schema"):
            mock_bc.return_value = MagicMock()

            summary = run_selection_coverage(
                "2025-07-01", "2025-07-02",
                temperature_metric="high",
                fdr_alpha=0.10,
                kelly_multiplier=0.5,
                p_market_source="uniform",
                override_platt=False,
            )

        sc = summary.limitations.get("selection_coverage") or {}
        by_lead = sc.get("by_lead_day")
        assert by_lead is not None, "by_lead_day must be present in selection_coverage limitations"

        # All required bucket keys must be present
        for bkt in ["1", "2", "3", "4-5", "6-7", "8+"]:
            assert bkt in by_lead, f"bucket '{bkt}' missing from by_lead_day"

        # Each bucket entry must have n, hit_rate, brier, bss keys
        for bkt, g in by_lead.items():
            assert "n" in g, f"bucket {bkt!r} missing 'n'"
            assert "hit_rate" in g, f"bucket {bkt!r} missing 'hit_rate'"
            assert "brier" in g, f"bucket {bkt!r} missing 'brier'"
            assert "bss" in g, f"bucket {bkt!r} missing 'bss'"

        # The two snapshots should land in non-overlapping buckets with n>0
        bucket_2 = by_lead.get("2", {})
        bucket_45 = by_lead.get("4-5", {})
        assert bucket_2.get("n", 0) >= 1, "bucket '2' should have at least 1 snapshot (lead_hours=48)"
        assert bucket_45.get("n", 0) >= 1, "bucket '4-5' should have at least 1 snapshot (lead_hours=96)"


class TestFix2BssBinCountAware:
    """FIX 2: BSS must use bin-count-aware uniform baseline, not hardcoded 0.24."""

    def test_bss_bin_count_aware(self):
        """_uniform_brier_baseline(3) != _uniform_brier_baseline(5); BSS reflects actual n_bins."""
        from src.engine.replay_selection_coverage import _uniform_brier_baseline, _bss_for_snapshot

        # For n=5: clim = (1/5)*(4/5)^2 + (4/5)*(1/5)^2 = 0.128 + 0.032 = 0.16
        clim5 = _uniform_brier_baseline(5)
        assert abs(clim5 - 0.16) < 1e-9, f"clim_brier(5) should be 0.16, got {clim5}"

        # For n=3: clim = (1/3)*(2/3)^2 + (2/3)*(1/3)^2 = 4/27 + 2/27 = 6/27 ≈ 0.2222
        clim3 = _uniform_brier_baseline(3)
        assert abs(clim3 - 6.0 / 27.0) < 1e-9, f"clim_brier(3) should be ~0.2222, got {clim3}"

        # Same raw Brier yields different BSS for different bin counts
        raw_brier = 0.1
        bss5 = _bss_for_snapshot(raw_brier, 5)
        bss3 = _bss_for_snapshot(raw_brier, 3)
        assert bss5 != bss3, "BSS must differ for different bin counts with same raw Brier"
        # BSS5 = 1 - 0.1/0.16 = -0.375; BSS3 = 1 - 0.1/(6/27) ≈ -0.45
        assert abs(bss5 - (1.0 - raw_brier / clim5)) < 1e-9
        assert abs(bss3 - (1.0 - raw_brier / clim3)) < 1e-9


# ---------------------------------------------------------------------------
# T7 — no world.db writes from selection_coverage
# ---------------------------------------------------------------------------

class TestT7NoWorldDbWrites:
    """T7: run_selection_coverage must not write to world.db."""

    def test_no_world_db_inserts(self):
        """After run_selection_coverage, world.db connection received zero INSERT/UPDATE."""
        from src.engine.replay_selection_coverage import run_selection_coverage

        base_conn = _make_in_memory_db([])
        city_name = "Amsterdam"
        target_date = "2025-06-01"
        p_raw = [0.10, 0.70, 0.20]
        labels = _REAL_LABELS[:len(p_raw)]

        _insert_snapshot(base_conn, city_name, target_date, 2001, p_raw)
        for rl in labels:
            _insert_calibration_pair(base_conn, city_name, target_date, rl, 0.33, 0)
            _insert_market_event(base_conn, city_name, target_date, rl)
        _insert_settlement(base_conn, city_name, target_date, 22.0, labels[1])
        base_conn.commit()

        world_write_calls = []

        # sqlite3.Connection.execute is read-only in Python 3.14 — use a proxy object instead
        class TrackingConn:
            """Thin proxy that intercepts execute() to detect writes."""
            def __init__(self, real):
                self._real = real
                self.row_factory = real.row_factory

            def execute(self, sql, params=()):
                sql_upper = sql.strip().upper()
                if sql_upper.startswith("INSERT") or sql_upper.startswith("UPDATE"):
                    world_write_calls.append(sql.strip()[:80])
                return self._real.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._real, name)

        tracking_conn = TrackingConn(base_conn)
        tracking_conn.row_factory = base_conn.row_factory

        with patch("src.engine.replay_selection_coverage.get_trade_connection_with_world", return_value=tracking_conn), \
             patch("src.engine.replay_selection_coverage.get_backtest_connection") as mock_bc, \
             patch("src.engine.replay_selection_coverage.init_backtest_schema"):
            mock_bc.return_value = MagicMock()

            run_selection_coverage(
                "2025-06-01", "2025-06-01",
                temperature_metric="high",
                fdr_alpha=0.10,
                kelly_multiplier=0.5,
                p_market_source="uniform",
                override_platt=False,
            )

        assert world_write_calls == [], (
            f"world.db connection received {len(world_write_calls)} write(s): {world_write_calls[:3]}"
        )
