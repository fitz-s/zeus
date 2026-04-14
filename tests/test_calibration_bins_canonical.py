"""R1–R13 relationship + unit tests for the canonical-bin calibration refactor.

These tests back the 2026-04-14 calibration pipeline refactor described in
``~/.claude/plans/logical-chasing-ritchie.md``. Each R-block corresponds
to a defect-class the reviewer flagged in v1 of the plan:

- R1/R2 — partition invariant (shoulders prevent Σ P_raw < 1) [RVW-2]
- R3/R4 — label round-trip through market_scanner + store
- R5    — bin_for_value exhaustive integer coverage [RVW-4]
- R6    — shoulder absorbs out-of-range values
- R7    — decision-group orphan cleanup [RVW-3]
- R8    — --force gate blocks destructive writes [RVW-5]
- R9    — bin_source discriminator precision [RVW-5]
- R10   — MC path parity: class method == free function [RVW-1]
- R11   — authority='VERIFIED' gate
- R12   — unit-provenance antibody
- R13   — end-to-end integration (seed → rebuild → verify)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest

from src.calibration.store import add_calibration_pair, infer_bin_width_from_label
from src.contracts.calibration_bins import (
    C_CANONICAL_GRID,
    F_CANONICAL_GRID,
    CanonicalBinGrid,
    UnitProvenanceError,
    grid_for_city,
    validate_members_unit_plausible,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.market_scanner import _parse_temp_range
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.state.db import init_schema
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeCity:
    """Minimal City stand-in to avoid pulling in the real cities.json.

    Must expose the attributes that ``SettlementSemantics.for_city`` reads:
    ``settlement_unit``, ``wu_station``, and optionally ``settlement_source``.
    ``cluster`` / ``lat`` / ``lon`` / ``timezone`` / ``name`` are read by the
    rebuild script itself.
    """

    def __init__(
        self,
        name,
        unit,
        *,
        wu_station,
        cluster="test_cluster",
        lat=40.0,
        lon=-74.0,
        timezone="America/New_York",
    ):
        self.name = name
        self.settlement_unit = unit
        self.wu_station = wu_station
        self.settlement_source = ""  # WU default path in for_city
        self.cluster = cluster
        self.lat = lat
        self.lon = lon
        self.timezone = timezone


NYC_F = _FakeCity(
    "NYC", "F", wu_station="KNYC", cluster="NYC", lat=40.7, lon=-74.0,
    timezone="America/New_York",
)
PARIS_C = _FakeCity(
    "Paris", "C", wu_station="LFPB", cluster="Paris", lat=48.85, lon=2.35,
    timezone="Europe/Paris",
)


def _sem_f():
    return SettlementSemantics.default_wu_fahrenheit("KNYC")


def _sem_c():
    return SettlementSemantics.default_wu_celsius("LFPB")


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# R1/R2 — partition invariant (Σ P_raw = 1.0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 42, 7_919])
def test_R1_F_partition_sums_to_one(seed):
    """[RVW-2] Canonical F grid must cover the real line; any member vector's
    Σ p_raw must equal 1.0 to double precision. Tests with random spreads."""
    rng = np.random.default_rng(seed)
    # 4 distinct climatological regimes so we hit shoulders + interior
    for center in (-100.0, 20.0, 75.0, 200.0):
        member_maxes = center + rng.normal(0, 8.0, 51)
        p = p_raw_vector_from_maxes(
            member_maxes,
            NYC_F,
            _sem_f(),
            F_CANONICAL_GRID.as_bins(),
            n_mc=200,
            rng=rng,
        )
        total = float(p.sum())
        assert abs(total - 1.0) < 1e-12, (
            f"F grid partition failure at center={center}: sum={total!r}"
        )


@pytest.mark.parametrize("seed", [0, 1, 42, 7_919])
def test_R2_C_partition_sums_to_one(seed):
    """[RVW-2] Canonical C grid partition invariant. Same as R1 but °C."""
    rng = np.random.default_rng(seed)
    for center in (-60.0, 10.0, 30.0, 80.0):
        member_maxes = center + rng.normal(0, 4.0, 51)
        p = p_raw_vector_from_maxes(
            member_maxes,
            PARIS_C,
            _sem_c(),
            C_CANONICAL_GRID.as_bins(),
            n_mc=200,
            rng=rng,
        )
        total = float(p.sum())
        assert abs(total - 1.0) < 1e-12, (
            f"C grid partition failure at center={center}: sum={total!r}"
        )


# ---------------------------------------------------------------------------
# R3/R4 — label round-trip through market_scanner + store
# ---------------------------------------------------------------------------


def test_R3_F_labels_round_trip_through_parsers():
    """[RVW-5 precondition] Every F canonical label must parse through the
    live market scanner and the calibration store width-inference helper."""
    for b in F_CANONICAL_GRID.iter_bins():
        if b.is_open_low:
            # "X°F or below" — _parse_temp_range returns (None, X)
            low, high = _parse_temp_range(b.label)
            assert low is None, f"shoulder low parse: {b.label!r} -> {(low, high)}"
            assert high == b.high, f"{b.label!r}: high={high!r} want {b.high}"
            assert infer_bin_width_from_label(b.label) is None
        elif b.is_open_high:
            low, high = _parse_temp_range(b.label)
            assert high is None, f"shoulder high parse: {b.label!r}"
            assert low == b.low
            assert infer_bin_width_from_label(b.label) is None
        else:
            low, high = _parse_temp_range(b.label)
            assert low == b.low, f"interior low: {b.label!r} parsed={low!r}"
            assert high == b.high, f"interior high: {b.label!r} parsed={high!r}"
            assert infer_bin_width_from_label(b.label) == 2.0, (
                f"F interior width: {b.label!r}"
            )
            # Bin constructor must also accept a clone built from the parsed data
            Bin(low=low, high=high, unit="F", label=b.label)  # raises if invalid


def test_R4_C_labels_round_trip_through_parsers():
    """[RVW-5 precondition] Every C canonical label round-trips."""
    for b in C_CANONICAL_GRID.iter_bins():
        if b.is_open_low:
            low, high = _parse_temp_range(b.label)
            assert low is None
            assert high == b.high
            assert infer_bin_width_from_label(b.label) is None
        elif b.is_open_high:
            low, high = _parse_temp_range(b.label)
            assert high is None
            assert low == b.low
            assert infer_bin_width_from_label(b.label) is None
        else:
            # Point bin: _parse_temp_range returns (v, v)
            low, high = _parse_temp_range(b.label)
            assert low == b.low
            assert high == b.high
            assert low == high, f"C point bin must have low==high: {b.label!r}"
            assert infer_bin_width_from_label(b.label) == 1.0, (
                f"C point width: {b.label!r}"
            )
            Bin(low=low, high=high, unit="C", label=b.label)


# ---------------------------------------------------------------------------
# R5 — bin_for_value exhaustive integer coverage
# ---------------------------------------------------------------------------


def test_R5_F_bin_for_value_exhaustive_integers():
    """[RVW-4] Every integer in [-200, 200] maps to exactly one F canonical bin
    and that bin's contains-predicate holds."""
    for v in range(-200, 201):
        b = F_CANONICAL_GRID.bin_for_value(float(v))
        if b.is_open_low:
            assert v <= b.high, f"v={v} low shoulder mismatch: bin={b.label}"
        elif b.is_open_high:
            assert v >= b.low, f"v={v} high shoulder mismatch: bin={b.label}"
        else:
            assert b.low <= v <= b.high, (
                f"v={v} interior mismatch: bin={b.label} (low={b.low}, high={b.high})"
            )


