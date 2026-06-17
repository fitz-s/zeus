# Lifecycle: created=2026-06-08; last_reviewed=2026-06-17; last_reused=2026-06-17
# Purpose: F4 regional polygon gate — icon_d2 in-polygon ENTERS; out-of-polygon cities ABSENT;
#   arome France-only; lead>1 excludes regional; icon_seamless NEVER in candidate set.
# Reuse: Run with pytest; update if domain polygons or regional eligibility logic in model_selection change.
# Created: 2026-06-08
# Last reused or audited: 2026-06-17
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §4 selection, §7 antibodies
#   (regional-outside-domain polygon). BAYES_PRECISION_FUSION_PROOF_RESULT.md:
#   "icon_d2 used ONLY at in-box cities; Moscow 0/0; icon_seamless removed from candidate set 2026-06-17".
# Purpose: REGIONAL GATE proof. icon_d2 in-polygon ENTERS; Moscow out-of-polygon ABSENT
#   (zero-leak); arome only inside France; lead>1 excludes regional.
"""F4 regional polygon gate tests."""

from __future__ import annotations

from src.forecast.model_selection import (
    is_alias,
    load_domain_polygons,
    regional_eligible,
    select_models,
)

# Proof city settlement coordinates (lat, lon) from bayes_precision_fusion_fixed_lead_dataset.json.
PARIS = (48.967, 2.428)
LONDON = (51.505, 0.055)
MUNICH = (48.348, 11.813)
MOSCOW = (55.592, 37.261)      # out of icon_d2 Central-EU polygon; INSIDE the ICON-EU domain
MADRID = (40.466, -3.555)      # out of icon_d2 Central-EU; INSIDE ICON-EU
ISTANBUL = (41.262, 28.74)     # out of icon_d2 Central-EU; INSIDE ICON-EU
HELSINKI = (60.327, 24.957)    # out of icon_d2 Central-EU; INSIDE ICON-EU
TOKYO = (35.68, 139.69)        # OUTSIDE the ICON-EU domain entirely (lon 139.69E >> 45E)


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
        "jma_seamless": 3.8, "ukmo_global_deterministic_10km": 4.5, "icon_eu": 4.1, "icon_d2": 4.2,
        "meteofrance_arome_france_hd": 4.3,
    }
    sel = select_models(present_models=present, lat=PARIS[0], lon=PARIS[1], lead_days=1)
    assert sel.anchor_present is True
    # BLOCKER 9 / spec §4(2): one representative per provider family. icon_d2 is the in-domain
    # DWD-ICON rep here, so icon_global AND icon_eu are BOTH suppressed from the globals — the
    # DWD/ICON family contributes exactly one instrument (icon_d2 as the regional expert).
    # 2026-06-17 COARSE-GLOBAL REMOVAL + JMA DROP: gfs_global/gem_global/jma_seamless are present
    # as STRAY values but are no longer in the selection vocabulary -> they NEVER enter the fusion.
    # Paris (EU; no NCEP/CMC nest in-domain) keeps only ukmo_global as a likelihood global.
    assert sel.likelihood_globals == ("ukmo_global_deterministic_10km",)
    assert not ({"gfs_global", "gem_global", "jma_seamless"} & set(sel.used_models))
    assert set(sel.regional_experts) == {"icon_d2", "meteofrance_arome_france_hd"}
    assert sel.excluded_regionals == ()
    assert set(sel.dropped_provider_dups) == {"icon_global", "icon_eu"}


def test_dwd_provider_uses_one_representative_by_default() -> None:
    # OUTSIDE the ICON-EU domain entirely (Tokyo): neither icon_d2 nor icon_eu can enter. Of the
    # DWD-ICON family, exactly ONE survives as the rep. With no in-EU evidence the conservative
    # default rep is the global-scope icon_global; icon_eu is suppressed so the DWD/ICON family is
    # never double-counted in one fusion.
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1,
    }
    sel = select_models(present_models=present, lat=TOKYO[0], lon=TOKYO[1], lead_days=1)
    icon_family = [m for m in sel.used_models if m in {"icon_global", "icon_eu", "icon_d2"}]
    assert icon_family == ["icon_global"]          # exactly one DWD-ICON representative
    assert "icon_eu" not in sel.used_models
    assert "icon_eu" in sel.dropped_provider_dups
    assert "icon_d2" not in sel.used_models        # out of polygon -> regional absent


