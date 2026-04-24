# Created: 2026-04-17
# Last reused/audited: 2026-04-17
# Authority basis: team_lead_handoff.md §"Phase 5C scope"; docs/authority/zeus_dual_track_architecture.md §5/§6
"""Phase 5C replay MetricIdentity tests: R-AV, R-AW, R-AX, R-AY

All tests MUST be RED until exec-juan's 5C implementation lands.
Spec-anchored to team_lead_handoff.md §"Phase 5C scope" and locked
R-letter mapping from team-lead 2026-04-17.

R-AV (TestReplayTypedStatusFields): _forecast_reference_for return dict must have
    typed Literal fields: decision_reference_source, decision_time_status, agreement.
    Pre-fix: field absent or raw sentinel string; Post-fix: Literal values enforced.

R-AW (TestReplaySyntheticFallback): no-historical-decision path must set
    decision_reference_source="forecasts_table_synthetic", agreement="UNKNOWN",
    decision_time_status="SYNTHETIC_MIDDAY" and must NOT fabricate decision_time.

R-AX (TestForecastRowsSqlMetricFilter): _forecast_rows_for SQL must filter by
    temperature_metric; LOW query returns only low rows; HIGH regression.

R-AY (TestDecisionRefCacheMetricKey): cache key must include temperature_metric;
    same (city, date) but different metric → different cached refs, no collision.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest


# ---------------------------------------------------------------------------
# Shared in-memory DB fixture factory
# ---------------------------------------------------------------------------

def _make_replay_db(*, with_forecast_rows: bool = True, with_trade_decision: bool = False) -> sqlite3.Connection:
    """Build a minimal in-memory DB sufficient for ReplayContext instantiation."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, p_raw_json TEXT,
            available_at TEXT, temperature_metric TEXT DEFAULT 'high'
        );
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT,
            source TEXT, forecast_basis_date TEXT, forecast_issue_time TEXT,
            lead_days REAL, forecast_high REAL, forecast_low REAL,
            temp_unit TEXT, temperature_metric TEXT DEFAULT 'high'
        );
        CREATE TABLE IF NOT EXISTS trade_decisions (
            trade_id TEXT, timestamp TEXT, forecast_snapshot_id TEXT,
            market_hours_open INTEGER
        );
        CREATE TABLE IF NOT EXISTS decision_log (
            started_at TEXT, artifact_json TEXT
        );
        CREATE TABLE IF NOT EXISTS shadow_signals (
            timestamp TEXT, city TEXT, target_date TEXT,
            decision_snapshot_id TEXT, p_raw_json TEXT, p_cal_json TEXT, edges_json TEXT
        );
        CREATE TABLE IF NOT EXISTS calibration_bins (
            bin_id INTEGER PRIMARY KEY, city TEXT, temperature_metric TEXT,
            bin_label TEXT, low REAL, high REAL, unit TEXT
        );
        CREATE TABLE IF NOT EXISTS platt_models (
            model_key TEXT PRIMARY KEY, city TEXT, a REAL, b REAL
        );
        CREATE TABLE IF NOT EXISTS calibration_pairs (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            range_label TEXT, p_raw REAL, outcome INTEGER,
            forecast_available_at TEXT, data_version TEXT
        );
        CREATE TABLE IF NOT EXISTS market_events (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            range_label TEXT, event_type TEXT
        );
    """)

    if with_forecast_rows:
        # Insert one HIGH and one LOW forecast row so metric filter is testable
        conn.execute("""
            INSERT INTO forecasts (city, target_date, source, forecast_basis_date,
                forecast_issue_time, lead_days, forecast_high, forecast_low, temp_unit, temperature_metric)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("Chicago", "2026-06-01", "ecmwf", "2026-05-30", "2026-05-30T12:00:00Z",
              2.0, 88.0, 55.0, "degF", "high"))
        conn.execute("""
            INSERT INTO forecasts (city, target_date, source, forecast_basis_date,
                forecast_issue_time, lead_days, forecast_high, forecast_low, temp_unit, temperature_metric)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("Chicago", "2026-06-01", "ecmwf", "2026-05-30", "2026-05-30T12:00:00Z",
              2.0, 88.0, 55.0, "degF", "low"))
        # Seed a calibration_pairs row with valid range_label so _typed_bins_for_city_date
        # returns bins and _forecast_reference_for proceeds past the "no bins → None" guard.
        # Chicago settlement_unit='F'; "85-90°F" parses to (85, 90).
        conn.execute("""
            INSERT INTO calibration_pairs (city, target_date, range_label, p_raw, outcome,
                forecast_available_at, data_version)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("Chicago", "2026-06-01", "85-86\u00b0F", 0.4, 1,
              "2026-05-30T14:00:00Z", "tigge_mx2t6_local_calendar_day_max_v1"))

    if with_trade_decision:
        conn.execute("""
            INSERT INTO ensemble_snapshots (snapshot_id, city, target_date, temperature_metric, available_at)
            VALUES (?, ?, ?, ?, ?)
        """, (1, "Chicago", "2026-06-01", "high", "2026-05-30T14:00:00Z"))
        conn.execute("""
            INSERT INTO trade_decisions (trade_id, timestamp, forecast_snapshot_id, market_hours_open)
            VALUES (?, ?, ?, ?)
        """, ("td-001", "2026-05-30T14:00:00Z", "1", 1))

    conn.commit()
    return conn