def test_R5_C_bin_for_value_exhaustive_integers():
    for v in range(-200, 201):
        b = C_CANONICAL_GRID.bin_for_value(float(v))
        if b.is_open_low:
            assert v <= b.high
        elif b.is_open_high:
            assert v >= b.low
        else:
            assert b.low <= v <= b.high


def test_R5_F_boundary_integers_go_to_shoulder_not_interior():
    """Shoulder-edge integers must go to the shoulder, never to the first interior."""
    # Default shoulder_low=-40 → low shoulder is 'v <= -40'. First interior is (-39,-38).
    b_low = F_CANONICAL_GRID.bin_for_value(-40.0)
    assert b_low.is_open_low, f"v=-40 should be low shoulder, got {b_low.label}"
    # Default shoulder_high=141 → high shoulder is 'v >= 141'.
    b_high = F_CANONICAL_GRID.bin_for_value(141.0)
    assert b_high.is_open_high, f"v=141 should be high shoulder, got {b_high.label}"
    # v=-39 is first interior
    b_first = F_CANONICAL_GRID.bin_for_value(-39.0)
    assert not b_first.is_shoulder
    assert (b_first.low, b_first.high) == (-39.0, -38.0)
    # v=140 is last interior
    b_last = F_CANONICAL_GRID.bin_for_value(140.0)
    assert not b_last.is_shoulder
    assert (b_last.low, b_last.high) == (139.0, 140.0)


