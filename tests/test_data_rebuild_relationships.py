"""Data-rebuild cross-module relationship tests (Changes A-N driver tests).

These are written BEFORE the implementation of Changes A-N and act as the
executable, failing contract for the rebuild. Each test encodes a cross-module
invariant that MUST hold once the corresponding Change lands. Tests that are
red today become green as Changes land; tests that are green today confirm the
invariant is already enforced and lock it in against regression.

Methodology: relationship tests → implementation → function tests.
  (See ~/.claude/CLAUDE.md "Fitz's Core Methodology")

Authority:
  - docs/reference/zeus_math_spec.md v2 (math source of truth)
  - docs/operations/data_rebuild_plan.md v2.1 (operational plan)
  - AGENTS.md §1 "Why settlement is integer" (WMO floor(x+0.5))

Each test's docstring names:
  - The invariant in one sentence
  - The Change letter(s) that make it green
  - The defect D# the Change fixes
  - Expected status at time of writing (RED/GREEN)
"""

from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pytest

from src.contracts.settlement_semantics import (
    SettlementSemantics,
    round_wmo_half_up_value,
    round_wmo_half_up_values,
)


# ---------------------------------------------------------------------------
# R1: WMO rounding identity across modules (GREEN at time of writing)
# ---------------------------------------------------------------------------

# WMO verification table from zeus_math_spec.md §1.2 and AGENTS.md §1.
# Every rounding site on any settlement-aligned path must match this table.
_WMO_TABLE: list[tuple[float, int]] = [
    (52.45, 52),
    (52.50, 53),   # positive half rounds UP
    (74.49, 74),
    (74.50, 75),   # positive half rounds UP
    (-0.50, 0),    # negative half rounds toward the more positive integer
    (-1.49, -1),
    (-1.50, -1),   # NOT -2 (away-from-zero would give -2; WMO gives -1)
    (-2.50, -2),   # NOT -3 (away-from-zero would give -3; WMO gives -2)
    (-3.50, -3),
]


def test_r1_wmo_rounding_verification_table_at_helper():
    """Invariant: `round_wmo_half_up_values` implements `floor(x+0.5)` on every row of
    the WMO verification table.

    Change: D (already landed)
    Defect: D1/D2 (was: np.round/banker's; fixed via wmo_half_up rule)
    Status: GREEN
    """
    xs = np.array([x for x, _ in _WMO_TABLE], dtype=float)
    expected = np.array([y for _, y in _WMO_TABLE], dtype=float)
    got = round_wmo_half_up_values(xs, precision=1.0)
    np.testing.assert_array_equal(got, expected)


def test_r1_wmo_rounding_identity_across_settlement_modules():
    """Invariant: SettlementSemantics.round_values, the standalone
    round_wmo_half_up_values helper, and market_analysis._settle MUST all
    produce byte-identical integers for the WMO verification table.

    If any of these diverge at a bin boundary (e.g., 74.50), then P_raw
    (computed via one rounding) and Y (stored via another) will live in
    different conventions and every calibration pair near an integer
    boundary is corrupt.

    Change: D (already landed), E (delegates to D)
    Authority: zeus_math_spec.md §4.5 non-negotiable property
    Status: GREEN
    """
    from src.strategy.market_analysis import MarketAnalysis

    xs = np.array([x for x, _ in _WMO_TABLE], dtype=float)
    expected = np.array([y for _, y in _WMO_TABLE], dtype=float)

    # Path 1: SettlementSemantics contract
    sem = SettlementSemantics.default_wu_fahrenheit("TEST")
    path1 = sem.round_values(xs)

    # Path 2: standalone helper (the single implementation both the contract
    # and market_analysis._settle call through)
    path2 = round_wmo_half_up_values(xs, precision=1.0)

    # Path 3: market_analysis._settle — instantiate with minimal state to
    # exercise the real settle method. We bypass __init__ to avoid pulling
    # in the full analysis pipeline; we only need the _settle method.
    ma = MarketAnalysis.__new__(MarketAnalysis)
    ma._precision = 1.0
    path3 = ma._settle(xs)

    np.testing.assert_array_equal(path1, expected, err_msg="SettlementSemantics.round_values drifted from WMO")
    np.testing.assert_array_equal(path2, expected, err_msg="round_wmo_half_up_values drifted from WMO")
    np.testing.assert_array_equal(path3, expected, err_msg="market_analysis._settle drifted from WMO")
    np.testing.assert_array_equal(path1, path3, err_msg="SettlementSemantics and market_analysis._settle disagree")


