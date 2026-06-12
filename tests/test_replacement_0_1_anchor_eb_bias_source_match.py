from __future__ import annotations

# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: wiring-audit 2026-06-09 (iron rule 1 + Fitz #4 data-provenance).
#   Empirical wrong-set finding: the replacement_0_1 9km ECMWF-IFS ANCHOR center was being
#   "corrected" by a bias fit on the OpenData ENS (a DIFFERENT model), which over-corrects
#   the anchor by ~1.1C mean (RMS 1.45C), >=1C on 27/56 city-metric cells, wrong-sign on 11/56.
#
# RELATIONSHIP ANTIBODY (Fitz immune-system: make the error CATEGORY unconstructable).
#
# The broken cross-module relationship this test pins:
#
#   model_bias_ens (edli_per_city_v1)        applied-to        replacement_0_1 anchor
#   fit on:  OpenData ENS forecast  ──────────────────────▶    center = ecmwf_ifs 9km/0.1
#   (live_data_version =                  (resolve_replacement_   deterministic single-runs.
#    ecmwf_opendata_mx2t3 / mn2t3)         eb_bias_shift_c)       DIFFERENT SOURCE.
#
# The resolver keys a bias ONLY by city|season|month|metric|live_data_version (live_source_id
# is BLANK in the rows) — so it will silently apply a bias fit on ANY model to the anchor of
# ANOTHER model. A per-city settlement bias is NOT source-portable: the higher-resolution 9km
# IFS deterministic anchor carries a much smaller bias (~0.5C) than the coarse ENS (~1.5C);
# borrowing the ENS bias over-corrects the better forecast and even flips sign in ~20% of cities.
# (B8 independently found a +0.65C delta even between ifs025 and ifs9 — two ECMWF products.)
#
# INVARIANT: the replacement_0_1 ANCHOR EB-bias correction may be enabled ONLY with a bias
# fit on the anchor's OWN source (ecmwf_ifs). It must NEVER be pointed at an OpenData/ENS
# (mx2t3 / mn2t3) live_data_version. This test fails loudly the moment someone re-enables the
# anchor correction against a foreign-source bias (e.g. the naive "just restore the deleted
# live_data_version key" — which would restore the WRONG, over-correcting ENS bias).
#
# When the CORRECT fix is built (an ecmwf_ifs-9km-vs-settled per-city bias, served under an
# ifs-sourced live_data_version), enabling the flag with that key passes this test unchanged.

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The replacement_0_1 anchor model identity (the fusion-identity stored id of the 9km/0.1
# ECMWF IFS deterministic single-runs anchor). Any EB-bias used to correct THIS anchor must be
# fit on THIS source.
ANCHOR_SOURCE_MODEL = "ecmwf_ifs"

# Tokens that mark a live_data_version as an OpenData ENS product — a FOREIGN source for the
# ifs9 anchor (the ENS is a different, coarser model). mx2t3 / mn2t3 are the ENS 3-hourly
# extrema fields; "opendata"/"open_data"/"_ens" name the ENS family. An ecmwf_ifs-fit bias
# would carry none of these tokens, so the correct future enable is NOT blocked.
_FOREIGN_ENS_MARKERS = ("opendata", "open_data", "mx2t3", "mn2t3", "_ens", "ensemble")


def _edli_cfg() -> dict:
    settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    edli = settings.get("edli", {})
    assert isinstance(edli, dict), "edli section missing"
    return edli


def test_replacement_0_1_anchor_eb_bias_never_pointed_at_a_foreign_ens_source():
    """The replacement_0_1 ANCHOR (ecmwf_ifs 9km) EB-bias correction must never be enabled
    against a bias fit on the OpenData ENS (a different model). Source-matched only."""
    edli = _edli_cfg()
    enabled = bool(edli.get("replacement_0_1_eb_bias_correction_enabled", False))
    if not enabled:
        # Honest OFF: no anchor correction is claimed, so nothing foreign can be applied.
        return

    ldv = edli.get("replacement_0_1_eb_bias_live_data_version")
    assert isinstance(ldv, dict) and ldv, (
        "replacement_0_1_eb_bias_correction_enabled=True but replacement_0_1_eb_bias_live_data_"
        "version is absent/empty — enabled-but-dead (the 2026-06-09 silent-dead-leg category). "
        "Either supply a source-matched (ecmwf_ifs-fit) live_data_version or set the flag False."
    )
    for metric, data_version in ldv.items():
        token = str(data_version).lower()
        foreign = [m for m in _FOREIGN_ENS_MARKERS if m in token]
        assert not foreign, (
            f"replacement_0_1 ANCHOR EB-bias enabled but live_data_version[{metric!r}]="
            f"{data_version!r} is an OpenData ENS product (markers {foreign}) — a FOREIGN source "
            f"for the {ANCHOR_SOURCE_MODEL} 9km anchor. A per-city ENS bias over-corrects the "
            "higher-res IFS anchor (~1.1C mean, wrong-sign on ~20% of cities). Build/point an "
            f"{ANCHOR_SOURCE_MODEL}-fit anchor bias instead, or set the flag False."
        )


def test_resolver_has_no_source_identity_guard_so_config_must_enforce_match():
    """Documents WHY the config-level guard above is load-bearing: the resolver matches a bias
    row by city/season/month/metric/live_data_version ONLY — it does NOT verify the bias was
    fit on the correction target's source (model_bias_ens.live_source_id is blank in the live
    rows). Until the resolver enforces source identity, the committed-config guard is the sole
    barrier against applying a foreign-source bias to the anchor. If this assumption changes
    (the resolver gains a source-identity match), update this antibody accordingly."""
    src = (ROOT / "src" / "calibration" / "replacement_eb_bias.py").read_text(encoding="utf-8")
    # The resolver's read_bias_model call keys on these fields; none is a source/model identity.
    assert "error_model_family" in src
    # Guard against a false sense of safety: assert the resolver does not (yet) filter on a
    # live_source_id / model-identity column. If someone adds such a guard, this flips and the
    # test should be updated to assert the in-code match instead of relying on config.
    assert "live_source_id" not in src, (
        "replacement_eb_bias.py now references live_source_id — if it enforces source-identity "
        "matching, update this antibody to assert the in-resolver guard and relax the config-only "
        "barrier in the companion test."
    )