# ---------------------------------------------------------------------------
# R6 — shoulder absorbs out-of-range mass
# ---------------------------------------------------------------------------


def test_R6_F_high_shoulder_absorbs_hot_ensemble():
    """Member forecasts well above grid high must land entirely in high shoulder."""
    rng = np.random.default_rng(0)
    member_maxes = np.full(51, 250.0) + rng.normal(0, 0.5, 51)
    p = p_raw_vector_from_maxes(
        member_maxes,
        NYC_F,
        _sem_f(),
        F_CANONICAL_GRID.as_bins(),
        n_mc=100,
        rng=rng,
    )
    # Find the high-shoulder index
    bins = F_CANONICAL_GRID.as_bins()
    high_idx = next(i for i, b in enumerate(bins) if b.is_open_high)
    assert p[high_idx] > 0.99, (
        f"high shoulder should absorb 250°F members, got p={p[high_idx]!r}"
    )


def test_R6_F_low_shoulder_absorbs_cold_ensemble():
    rng = np.random.default_rng(0)
    member_maxes = np.full(51, -300.0) + rng.normal(0, 0.5, 51)
    p = p_raw_vector_from_maxes(
        member_maxes,
        NYC_F,
        _sem_f(),
        F_CANONICAL_GRID.as_bins(),
        n_mc=100,
        rng=rng,
    )
    bins = F_CANONICAL_GRID.as_bins()
    low_idx = next(i for i, b in enumerate(bins) if b.is_open_low)
    assert p[low_idx] > 0.99, (
        f"low shoulder should absorb -300°F members, got p={p[low_idx]!r}"
    )


# ---------------------------------------------------------------------------
# R7 — decision-group orphan cleanup
# ---------------------------------------------------------------------------


def test_R7_delete_canonical_slice_cleans_linked_groups_preserves_legacy(mem_db):
    """[RVW-3] Deleting canonical_v1 pairs must also purge their decision groups
    but leave legacy pairs and unrelated decision groups intact."""
    from scripts.rebuild_calibration_pairs_canonical import _delete_canonical_slice

    # Seed 1 canonical pair + 1 legacy pair, each with its own decision group
    add_calibration_pair(
        mem_db,
        city="NYC",
        target_date="2025-06-15",
        range_label="71-72°F",
        p_raw=0.5,
        outcome=1,
        lead_days=1.0,
        season="JJA",
        cluster="NYC",
        forecast_available_at="2025-06-14T00:00:00Z",
        decision_group_id="NYC|2025-06-15|canon",
        bin_source="canonical_v1",
    )
    add_calibration_pair(
        mem_db,
        city="Chicago",
        target_date="2025-06-15",
        range_label="68-69°F",
        p_raw=0.5,
        outcome=1,
        lead_days=1.0,
        season="JJA",
        cluster="Chicago",
        forecast_available_at="2025-06-14T00:00:00Z",
        decision_group_id="Chicago|2025-06-15|legacy",
        bin_source="legacy",
    )
    mem_db.executemany(
        """
        INSERT INTO calibration_decision_group
        (group_id, city, target_date, forecast_available_at,
         cluster, season, lead_days, settlement_value, winning_range_label,
         bias_corrected, n_pair_rows, n_positive_rows, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 1, ?)
        """,
        [
            ("NYC|2025-06-15|canon", "NYC", "2025-06-15", "2025-06-14T00:00:00Z",
             "NYC", "JJA", 1.0, 72.0, "71-72°F", "2025-06-14T00:00:00Z"),
            ("Chicago|2025-06-15|legacy", "Chicago", "2025-06-15",
             "2025-06-14T00:00:00Z", "Chicago", "JJA", 1.0, 68.0, "68-69°F",
             "2025-06-14T00:00:00Z"),
        ],
    )
    mem_db.commit()

    # Execute the destructive slice
    _delete_canonical_slice(mem_db)
    mem_db.commit()

    # Canonical pair gone; legacy pair survives
    remaining_pairs = mem_db.execute(
        "SELECT city, range_label, bin_source FROM calibration_pairs "
        "ORDER BY city"
    ).fetchall()
    assert len(remaining_pairs) == 1
    assert remaining_pairs[0]["city"] == "Chicago"
    assert remaining_pairs[0]["bin_source"] == "legacy"

    # Canonical decision group gone; legacy decision group survives
    remaining_groups = mem_db.execute(
        "SELECT group_id FROM calibration_decision_group ORDER BY group_id"
    ).fetchall()
    assert [r["group_id"] for r in remaining_groups] == ["Chicago|2025-06-15|legacy"]


