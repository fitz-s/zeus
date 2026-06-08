# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §3 (DEDUP icon_seamless==icon_d2), §4 selection,
#   §7 antibodies (regional-outside-domain polygon; alias dedup). U0R_PROOF_RESULT.md:
#   "icon_d2 used ONLY at in-box cities; Moscow 0/0; icon_seamless in 0/27684 used_models".
# Purpose: REGIONAL GATE proof. icon_d2 in-polygon ENTERS; Moscow out-of-polygon ABSENT
#   (zero-leak); arome only inside France; dedup drops icon_seamless; lead>1 excludes regional.
"""F4 regional polygon gate + alias dedup tests."""

from __future__ import annotations

from src.forecast.model_selection import (
    is_alias,
    load_domain_polygons,
    regional_eligible,
    select_models,
)

# Proof city settlement coordinates (lat, lon) from u0r_fixed_lead_dataset.json.
PARIS = (48.967, 2.428)
LONDON = (51.505, 0.055)
MUNICH = (48.348, 11.813)
MOSCOW = (55.592, 37.261)      # out of Central-EU polygon (37.26E > 20.34E hull)
MADRID = (40.466, -3.555)      # out of TIGHTENED polygon (40.47N < 43.18N, -3.56E)
ISTANBUL = (41.262, 28.74)     # out (28.74E > 20.34E)
HELSINKI = (60.327, 24.957)    # out (60.33N > 58.08N, 24.96E > 20.34E)


def test_icon_d2_eligible_inside_central_eu_polygon_at_lead_1() -> None:
    for lat, lon in (PARIS, LONDON, MUNICH):
        assert regional_eligible("icon_d2", lat=lat, lon=lon, lead_days=1) is True


def test_icon_d2_absent_outside_polygon_moscow_zero_leak() -> None:
    # Moscow: the canonical out-of-domain city. icon_d2 must be ABSENT (proven D1-D0 == 0.0).
    assert regional_eligible("icon_d2", lat=MOSCOW[0], lon=MOSCOW[1], lead_days=1) is False


def test_icon_d2_absent_for_loose_box_cities_after_tightening() -> None:
    # These were flagged in_box by the LOOSE proof box but have no ICON-D2 data; the tightened
    # polygon makes the domain gate agree with the data-presence gate (open question #3).
    for lat, lon in (MADRID, ISTANBUL, HELSINKI):
        assert regional_eligible("icon_d2", lat=lat, lon=lon, lead_days=1) is False


def test_icon_d2_excluded_beyond_lead_horizon() -> None:
    # Regional expert is lead<=1 only; leads 2/3 are physically absent / out of horizon.
    assert regional_eligible("icon_d2", lat=PARIS[0], lon=PARIS[1], lead_days=1) is True
    assert regional_eligible("icon_d2", lat=PARIS[0], lon=PARIS[1], lead_days=2) is False
    assert regional_eligible("icon_d2", lat=PARIS[0], lon=PARIS[1], lead_days=3) is False


def test_arome_eligible_only_in_france() -> None:
    assert regional_eligible("meteofrance_arome_france_hd", lat=PARIS[0], lon=PARIS[1], lead_days=1) is True
    # London / Munich / Moscow are NOT in the France polygon.
    for lat, lon in (LONDON, MUNICH, MOSCOW):
        assert regional_eligible("meteofrance_arome_france_hd", lat=lat, lon=lon, lead_days=1) is False


def test_select_models_paris_in_domain_enters_both_regionals() -> None:
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1, "icon_d2": 4.2,
        "meteofrance_arome_france_hd": 4.3,
    }
    sel = select_models(present_models=present, lat=PARIS[0], lon=PARIS[1], lead_days=1)
    assert sel.anchor_present is True
    # BLOCKER 9 / spec §4(2): one representative per provider family. icon_d2 is the in-domain
    # DWD-ICON rep here, so icon_global AND icon_eu are BOTH suppressed from the globals — the
    # DWD/ICON family contributes exactly one instrument (icon_d2 as the regional expert).
    assert sel.likelihood_globals == ("gfs_global", "gem_global", "jma_seamless")
    assert set(sel.regional_experts) == {"icon_d2", "meteofrance_arome_france_hd"}
    assert sel.excluded_regionals == ()
    assert set(sel.dropped_provider_dups) == {"icon_global", "icon_eu"}


