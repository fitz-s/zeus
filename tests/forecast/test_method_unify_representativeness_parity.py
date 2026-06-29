# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: Option C raw-precision representativeness center warming
#   (consult REQ-20260621-033315; forecast-gap-is-data-precision). METHOD-UNIFY
#   coherence WITH repr: the spine ENTRY (walk_forward_model_weights via
#   RawModelMember.representativeness_m2_native) and the materializer EXIT
#   (raw_precision_center repr_m2_by_model) must produce IDENTICAL weights AND
#   IDENTICAL center for the same inputs + repr — incl. an outlier fixture (the
#   #135 two-center split must not reopen through the repr channel).
"""RED-on-revert — ENTRY/EXIT parity of the representativeness center.

The spine ENTRY consumes repr via ``RawModelMember.representativeness_m2_native``
inside ``walk_forward_model_weights``; the materializer EXIT consumes it via
``raw_precision_center(..., repr_m2_by_model=...)``. Both use the SAME Form-A
denominator (max(residual_floored, floor) + repr). These tests assert byte-equal
weights and byte-equal center, so threading repr at one seam only (re-opening #135)
turns them RED.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from src.forecast.center import (
    MIN_SETTLED_N,
    raw_precision_center,
    raw_second_moment_weights,
    walk_forward_model_weights,
)
from src.forecast.types import RawModelMember


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _case(unit: str = "C") -> SimpleNamespace:
    return SimpleNamespace(unit=unit)


def _member(raw_m2: float | None, n: int, value: float, repr_m2: float = 0.0) -> RawModelMember:
    return RawModelMember(
        model_id="m",
        product_id="p",
        source_run_id="r",
        source_cycle_time_utc=_NOW,
        available_at_utc=_NOW,
        value_native=value,
        station_mapping_id="s",
        raw_forecast_artifact_id="a",
        data_version="v1",
        walk_forward_raw_m2_native=raw_m2,
        walk_forward_n=n,
        representativeness_m2_native=repr_m2,
    )


class TestEntryExitWeightParity:
    def test_full_history_with_repr_weights_match(self):
        raw_m2s = [0.5, 1.0, 4.0]
        ns = [40, 40, 40]
        zs = [28.0, 30.0, 31.0]
        reprs = [4.0, 0.0, 1.0]
        members = [_member(m2, n, z, r) for m2, n, z, r in zip(raw_m2s, ns, zs, reprs)]
        case = _case("C")

        spine_w = walk_forward_model_weights(case, members)

        raw_m2_and_n = {f"m{i}": (m2, n) for i, (m2, n) in enumerate(zip(raw_m2s, ns))}
        repr_by = {f"m{i}": r for i, r in enumerate(reprs)}
        helper_w = raw_second_moment_weights(raw_m2_and_n, unit="C", repr_m2_by_model=repr_by)

        for i in range(3):
            assert abs(spine_w[i] - helper_w[f"m{i}"]) < 1e-12, (
                f"weight mismatch {i}: spine={spine_w[i]} helper={helper_w[f'm{i}']}"
            )

    def test_entry_exit_center_match(self):
        raw_m2s = [0.5, 1.0, 4.0]
        ns = [40, 40, 40]
        zs = [28.0, 30.0, 31.0]
        reprs = [4.0, 0.0, 1.0]
        members = [_member(m2, n, z, r) for m2, n, z, r in zip(raw_m2s, ns, zs, reprs)]
        case = _case("C")

        spine_w = walk_forward_model_weights(case, members)
        spine_mu = float(np.sum(spine_w * np.asarray(zs)))

        raw_m2_and_n = {f"m{i}": (m2, n) for i, (m2, n) in enumerate(zip(raw_m2s, ns))}
        repr_by = {f"m{i}": r for i, r in enumerate(reprs)}
        z_by = {f"m{i}": z for i, z in enumerate(zs)}
        _, exit_mu = raw_precision_center(
            raw_m2_and_n, z_by, unit="C", repr_m2_by_model=repr_by
        )
        assert abs(spine_mu - exit_mu) < 1e-12, f"center mismatch: spine={spine_mu} exit={exit_mu}"

    def test_thin_n_with_repr_parity(self):
        """Low-n EB shrink + repr: ENTRY == EXIT (repr added AFTER shrink at both)."""
        n_thin = max(1, MIN_SETTLED_N - 1)
        raw_m2s = [0.3, 2.0]
        ns = [n_thin, 40]
        zs = [28.0, 31.0]
        reprs = [5.0, 0.0]
        members = [_member(m2, n, z, r) for m2, n, z, r in zip(raw_m2s, ns, zs, reprs)]
        case = _case("C")
        spine_w = walk_forward_model_weights(case, members)
        raw_m2_and_n = {f"m{i}": (m2, n) for i, (m2, n) in enumerate(zip(raw_m2s, ns))}
        repr_by = {f"m{i}": r for i, r in enumerate(reprs)}
        helper_w = raw_second_moment_weights(raw_m2_and_n, unit="C", repr_m2_by_model=repr_by)
        for i in range(2):
            assert abs(spine_w[i] - helper_w[f"m{i}"]) < 1e-12

    def test_cold_start_repr_parity(self):
        """No raw m2, positive repr: ENTRY == EXIT (both break equal weights)."""
        members = [_member(None, 0, 28.0, 4.0), _member(None, 0, 31.0, 0.0)]
        case = _case("C")
        spine_w = walk_forward_model_weights(case, members)
        helper_w = raw_second_moment_weights(
            {"m0": (None, 0), "m1": (None, 0)},
            unit="C",
            repr_m2_by_model={"m0": 4.0, "m1": 0.0},
        )
        assert abs(spine_w[0] - helper_w["m0"]) < 1e-12
        assert abs(spine_w[1] - helper_w["m1"]) < 1e-12
        # Both must have broken the equal tie.
        assert abs(spine_w[0] - 0.5) > 1e-6


class TestOutlierCenterFunctionalParity:
    """Same weights AND same arithmetic center even with an outlier member.

    The materializer uses Σ w·z; the spine center authority (build_center) uses
    weighted_huber_location. With identical Σwz weights AND an outlier z, Huber would
    DIVERGE from Σwz — so the SERVED replacement path must use the arithmetic
    raw_precision_center (Σwz), which this test pins. Reverting the EXIT center to a
    robust/Huber functional turns this RED.
    """

    def test_outlier_arithmetic_center_parity(self):
        # A tight cluster + one extreme outlier (z=45) that a Huber location down-weights
        # into its linear tail but Σwz keeps at full weight.
        raw_m2_and_n = {
            "a": (1.0, 40), "b": (1.0, 40), "c": (1.0, 40), "d": (1.0, 40),
            "outlier": (1.0, 40),
        }
        repr_by = {"a": 2.0}  # one member penalized; outlier carries no repr
        z_by = {"a": 28.0, "b": 28.2, "c": 28.1, "d": 27.9, "outlier": 45.0}

        w, mu_arith = raw_precision_center(raw_m2_and_n, z_by, unit="C", repr_m2_by_model=repr_by)
        # Independent Σwz recomputation must equal the helper's mu.
        mu_recomputed = sum(w[m] * z_by[m] for m in z_by)
        assert abs(mu_arith - mu_recomputed) < 1e-12

        # And a Huber location with the SAME weights would NOT equal Σwz (proving the
        # served path must be arithmetic — this is the center-functional guard).
        from src.forecast.center import weighted_huber_location

        order = list(z_by.keys())
        mu_huber = weighted_huber_location([z_by[m] for m in order], [w[m] for m in order])
        assert abs(mu_huber - mu_arith) > 0.5, (
            "outlier fixture must make Huber diverge from Σwz; if not, the test does not "
            "guard the center-functional choice"
        )