def test_r1_wmo_scalar_helper_matches_vector_helper():
    """Invariant: round_wmo_half_up_value(x) == round_wmo_half_up_values([x])[0]
    for every row of the WMO table. Used by store.py to round settlement_value
    on write; divergence from the vector helper would corrupt stored values.

    Change: D (already landed)
    Status: GREEN
    """
    for x, y in _WMO_TABLE:
        assert round_wmo_half_up_value(x) == y, f"scalar helper drifted at x={x}: expected {y}"


# ---------------------------------------------------------------------------
# R2: Monte Carlo bin probability live/rebuild equivalence (RED)
# ---------------------------------------------------------------------------

def test_r2_rebuild_calibration_uses_live_monte_carlo_pipeline():
    """Invariant (Change F): the legacy `rebuild_calibration.py` entrypoint
    fails closed and points operators at the canonical rebuild script.

    The active implementation is `rebuild_calibration_pairs_canonical.py`.
    Keeping the old command as a redirect would make the retired surface look
    valid, so this test requires an explicit non-zero tombstone.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "rebuild_calibration.py"
    canonical = root / "scripts" / "rebuild_calibration_pairs_canonical.py"

    source = script.read_text()

    legacy_markers = [
        "simplified local p_raw",
        "simplified p_raw",
        "bin taxonomy may differ",
        "_compute_p_raw_for_bins",
    ]
    stale = [m for m in legacy_markers if m in source]
    if stale:
        pytest.fail(
            f"retired rebuild_calibration.py still contains legacy executable "
            f"markers: {stale}."
        )

    proc = subprocess.run(
        [sys.executable, str(script), "--dry-run", "--db", "/tmp/should-not-exist.db"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "rebuild_calibration_pairs_canonical.py" in proc.stderr
    assert "retired" in proc.stderr

    canonical_source = canonical.read_text()
    for marker in [
        "p_raw_vector_from_maxes",
        "SettlementSemantics",
        "grid_for_city",
        "--force",
        "--allow-unaudited-ensemble",
    ]:
        assert marker in canonical_source


def test_r2_canonical_rebuild_refuses_unaudited_with_nonzero_status():
    from src.state.db import init_schema
    from scripts.rebuild_calibration_pairs_canonical import rebuild

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            city, target_date, issue_time, valid_time, available_at,
            fetch_time, lead_hours, members_json, model_version, data_version,
            authority, temperature_metric
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "NYC",
            "2026-01-01",
            "2025-12-30T00:00:00Z",
            "2026-01-01T12:00:00Z",
            "2025-12-30T08:00:00Z",
            "2025-12-30T08:00:00Z",
            48.0,
            json.dumps([72.0] * 51),
            "ecmwf_tigge",
            "tigge_step024_v1_test",
            "VERIFIED",
            "high",  # B4 Phase 3: temperature_metric defaulted to 'high'
        ),
    )

    stats = rebuild(
        conn,
        dry_run=True,
        force=False,
        allow_unaudited_ensemble=False,
    )
    assert stats.refused is True


def test_r2_canonical_rebuild_refuses_empty_live_replacement():
    from src.state.db import init_schema
    from scripts.rebuild_calibration_pairs_canonical import rebuild

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO calibration_pairs (
            city, target_date, range_label, p_raw, outcome, lead_days,
            season, cluster, forecast_available_at, decision_group_id,
            authority, bin_source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "NYC",
            "2026-01-01",
            "39-40°F",
            0.5,
            1,
            1.0,
            "DJF",
            "NYC",
            "2025-12-31T00:00:00Z",
            "existing-canonical",
            "VERIFIED",
            "canonical_v1",
        ),
    )

    with pytest.raises(RuntimeError, match="no eligible snapshots"):
        rebuild(conn, dry_run=False, force=True, allow_unaudited_ensemble=True)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs WHERE bin_source='canonical_v1'"
    ).fetchone()[0]
    assert remaining == 1


def test_r2_canonical_rebuild_refuses_partial_live_replacement():
    from src.state.db import init_schema
    from scripts.rebuild_calibration_pairs_canonical import rebuild

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    members = json.dumps([72.0] * 51)
    for day in ("2026-01-01", "2026-01-02"):
        conn.execute(
            """
            INSERT INTO ensemble_snapshots (
                city, target_date, issue_time, valid_time, available_at,
                fetch_time, lead_hours, members_json, model_version, data_version,
                authority, temperature_metric
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "NYC",
                day,
                "2025-12-31T00:00:00Z",
                f"{day}T12:00:00Z",
                "2025-12-31T08:00:00Z",
                "2025-12-31T08:00:00Z",
                24.0,
                members,
                "ecmwf_tigge",
                f"canonical-test-{day}",
                "VERIFIED",
                "high",  # B4 Phase 3: temperature_metric defaulted to 'high'
            ),
        )
    conn.execute(
        """
        INSERT INTO observations (city, target_date, source, high_temp, low_temp, unit, authority)
        VALUES ('NYC', '2026-01-01', 'wu_icao_history', 72.0, 60.0, 'F', 'VERIFIED')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs (
            city, target_date, range_label, p_raw, outcome, lead_days,
            season, cluster, forecast_available_at, decision_group_id,
            authority, bin_source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "NYC",
            "2025-12-01",
            "39-40°F",
            0.5,
            1,
            1.0,
            "DJF",
            "NYC",
            "2025-11-30T00:00:00Z",
            "existing-canonical",
            "VERIFIED",
            "canonical_v1",
        ),
    )

    with pytest.raises(RuntimeError, match="no matching VERIFIED observation"):
        rebuild(conn, dry_run=False, force=True, n_mc=10, allow_unaudited_ensemble=True)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs WHERE decision_group_id='existing-canonical'"
    ).fetchone()[0]
    assert remaining == 1


# ---------------------------------------------------------------------------
# R3: decision_group_id hash stability across 3 paths (RED)
# ---------------------------------------------------------------------------

def test_r3_compute_id_exists_and_rejects_naive_datetime():
    """Invariant: src.calibration.decision_group.compute_id() is the canonical
    (and only) producer of decision_group_id, and it raises TypeError on a
    naive (timezone-unaware) datetime.

    Change: G.1-G.5
    Defect: D18 (.isoformat() fragility)
    Authority: data_rebuild_plan.md §3.3
    Status: RED (module does not exist)
    """
    try:
        from src.calibration.decision_group import compute_id  # type: ignore
    except ImportError:
        pytest.fail(
            "src.calibration.decision_group.compute_id() missing. "
            "Change G.1: create the module with the canonical hash template "
            "from data_rebuild_plan.md §3.3 (explicit strftime, no .isoformat)."
        )

    with pytest.raises(TypeError):
        compute_id(
            city="NYC",
            target_date=date(2024, 1, 15),
            issue_time=datetime(2024, 1, 15, 0, 0),  # naive: no tzinfo
            source_model_version="tigge_v1",
        )


def test_r3_tigge_issue_time_uses_cycle_data_time():
    from scripts.etl_tigge_ens import tigge_issue_time_from_members

    members = [
        {"data_date": "20240601", "data_time": "1200"},
        {"data_date": "20240601", "data_time": "1200"},
    ]
    assert tigge_issue_time_from_members(members) == "2024-06-01T12:00:00Z"

    with pytest.raises(ValueError, match="disagree"):
        tigge_issue_time_from_members([
            {"data_date": "20240601", "data_time": "0000"},
            {"data_date": "20240601", "data_time": "1200"},
        ])


def test_r3_compute_id_stable_across_three_paths():
    """Invariant (TRAP A regression): the same logical snapshot reaching
    compute_id() via three distinct code paths MUST produce identical hashes.

      Path 1: direct Python (datetime.datetime with tzinfo=UTC)
      Path 2: SQLite TEXT round-trip via datetime.fromisoformat
      Path 3: SQLite TEXT round-trip via datetime.strptime

    If any pair diverges, the same physical ensemble snapshot gets distinct
    decision_group_ids depending on the code path, inflating n_eff and
    breaking bootstrap CI correctness.

    Change: G (compute_id + M.7 UTC-explicit write format)
    Defect: D18
    Authority: data_rebuild_plan.md §3.3, §14.1 Change G test 3
    Status: RED
    """
    try:
        from src.calibration.decision_group import compute_id  # type: ignore
    except ImportError:
        pytest.fail("compute_id() missing — Change G prerequisite not met.")

    city = "NYC"
    target = date(2024, 1, 15)
    source = "tigge_cycle_2024011500"

    # Path 1: direct Python construction
    t1 = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    h1 = compute_id(city, target, t1, source)

    # Path 2: write via M.7 UTC-explicit format, read back via fromisoformat
    wire = t1.strftime("%Y-%m-%dT%H:%M:%SZ")  # "2024-01-15T12:00:00Z"
    # datetime.fromisoformat accepts trailing "Z" from Python 3.11+; for
    # safety we replace it explicitly.
    t2 = datetime.fromisoformat(wire.replace("Z", "+00:00"))
    h2 = compute_id(city, target, t2, source)

    # Path 3: same wire, parsed with strptime
    t3 = datetime.strptime(wire, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    h3 = compute_id(city, target, t3, source)

    assert h1 == h2 == h3, (
        f"decision_group_id unstable across code paths: "
        f"direct={h1}, fromisoformat={h2}, strptime={h3}"
    )


def test_r3_store_add_pair_raises_without_decision_group_id():
    """Invariant (Change G.6): src.calibration.store.add_calibration_pair must
    REFUSE to accept decision_group_id=None. The legacy fallback string
    `f"{city}|{target_date}|..."` must be deleted.

    Change: G.6
    Defect: D21
    Authority: data_rebuild_plan.md §5 Change G, §14.1 test 5
    Status: RED — fallback still live at src/calibration/store.py:71-72
    """
    from src.calibration import store as store_module

    with sqlite3.connect(":memory:") as conn:
        conn.execute("""
            CREATE TABLE calibration_pairs (
                city TEXT, target_date TEXT, range_label TEXT,
                p_raw REAL, outcome INTEGER, lead_days REAL,
                season TEXT, cluster TEXT, forecast_available_at TEXT,
                settlement_value REAL, decision_group_id TEXT NOT NULL,
                bias_corrected INTEGER
            )
        """)
        from src.config import City
        nyc = City(
            name="NYC", lat=40.7772, lon=-73.8726,
            timezone="America/New_York", cluster="NYC",
            settlement_unit="F", wu_station="KLGA",
        )
        with pytest.raises((TypeError, ValueError, sqlite3.IntegrityError)):
            store_module.add_calibration_pair(
                conn,
                city="NYC",
                target_date="2024-01-15",
                range_label="74-75°F",
                p_raw=0.5,
                outcome=1,
                lead_days=3.0,
                season="DJF",
                cluster="NE",
                forecast_available_at="2024-01-12T12:00:00Z",
                decision_group_id=None,  # MUST be rejected
                city_obj=nyc,
            )


# ---------------------------------------------------------------------------
# R4: Bin ±inf SQLite round-trip preservation (RED)
# ---------------------------------------------------------------------------

def test_r4_sqlite_real_column_preserves_positive_infinity():
    """Invariant (TRAP B part 1): SQLite REAL columns preserve ±inf via IEEE
    754. If the sqlite3 driver coerces inf to 'inf' string or NaN, the entire
    Bin ±inf migration (Change H) is impossible and we need a different
    sentinel scheme.

    This test exercises ONLY the SQLite boundary — it does not depend on the
    Bin type migration. It's the environmental precondition for Change H.

    Status: GREEN (sqlite3 driver preserves inf natively); if this ever fails
    on a new Python/SQLite version, Change H must switch to sentinel integers.
    """
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE t (id INTEGER, lo REAL, hi REAL)")
        conn.execute("INSERT INTO t VALUES (1, ?, ?)", (float("-inf"), float("inf")))
        row = conn.execute("SELECT lo, hi FROM t WHERE id=1").fetchone()
        lo, hi = row[0], row[1]
        assert isinstance(lo, float) and math.isinf(lo) and lo < 0, f"lo={lo!r}"
        assert isinstance(hi, float) and math.isinf(hi) and hi > 0, f"hi={hi!r}"

        # Boundary comparison works at SQL layer
        row2 = conn.execute(
            "SELECT 1 FROM t WHERE lo <= ? AND hi >= ?", (-9999.0, 9999.0)
        ).fetchone()
        assert row2 is not None, "SQL WHERE clause with ±inf boundaries failed"


def test_r4_bin_accepts_inf_edges():
    """Invariant: after Change H, Bin(low=float('-inf'), high=75, unit='F') is
    constructible and Bin(low=75, high=float('inf'), unit='F') is constructible.
    The `is_open_low` / `is_open_high` properties detect the inf edge.

    Change: H
    Defect: D10
    Status: RED — current Bin type uses `None` for open edges and
    __post_init__ would reject inf for failing width validation
    (F non-shoulder must have width=2).
    """
    from src.types import Bin

    try:
        b_lo = Bin(low=float("-inf"), high=75, unit="F", label="75°F or below")
    except (ValueError, TypeError) as exc:
        pytest.fail(
            f"Bin rejects -inf on open-low edge: {exc}. "
            "Change H: migrate Bin.low/high from Optional[float] to float, "
            "allow ±inf, rework __post_init__ shoulder detection."
        )

    try:
        b_hi = Bin(low=75, high=float("inf"), unit="F", label="75°F or above")
    except (ValueError, TypeError) as exc:
        pytest.fail(
            f"Bin rejects +inf on open-high edge: {exc}. Change H precondition."
        )

    assert b_lo.is_open_low and not b_lo.is_open_high
    assert b_hi.is_open_high and not b_hi.is_open_low


# ---------------------------------------------------------------------------
# R5: Bin ±inf JSON round-trip via to_json_safe / from_json_safe (RED)
# ---------------------------------------------------------------------------

def test_r5_bin_to_json_safe_round_trip():
    """Invariant (TRAP B part 2): Bin(low=-inf, high=+inf) can be serialized
    via to_json_safe → json.dumps(..., allow_nan=False) → json.loads →
    from_json_safe and round-trips losslessly. `json.dumps(..., allow_nan=False)`
    MUST NOT raise ValueError at any point in the chain.

    Change: H (v2 JSON extension)
    Defect: D19
    Authority: data_rebuild_plan.md §3.5
    Status: RED (to_json_safe/from_json_safe do not exist yet)
    """
    try:
        from src.types.market import Bin, to_json_safe, from_json_safe  # type: ignore
    except ImportError:
        pytest.fail(
            "to_json_safe/from_json_safe not defined in src.types.market. "
            "Change H (v2 JSON extension) must add these helpers with the "
            "integer sentinel convention (-32768, +32767) per §3.5."
        )

    original = Bin(low=float("-inf"), high=float("inf"), unit="F", label="open-open")
    payload = to_json_safe(original)
    wire = json.dumps(payload, allow_nan=False)  # MUST NOT raise
    parsed = json.loads(wire)
    restored = from_json_safe(parsed)

    assert math.isinf(restored.low) and restored.low < 0
    assert math.isinf(restored.high) and restored.high > 0


def test_r5_naive_json_dumps_on_bin_raises():
    """Invariant: a bare `json.dumps(bin_dict, allow_nan=False)` on a dict
    containing float('inf') MUST raise ValueError. This is the environmental
    check that confirms WHY to_json_safe is necessary — if Python ever
    silently allows "Infinity" strings by default, the semantic_linter rule
    would be toothless.

    Authority: RFC 8259, data_rebuild_plan.md §3.5
    Status: GREEN (Python enforces allow_nan=False correctly)
    """
    payload = {"low": float("-inf"), "high": float("inf")}
    with pytest.raises(ValueError):
        json.dumps(payload, allow_nan=False)


# ---------------------------------------------------------------------------
# R6: Bin topology coverage invariant (RED)
# ---------------------------------------------------------------------------

def test_r6_validate_bin_topology_exists_and_enforces_coverage():
    """Invariant: validate_bin_topology(bins) accepts a valid market (outer
    ±inf bins, contiguous inner bins) and raises on gap / missing outer.

    Change: I (validate_bin_topology) + H (Bin supports ±inf)
    Defect: D11
    Authority: data_rebuild_plan.md §3.2
    Status: RED
    """
    try:
        from src.types.market import Bin, validate_bin_topology  # type: ignore
    except ImportError:
        pytest.fail(
            "src.types.market.validate_bin_topology missing. "
            "Change I: create the helper with sorted-by-low check, "
            "leftmost=-inf, rightmost=+inf, no integer gap."
        )

    # Happy path — ±inf outer bins, contiguous inner (widths 2 for °F)
    bins_ok = [
        Bin(low=float("-inf"), high=71, unit="F", label="71°F or below"),
        Bin(low=72, high=73, unit="F", label="72-73°F"),
        Bin(low=74, high=75, unit="F", label="74-75°F"),
        Bin(low=76, high=float("inf"), unit="F", label="76°F or above"),
    ]
    validate_bin_topology(bins_ok)  # must not raise

    # Missing left outer bin
    bins_gap_left = bins_ok[1:]
    with pytest.raises(Exception):
        validate_bin_topology(bins_gap_left)

    # Missing right outer bin
    bins_gap_right = bins_ok[:-1]
    with pytest.raises(Exception):
        validate_bin_topology(bins_gap_right)

    # Gap between interior bins
    bins_internal_gap = [
        Bin(low=float("-inf"), high=71, unit="F", label="71°F or below"),
        Bin(low=72, high=73, unit="F", label="72-73°F"),
        # missing 74-75
        Bin(low=76, high=float("inf"), unit="F", label="76°F or above"),
    ]
    with pytest.raises(Exception):
        validate_bin_topology(bins_internal_gap)


# ---------------------------------------------------------------------------
# R7, R8, R9: moved to post-this-packet (data_rebuild_plan.md v2.2 §10.1).
#
# R7 (Platt fit survives p_raw boundary extremes) — Change J deferred
# R8 (Platt bootstrap by decision_group)           — Change K deferred
# R9 (maturity gate n_eff, not row count)          — Change G.7 deferred
#
# Reason: all three test consumers of the data, not the data itself.
# None matter until TIGGE completes and the at-recalibrate-time packet runs.
# Test definitions will be re-added to a new file when that packet starts.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# R10: issue_time SQLite UTC round-trip yields stable hash (M.7) (RED)
# ---------------------------------------------------------------------------

def test_r10_issue_time_sqlite_round_trip_preserves_hash():
    """Invariant (Change M.7): when ingest_grib_to_snapshots writes issue_time
    to SQLite as an M.7-format UTC string ('%Y-%m-%dT%H:%M:%SZ'), and a
    downstream consumer reads that string, parses it back to a datetime, and
    calls compute_id() on it, the resulting hash MUST match compute_id()
    called on the original Python UTC datetime.

    This is the production analogue of R3 path 2: it fails if the wire
    format drops UTC awareness, if compute_id() doesn't re-normalize to UTC,
    or if fromisoformat on the trailing 'Z' is mis-handled.

    Change: G (compute_id) + M.7 (UTC-explicit write format)
    Defect: D18
    Authority: data_rebuild_plan.md §5 Change M.7, §14.1 Change M test 4
    Status: RED (compute_id does not exist yet)
    """
    try:
        from src.calibration.decision_group import compute_id  # type: ignore
    except ImportError:
        pytest.fail("compute_id() missing — Change G prerequisite for Change M.7.")

    city = "NYC"
    target = date(2024, 6, 1)
    source = "tigge_cycle_2024060112"
    original_dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE snap (city TEXT, target_date TEXT, issue_time TEXT, smv TEXT)")
        wire = original_dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # M.7 format
        conn.execute(
            "INSERT INTO snap VALUES (?, ?, ?, ?)",
            (city, target.strftime("%Y-%m-%d"), wire, source),
        )
        row = conn.execute("SELECT city, target_date, issue_time, smv FROM snap").fetchone()

    read_dt = datetime.fromisoformat(row[2].replace("Z", "+00:00"))
    assert read_dt.tzinfo is not None and read_dt.utcoffset().total_seconds() == 0

    h_original = compute_id(city, target, original_dt, source)
    h_roundtrip = compute_id(
        row[0],
        date.fromisoformat(row[1]),
        read_dt,
        row[3],
    )
    assert h_original == h_roundtrip, (
        f"Hash changed across SQLite round-trip: "
        f"original={h_original}, roundtrip={h_roundtrip}. "
        "Check M.7 wire format and compute_id UTC normalization."
    )
