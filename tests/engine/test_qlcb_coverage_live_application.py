# Created: 2026-06-03
# Last reused or audited: 2026-06-14
# Authority basis: Phase-2 K3 + 2026-06-14 CALIBRATION REBUILD
#   (docs/evidence/deadloop_2026-06-14/qlcb_suppression.md + RULE 1). End-to-end test of
#   the LIVE coverage application step (_maybe_apply_settlement_coverage_to_lcb) including
#   the grade_receipt-backed backward-coverage loader (_settlement_coverage_observations).
#   The loader now pairs each settled day with the PER-DAY ACTUAL claimed q_lcb (read from
#   edli_no_submit_receipts), NOT one constant stamped on every day (the climatology defect).
#   Proves: (1) PROVEN-overconfident per-day calibration record -> the carrier entry
#   shrinks and its calibration_source flips to SETTLEMENT_ISOTONIC; (3) absent per-day
#   claim history -> INSUFFICIENT_DATA -> unchanged (inert). The settled win/loss is
#   produced ONLY through the spine grade_receipt. Per-day claims are injected via a
#   monkeypatch of _per_day_claimed_qlcb_by_date (the world-DB receipt reader) so the test
#   does not depend on a populated zeus-world.db.
"""Live-path tests for the K3 settlement-coverage shrink + its per-day calibration loader."""
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