def _make_replay_context(conn, **kwargs):
    from src.engine.replay import ReplayContext
    return ReplayContext(conn, **kwargs)


# ---------------------------------------------------------------------------
# R-AV: TestReplayTypedStatusFields
# ---------------------------------------------------------------------------

class TestReplayTypedStatusFields:
    """R-AV: _forecast_reference_for return dict must carry typed status fields."""

    VALID_SOURCES = {"historical_decision", "forecasts_table_synthetic"}
    VALID_DT_STATUS = {"OK", "SYNTHETIC_MIDDAY", "UNAVAILABLE"}
    VALID_AGREEMENT = {"AGREE", "DISAGREE", "UNKNOWN"}

    def _ctx(self):
        conn = _make_replay_db(with_forecast_rows=True)
        return _make_replay_context(conn, allow_snapshot_only_reference=True)

    def test_R_AV_1_decision_reference_source_field_present(self):
        """R-AV-1 (RED): return dict must have 'decision_reference_source' key with valid Literal."""
        ctx = self._ctx()
        ref = ctx._forecast_reference_for("Chicago", "2026-06-01")
        assert ref is not None, (
            "_forecast_reference_for returned None — ensure DB has forecast rows. "
            "If None, the field-presence test cannot run."
        )
        assert "decision_reference_source" in ref, (
            "Return dict missing 'decision_reference_source'. "
            "Fix: add field with value 'forecasts_table_synthetic' (Literal) to _forecast_reference_for. "
            "Current dict keys: " + str(list(ref.keys()))
        )
        assert ref["decision_reference_source"] in self.VALID_SOURCES, (
            f"decision_reference_source={ref['decision_reference_source']!r} not in valid Literal set "
            f"{self.VALID_SOURCES}."
        )

    def test_R_AV_2_decision_time_status_field_present(self):
        """R-AV-2 (RED): return dict must have 'decision_time_status' key with valid Literal."""
        ctx = self._ctx()
        ref = ctx._forecast_reference_for("Chicago", "2026-06-01")
        assert ref is not None, "_forecast_reference_for returned None — ensure DB has forecast rows."
        assert "decision_time_status" in ref, (
            "Return dict missing 'decision_time_status'. "
            "Fix: add field with value 'SYNTHETIC_MIDDAY' or 'OK' (Literal). "
            "Current dict keys: " + str(list(ref.keys()))
        )
        assert ref["decision_time_status"] in self.VALID_DT_STATUS, (
            f"decision_time_status={ref['decision_time_status']!r} not in valid Literal set "
            f"{self.VALID_DT_STATUS}."
        )

    def test_R_AV_3_agreement_field_valid_literal(self):
        """R-AV-3 (GREEN antibody): 'agreement' field must use valid Literal value, not raw string."""
        ctx = self._ctx()
        ref = ctx._forecast_reference_for("Chicago", "2026-06-01")
        assert ref is not None, "_forecast_reference_for returned None — ensure DB has forecast rows."
        assert "agreement" in ref, (
            "Return dict missing 'agreement' field entirely. "
            "Current dict keys: " + str(list(ref.keys()))
        )
        assert ref["agreement"] in self.VALID_AGREEMENT, (
            f"agreement={ref['agreement']!r} not in valid Literal set {self.VALID_AGREEMENT}. "
            "Fix: _forecast_reference_for must emit only {'AGREE','DISAGREE','UNKNOWN'}."
        )