def test_dwd_provider_uses_one_representative_by_default() -> None:
    # OUT of every regional polygon (Moscow): icon_d2 cannot enter. Of the two remaining
    # DWD-ICON globals (icon_global, icon_eu), exactly ONE survives as the family rep. Without
    # explicit in-EU evidence the conservative default rep is the global-scope icon_global;
    # icon_eu is suppressed so the DWD/ICON family is never double-counted in one fusion.
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1,
    }
    sel = select_models(present_models=present, lat=MOSCOW[0], lon=MOSCOW[1], lead_days=1)
    icon_family = [m for m in sel.used_models if m in {"icon_global", "icon_eu", "icon_d2"}]
    assert icon_family == ["icon_global"]          # exactly one DWD-ICON representative
    assert "icon_eu" not in sel.used_models
    assert "icon_eu" in sel.dropped_provider_dups
    assert "icon_d2" not in sel.used_models        # out of polygon -> regional absent


def test_icon_d2_replaces_icon_global_inside_domain() -> None:
    # Inside the Central-EU polygon (Paris), icon_d2 is the highest-resolution DWD-ICON expert
    # and becomes the family representative. It REPLACES icon_global (and icon_eu): neither
    # global ICON instrument enters used_models, so the family is represented once, by icon_d2.
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1, "icon_d2": 4.2,
    }
    sel = select_models(present_models=present, lat=PARIS[0], lon=PARIS[1], lead_days=1)
    icon_family = [m for m in sel.used_models if m in {"icon_global", "icon_eu", "icon_d2"}]
    assert icon_family == ["icon_d2"]              # icon_d2 is the sole DWD-ICON rep in-domain
    assert "icon_global" not in sel.used_models
    assert "icon_eu" not in sel.used_models
    assert {"icon_global", "icon_eu"} <= set(sel.dropped_provider_dups)


def test_icon_eu_not_selected_with_icon_global_without_explicit_evidence() -> None:
    # Both DWD globals present, NO icon_d2, OUT of every regional polygon (Moscow). icon_eu must
    # NOT join icon_global in the same fusion: without explicit in-EU evidence (an eligible
    # regional / in-domain city) the family collapses to the single global-scope rep.
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1,
    }
    sel = select_models(present_models=present, lat=MOSCOW[0], lon=MOSCOW[1], lead_days=1)
    assert "icon_global" in sel.likelihood_globals
    assert "icon_eu" not in sel.likelihood_globals
    assert "icon_eu" not in sel.used_models
    # The two ICON globals never coexist in the selected set.
    assert not ({"icon_global", "icon_eu"} <= set(sel.used_models))


def test_select_models_moscow_out_of_domain_no_regional() -> None:
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1, "icon_d2": 4.2,
    }
    sel = select_models(present_models=present, lat=MOSCOW[0], lon=MOSCOW[1], lead_days=1)
    assert sel.regional_experts == ()           # zero-leak
    assert "icon_d2" in sel.excluded_regionals
    assert "icon_d2" not in sel.used_models


def test_select_models_dedups_icon_seamless_against_icon_d2() -> None:
    # icon_seamless bit-identical to icon_d2 -> dropped; never enters used_models.
    d2 = [4.2, 5.1, 3.3, 6.0, 2.8, 4.4, 5.5]
    seam = list(d2)  # bit-identical
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_eu": 4.1,
        "icon_d2": 4.2, "icon_seamless": 4.2,
    }
    sel = select_models(
        present_models=present, lat=PARIS[0], lon=PARIS[1], lead_days=1,
        alias_series={"icon_d2": d2, "icon_seamless": seam},
    )
    assert "icon_seamless" in sel.dropped_aliases
    assert "icon_seamless" not in sel.used_models
    assert "icon_d2" in sel.regional_experts


def test_is_alias_distinguishes_icon_eu_from_icon_d2() -> None:
    # icon_eu is a DISTINCT model (proof mean|delta| = 0.51 degC) -> NOT an alias.
    d2 = [4.2, 5.1, 3.3, 6.0, 2.8, 4.4, 5.5]
    eu = [v + 0.51 for v in d2]
    assert is_alias(d2, eu) is False
    assert is_alias(d2, list(d2)) is True


def test_polygons_load_from_config() -> None:
    polys = load_domain_polygons()
    assert "icon_d2" in polys
    assert "meteofrance_arome_france_hd" in polys
    assert polys["icon_d2"].max_lead_days == 1