def _settlement_db(
    tmp_path, city, metric, unit, in_bin_value, out_bin_value, n_in, n_out,
    *, start_date="2026-06-01",
):
    """A temp forecasts DB with VERIFIED settlement_outcomes for one (city, metric).

    n_in settlements land at in_bin_value (a value INSIDE the traded bin), n_out at
    out_bin_value (OUTSIDE). For a buy_no on the bin: in-bin -> LOSS, out-bin -> WIN.
    Each settled row gets a DISTINCT target_date (the rebuilt loader keys per-day claims
    on target_date). Returns (conn, [target_dates]).
    """
    import datetime as _dt

    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE settlement_outcomes ("
        "city TEXT, temperature_metric TEXT, target_date TEXT, settlement_value REAL, "
        "settlement_unit TEXT, authority TEXT DEFAULT 'VERIFIED')"
    )
    base = _dt.date.fromisoformat(start_date)
    target_dates: list[str] = []
    rows = []
    values = [in_bin_value] * n_in + [out_bin_value] * n_out
    for i, val in enumerate(values):
        td = (base + _dt.timedelta(days=i)).isoformat()
        target_dates.append(td)
        rows.append((city, metric, td, float(val), unit, "VERIFIED"))
    conn.executemany(
        "INSERT INTO settlement_outcomes "
        "(city,temperature_metric,target_date,settlement_value,settlement_unit,authority) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn, target_dates


def _patch_per_day_claims(monkeypatch, claims_by_date):
    """Inject the per-day ACTUAL claimed q_lcb history the rebuilt loader reads from
    edli_no_submit_receipts, without needing a populated zeus-world.db."""
    monkeypatch.setattr(
        adapter,
        "_per_day_claimed_qlcb_by_date",
        lambda *, city, metric, direction, band_template, coverage_cache=None: dict(claims_by_date),
    )


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


def test_observations_built_through_grade_receipt(tmp_path, monkeypatch):
    """The backward-coverage loader grades each settled value through grade_receipt:
    a buy_no on '64-65°F' WINS when settled out-of-bin, LOSES when settled in-bin.
    REBUILD: a settled day enters the stream ONLY when the model CLAIMED a q_lcb on this
    band that day (per-day claim history injected). Each obs carries that day's ACTUAL
    claim, NOT one constant."""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn, target_dates = _settlement_db(
        tmp_path, "San Francisco", "high", "F",
        in_bin_value=64.0, out_bin_value=70.0, n_in=10, n_out=20,
    )
    # Per-day claimed q_lcb history: a DISTINCT claim per settled day (varies day to day).
    claims = {td: 0.85 + 0.001 * i for i, td in enumerate(target_dates)}
    _patch_per_day_claims(monkeypatch, claims)

    obs = adapter._settlement_coverage_observations(
        forecast_conn=conn, city="San Francisco", metric="high",
        bin=bin_f, direction="buy_no", claimed_q_lcb=0.90,
    )
    assert len(obs) == 30  # one per settled day that has a per-day claim
    wins = sum(1 for o in obs if o.won)
    assert wins == 20  # out-of-bin = buy_no win (graded through grade_receipt)
    # The claimed band VARIES day to day (the whole rebuild — no single constant).
    assert len({round(o.q_lcb, 6) for o in obs}) > 1


def test_observations_empty_without_per_day_claim_history(tmp_path, monkeypatch):
    """REBUILD: with NO per-day claim history (the live state today), the loader returns an
    EMPTY stream -> INSUFFICIENT_DATA -> inert. Absence of claim history never shrinks."""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn, _td = _settlement_db(
        tmp_path, "San Francisco", "high", "F",
        in_bin_value=64.0, out_bin_value=70.0, n_in=10, n_out=20,
    )
    _patch_per_day_claims(monkeypatch, {})  # no claim history
    obs = adapter._settlement_coverage_observations(
        forecast_conn=conn, city="San Francisco", metric="high",
        bin=bin_f, direction="buy_no", claimed_q_lcb=0.90,
    )
    assert obs == []


def test_proven_overconfident_shrinks_with_isotonic_source(tmp_path, monkeypatch):
    """PROVEN overconfident per-day record: the model CLAIMED ~0.90 each of 30
    settled days but the band realized only 20/30=0.667 -> UNLICENSED -> shrunk below the
    claim, source SETTLEMENT_ISOTONIC. (Per-day claims clustered near 0.90 so the isotonic
    reads ~0.667 at the live 0.90 claim.)"""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn, target_dates = _settlement_db(
        tmp_path, "San Francisco", "high", "F",
        in_bin_value=64.0, out_bin_value=70.0, n_in=10, n_out=20,
    )
    claims = {td: 0.90 for td in target_dates}  # claimed ~0.90 every day, realized 0.667
    _patch_per_day_claims(monkeypatch, claims)

    lcb = _typed_lcb("cond0", yes_q=0.10, no_q=0.90)
    adapter._maybe_apply_settlement_coverage_to_lcb(
        family=_family("San Francisco", "high", bin_f),
        forecast_conn=conn, lcb_by_direction=lcb,
    )
    no_entry = lcb[("cond0", "buy_no")]
    assert no_entry.q_lcb == pytest.approx(20.0 / 30.0 - 0.01, abs=1e-9)
    assert no_entry.calibration_source == "SETTLEMENT_ISOTONIC"
    assert no_entry.n_settlement_observations == 30


def test_insufficient_data_keeps_lcb(tmp_path, monkeypatch):
    """< min_n=30 settled CLAIM days: INSUFFICIENT_DATA -> q_lcb unchanged."""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn, target_dates = _settlement_db(
        tmp_path, "San Francisco", "high", "F",
        in_bin_value=64.0, out_bin_value=70.0, n_in=4, n_out=8,  # n=12 < 30
    )
    _patch_per_day_claims(monkeypatch, {td: 0.90 for td in target_dates})
    lcb = _typed_lcb("cond0", yes_q=0.10, no_q=0.90)

    adapter._maybe_apply_settlement_coverage_to_lcb(
        family=_family("San Francisco", "high", bin_f),
        forecast_conn=conn, lcb_by_direction=lcb,
    )
    assert lcb[("cond0", "buy_no")].q_lcb == pytest.approx(0.90)  # unchanged
    assert lcb[("cond0", "buy_no")].calibration_source == "FORECAST_BOOTSTRAP"


def test_calibrated_record_does_not_shrink(tmp_path, monkeypatch):
    """REBUILD RED-on-revert: a CALIBRATED per-day record (claimed ~= realized over 30
    settled days) is LICENSED -> q_lcb UNCHANGED. The OLD climatology loader stamped one
    constant claim and graded a fixed bin -> shrank this calibrated case to the bin base
    rate. Here the buy_no claimed ~0.667 each day and realized 20/30=0.667 -> calibrated."""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    conn, target_dates = _settlement_db(
        tmp_path, "San Francisco", "high", "F",
        in_bin_value=64.0, out_bin_value=70.0, n_in=10, n_out=20,
    )
    # Claimed ~= realized (0.667): a calibrated band. Live claim also 0.667.
    _patch_per_day_claims(monkeypatch, {td: 2.0 / 3.0 for td in target_dates})
    lcb = _typed_lcb("cond0", yes_q=0.10, no_q=2.0 / 3.0)

    adapter._maybe_apply_settlement_coverage_to_lcb(
        family=_family("San Francisco", "high", bin_f),
        forecast_conn=conn, lcb_by_direction=lcb,
    )
    no_entry = lcb[("cond0", "buy_no")]
    # Calibrated -> LICENSED -> not shrunk (the suppression the rebuild removes).
    assert no_entry.q_lcb == pytest.approx(2.0 / 3.0, abs=1e-9)
    assert no_entry.calibration_source == "FORECAST_BOOTSTRAP"  # unchanged source


def test_deep_otm_bin_forecast_bootstrap_write_does_not_collapse_family():
    """K3 family-formation regression (adversarial-verify finding #1, CRITICAL).

    The FORECAST_BOOTSTRAP restore at adapter:3402 writes
    ``float(hyp.ci_lower) + cost`` into the typed lcb_by_direction. For a deep-OTM
    bin (p_posterior~0) the edge CI lower bound is negative, so the restored q_lcb
    is NEGATIVE. Pre-fix, QlcbProvenance raised ValueError on the out-of-range value;
    that propagated to the family catch (adapter:732) -> LIVE_INFERENCE_INPUTS_MISSING
    and collapsed the WHOLE family (the unconditional type caused the failure).
    Legacy (origin/main, plain dict) tolerated it: the bin lost
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