# ---------------------------------------------------------------------------
# R8 — --force gate blocks destructive writes
# ---------------------------------------------------------------------------


def test_R8_force_gate_refuses_delete_without_force_flag(mem_db):
    """[RVW-5] Calling rebuild with dry_run=False but force=False must raise."""
    from scripts.rebuild_calibration_pairs_canonical import rebuild

    with pytest.raises(RuntimeError, match=r"--no-dry-run requires --force"):
        rebuild(
            mem_db,
            dry_run=False,
            force=False,
            allow_unaudited_ensemble=True,
            n_mc=10,
        )
    # DB must be unchanged — no side effects from the rejected call
    row_count = mem_db.execute(
        "SELECT COUNT(*) FROM calibration_pairs"
    ).fetchone()[0]
    assert row_count == 0


# ---------------------------------------------------------------------------
# R9 — bin_source discriminator precision
# ---------------------------------------------------------------------------


def test_R9_delete_scope_never_matches_legacy_rows(mem_db):
    """[RVW-5] Legacy C rows with labels like '15°C' must survive a canonical
    delete pass, even though the legacy rows' labels are syntactically
    identical to what canonical rebuild would write."""
    from scripts.rebuild_calibration_pairs_canonical import _delete_canonical_slice

    # Two rows, same label format, different bin_source
    add_calibration_pair(
        mem_db,
        city="Paris",
        target_date="2025-06-15",
        range_label="15°C",
        p_raw=0.4,
        outcome=1,
        lead_days=1.0,
        season="JJA",
        cluster="Paris",
        forecast_available_at="2025-06-14T00:00:00Z",
        bin_source="legacy",
    )
    add_calibration_pair(
        mem_db,
        city="Paris",
        target_date="2025-06-16",
        range_label="15°C",
        p_raw=0.4,
        outcome=1,
        lead_days=1.0,
        season="JJA",
        cluster="Paris",
        forecast_available_at="2025-06-15T00:00:00Z",
        bin_source="canonical_v1",
    )
    mem_db.commit()

    _delete_canonical_slice(mem_db)
    mem_db.commit()

    rows = mem_db.execute(
        "SELECT target_date, bin_source FROM calibration_pairs ORDER BY target_date"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["bin_source"] == "legacy"
    assert rows[0]["target_date"] == "2025-06-15"


# ---------------------------------------------------------------------------
# R10 — MC path parity: class method == free function
# ---------------------------------------------------------------------------


def test_R10_class_method_delegates_to_free_function():
    """[RVW-1] ``EnsembleSignal.p_raw_vector`` must be a pure delegate of
    ``p_raw_vector_from_maxes`` so training and inference share one code
    path. Build an EnsembleSignal with minimal shape, patch the free
    function to a sentinel, verify delegation."""
    from src.signal import ensemble_signal as es_mod
    from src.signal.ensemble_signal import EnsembleSignal

    # 51 members × 24 hours, unique identifiable max per member so we can
    # verify member_maxes is what gets forwarded
    n_members = 51
    hours_per_day = 24
    members_hourly = np.tile(
        np.arange(n_members, dtype=float).reshape(-1, 1), (1, hours_per_day)
    )
    # Use UTC hour timestamps; America/New_York offset is -05:00/-04:00
    times = [
        f"2025-06-15T{h:02d}:00:00+00:00" for h in range(hours_per_day)
    ]

    esig = EnsembleSignal(
        members_hourly=members_hourly,
        times=times,
        city=NYC_F,
        target_date=datetime(2025, 6, 15).date(),
        settlement_semantics=_sem_f(),
    )
    # In normal execution the class method now forwards to the free function.
    # Patch the free function to return a sentinel, confirm the class method
    # returns the sentinel and received the expected forwarded arguments.
    sentinel = np.array([0.42])
    captured = {}

    def fake(member_maxes, city, sem, bins, *, n_mc=None, rng=None):
        captured["member_maxes"] = member_maxes.copy()
        captured["city"] = city
        captured["sem"] = sem
        captured["bins"] = bins
        captured["n_mc"] = n_mc
        return sentinel

    fake_bins = [
        Bin(low=None, high=70.0, unit="F", label="70°F or below"),
        Bin(low=71.0, high=72.0, unit="F", label="71-72°F"),
        Bin(low=73.0, high=None, unit="F", label="73°F or higher"),
    ]
    with patch.object(es_mod, "p_raw_vector_from_maxes", side_effect=fake) as mocked:
        result = esig.p_raw_vector(fake_bins, n_mc=123)
        assert mocked.called
    assert np.array_equal(result, sentinel)
    assert captured["city"] is NYC_F
    assert captured["sem"] is esig.settlement_semantics
    assert captured["bins"] is fake_bins
    assert captured["n_mc"] == 123
    assert np.array_equal(captured["member_maxes"], esig.member_maxes)


def test_R10_seeded_rng_produces_deterministic_output():
    """Same seed → same vector. Train and infer can reproduce each other
    when callers pass matching seeds, which is how parity would be audited."""
    bins = F_CANONICAL_GRID.as_bins()
    mem = np.full(51, 72.0) + np.linspace(-1, 1, 51)
    rng_a = np.random.default_rng(12345)
    rng_b = np.random.default_rng(12345)
    p_a = p_raw_vector_from_maxes(mem, NYC_F, _sem_f(), bins, n_mc=50, rng=rng_a)
    p_b = p_raw_vector_from_maxes(mem, NYC_F, _sem_f(), bins, n_mc=50, rng=rng_b)
    assert np.array_equal(p_a, p_b)


# ---------------------------------------------------------------------------
# R11 — authority='VERIFIED' gate
# ---------------------------------------------------------------------------


def test_R11_unverified_snapshot_excluded(mem_db, monkeypatch):
    """Unverified ensemble_snapshots must not produce calibration_pairs rows."""
    from scripts.rebuild_calibration_pairs_canonical import rebuild
    import src.contracts.calibration_bins as cb

    # Patch grid_for_city to return a tiny 3-bin grid (low shoulder + 1 interior +
    # high shoulder) so the test runs fast under low n_mc
    _seed_ensemble_snapshot(
        mem_db, "NYC", "2025-06-15", members_json=_F_members_near(72.0),
        authority="UNVERIFIED",
    )
    _seed_observation(mem_db, "NYC", "2025-06-15", high_temp=72.0, unit="F")

    monkeypatch.setattr(
        "src.config.cities_by_name", {"NYC": NYC_F}, raising=False,
    )
    monkeypatch.setattr(
        "scripts.rebuild_calibration_pairs_canonical.cities_by_name",
        {"NYC": NYC_F},
    )

    stats = rebuild(
        mem_db,
        dry_run=False,
        force=True,
        n_mc=10,
        allow_unaudited_ensemble=True,
    )
    assert stats.pairs_written == 0
    assert stats.snapshots_scanned == 0
    # Because the snapshot is not VERIFIED, it should not even be fetched:
    n = mem_db.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    assert n == 0


def test_R11_unverified_observation_excluded(mem_db, monkeypatch):
    """Observations with authority!=VERIFIED are skipped at JOIN time."""
    from scripts.rebuild_calibration_pairs_canonical import rebuild

    _seed_ensemble_snapshot(
        mem_db, "NYC", "2025-06-15", members_json=_F_members_near(72.0),
        authority="VERIFIED",
    )
    _seed_observation(
        mem_db, "NYC", "2025-06-15", high_temp=72.0, unit="F",
        authority="UNVERIFIED",
    )

    monkeypatch.setattr(
        "scripts.rebuild_calibration_pairs_canonical.cities_by_name",
        {"NYC": NYC_F},
    )

    stats = rebuild(
        mem_db,
        dry_run=False,
        force=True,
        n_mc=10,
        allow_unaudited_ensemble=True,
    )
    assert stats.snapshots_no_observation == 1
    assert stats.pairs_written == 0


# ---------------------------------------------------------------------------
# R12 — unit-provenance antibody
# ---------------------------------------------------------------------------


def test_R12_unit_provenance_rejects_F_values_on_C_city():
    # NYC-hot-summer values (65-75°F) disguised into a °C city
    f_values = np.array([65.0, 70.0, 75.0, 68.0, 72.0])
    with pytest.raises(UnitProvenanceError, match=r"outside plausible range"):
        validate_members_unit_plausible(f_values, PARIS_C)


def test_R12_unit_provenance_accepts_plausible_C_values():
    c_values = np.array([15.0, 18.0, 22.0, 20.0, 17.0])
    validate_members_unit_plausible(c_values, PARIS_C)  # no raise


def test_R12_unit_provenance_accepts_plausible_F_values():
    f_values = np.array([65.0, 70.0, 75.0, 68.0, 72.0])
    validate_members_unit_plausible(f_values, NYC_F)  # no raise


def test_R12_unit_provenance_rejects_nonfinite():
    with pytest.raises(UnitProvenanceError, match=r"non-finite"):
        validate_members_unit_plausible(
            np.array([15.0, float("nan"), 20.0]), PARIS_C
        )


def test_R12_unit_provenance_rejects_empty():
    with pytest.raises(UnitProvenanceError, match=r"empty"):
        validate_members_unit_plausible(np.array([]), PARIS_C)


# ---------------------------------------------------------------------------
# R13 — end-to-end integration
# ---------------------------------------------------------------------------


def test_R13_end_to_end_rebuild_produces_expected_pair_shape(mem_db, monkeypatch):
    """Seed 1 VERIFIED observation + 1 VERIFIED snapshot for NYC. Run rebuild.
    Assert: exactly 92 calibration_pairs rows, exactly 1 outcome=1, the
    winning row's range_label is the correct canonical F bin for 72°F, and
    a decision_group row was written with n_pair_rows=92."""
    from scripts.rebuild_calibration_pairs_canonical import rebuild

    # 51 members clustered tightly near 72°F
    _seed_ensemble_snapshot(
        mem_db, "NYC", "2025-06-15",
        members_json=_F_members_near(72.0, spread=0.1),
        authority="VERIFIED",
    )
    _seed_observation(
        mem_db, "NYC", "2025-06-15",
        high_temp=72.0, unit="F", authority="VERIFIED",
    )

    monkeypatch.setattr(
        "scripts.rebuild_calibration_pairs_canonical.cities_by_name",
        {"NYC": NYC_F},
    )

    stats = rebuild(
        mem_db,
        dry_run=False,
        force=True,
        n_mc=200,
        allow_unaudited_ensemble=True,
    )
    assert stats.snapshots_processed == 1
    assert stats.pairs_written == F_CANONICAL_GRID.n_bins  # 92

    rows = mem_db.execute(
        "SELECT range_label, outcome, bin_source, p_raw, decision_group_id "
        "FROM calibration_pairs WHERE city='NYC'"
    ).fetchall()
    assert len(rows) == 92
    winners = [r for r in rows if r["outcome"] == 1]
    assert len(winners) == 1, f"expected 1 winning bin, got {len(winners)}"
    # 72°F in odd-start interior → bin (71, 72)
    assert winners[0]["range_label"] == "71-72°F"
    # All rows must be canonical_v1
    assert {r["bin_source"] for r in rows} == {"canonical_v1"}
    # Winning bin should carry most of the probability mass (tight spread)
    assert winners[0]["p_raw"] > 0.5

    # Decision group populated
    dg_rows = mem_db.execute(
        "SELECT group_id, n_pair_rows, n_positive_rows FROM calibration_decision_group"
    ).fetchall()
    assert len(dg_rows) == 1
    assert dg_rows[0]["n_pair_rows"] == 92
    assert dg_rows[0]["n_positive_rows"] == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _F_members_near(center: float, spread: float = 0.5, n: int = 51) -> str:
    rng = np.random.default_rng(0)
    return json.dumps((center + rng.normal(0, spread, n)).tolist())


def _seed_ensemble_snapshot(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    *,
    members_json: str,
    authority: str = "VERIFIED",
    data_version: str = "tigge_step024_v1_test",
    lead_hours: float = 24.0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city, target_date,
            f"{target_date}T00:00:00Z",
            f"{target_date}T12:00:00Z",
            f"{target_date}T00:00:00Z",
            now, lead_hours, members_json, "test_model",
            data_version, authority,
        ),
    )
    conn.commit()


def _seed_observation(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    *,
    high_temp: float,
    unit: str,
    authority: str = "VERIFIED",
    source: str = "wu_icao_history",
) -> None:
    conn.execute(
        """
        INSERT INTO observations
        (city, target_date, source, high_temp, low_temp, unit, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (city, target_date, source, high_temp, high_temp - 10.0, unit, authority),
    )
    conn.commit()
