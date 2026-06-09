from __future__ import annotations

# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: live-wiring root-cause 2026-06-09 (iron rule 1 + Fitz #2 boundary bug).
#   The multi-model U0R Bayesian fusion fired on 0/598 live posteriors -> the live forecast was
#   the cold AIFS-soft-anchor, NOT the physically-superior de-biased multi-model fusion. Root
#   cause: `_read_persisted_current_capture` filtered raw_model_forecasts on `lead_days`, but the
#   DOWNLOAD persists lead on the cycle/UTC calendar (target - source_cycle) while the
#   MATERIALIZER re-derived lead from `computed_at` on the CITY-LOCAL calendar (BLOCKER 6) — a
#   different reference time AND calendar. The two leads disagreed (e.g. Wuhan: download lead=2
#   from cycle 06-08, reader lead=1 from computed_at 06-09 local), so the read returned EMPTY,
#   `capture.has_extras` was False, and `_replacement_u0r_fusion_override` silently returned None
#   (no warning) -> soft-anchor fallback.
#
# RELATIONSHIP ANTIBODY (make the error CATEGORY unconstructable): the persisted-current read
# identifies the forecast by its NATURAL KEY (city, metric, target_date, source_cycle_time);
# `lead_days` is a DERIVED field (= target - cycle, unique per natural key) and MUST NOT filter
# the read. This test pins that the read returns the persisted single_runs models REGARDLESS of
# the lead_days value passed — so a download/materializer lead-calendar mismatch can never again
# silently empty the read and disable the entire multi-model fusion.

import sqlite3

from src.data.replacement_forecast_materializer import _read_persisted_current_capture


def _conn_with_single_runs(stored_lead: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER, model TEXT, forecast_value_c REAL,
            city TEXT, metric TEXT, target_date TEXT, lead_days INTEGER,
            source_cycle_time TEXT, endpoint TEXT
        )
        """
    )
    # The download persists the single_runs current capture at the CYCLE-based lead.
    rows = [
        (1, "ecmwf_ifs", 30.0),
        (2, "gfs_global", 31.0),
        (3, "icon_global", 29.5),
        (4, "jma_seamless", 33.0),
    ]
    for rid, model, val in rows:
        conn.execute(
            "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, model, val, "Wuhan", "high", "2026-06-11", stored_lead,
             "2026-06-08T18:00:00+00:00", "single_runs"),
        )
    conn.commit()
    return conn


EXPECTED = {"ecmwf_ifs", "gfs_global", "icon_global", "jma_seamless"}
CYCLE = "2026-06-08T18:00:00+00:00"


def test_read_returns_models_when_passed_lead_matches_stored():
    conn = _conn_with_single_runs(stored_lead=2)
    out = _read_persisted_current_capture(
        conn, city="Wuhan", metric="high", target_date="2026-06-11",
        lead_days=2, source_cycle_time_iso=CYCLE,
    )
    assert set(out.keys()) == EXPECTED


def test_read_is_robust_to_a_mismatched_lead_days():
    """The download stored lead=2 (cycle-based); the materializer historically re-derived a
    DIFFERENT lead (e.g. 1 from computed_at) and queried it -> EMPTY -> fusion silently disabled.
    The read must now return the models regardless of the lead passed (lead is not a filter)."""
    conn = _conn_with_single_runs(stored_lead=2)
    for mismatched_lead in (1, 0, 3, 99):
        out = _read_persisted_current_capture(
            conn, city="Wuhan", metric="high", target_date="2026-06-11",
            lead_days=mismatched_lead, source_cycle_time_iso=CYCLE,
        )
        assert set(out.keys()) == EXPECTED, (
            f"read returned {sorted(out.keys())} for lead_days={mismatched_lead}: the read must "
            "match on the natural key (city,metric,target_date,source_cycle_time) and never filter "
            "on the derived lead_days, or the download/materializer lead-calendar mismatch silently "
            "empties the read and disables the entire multi-model fusion (the 0/598 live defect)."
        )


def test_read_still_isolates_by_natural_key():
    """Robustness to lead must NOT leak across the natural key: a different cycle/target/city/
    metric/endpoint must still be excluded."""
    conn = _conn_with_single_runs(stored_lead=2)
    # wrong cycle -> empty
    assert _read_persisted_current_capture(
        conn, city="Wuhan", metric="high", target_date="2026-06-11",
        lead_days=2, source_cycle_time_iso="2026-06-09T00:00:00+00:00",
    ) == {}
    # wrong target_date -> empty
    assert _read_persisted_current_capture(
        conn, city="Wuhan", metric="high", target_date="2026-06-12",
        lead_days=2, source_cycle_time_iso=CYCLE,
    ) == {}
