# Created: 2026-09-30
# Last reused/audited: 2026-10-02
# Authority basis: review F2 of the Day0/source-clock rebuild (one source-set law).
"""The capture's model selection uses the same scheme as the representative collapse.

Chicago LOW weights icon_global + ncep_nbm_conus.  gfs_hrrr is the more specific
in-domain NCEP member, so specificity alone would replace the weighted NBM in the
fusion likelihood.  With the scheme passed through, NBM is the family's single
representative and HRRR is recorded as a provider duplicate.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.data.bayes_precision_fusion_capture import capture_bayes_precision_instruments, select_current_extra_models

_CHICAGO = (41.9786, -87.9048)
_VALUES = {
    "icon_global": 17.0,
    "ncep_nbm_conus": 17.3,
    "gfs_hrrr": 16.8,
}


def _capture(configured: tuple[str, ...]):
    return capture_bayes_precision_instruments(
        city="Chicago",
        metric="low",
        latitude=_CHICAGO[0],
        longitude=_CHICAGO[1],
        timezone_name="America/Chicago",
        run=datetime(2026, 9, 30, 6, tzinfo=timezone.utc),
        target_local_date=date(2026, 10, 1),
        lead_days=1,
        forecast_hours=48,
        anchor_z_corrected=17.1,
        live_fetch=lambda *, model, **_: _VALUES.get(model),
        configured=configured,
    )


def test_capture_family_rep_is_the_scheme_member() -> None:
    result = _capture(("icon_global", "ncep_nbm_conus"))
    used = {instrument.model for instrument in result.likelihood}
    assert "ncep_nbm_conus" in used
    assert "gfs_hrrr" not in used


def test_capture_without_scheme_keeps_specificity_order() -> None:
    result = _capture(())
    used = {instrument.model for instrument in result.likelihood}
    assert "gfs_hrrr" in used
    assert "ncep_nbm_conus" not in used


def test_shared_capture_selector_preserves_complete_positive_selection():
    from src.forecast.model_selection import select_models

    for configured in ((), ("icon_global", "ncep_nbm_conus")):
        expected = select_models(present_models=_VALUES, lat=_CHICAGO[0],
            lon=_CHICAGO[1], lead_days=1, configured=configured)
        capture = _capture(configured)
        assert capture.selection == expected
        assert [(item.model, item.z, item.is_regional) for item in capture.likelihood] == [
            (model, _VALUES[model], regional)
            for models, regional in ((expected.likelihood_globals, False), (expected.regional_experts, True))
            for model in models
        ]
        assert capture.anchor_z == 17.1 and capture.anchor_tau0 is None


def test_shared_capture_selector_uses_real_candidates_and_arrival_guard():
    at = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
    values = {"ecmwf_ifs": 20., "icon_global": 21., "gfs_hrrr": float("nan"),
              "dmi_harmonie_europe": 22., "ukmo_global_deterministic_10km": 23.}
    _, _, selection = select_current_extra_models(values=values,
        latitude=51.4775, longitude=-.4614, lead_days=2, decision_utc=at,
        model_available_at={"icon_global": "2026-10-02T09:00:00+00:00"},
        configured=("dmi_harmonie_europe",))
    assert not selection.likelihood_globals and not selection.regional_experts
    # Anchor, non-candidate configured DMI, non-finite HRRR, future ICON and
    # finite UKMO without possession do not become extra evidence.