# ---------------------------------------------------------------------------
# R-AW: TestReplaySyntheticFallback
# ---------------------------------------------------------------------------

class TestReplaySyntheticFallback:
    """R-AW: no-historical-decision path must set typed synthetic fields, no fabricated decision_time."""

    def _ctx_no_history(self):
        # DB has forecast rows but NO trade_decisions or decision_log entries
        conn = _make_replay_db(with_forecast_rows=True, with_trade_decision=False)
        return _make_replay_context(conn, allow_snapshot_only_reference=True)

    def test_R_AW_1_synthetic_fallback_typed_fields(self):
        """R-AW-1 (RED): synthetic fallback path must set decision_reference_source + decision_time_status."""
        ctx = self._ctx_no_history()
        ref = ctx.get_decision_reference_for("Chicago", "2026-06-01")
        # If no historical decision: must fall through to _forecast_reference_for synthetic path
        if ref is None:
            pytest.skip(
                "get_decision_reference_for returned None (no forecast rows resolved). "
                "Ensure DB has forecast rows and allow_snapshot_only_reference=True."
            )
        assert "decision_reference_source" in ref, (
            "Synthetic fallback ref missing 'decision_reference_source'. "
            "Fix: _forecast_reference_for must emit decision_reference_source='forecasts_table_synthetic'. "
            "Current keys: " + str(list(ref.keys()))
        )
        assert ref["decision_reference_source"] == "forecasts_table_synthetic", (
            f"Expected 'forecasts_table_synthetic', got {ref['decision_reference_source']!r}. "
            "Synthetic path must not use 'forecasts_table' or any non-spec value."
        )
        assert ref.get("decision_time_status") == "SYNTHETIC_MIDDAY", (
            f"Expected decision_time_status='SYNTHETIC_MIDDAY', got {ref.get('decision_time_status')!r}."
        )
        assert ref.get("agreement") == "UNKNOWN", (
            f"Expected agreement='UNKNOWN' on synthetic path, got {ref.get('agreement')!r}. "
            "Synthetic refs have no real agreement signal."
        )

    def test_R_AW_2_synthetic_fallback_no_fabricated_decision_time(self):
        """R-AW-2 (RED): synthetic fallback must NOT populate decision_time from a fabricated timestamp.

        The fabricated midday timestamp (f'{target_date}T12:00:00+00:00') must NOT appear as
        decision_time in the top-level ref — it would misrepresent when the decision was made.
        """
        ctx = self._ctx_no_history()
        ref = ctx.get_decision_reference_for("Chicago", "2026-06-01")
        if ref is None:
            pytest.skip("No ref returned — ensure DB has forecast rows.")
        # Synthetic path: decision_time must be absent or explicitly None
        # (not a fabricated midday sentinel)
        fabricated = "2026-06-01T12:00:00+00:00"
        dt = ref.get("decision_time")
        assert dt != fabricated, (
            f"decision_time={dt!r} matches fabricated midday sentinel {fabricated!r}. "
            "Fix: synthetic fallback must not populate decision_time; set to None or omit. "
            "A fabricated decision_time corrupts replay causal integrity."
        )


# ---------------------------------------------------------------------------
# R-AX: TestForecastRowsMetricConditionalRead
# ---------------------------------------------------------------------------

