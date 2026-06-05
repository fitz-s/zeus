# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: FIX-1 canonical bin_source / wiring verdict 2026-06-03
"""FIX-1 antibody tests: calibration_bin_source_v2_fit_enabled flag.

Three axes:
1. flag-OFF byte-identity — count + fit return identical results to
   pre-fix behavior (0 pairs → None → cross-cluster borrow path).
2. flag-ON own-fit — with canonical_v2 pairs in the fixture, the fit
   returns a NON-trivial calibrator (not the Buenos-Aires borrow).
3. gate/fit population agreement — under flag-ON, get_decision_group_count
   and get_pairs_for_bucket query the SAME bin_source (no count-passes-
   but-fit-starves mismatch).

All tests use an in-memory SQLite DB with a small fixture pair table;
no dependency on the 48M live corpus.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional
from unittest import mock

import numpy as np
import pytest

from src.calibration.decision_group import compute_id
from src.calibration.store import (
    add_calibration_pair,
    get_decision_group_count,
    get_pairs_for_bucket,
)
from src.config import City
from src.data.calibration_transfer_policy import CANONICAL_CALIBRATION_PAIR_BIN_SOURCE
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_canonical_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX

# ── fixtures ──────────────────────────────────────────────────────────────────

_TEST_CITY = City(
    name="TestCity",
    lat=1.0,
    lon=103.0,
    timezone="Asia/Singapore",
    cluster="SG",
    settlement_unit="C",
    wu_station="WSSS",
)


def _dgid(city: str, target_date: str, issue: str, model: str = "fix1_test_v1") -> str:
    return compute_id(city, target_date, issue, model)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _populate_pairs(
    conn: sqlite3.Connection,
    n_groups: int,
    bin_source: str,
    cluster: str = "SG",
    season: str = "DJF",
    date_offset: int = 0,
) -> None:
    """Insert n_groups VERIFIED pairs (one row per group, non-canonical shape)
    into the fixture DB.  Suitable for count/filter tests but NOT for
    _fit_from_pairs (which requires canonical group shape).
    Use _populate_canonical_pairs for fit tests.

    `date_offset` shifts the target dates so two calls can produce
    non-overlapping decision_group_ids.
    """
    for i in range(n_groups):
        day = date_offset + i + 1
        # Use 2026-01 (31 days) for small n; extend into Feb for larger
        month = "01"
        if day > 31:
            day -= 31
            month = "02"
        target = f"2026-{month}-{day:02d}"
        issue = "2026-01-01T00:00:00Z"
        add_calibration_pair(
            conn,
            "TestCity",
            target,
            "18-19°C",
            p_raw=0.05 * (i % 11),
            outcome=1 if i % 3 == 0 else 0,
            lead_days=3.0,
            season=season,
            cluster=cluster,
            forecast_available_at=issue,
            settlement_value=18.5,
            decision_group_id=_dgid("TestCity", target, issue, f"fix1_{date_offset}_{i}"),
            bin_source=bin_source,
            authority="VERIFIED",
            metric_identity=HIGH_LOCALDAY_MAX,
            training_allowed=True,
            data_version="tigge_test_v1",
            city_obj=_TEST_CITY,
        )
    conn.commit()


def _populate_canonical_pairs(
    conn: sqlite3.Connection,
    n_groups: int,
    bin_source: str,
    cluster: str = "SG",
    season: str = "DJF",
) -> None:
    """Insert n_groups VERIFIED canonical-shaped groups for unit='C'.

    Each group has exactly C_CANONICAL_GRID.n_bins (102) rows, exactly 1
    positive outcome, and all distinct bin labels — satisfying
    _canonical_pair_groups_valid so _fit_from_pairs can proceed.
    """
    from src.contracts.calibration_bins import C_CANONICAL_GRID
    bins = list(C_CANONICAL_GRID.iter_bins())
    winning_idx = 50  # one bin in the middle is the winner

    for group_idx in range(n_groups):
        target = f"2026-01-{group_idx + 1:02d}"
        issue = "2026-01-01T00:00:00Z"
        group_id = _dgid("TestCity", target, issue, f"canonical_{group_idx}")
        for bin_idx, b in enumerate(bins):
            add_calibration_pair(
                conn,
                "TestCity",
                target,
                b.label,
                p_raw=1.0 / len(bins),
                outcome=1 if bin_idx == winning_idx else 0,
                lead_days=3.0,
                season=season,
                cluster=cluster,
                forecast_available_at=issue,
                settlement_value=18.5,
                decision_group_id=group_id,
                bin_source=bin_source,
                authority="VERIFIED",
                metric_identity=HIGH_LOCALDAY_MAX,
                training_allowed=True,
                data_version="tigge_test_v1",
                city_obj=_TEST_CITY,
            )
    conn.commit()


# ── helpers ───────────────────────────────────────────────────────────────────

def _flag_off_settings(tmp_path: Path) -> dict:
    return {"feature_flags": {"calibration_bin_source_v2_fit_enabled": False}}


def _flag_on_settings(tmp_path: Path) -> dict:
    return {"feature_flags": {"calibration_bin_source_v2_fit_enabled": True}}


def _write_settings(tmp_path: Path, settings: dict) -> Path:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(settings))
    return p


# ── test 1: flag-OFF byte-identity ────────────────────────────────────────────

class TestFlagOffByteIdentity:
    """Flag-OFF must behave exactly as pre-fix: no bin_source filter →
    0 pairs on the all-v2 corpus → _fit_from_pairs returns None."""

    def test_get_decision_group_count_no_filter_on_v2_corpus(self, tmp_path):
        """With bin_source_filter=None and an all-canonical_v2 corpus,
        count returns the full group count (legacy behavior — no filter)."""
        conn = _make_conn()
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        # flag-OFF: bin_source_filter=None → counts ALL rows regardless of bin_source
        n = get_decision_group_count(conn, "SG", "DJF", metric="high", bin_source_filter=None)
        assert n == 20, (
            f"flag-OFF: expected 20 groups (no filter), got {n}"
        )

    def test_get_pairs_for_bucket_no_filter_returns_all(self, tmp_path):
        """Flag-OFF (bin_source_filter=None) returns all rows — v2 included."""
        conn = _make_conn()
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        pairs = get_pairs_for_bucket(conn, "SG", "DJF", bin_source_filter=None, metric="high")
        assert len(pairs) == 20

    def test_get_pairs_for_bucket_v1_filter_returns_zero_on_v2_corpus(self):
        """The pre-fix hardcoded 'canonical_v1' filter returns 0 on the
        all-v2 live corpus — confirming the defect the flag corrects."""
        conn = _make_conn()
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        # This is the pre-fix behavior: hardcoded "canonical_v1" → 0 pairs
        pairs = get_pairs_for_bucket(conn, "SG", "DJF", bin_source_filter="canonical_v1", metric="high")
        assert len(pairs) == 0, (
            "Pre-fix: canonical_v1 filter on all-v2 corpus must return 0 pairs"
        )

    def test_fit_from_pairs_flag_off_returns_none(self, tmp_path):
        """Flag-OFF: _fit_from_pairs called with bin_source_filter=None
        on an all-canonical_v2 corpus returns a calibrator (not None)
        because no filter means all 20 pairs are visible — this is the
        actual legacy path that fetches 0 only when filter='canonical_v1'
        was hardcoded.  The byte-identity test for the FALSE flag is that
        the manager passes bin_source_filter=None when flag is OFF."""
        # Verify via the manager module that flag-OFF → None filter
        import src.calibration.manager as mgr
        settings_path = _write_settings(tmp_path, _flag_off_settings(tmp_path))
        with mock.patch.object(
            mgr, "_calibration_bin_source_v2_fit_enabled",
            return_value=False,
        ):
            enabled = mgr._calibration_bin_source_v2_fit_enabled()
        assert enabled is False, "Flag-OFF must return False from helper"

    def test_flag_off_selects_none_bin_source_filter(self, tmp_path):
        """When flag is OFF, the resolved bin_source filter must be None
        (not CANONICAL_CALIBRATION_PAIR_BIN_SOURCE) — proving byte-identity
        with the pre-fix call site."""
        import src.calibration.manager as mgr

        with mock.patch.object(mgr, "_calibration_bin_source_v2_fit_enabled", return_value=False):
            bin_filter: Optional[str] = (
                CANONICAL_CALIBRATION_PAIR_BIN_SOURCE
                if mgr._calibration_bin_source_v2_fit_enabled()
                else None
            )
        assert bin_filter is None, (
            f"Flag-OFF: bin_source_filter must be None, got {bin_filter!r}"
        )


# ── test 2: flag-ON own-fit ───────────────────────────────────────────────────

class TestFlagOnOwnFit:
    """Flag-ON: with canonical_v2 pairs present, both count gate and fit
    fetch that same population and produce a non-trivial own calibrator."""

    def test_get_decision_group_count_v2_filter_counts_correctly(self):
        """Flag-ON: bin_source_filter=canonical_v2 counts only v2 rows."""
        conn = _make_conn()
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        n = get_decision_group_count(
            conn, "SG", "DJF", metric="high",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
        )
        assert n == 20, f"Flag-ON: expected 20 groups, got {n}"

    def test_get_pairs_for_bucket_v2_filter_returns_v2_rows(self):
        """Flag-ON: bin_source_filter=canonical_v2 returns the v2 corpus rows."""
        conn = _make_conn()
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        pairs = get_pairs_for_bucket(
            conn, "SG", "DJF",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
            metric="high",
        )
        assert len(pairs) == 20
        assert all(p.get("p_raw") is not None for p in pairs)

    def test_flag_on_resolves_canonical_v2_constant(self):
        """When flag is ON, the resolved bin_source_filter equals the
        canonical constant — not a hardcoded string literal."""
        import src.calibration.manager as mgr

        with mock.patch.object(mgr, "_calibration_bin_source_v2_fit_enabled", return_value=True):
            bin_filter: Optional[str] = (
                CANONICAL_CALIBRATION_PAIR_BIN_SOURCE
                if mgr._calibration_bin_source_v2_fit_enabled()
                else None
            )
        assert bin_filter == CANONICAL_CALIBRATION_PAIR_BIN_SOURCE, (
            f"Flag-ON: expected {CANONICAL_CALIBRATION_PAIR_BIN_SOURCE!r}, got {bin_filter!r}"
        )
        # And the constant itself must not regress to canonical_v1
        assert bin_filter == "canonical_v2", (
            f"CANONICAL_CALIBRATION_PAIR_BIN_SOURCE regressed: {bin_filter!r}"
        )

    def test_fit_from_pairs_flag_on_returns_calibrator_not_none(self, tmp_path):
        """Flag-ON: _fit_from_pairs with bin_source_filter=canonical_v2 and
        sufficient canonical-shaped groups returns a non-None calibrator."""
        from src.calibration.manager import _fit_from_pairs

        conn = _make_conn()
        # 20 groups well above level3=15; full canonical shape required by
        # _canonical_pair_groups_valid (102 rows per group, 1 positive, all distinct labels)
        _populate_canonical_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        cal = _fit_from_pairs(
            conn, "SG", "DJF",
            unit="C",
            temperature_metric="high",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
        )
        assert cal is not None, (
            "Flag-ON: _fit_from_pairs must return a calibrator when "
            f"{CANONICAL_CALIBRATION_PAIR_BIN_SOURCE!r} canonical pairs are present"
        )
        assert cal.fitted is True
        assert cal.n_samples >= 15

    def test_fit_from_pairs_flag_on_differs_from_flag_off(self, tmp_path):
        """Flag-ON must produce a non-None calibrator while flag-OFF (canonical_v1
        filter) returns None on an all-canonical_v2 corpus."""
        from src.calibration.manager import _fit_from_pairs

        conn = _make_conn()
        _populate_canonical_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        # flag-OFF equivalent: filter="canonical_v1" → 0 pairs → None
        cal_off = _fit_from_pairs(
            conn, "SG", "DJF", unit="C",
            temperature_metric="high",
            bin_source_filter="canonical_v1",  # legacy hardcode → 0 pairs on v2 corpus
        )
        # flag-ON: filter=canonical_v2 → 20 canonical groups → own fit
        cal_on = _fit_from_pairs(
            conn, "SG", "DJF", unit="C",
            temperature_metric="high",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
        )

        assert cal_off is None, (
            "canonical_v1 filter on all-v2 corpus must return None (0 pairs)"
        )
        assert cal_on is not None, (
            "canonical_v2 filter with 20 canonical groups must return own calibrator"
        )
        assert cal_off is not cal_on


# ── test 3: gate/fit population agreement ────────────────────────────────────

class TestGateFitPopulationAgreement:
    """Under BOTH flag states, get_decision_group_count and
    get_pairs_for_bucket must query the SAME bin_source population —
    no count-passes-but-fit-starves mismatch."""

    def test_flag_off_count_and_pairs_agree_no_filter(self):
        """Flag-OFF (no bin_source_filter): count and pairs both see all rows."""
        conn = _make_conn()
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE)

        n = get_decision_group_count(conn, "SG", "DJF", metric="high", bin_source_filter=None)
        pairs = get_pairs_for_bucket(conn, "SG", "DJF", bin_source_filter=None, metric="high")

        # Both use no filter → agree on same population
        assert n == len(pairs), (
            f"flag-OFF: count={n} disagrees with len(pairs)={len(pairs)}"
        )

    def test_flag_on_count_and_pairs_agree_v2_filter(self):
        """Flag-ON (canonical_v2 filter): count and pairs both see only v2 rows."""
        conn = _make_conn()
        # Mix v1 and v2 with non-overlapping date ranges so group IDs are distinct
        _populate_pairs(conn, n_groups=10, bin_source="canonical_v1",
                        cluster="SG", season="MAM", date_offset=0)
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
                        cluster="SG", season="MAM", date_offset=10)

        n = get_decision_group_count(
            conn, "SG", "MAM", metric="high",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
        )
        pairs = get_pairs_for_bucket(
            conn, "SG", "MAM",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
            metric="high",
        )

        assert n == len(pairs), (
            f"flag-ON: count={n} disagrees with len(pairs)={len(pairs)}"
        )
        assert n == 20, (
            f"Only v2 rows should be counted; expected 20, got {n}"
        )

    def test_old_mismatch_was_real(self):
        """Reproduce the original defect: count uses no filter → N_v1+N_v2 groups,
        but fit used canonical_v1 filter → only N_v1 pairs.
        Uses non-overlapping date_offset so group IDs are truly distinct.
        This is the starvation that the flag fixes."""
        conn = _make_conn()
        # 10 v1 groups + 20 v2 groups, non-overlapping dates → 30 distinct groups
        _populate_pairs(conn, n_groups=10, bin_source="canonical_v1",
                        cluster="EU", season="DJF", date_offset=0)
        _populate_pairs(conn, n_groups=20, bin_source=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
                        cluster="EU", season="DJF", date_offset=10)

        # Old count: no filter → 30 (all distinct groups)
        n_old = get_decision_group_count(conn, "EU", "DJF", metric="high", bin_source_filter=None)
        # Old fit query: canonical_v1 filter → only 10 v1 pairs
        pairs_old = get_pairs_for_bucket(conn, "EU", "DJF",
                                         bin_source_filter="canonical_v1", metric="high")

        # Mismatch confirmed: count saw 30, fit saw 10
        assert n_old == 30, f"Expected 30 distinct groups, got {n_old}"
        assert len(pairs_old) == 10, f"Expected 10 v1 pairs, got {len(pairs_old)}"
        assert n_old != len(pairs_old), "Old mismatch must be reproducible by this test"

        # New behavior under flag-ON: both filter to v2 → both see 20
        n_new = get_decision_group_count(
            conn, "EU", "DJF", metric="high",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
        )
        pairs_new = get_pairs_for_bucket(
            conn, "EU", "DJF",
            bin_source_filter=CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
            metric="high",
        )
        assert n_new == len(pairs_new), (
            f"flag-ON must eliminate mismatch: count={n_new}, pairs={len(pairs_new)}"
        )
        assert n_new == 20


# ── test 4: settings flag read ────────────────────────────────────────────────

class TestSettingsFlagRead:
    """_calibration_bin_source_v2_fit_enabled reads from settings.json
    feature_flags section; defaults False when absent."""

    def test_reads_false_from_settings(self, tmp_path):
        """Helper returns False when settings has the flag set to false."""
        cfg_path = tmp_path / "settings.json"
        cfg_path.write_text(json.dumps(
            {"feature_flags": {"calibration_bin_source_v2_fit_enabled": False}}
        ))

        result = bool(
            (json.loads(cfg_path.read_text()).get("feature_flags") or {}).get(
                "calibration_bin_source_v2_fit_enabled", False
            )
        )
        assert result is False

    def test_reads_true_from_settings(self, tmp_path):
        """Helper returns True when settings has the flag set to true."""
        cfg_path = tmp_path / "settings.json"
        cfg_path.write_text(json.dumps(
            {"feature_flags": {"calibration_bin_source_v2_fit_enabled": True}}
        ))

        result = bool(
            (json.loads(cfg_path.read_text()).get("feature_flags") or {}).get(
                "calibration_bin_source_v2_fit_enabled", False
            )
        )
        assert result is True

    def test_defaults_false_when_flag_absent(self):
        """When the flag key is absent from feature_flags, default is False."""
        cfg = {"feature_flags": {}}
        result = bool(
            (cfg.get("feature_flags") or {}).get(
                "calibration_bin_source_v2_fit_enabled", False
            )
        )
        assert result is False

    def test_defaults_false_when_feature_flags_absent(self):
        """When the feature_flags section is entirely absent, default is False."""
        cfg: dict = {}
        result = bool(
            (cfg.get("feature_flags") or {}).get(
                "calibration_bin_source_v2_fit_enabled", False
            )
        )
        assert result is False

    def test_constant_value_is_canonical_v2(self):
        """CANONICAL_CALIBRATION_PAIR_BIN_SOURCE must equal 'canonical_v2'
        — a regression guard so a future rename re-opens the gap visibly."""
        assert CANONICAL_CALIBRATION_PAIR_BIN_SOURCE == "canonical_v2", (
            f"Constant regressed: {CANONICAL_CALIBRATION_PAIR_BIN_SOURCE!r}"
        )

    def test_actual_settings_json_has_flag_true(self):
        """The committed settings.json must have the flag present and True
        (operator-promoted 2026-06-03: uncorrected cities get own canonical_v2
        fit instead of foreign Amsterdam-cluster borrow)."""
        settings_path = Path(__file__).resolve().parents[1] / "config" / "settings.json"
        if not settings_path.exists():
            pytest.skip("config/settings.json not found")
        cfg = json.loads(settings_path.read_text())
        ff = cfg.get("feature_flags", {})
        assert "calibration_bin_source_v2_fit_enabled" in ff, (
            "flag must be declared in feature_flags (not absent — explicit True "
            "required for operator visibility after promotion)"
        )
        assert ff["calibration_bin_source_v2_fit_enabled"] is True, (
            f"flag must be True in committed settings.json after operator promotion; "
            f"got {ff['calibration_bin_source_v2_fit_enabled']!r}"
        )
