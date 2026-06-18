# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md §4
#   (Unify consumed posteriors on the RAW belief: remove _eb_corrected; instruments enter z=x).
"""RED-on-revert test for the EXIT/MONITOR unify onto RAW (forecast_posteriors is RAW).

The capture path that writes forecast_posteriors (the EXIT belief via position_belief, the
MONITOR belief via monitor_refresh) must emit RAW instruments — ``z = x`` (the raw model value),
NOT the EB-corrected ``z = x − b̂``. This is the change that makes the materialized posterior
center the RAW diagonal center, so entry (spine, already RAW), exit, and monitor read ONE RAW
belief.

The test supplies a walk-forward history with a CLEAR non-zero residual (every model runs +3.0°C
hot vs settlement). The OLD ``_eb_corrected`` would have shifted each instrument's z DOWN by ~the
EB bias (toward settlement); the NEW ``_raw_instrument`` leaves z at the raw value. Asserting
``z == raw value`` (NOT raw − bias) is RED if the EB shift is reinstated.

It also asserts the EB shift primitive ``eb_bias`` is no longer imported into the capture module
(the structural antibody that the consumed-center path can never re-acquire a de-bias).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import src.data.bayes_precision_fusion_capture as capture_mod
from src.data.bayes_precision_fusion_capture import (
    ModelHistory,
    capture_bayes_precision_instruments,
)

TOKYO = (35.68, 139.69)
RUN = datetime(2026, 6, 8, 0, 0, tzinfo=timezone.utc)
TARGET = date(2026, 6, 9)

RAW_GLOBAL_VALUE_C = 13.0   # the raw model forecast today
SETTLEMENT_C = 10.0         # every historical settlement ran 3°C COLDER than the forecast
HOT_BIAS_C = RAW_GLOBAL_VALUE_C - SETTLEMENT_C  # +3.0°C systematic warm bias


def _hot_biased_history(model: str, n: int) -> ModelHistory:
    """n walk-forward rows where forecast − settlement == +3.0 (a clear warm bias).

    The OLD EB path would shrink today's z DOWN toward settlement by ~the EB bias; the RAW path
    leaves z at the raw forecast value.
    """
    dates = tuple(f"2026-05-{d:02d}" for d in range(1, n + 1))
    return ModelHistory(
        model=model,
        forecast_values=(RAW_GLOBAL_VALUE_C,) * n,
        settlement_values=(SETTLEMENT_C,) * n,
        target_dates=dates,
    )


def _live_fetch_all(*, model: str, **_kw) -> float | None:
    return RAW_GLOBAL_VALUE_C


def test_instruments_enter_raw_not_eb_corrected():
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless"]
    # Deep history (n >= MIN_TRAIN) so the OLD EB bias would be ~the full +3.0 warm bias (lam ~ 1).
    provider = lambda **_kw: {m: _hot_biased_history(m, 40) for m in models}  # noqa: E731
    cap = capture_bayes_precision_instruments(
        city="Tokyo", metric="high", latitude=TOKYO[0], longitude=TOKYO[1],
        timezone_name="Asia/Tokyo", run=RUN, target_local_date=TARGET, lead_days=1,
        anchor_z_corrected=RAW_GLOBAL_VALUE_C,
        history_provider=provider, live_fetch=_live_fetch_all,
    )
    assert cap.has_extras, "the test needs surviving likelihood instruments"

    # EVERY likelihood instrument's z is the RAW value — NOT raw − bias (the EB shift) —
    # regardless of whether it carried walk-forward history.
    for ins in cap.likelihood:
        assert ins.z == pytest.approx(RAW_GLOBAL_VALUE_C, abs=1e-9), (
            f"instrument {ins.model} z={ins.z} != raw {RAW_GLOBAL_VALUE_C}; the EB de-bias shift "
            f"was reinstated on the consumed (forecast_posteriors) center path"
        )
    # For instruments that DID carry history (the hot-biased models above), the walk-forward
    # residual history is RETAINED for width/provenance (not discarded by the RAW change).
    hot_models = {"ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless"}
    with_history = [ins for ins in cap.likelihood if ins.model in hot_models]
    assert with_history, "expected at least one likelihood instrument with walk-forward history"
    for ins in with_history:
        assert ins.n_train == 40, f"{ins.model} lost its retained walk-forward count"
        assert len(ins.train_residuals) == 40

    # The anchor center passed through unchanged (RAW): the materializer hands the RAW anchor.
    assert cap.anchor_z == pytest.approx(RAW_GLOBAL_VALUE_C, abs=1e-9)

    # Discriminating control: the EB-corrected value (raw − bias) would have been SETTLEMENT_C.
    # Assert NO instrument landed at the EB-corrected center (proves it is RAW, not EB).
    for ins in cap.likelihood:
        assert ins.z != pytest.approx(SETTLEMENT_C, abs=0.5), (
            "an instrument z landed at the EB-corrected (raw − bias) center — RAW unify reverted"
        )


def test_eb_bias_primitive_not_imported_into_capture():
    # Structural antibody: the EB shift primitive must not be reachable from the consumed-center
    # path. ``eb_bias`` is no longer imported into the capture module namespace.
    assert not hasattr(capture_mod, "eb_bias"), (
        "eb_bias is imported into bayes_precision_fusion_capture — the consumed center can "
        "re-acquire a forbidden EB de-bias"
    )
    # The RAW instrument builder exists; the EB builder is gone.
    assert hasattr(capture_mod, "_raw_instrument")
    assert not hasattr(capture_mod, "_eb_corrected")
