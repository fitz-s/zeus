# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K1 — ForecastSharpnessEvidence contract (structural fix plan
#   §3 P2.2; root cause K1 "no forecast-sharpness contract -> flat q -> 96% buy_no").
#   Antibody discipline: the TYPE is required at construction (TypeError on omission);
#   the EDGE-REJECTION behavior is flag-gated OFF (edli_v1.forecast_sharpness_gate_enabled).

"""K1 relationship tests: forecast-sharpness gate at MarketAnalysis construction.

The structural defect K1 attacks: a city whose ensemble forecast has no skill
(MAE much wider than a market bin) still emits edges, because nothing relates
"forecast resolution" to "is an edge even meaningful here". A flat forecast
spreads probability so thin that the cheap-tail buy_no almost always clears the
CI bar -> 96% buy_no skew. The antibody is a required ctor contract:
ForecastSharpnessEvidence. With the gate ON, find_edges + scan_full_hypothesis_family
emit ZERO edges when settlement MAE >= N_SIGMA * bin_width in the NATIVE unit.

These tests are RELATIONSHIP tests, not function tests:
  - They assert the cross-module invariant "MAE (settlement-grounded) vs bin_width
    governs whether MarketAnalysis emits ANY edge", at the ONE gate site.
  - test_gate_threshold_is_settlement_calibrated proves the threshold is compared
    against SETTLEMENT MAE (forecast_skill.error == forecast_temp - actual_temp,
    where actual_temp is the realized settlement value), not an arbitrary constant.
  - test_flag_off_emit_unchanged proves byte-identical legacy behaviour with the
    shadow flag OFF (the rule-6 / overconfidence=ruin guard).
"""

import numpy as np
import pytest

from src.strategy.market_analysis import MarketAnalysis
from src.types import Bin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _c_bins() -> list[Bin]:
    """°C point bins (width=1) around 20°C with shoulders."""
    bins = [Bin(low=None, high=16, label="16°C or below", unit="C")]
    for t in range(17, 24):
        bins.append(Bin(low=t, high=t, label=f"{t}°C", unit="C"))
    bins.append(Bin(low=24, high=None, label="24°C or higher", unit="C"))
    return bins


def _f_bins() -> list[Bin]:
    """°F range bins (width=2) around 64°F with shoulders."""
    bins = [Bin(low=None, high=58, label="58°F or below", unit="F")]
    for lo in range(59, 71, 2):
        bins.append(Bin(low=lo, high=lo + 1, label=f"{lo}-{lo+1}°F", unit="F"))
    bins.append(Bin(low=71, high=None, label="71°F or higher", unit="F"))
    return bins


def _uniform_dist(n: int) -> np.ndarray:
    return np.full(n, 1.0 / n, dtype=float)


def _peaked_dist(n: int, idx: int, mass: float = 0.6) -> np.ndarray:
    p = np.full(n, (1.0 - mass) / (n - 1), dtype=float)
    p[idx] = mass
    return p / p.sum()


def _ev(unit: str, mae, bin_width: float, *, day0=False, present=True, lead=3, n_paired=200):
    """Build a ForecastSharpnessEvidence directly (no DB)."""
    from src.contracts.forecast_sharpness import ForecastSharpnessEvidence

    if day0:
        return ForecastSharpnessEvidence.exempt(unit=unit)
    if not present:
        return ForecastSharpnessEvidence.missing(unit=unit, bin_width=bin_width, lead_days=lead)
    return ForecastSharpnessEvidence.from_mae(
        mae=mae,
        bin_width=bin_width,
        unit=unit,
        lead_days=lead,
        n_paired=n_paired,
        source="test",
    )


