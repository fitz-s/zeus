# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3. End-to-end test of the LIVE coverage application step
#   (_maybe_apply_settlement_coverage_to_lcb in event_reactor_adapter) including the
#   grade_receipt-backed backward-coverage loader (_settlement_coverage_observations).
#   Proves: (1) flag OFF -> the live q_lcb carrier is byte-identical (no DB even read);
#   (2) flag ON + UNLICENSED settled record -> the carrier entry shrinks to the realized
#   rate and its calibration_source flips to SETTLEMENT_ISOTONIC. The settled win/loss is
#   produced ONLY through the spine grade_receipt.
"""Live-path tests for the K3 settlement-coverage shrink + its grade_receipt loader."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.engine.event_reactor_adapter as adapter
from src.calibration.qlcb_provenance import QlcbByDirection, QlcbProvenance
from src.types.market import Bin


def _settlement_db(tmp_path, city, metric, unit, in_bin_value, out_bin_value, n_in, n_out):
    """A temp forecasts DB with settlement_outcomes for one (city, metric).

    n_in settlements land at in_bin_value (a value INSIDE the traded bin), n_out at
    out_bin_value (OUTSIDE). For a buy_no on the bin: in-bin -> LOSS, out-bin -> WIN.
    """
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE settlement_outcomes ("
        "city TEXT, temperature_metric TEXT, settlement_value REAL, settlement_unit TEXT)"
    )
    rows = (
        [(city, metric, float(in_bin_value), unit)] * n_in
        + [(city, metric, float(out_bin_value), unit)] * n_out
    )
    conn.executemany(
        "INSERT INTO settlement_outcomes (city,temperature_metric,settlement_value,settlement_unit) "
        "VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


def _set_flag(monkeypatch, enabled: bool):
    from src.config import settings

    edli = dict(settings._data["edli"])
    edli["q_lcb_settlement_coverage_gate_enabled"] = enabled
    monkeypatch.setitem(settings._data, "edli", edli)


def _family(city, metric, bin_obj, condition_id="cond0"):
    return SimpleNamespace(
        city=city,
        metric=metric,
        target_date="2026-07-15",  # JJA NH
        candidates=[SimpleNamespace(condition_id=condition_id, bin=bin_obj)],
    )


def _typed_lcb(condition_id, yes_q, no_q):
    d = QlcbByDirection()
    d[(condition_id, "buy_yes")] = QlcbProvenance(yes_q, "FORECAST_BOOTSTRAP")
    d[(condition_id, "buy_no")] = QlcbProvenance(no_q, "FORECAST_BOOTSTRAP")
    return d


def test_observations_built_through_grade_receipt(tmp_path):
    """The backward-coverage loader grades each settled value through grade_receipt:
    a buy_no on '64-65°F' WINS when settled out-of-bin, LOSES when settled in-bin."""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn = _settlement_db(tmp_path, "San Francisco", "high", "F",
                          in_bin_value=64.0, out_bin_value=70.0, n_in=10, n_out=20)
    obs = adapter._settlement_coverage_observations(
        forecast_conn=conn, city="San Francisco", metric="high",
        bin=bin_f, direction="buy_no", claimed_q_lcb=0.90,
    )
    assert len(obs) == 30
    wins = sum(1 for o in obs if o.won)
    assert wins == 20  # out-of-bin = buy_no win


def test_flag_off_live_application_is_byte_identical(tmp_path, monkeypatch):
    """Flag OFF: _maybe_apply_settlement_coverage_to_lcb is an immediate no-op; the
    q_lcb carrier is byte-identical (the UNLICENSED settled record is never even read)."""
    _set_flag(monkeypatch, False)
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn = _settlement_db(tmp_path, "San Francisco", "high", "F",
                          in_bin_value=64.0, out_bin_value=70.0, n_in=10, n_out=20)
    lcb = _typed_lcb("cond0", yes_q=0.10, no_q=0.90)
    before_yes = lcb[("cond0", "buy_yes")].q_lcb
    before_no = lcb[("cond0", "buy_no")].q_lcb

    adapter._maybe_apply_settlement_coverage_to_lcb(
        family=_family("San Francisco", "high", bin_f),
        forecast_conn=conn, lcb_by_direction=lcb,
    )
    assert lcb[("cond0", "buy_yes")].q_lcb == before_yes
    assert lcb[("cond0", "buy_no")].q_lcb == before_no
    assert lcb[("cond0", "buy_no")].calibration_source == "FORECAST_BOOTSTRAP"  # unchanged


def test_flag_on_unlicensed_shrinks_to_realized_with_isotonic_source(tmp_path, monkeypatch):
    """Flag ON: a buy_no claiming q_lcb=0.90 but realizing only 20/30=0.667 in the
    settled record is UNLICENSED -> shrunk to 0.667-0.01=0.657, source SETTLEMENT_ISOTONIC."""
    _set_flag(monkeypatch, True)
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn = _settlement_db(tmp_path, "San Francisco", "high", "F",
                          in_bin_value=64.0, out_bin_value=70.0, n_in=10, n_out=20)
    lcb = _typed_lcb("cond0", yes_q=0.10, no_q=0.90)

    adapter._maybe_apply_settlement_coverage_to_lcb(
        family=_family("San Francisco", "high", bin_f),
        forecast_conn=conn, lcb_by_direction=lcb,
    )
    no_entry = lcb[("cond0", "buy_no")]
    assert no_entry.q_lcb == pytest.approx(20.0 / 30.0 - 0.01, abs=1e-9)
    assert no_entry.calibration_source == "SETTLEMENT_ISOTONIC"
    assert no_entry.n_settlement_observations == 30


def test_flag_on_insufficient_data_keeps_lcb(tmp_path, monkeypatch):
    """Flag ON but < min_n=30 settled obs: INSUFFICIENT_DATA -> q_lcb unchanged."""
    _set_flag(monkeypatch, True)
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn = _settlement_db(tmp_path, "San Francisco", "high", "F",
                          in_bin_value=64.0, out_bin_value=70.0, n_in=4, n_out=8)  # n=12 < 30
    lcb = _typed_lcb("cond0", yes_q=0.10, no_q=0.90)

    adapter._maybe_apply_settlement_coverage_to_lcb(
        family=_family("San Francisco", "high", bin_f),
        forecast_conn=conn, lcb_by_direction=lcb,
    )
    assert lcb[("cond0", "buy_no")].q_lcb == pytest.approx(0.90)  # unchanged
    assert lcb[("cond0", "buy_no")].calibration_source == "FORECAST_BOOTSTRAP"


def test_deep_otm_bin_forecast_bootstrap_write_does_not_collapse_family():
    """K3 family-formation regression (adversarial-verify finding #1, CRITICAL).

    The FORECAST_BOOTSTRAP restore at adapter:3402 writes
    ``float(hyp.ci_lower) + cost`` into the typed lcb_by_direction. For a deep-OTM
    bin (p_posterior~0) the edge CI lower bound is negative, so the restored q_lcb
    is NEGATIVE. Pre-fix, QlcbProvenance raised ValueError on the out-of-range value;
    that propagated to the family catch (adapter:732) -> LIVE_INFERENCE_INPUTS_MISSING
    and collapsed the WHOLE family even with the K3 shadow flag OFF (the unconditional
    type, not the flag). Legacy (origin/main, plain dict) tolerated it: the bin lost
    selection, the family still formed.

    This drives the SAME write helper the family scan uses with a negative deep-tail
    value and asserts: (1) it does NOT raise (the family still forms), and (2) the
    value is clamped to 0.0 with clamped=True — decision-equivalent to the legacy raw
    negative (q_lcb=0.0 and q_lcb<0 both yield a negative robust trade score, so the
    bin loses selection identically). Flag is irrelevant — the type is unconditional.
    """
    from src.calibration.qlcb_provenance import (
        QlcbByDirection,
        _qlcb_float,
        _set_qlcb_provenance,
    )

    lcb = QlcbByDirection()
    # ci_lower=-0.07 (deep-OTM edge CI), cost=0.02 -> restored q_lcb = -0.05.
    deep_tail_q_lcb = -0.07 + 0.02
    # Must NOT raise — pre-fix this raised ValueError -> LIVE_INFERENCE_INPUTS_MISSING.
    _set_qlcb_provenance(
        lcb, ("cond_deep_otm", "buy_no"), deep_tail_q_lcb, source="FORECAST_BOOTSTRAP"
    )
    entry = lcb[("cond_deep_otm", "buy_no")]
    assert entry.q_lcb == pytest.approx(0.0)  # clamped into [0,1]
    assert entry.clamped is True
    assert _qlcb_float(entry) == pytest.approx(0.0)  # consumer reads 0.0, loses selection