def test_icon_eu_is_the_dwd_rep_inside_its_own_icon_eu_domain() -> None:
    # 2026-06-09 FIX (regression antibody): for EU-edge cities inside the ICON-EU 7km nest but
    # OUTSIDE the icon_d2 Central-EU box (Moscow/Madrid/Istanbul/Helsinki), icon_eu — the more
    # skilful 7km regional (Exp O: -0.22 MAE vs ECMWF-9km) — MUST be the single DWD-ICON rep,
    # NOT icon_global (13km). Previously icon_eu borrowed the tightened icon_d2 box and was wrongly
    # dropped as a provider_dup of icon_global for all 7 EU-edge cities.
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1,
    }
    for lat, lon in (MOSCOW, MADRID, ISTANBUL, HELSINKI):
        sel = select_models(present_models=present, lat=lat, lon=lon, lead_days=1)
        icon_family = [m for m in sel.used_models if m in {"icon_global", "icon_eu", "icon_d2"}]
        assert icon_family == ["icon_eu"], (lat, lon, sel.used_models)
        assert "icon_global" in sel.dropped_provider_dups   # 13km global yields to the 7km nest
        assert "icon_d2" not in sel.used_models             # 2km nest absent out of Central-EU
    # And at lead beyond the ICON-EU horizon (>3) icon_eu falls back to the global rep.
    sel_far = select_models(present_models=present, lat=MOSCOW[0], lon=MOSCOW[1], lead_days=5)
    assert [m for m in sel_far.used_models if m in {"icon_global", "icon_eu"}] == ["icon_global"]


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
    # Both DWD globals present, NO icon_d2, OUTSIDE the ICON-EU domain entirely (Tokyo). icon_eu
    # must NOT join icon_global in the same fusion: with no in-EU domain evidence the family
    # collapses to the single global-scope rep.
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8, "icon_eu": 4.1,
    }
    sel = select_models(present_models=present, lat=TOKYO[0], lon=TOKYO[1], lead_days=1)
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


def test_icon_seamless_never_in_candidate_set() -> None:
    # RED-ON-REVERT (2026-06-17 icon_seamless removal): icon_seamless must NEVER appear in
    # used_models, regional_experts, or likelihood_globals regardless of what is passed in
    # present_models. It is not a member of GLOBAL_LIKELIHOOD_MODELS or REGIONAL_MODELS.
    # If someone re-adds it, this test goes RED.
    from src.data.bayes_precision_fusion_download import BAYES_PRECISION_FUSION_EXTRA_MODELS
    from src.forecast.model_selection import GLOBAL_LIKELIHOOD_MODELS, REGIONAL_MODELS

    assert "icon_seamless" not in GLOBAL_LIKELIHOOD_MODELS, (
        "icon_seamless was removed from GLOBAL_LIKELIHOOD_MODELS (2026-06-17 alias-dedup removal)"
    )
    assert "icon_seamless" not in REGIONAL_MODELS, (
        "icon_seamless was never in REGIONAL_MODELS and must not be re-added"
    )
    assert "icon_seamless" not in BAYES_PRECISION_FUSION_EXTRA_MODELS, (
        "icon_seamless was removed from BAYES_PRECISION_FUSION_EXTRA_MODELS (2026-06-17)"
    )
    # Even if a stray value appears in present_models, select_models must never emit it.
    present = {
        "ecmwf_ifs": 4.7, "icon_eu": 4.1, "icon_global": 3.7,
        "icon_d2": 4.2, "icon_seamless": 4.2,  # stray — must be ignored
    }
    sel = select_models(
        present_models=present, lat=PARIS[0], lon=PARIS[1], lead_days=1,
    )
    assert "icon_seamless" not in sel.used_models
    assert "icon_seamless" not in sel.likelihood_globals
    assert "icon_seamless" not in sel.regional_experts
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