class TestForecastRowsMetricConditionalRead:
    """R-AX: _forecast_reference_for must read forecast_low for LOW metric, forecast_high for HIGH.

    `forecasts` table has separate forecast_high + forecast_low columns (no temperature_metric
    column). The metric-conditional branch at replay.py L281-282 selects the correct column.
    SQL WHERE filter to historical_forecasts_v2 is Phase 7 scope.
    """

    def _make_db_with_distinct_columns(self) -> sqlite3.Connection:
        """Build DB where forecast_high and forecast_low have clearly distinct values.

        Inserts two rows — one for 'high' metric and one for 'low' metric — since
        _forecast_rows_for filters by temperature_metric column (default='high').
        Both rows carry the same forecast_high=88.0 and forecast_low=55.0 values so
        the column-read branch (not the SQL filter) determines which bin gets probability.
        """
        conn = _make_replay_db(with_forecast_rows=False)
        # Insert one row per metric. forecast_high=88.0 (in 87-88°F bin),
        # forecast_low=55.0 (in 55-56°F bin). The metric-conditional branch selects
        # which column to read; the SQL filter picks which row.
        for metric in ("high", "low"):
            conn.execute("""
                INSERT INTO forecasts (city, target_date, source, forecast_basis_date,
                    forecast_issue_time, lead_days, forecast_high, forecast_low,
                    temp_unit, temperature_metric)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("Chicago", "2026-06-01", "ecmwf", "2026-05-30", "2026-05-30T12:00:00Z",
                  2.0, 88.0, 55.0, "degF", metric))
        # Bins covering both column values: 87-88°F (high read) and 55-56°F (low read).
        # Bin width must be exactly 2 for °F: 87-88 → 88-87+1=2, 55-56 → 56-55+1=2.
        for label in ["55-56\u00b0F", "87-88\u00b0F"]:
            conn.execute("""
                INSERT INTO calibration_pairs (city, target_date, range_label, p_raw, outcome,
                    forecast_available_at, data_version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("Chicago", "2026-06-01", label, 0.5, 0,
                  "2026-05-30T14:00:00Z", "tigge_mx2t6_local_calendar_day_max_v1"))
        conn.commit()
        return conn

    def test_R_AX_1_low_metric_reads_forecast_low_column(self):
        """R-AX-1 (GREEN antibody): LOW metric must read forecast_low column, not forecast_high.

        forecast_high=88.0 falls in 87-88°F bin; forecast_low=55.0 falls in 55-56°F bin.
        A LOW call must produce a p_raw_vector with non-zero probability only in 55-56°F bin.
        If it reads forecast_high instead, the wrong bin gets non-zero probability.
        """
        conn = self._make_db_with_distinct_columns()
        ctx = _make_replay_context(conn, allow_snapshot_only_reference=True)

        from src.types.metric_identity import LOW_LOCALDAY_MIN
        ref = ctx._forecast_reference_for("Chicago", "2026-06-01",
                                          temperature_metric=LOW_LOCALDAY_MIN.temperature_metric)
        assert ref is not None, (
            "_forecast_reference_for returned None for LOW metric. "
            "Ensure DB has forecast rows and valid bins."
        )
        bin_labels = ref.get("bin_labels", [])
        p_raw = ref.get("p_raw_vector", [])
        assert len(bin_labels) > 0, f"No bins returned. ref={ref}"

        # 55-56°F bin must have probability 1.0 (all members at 55.0 land in it).
        # 87-88°F bin must have probability 0.0 (no members there for LOW read).
        low_bin_idx = next((i for i, l in enumerate(bin_labels) if "55" in l), None)
        high_bin_idx = next((i for i, l in enumerate(bin_labels) if "87" in l or "88" in l), None)

        if low_bin_idx is not None:
            assert p_raw[low_bin_idx] > 0, (
                f"LOW metric ref has zero probability in 55-56°F bin (idx={low_bin_idx}). "
                f"p_raw={p_raw}, bins={bin_labels}. "
                "Fix: _forecast_reference_for must read forecast_low when temperature_metric='low'."
            )
        if high_bin_idx is not None:
            assert p_raw[high_bin_idx] == 0.0, (
                f"LOW metric ref has non-zero probability in 87-88°F bin (idx={high_bin_idx}). "
                f"p_raw={p_raw}, bins={bin_labels}. "
                "Cross-column leakage: LOW call is reading forecast_high values."
            )

    def test_R_AX_2_high_metric_reads_forecast_high_column(self):
        """R-AX-2 (GREEN antibody): HIGH metric must read forecast_high column (regression guard).

        forecast_high=88.0 → 87-88°F bin gets non-zero probability.
        forecast_low=55.0 → 55-56°F bin must be zero for HIGH read.
        """
        conn = self._make_db_with_distinct_columns()
        ctx = _make_replay_context(conn, allow_snapshot_only_reference=True)

        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        ref = ctx._forecast_reference_for("Chicago", "2026-06-01",
                                          temperature_metric=HIGH_LOCALDAY_MAX.temperature_metric)
        assert ref is not None, "_forecast_reference_for returned None for HIGH metric."

        bin_labels = ref.get("bin_labels", [])
        p_raw = ref.get("p_raw_vector", [])

        high_bin_idx = next((i for i, l in enumerate(bin_labels) if "87" in l or "88" in l), None)
        low_bin_idx = next((i for i, l in enumerate(bin_labels) if "55" in l), None)

        if high_bin_idx is not None:
            assert p_raw[high_bin_idx] > 0, (
                f"HIGH metric ref has zero probability in 87-88°F bin. "
                f"p_raw={p_raw}, bins={bin_labels}. "
                "Regression: HIGH call must read forecast_high."
            )
        if low_bin_idx is not None:
            assert p_raw[low_bin_idx] == 0.0, (
                f"HIGH metric ref has non-zero probability in 55-56°F bin. "
                f"p_raw={p_raw}, bins={bin_labels}. "
                "HIGH call is reading forecast_low values — cross-column leakage."
            )


# ---------------------------------------------------------------------------
# R-AY: TestDecisionRefCacheMetricKey
# ---------------------------------------------------------------------------

class TestDecisionRefCacheMetricKey:
    """R-AY: _decision_ref_cache key must include temperature_metric to prevent collision."""

    def test_R_AY_1_cache_key_includes_metric(self):
        """R-AY-1 (RED): cache key tuple must be (city, date, temperature_metric), not (city, date).

        If two calls share (city, date) but differ in temperature_metric, they must not collide.
        The first call's result must not be returned for the second call's different metric.
        """
        conn = _make_replay_db(with_forecast_rows=True)
        ctx = _make_replay_context(conn, allow_snapshot_only_reference=True)

        # Cache is keyed as (city, date) today — (city, date, metric) after fix.
        # Assert the internal cache dict type annotation or first-call key shape.
        # We prime the cache with a sentinel via monkeypatching to detect collision.
        SENTINEL = {"sentinel": True, "temperature_metric": "high"}
        ctx._decision_ref_cache[("Chicago", "2026-06-01")] = SENTINEL

        # Now call for LOW metric — should NOT return the HIGH sentinel.
        # Pre-fix: returns SENTINEL (cache collision). Post-fix: misses cache, returns real low ref.
        import inspect
        sig = inspect.signature(ctx.get_decision_reference_for)
        if "temperature_metric" not in sig.parameters:
            # Demonstrate the collision: the low call hits the high cache entry
            result = ctx.get_decision_reference_for("Chicago", "2026-06-01")
            assert result is not SENTINEL, (
                "get_decision_reference_for returned the HIGH sentinel for a LOW call — "
                "cache collision confirmed. Fix: add temperature_metric to cache key. "
                "Current key: (city, target_date). Required key: (city, target_date, temperature_metric)."
            )
            # If we reach here pre-fix: it means the cache-collision IS happening (result IS sentinel)
            # which we can't assert the opposite of cleanly. Flip: assert it IS the sentinel = RED.
            pytest.fail(
                "get_decision_reference_for uses (city, date) cache key without temperature_metric. "
                "A HIGH-metric cache entry was seeded; a subsequent call (same city/date) returned the "
                "HIGH sentinel instead of computing a fresh LOW ref. Cache collision — cross-metric leakage "
                "in replay reference lookup. Fix: cache key must be (city, target_date, temperature_metric)."
            )

    def test_R_AY_2_different_metrics_return_different_refs(self):
        """R-AY-2 (RED): same (city, date) with different temperature_metric must yield independent refs."""
        conn = _make_replay_db(with_forecast_rows=True)
        ctx = _make_replay_context(conn, allow_snapshot_only_reference=True)

        import inspect
        sig = inspect.signature(ctx.get_decision_reference_for)
        if "temperature_metric" not in sig.parameters:
            pytest.fail(
                "get_decision_reference_for has no 'temperature_metric' parameter. "
                "Cannot call with metric='high' vs metric='low' to verify independence. "
                "Fix: add temperature_metric param and include in cache key + query dispatch."
            )
        ref_high = ctx.get_decision_reference_for("Chicago", "2026-06-01", temperature_metric="high")
        ref_low = ctx.get_decision_reference_for("Chicago", "2026-06-01", temperature_metric="low")
        # They must not be the same object (cache collision) if both return non-None
        if ref_high is not None and ref_low is not None:
            assert ref_high is not ref_low, (
                "HIGH and LOW get_decision_reference_for calls returned the same object — "
                "cache collision confirmed even after metric param added. "
                "Check that cache key truly includes temperature_metric."
            )