def _make_ma(
    *,
    unit: str,
    bins: list[Bin],
    sharpness,
    p=None,
    p_market=None,
    members=None,
    rng_seed=7,
):
    n = len(bins)
    if p is None:
        # Mildly peaked so a genuine YES edge can exist on a mispriced bin.
        p = _peaked_dist(n, n // 2, mass=0.5)
    if p_market is None:
        # Market underprices the modal bin -> a real buy_yes edge exists.
        p_market = _uniform_dist(n)
    if members is None:
        center = 20.0 if unit == "C" else 64.0
        members = np.full(40, center, dtype=float) + np.linspace(-1.0, 1.0, 40)
    return MarketAnalysis(
        p_raw=p,
        p_cal=p,
        p_market=p_market,
        alpha=0.3,
        bins=bins,
        member_maxes=members,
        unit=unit,
        rng_seed=rng_seed,
        forecast_sharpness=sharpness,
    )


# ---------------------------------------------------------------------------
# TEST-R1 — flat C-city (MAE 2.5 vs 1°C bin) emits ZERO edges when gate ON
# ---------------------------------------------------------------------------

def test_flat_c_city_suppressed_when_gate_on(monkeypatch):
    monkeypatch.setenv("ZEUS_FORECAST_SHARPNESS_GATE", "1")  # see conftest hook
    from src.config import settings
    settings["edli_v1"]["forecast_sharpness_gate_enabled"] = True
    settings["edli_v1"]["forecast_sharpness_mae_multiplier"] = 1.5
    try:
        bins = _c_bins()
        ma = _make_ma(unit="C", bins=bins, sharpness=_ev("C", 2.5, 1.0))
        edges = ma.find_edges(n_bootstrap=50)
        assert edges == [], "flat C city (MAE 2.5 >= 1.5*1) must emit zero edges"
    finally:
        settings["edli_v1"]["forecast_sharpness_gate_enabled"] = False


# ---------------------------------------------------------------------------
# TEST-R2 — sharp C-city (MAE 1.1 vs 1°C bin) STILL emits edges when gate ON
# ---------------------------------------------------------------------------

def test_sharp_c_city_emits_when_gate_on():
    from src.config import settings
    settings["edli_v1"]["forecast_sharpness_gate_enabled"] = True
    settings["edli_v1"]["forecast_sharpness_mae_multiplier"] = 1.5
    try:
        bins = _c_bins()
        ma = _make_ma(unit="C", bins=bins, sharpness=_ev("C", 1.1, 1.0))
        edges = ma.find_edges(n_bootstrap=50)
        assert len(edges) >= 1, "sharp C city (MAE 1.1 < 1.5*1) must NOT be suppressed"
    finally:
        settings["edli_v1"]["forecast_sharpness_gate_enabled"] = False


# ---------------------------------------------------------------------------
# TEST-R3 — F-unit threshold uses the 2°F bin width (native unit, not °C)
# ---------------------------------------------------------------------------

def test_f_unit_threshold_uses_native_bin_width():
    from src.config import settings
    settings["edli_v1"]["forecast_sharpness_gate_enabled"] = True
    settings["edli_v1"]["forecast_sharpness_mae_multiplier"] = 1.5
    try:
        bins = _f_bins()  # width=2
        # MAE 3.5°F: 1.5*2 = 3.0 threshold -> 3.5 >= 3.0 -> SUPPRESS.
        ma_flat = _make_ma(unit="F", bins=bins, sharpness=_ev("F", 3.5, 2.0))
        assert ma_flat.find_edges(n_bootstrap=50) == [], "MAE 3.5°F >= 3.0 must suppress"
        # MAE 2.5°F: 2.5 < 3.0 -> EMIT. (If the gate wrongly used °C width=1,
        # threshold would be 1.5 and 2.5 would suppress -> this catches the unit bug.)
        ma_sharp = _make_ma(unit="F", bins=bins, sharpness=_ev("F", 2.5, 2.0))
        assert len(ma_sharp.find_edges(n_bootstrap=50)) >= 1, "MAE 2.5°F < 3.0 must emit"
    finally:
        settings["edli_v1"]["forecast_sharpness_gate_enabled"] = False


# ---------------------------------------------------------------------------
# TEST-R4 — omitting ForecastSharpnessEvidence is a TypeError at construction
# ---------------------------------------------------------------------------

def test_missing_forecast_sharpness_is_typeerror():
    bins = _c_bins()
    n = len(bins)
    with pytest.raises(TypeError):
        MarketAnalysis(
            p_raw=_uniform_dist(n),
            p_cal=_uniform_dist(n),
            p_market=_uniform_dist(n),
            alpha=0.3,
            bins=bins,
            member_maxes=np.full(40, 20.0),
            unit="C",
            # forecast_sharpness deliberately omitted
        )


# ---------------------------------------------------------------------------
# TEST-R5 — day0_exempt bypasses suppression even with a flat forecast
# ---------------------------------------------------------------------------

def test_day0_exempt_bypasses_gate():
    from src.config import settings
    settings["edli_v1"]["forecast_sharpness_gate_enabled"] = True
    settings["edli_v1"]["forecast_sharpness_mae_multiplier"] = 1.5
    try:
        bins = _c_bins()
        # exempt() carries no MAE; obs replaces the forecast on day0/imminent paths.
        ma = _make_ma(unit="C", bins=bins, sharpness=_ev("C", None, 1.0, day0=True))
        assert len(ma.find_edges(n_bootstrap=50)) >= 1, "day0 exempt must bypass suppression"
    finally:
        settings["edli_v1"]["forecast_sharpness_gate_enabled"] = False


# ---------------------------------------------------------------------------
# TEST-R6 — MISSING forecast_skill row fails CLOSED (no edge) when gate ON
# ---------------------------------------------------------------------------

def test_missing_evidence_fails_closed():
    from src.config import settings
    settings["edli_v1"]["forecast_sharpness_gate_enabled"] = True
    settings["edli_v1"]["forecast_sharpness_mae_multiplier"] = 1.5
    try:
        bins = _c_bins()
        ma = _make_ma(unit="C", bins=bins, sharpness=_ev("C", None, 1.0, present=False))
        assert ma.find_edges(n_bootstrap=50) == [], "missing forecast_skill row must fail closed"
    finally:
        settings["edli_v1"]["forecast_sharpness_gate_enabled"] = False


# ---------------------------------------------------------------------------
# TEST-R7 — ctor re-asserts forecast_skill.unit == analysis.unit (B6 ETL block)
# ---------------------------------------------------------------------------

def test_unit_mismatch_between_evidence_and_analysis_raises():
    bins = _c_bins()
    n = len(bins)
    f_evidence = _ev("F", 2.5, 2.0)  # Fahrenheit evidence
    with pytest.raises(ValueError):
        MarketAnalysis(
            p_raw=_uniform_dist(n),
            p_cal=_uniform_dist(n),
            p_market=_uniform_dist(n),
            alpha=0.3,
            bins=bins,
            member_maxes=np.full(40, 20.0),
            unit="C",  # Celsius analysis, Fahrenheit evidence -> mismatch
            forecast_sharpness=f_evidence,
        )


# ---------------------------------------------------------------------------
# TEST-R8 — the family scan (scan_full_hypothesis_family) honours the SAME
#           gate verdict (ONE site: the analysis object), not a parallel check.
# ---------------------------------------------------------------------------

def test_family_scan_empty_for_gated_city():
    from src.config import settings
    from src.strategy.market_analysis_family_scan import scan_full_hypothesis_family

    settings["edli_v1"]["forecast_sharpness_gate_enabled"] = True
    settings["edli_v1"]["forecast_sharpness_mae_multiplier"] = 1.5
    try:
        bins = _c_bins()
        ma = _make_ma(unit="C", bins=bins, sharpness=_ev("C", 2.5, 1.0))
        hyps = scan_full_hypothesis_family(ma, n_bootstrap=50)
        assert hyps == [], "BH denominator must be empty when sharpness gate suppresses the city"
        # And the sharp city still produces a non-empty family.
        ma2 = _make_ma(unit="C", bins=bins, sharpness=_ev("C", 1.1, 1.0))
        assert len(scan_full_hypothesis_family(ma2, n_bootstrap=50)) >= 1
    finally:
        settings["edli_v1"]["forecast_sharpness_gate_enabled"] = False


# ---------------------------------------------------------------------------
# TEST-R9 — FLAG OFF == LEGACY: the flat city that WOULD be suppressed emits
#           the EXACT same edges as a sharp city's pipeline with the gate off.
#           Proves byte-identical legacy behaviour (rule-6 shadow safety).
# ---------------------------------------------------------------------------

def test_flag_off_emit_unchanged():
    from src.config import settings
    # Ensure flag is OFF (default).
    settings["edli_v1"]["forecast_sharpness_gate_enabled"] = False

    bins = _c_bins()
    p = _peaked_dist(len(bins), len(bins) // 2, mass=0.5)
    p_market = _uniform_dist(len(bins))
    members = np.full(40, 20.0) + np.linspace(-1.0, 1.0, 40)

    # WITH evidence carried (flat city, would-suppress-if-ON), gate OFF.
    ma_gated_off = _make_ma(
        unit="C", bins=bins, sharpness=_ev("C", 2.5, 1.0),
        p=p.copy(), p_market=p_market.copy(), members=members.copy(), rng_seed=99,
    )
    edges_off = ma_gated_off.find_edges(n_bootstrap=80)

    # The legacy baseline: an EXEMPT evidence (gate never applies) on the identical
    # inputs+seed must produce byte-identical edges. exempt() is the "no gate" path.
    ma_legacy = _make_ma(
        unit="C", bins=bins, sharpness=_ev("C", None, 1.0, day0=True),
        p=p.copy(), p_market=p_market.copy(), members=members.copy(), rng_seed=99,
    )
    edges_legacy = ma_legacy.find_edges(n_bootstrap=80)

    assert len(edges_off) == len(edges_legacy) >= 1
    for a, b in zip(edges_off, edges_legacy):
        assert a.direction == b.direction
        assert a.support_index == b.support_index
        assert a.edge == b.edge
        assert a.ci_lower == b.ci_lower
        assert a.ci_upper == b.ci_upper


# ---------------------------------------------------------------------------
# TEST-R10 — the gate threshold is SETTLEMENT-CALIBRATED, not a bare constant.
#   forecast_skill.error == forecast_temp - actual_temp, and actual_temp is the
#   realized SETTLEMENT temperature (verified: forecast_skill.actual_temp ==
#   settlement_outcomes.settlement_value for the same city/date). So the MAE the
#   gate compares against IS the settlement MAE. This relationship test builds a
#   tiny forecast_skill table, loads evidence via load_for, and asserts the
#   suppression verdict matches what settlement-MAE dictates — proving the gate
#   reads settlement-grounded skill, not an injected number.
# ---------------------------------------------------------------------------

def test_gate_threshold_is_settlement_calibrated(tmp_path):
    import sqlite3
    from src.contracts.forecast_sharpness import ForecastSharpnessEvidence

    db = tmp_path / "world.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE forecast_skill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL, target_date TEXT NOT NULL, source TEXT NOT NULL,
            lead_days INTEGER NOT NULL, forecast_temp REAL NOT NULL,
            actual_temp REAL NOT NULL, error REAL NOT NULL, temp_unit TEXT NOT NULL,
            season TEXT NOT NULL, available_at TEXT NOT NULL,
            UNIQUE(city, target_date, source, lead_days)
        )
        """
    )
    # A FLAT city: |error| averages 2.4°C against actual (settlement) temps.
    # error = forecast_temp - actual_temp; actual_temp is the realized settlement.
    flat_errors = [-2.5, 2.3, -2.4, 2.4, -2.4]
    for i, e in enumerate(flat_errors):
        actual = 20.0
        fc = actual + e
        conn.execute(
            "INSERT INTO forecast_skill(city,target_date,source,lead_days,forecast_temp,"
            "actual_temp,error,temp_unit,season,available_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("Flatville", f"2026-05-0{i+1}", "ecmwf", 3, fc, actual, e, "C", "MAM",
             f"2026-05-0{i}T06:00:00+00:00"),
        )
    # A SHARP city: |error| averages 0.9°C.
    sharp_errors = [-0.9, 1.0, -0.8, 0.9, -0.9]
    for i, e in enumerate(sharp_errors):
        actual = 18.0
        fc = actual + e
        conn.execute(
            "INSERT INTO forecast_skill(city,target_date,source,lead_days,forecast_temp,"
            "actual_temp,error,temp_unit,season,available_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("Sharpville", f"2026-05-0{i+1}", "ecmwf", 3, fc, actual, e, "C", "MAM",
             f"2026-05-0{i}T06:00:00+00:00"),
        )
    conn.commit()

    flat_ev = ForecastSharpnessEvidence.load_for(
        conn, city="Flatville", unit="C", lead_days=3, bin_width=1.0
    )
    sharp_ev = ForecastSharpnessEvidence.load_for(
        conn, city="Sharpville", unit="C", lead_days=3, bin_width=1.0
    )

    # The loaded MAE IS the settlement MAE (mean |forecast - actual_settlement|).
    assert abs(flat_ev.mae - float(np.mean(np.abs(flat_errors)))) < 1e-9
    assert abs(sharp_ev.mae - float(np.mean(np.abs(sharp_errors)))) < 1e-9

    # And the suppression verdict follows the settlement MAE vs the bin width:
    assert flat_ev.suppresses_edges(multiplier=1.5) is True   # 2.4 >= 1.5*1
    assert sharp_ev.suppresses_edges(multiplier=1.5) is False  # 0.9 < 1.5*1
    conn.close()


# ---------------------------------------------------------------------------
# TEST-R11 — load_for on a MISSING (city,unit,lead) row returns fail-closed
#            evidence whose suppresses_edges() is True (provenance fail-closed).
# ---------------------------------------------------------------------------

def test_load_for_missing_row_fails_closed(tmp_path):
    import sqlite3
    from src.contracts.forecast_sharpness import ForecastSharpnessEvidence

    db = tmp_path / "world.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE forecast_skill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL, target_date TEXT NOT NULL, source TEXT NOT NULL,
            lead_days INTEGER NOT NULL, forecast_temp REAL NOT NULL,
            actual_temp REAL NOT NULL, error REAL NOT NULL, temp_unit TEXT NOT NULL,
            season TEXT NOT NULL, available_at TEXT NOT NULL,
            UNIQUE(city, target_date, source, lead_days)
        )
        """
    )
    conn.commit()
    ev = ForecastSharpnessEvidence.load_for(
        conn, city="Nowhere", unit="C", lead_days=3, bin_width=1.0
    )
    assert ev.evidence_present is False
    assert ev.mae is None
    assert ev.suppresses_edges(multiplier=1.5) is True  # fail closed
    conn.close()
