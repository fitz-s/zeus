# Created: 2026-09-30
# Last reused/audited: 2026-09-30
# Authority basis: review F2 of the Day0/source-clock rebuild (one source-set law).
"""The capture's model selection uses the same scheme as the representative collapse.

Chicago LOW weights icon_global + ncep_nbm_conus.  gfs_hrrr is the more specific
in-domain NCEP member, so specificity alone would replace the weighted NBM in the
fusion likelihood.  With the scheme passed through, NBM is the family's single
representative and HRRR is recorded as a provider duplicate.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.data.bayes_precision_fusion_capture import capture_bayes_precision_instruments

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
