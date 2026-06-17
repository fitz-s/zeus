# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §3 (source identities, DEDUP icon_seamless==icon_d2),
#   §4 algorithm steps (1) eligible / (2) provider reps / (3) alias dedup, §7 antibodies
#   ("regional-outside-domain (polygon)", "icon_seamless==icon_d2 (alias dedup)"); F4.
#   BAYES_PRECISION_FUSION_PROOF_RESULT.md: regional SHADOW-ONLY/DEFER, polygon tightened (open question #3).
"""F4 model selection — decorrelated provider reps, alias dedup, regional polygon gate.

THE STRUCTURAL ANTIBODIES (Fitz #1: K structural decisions, not N if-statements):

  1. DECORRELATED PROVIDER REPS — ONE representative per physical provider family
     (spec §4 step 2): ECMWF anchor + GFS(NOAA) + DWD-ICON + GEM(CMC) + JMA. Each is a
     structurally-decorrelated error source; the fusion's Sigma down-weights residual
     correlation, but we never feed two reps of the SAME provider as if independent.
     The DWD/ICON family in particular ships THREE instruments — icon_d2 (2km EU nest),
     icon_eu (7km nest), icon_global — that are the same provider's physics at different
     scopes. Exactly one enters a fusion, picked most-specific-eligible-first:
     icon_d2 in-domain ELSE icon_eu (if eligible) ELSE icon_global (spec §3: "use icon_d2
     in-EU, icon_global out"). The other ICON members are recorded in ``dropped_provider_dups``.
     This makes the DWD-family triple-count CATEGORY impossible (BLOCKER 9), not just the
     one Paris instance.

  2. ALIAS DEDUP — icon_seamless is BIT-IDENTICAL to icon_d2 inside the EU nest (spec §3 /
     proof _dedup_bit_identity). Feeding both double-counts one instrument. The dedup drops
     icon_seamless whenever it is an alias of icon_d2 (corr > 0.995 AND mean|delta| < eps).

  3. REGIONAL POLYGON GATE — icon_d2 enters ONLY inside the Central-EU polygon at lead <= 1;
     arome ONLY inside the France polygon. Out-of-polygon -> ABSENT (zero-leak). The gate is
     point-in-polygon over the city settlement coordinate, composed with the runtime
     data-presence gate (a regional that is in-polygon but failed to fetch is simply dropped
     fail-soft upstream). Moscow (out-of-polygon) -> icon_d2 ABSENT, proven D1-D0 == 0.0.

These make the out-of-domain-leak and double-count error CATEGORIES impossible, rather than
patching each city. The selection output is consumed by bayes_precision_fusion.fuse_bayes_precision_posterior.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLYGON_CONFIG_PATH = PROJECT_ROOT / "config" / "model_domain_polygons.yaml"

# Spec §3 source identities. The anchor is the prior; the rest are likelihood instruments.
# 2026-06-09 PROMOTION (operator-directed, settlement-graded evidence
# /tmp/uncovered_cities_regional_report.md — same evidence class as the icon_eu promotion):
#   ukmo_global_deterministic_10km — 5th decorrelated global (UK Met Office dynamical core,
#     distinct from NOAA/DWD/CMC/JMA physics). Pooled MAE 1.266 vs ecmwf_ifs 1.411 (-10.3%,
#     n=1099, 16 non-EU cities; strongest SE-Asia/tropics). The EB walk-forward de-bias
#     (backfilled previous-runs history) absorbs its South-Asia warm bias at member level.
#   ncep_nbm_conus — NCEP-family CONUS representative via the family single-rep contest
#     (NBM blends NCEP models incl. GFS -> it must REPLACE gfs_global in-domain, never ride
#     alongside it). Pooled MAE 1.193 vs ecmwf_ifs 1.395 (-14.4%, n=1029).
#   ukmo_uk_deterministic_2km — London regional expert (icon_d2 pattern; UKMO-family rep
#     in-domain so ukmo_global is suppressed there). MAE 0.919 vs 1.039, n=112.
ANCHOR_MODEL = "ecmwf_ifs"
UKMO_GLOBAL_MODEL = "ukmo_global_deterministic_10km"
NBM_MODEL = "ncep_nbm_conus"
UKMO_UK_MODEL = "ukmo_uk_deterministic_2km"
# 2026-06-17 PRECISION-INPUT FIX (operator directive: "the data we use is 9km level and the
# regional data is even more precise; your 25 and 15 and the not-precise cell is breaking the
# fusion calculation"; "no 25km model we don't use them and don't download"). Settlement-graded
# evidence (state/zeus-forecasts.db, recent settled WU highs, previous-runs raw bias — NO
# statistical correction): the coarse globals run ~1-2C cold vs settlement (gfs_global -0.86;
# gem_global similar coarse-cell cold; icon_global -1.08; ukmo_global -1.10) while the
# high-resolution station-resolving models are near-zero raw bias (gfs_hrrr 3km CONUS +0.004
# MAE 1.33; gem_hrdps 2.5km N-America +0.86; the in-EU icon_d2/arome/ukmo_uk already in the set,
# all ~0 bias).
#   gfs_hrrr — NOAA HRRR 3km CONUS deterministic. The NCEP-family rep in-CONUS.
#   gem_hrdps_continental — CMC HRDPS 2.5km North-America deterministic. The CMC-family rep
#     in N-America.
# 2026-06-17 COARSE-GLOBAL REMOVAL (this commit): gfs_global (0.25°/25km) and gem_global (~15km)
# are DROPPED from selection entirely — not merely suppressed in-CONUS. Removing them from
# DECORR_GLOBALS removes them from GLOBAL_LIKELIHOOD_MODELS and hence from
# bayes_precision_fusion_download.BAYES_PRECISION_FUSION_EXTRA_MODELS, so they are no longer
# fused AND no longer downloaded (forward single_runs + previous_runs both stop; the existing
# de-bias history rows age out). CONSEQUENCE: NCEP is repped ONLY by gfs_hrrr/ncep_nbm (CONUS)
# and CMC ONLY by gem_hrdps (N-America); OUTSIDE those nest domains NCEP/CMC have NO rep and are
# structurally absent — the domain-aware completeness contract
# (replacement_fusion_upgrade_trigger.expected_provider_families_for_city) does NOT expect them
# there, so a non-CONUS/non-NA city is COMPLETE on {DWD, JMA, UKMO} (+ ECMWF anchor) with no
# phantom upgrade loop. icon_global/ukmo_global remain the always-global reps.
# 2026-06-17 JMA DROP (operator, settlement-graded): jma_seamless is the COLDEST/least-precise
# declared global — recent lead-1 day-ahead settlement raw bias -1.46, MAE 2.124 (n=1002), worse
# than the just-dropped gfs_global (1.696) and far worse than the 9km anchor (1.309) and the
# regional nests (icon_eu 1.062). It is the ONLY JMA-family member, so dropping it removes the
# JMA decorrelated family entirely → the contract reduces to {NCEP, DWD, CMC, UKMO} + the ECMWF
# anchor. (Caveat on record: the historical residuals were measured at the pre-2026-06-17 coords,
# so part of jma's cold may be coarse-cell offshore-snap; the finer model-subset combinations are
# to be RE-TESTED on clean post-coord-fix settled data — operator-deferred.) This also thins
# decorrelation diversity (JMA was the only non-Western global); accepted pending that re-test.
GFS_HRRR_MODEL = "gfs_hrrr"
GEM_HRDPS_MODEL = "gem_hrdps_continental"
DECORR_GLOBALS = (
    "icon_global",
    UKMO_GLOBAL_MODEL,
)
ICON_EU_MODEL = "icon_eu"
GLOBAL_LIKELIHOOD_MODELS = DECORR_GLOBALS + (ICON_EU_MODEL, NBM_MODEL)
REGIONAL_MODELS = (
    "icon_d2",
    "meteofrance_arome_france_hd",
    UKMO_UK_MODEL,
    GFS_HRRR_MODEL,
    GEM_HRDPS_MODEL,
)

# Spec §4(2) provider representatives. Each PHYSICAL provider family contributes exactly ONE
# instrument to a fusion — the same single-rep doctrine that already governs "icon_d2 in-domain
# else icon_global". The DWD/ICON family ships three instruments at different scopes
# (icon_d2 2km nest, icon_eu 7km nest, icon_global) that are the SAME provider's physics, so
# feeding two as if independent triple-counts one error source and corrupts the fusion Sigma
# (BLOCKER 9). The family rep is chosen most-specific-eligible-first:
#   icon_d2 (if regional-eligible: in-domain + lead ok)  >  icon_eu (if present+eligible)  >  icon_global
# Whichever wins, the OTHER ICON-family members are suppressed as provider duplicates. The
# tuple is ordered highest-resolution-first; selection walks it and keeps the first eligible.
ICON_FAMILY = ("icon_d2", ICON_EU_MODEL, "icon_global")
# 2026-06-09: the SAME single-rep mechanism, two more instances (one mechanism, K<<N):
#   NCEP/NOAA family — gfs_hrrr 3km CONUS nest and ncep_nbm ~13km CONUS blend are the SAME NOAA
#   physics at different scopes; feeding two as independent double-counts one error source.
#   gfs_hrrr (3km, +0.004 raw bias) is the most-specific NOAA rep — in-CONUS it carries the
#   family and ncep_nbm is suppressed as a provider dup. 2026-06-17 COARSE-GLOBAL REMOVAL: the
#   0.25°/25km gfs_global is no longer in the family — OUTSIDE CONUS both members are
#   domain-ineligible, so NCEP has NO rep there (structurally absent, not expected).
NCEP_FAMILY = (GFS_HRRR_MODEL, NBM_MODEL)
#   UKMO family — the UKV 2km nest and the 10km global are the same Met Office physics; in
#   the UK the 2km nest is the rep (ukmo_global suppressed), elsewhere the global carries it.
UKMO_FAMILY = (UKMO_UK_MODEL, UKMO_GLOBAL_MODEL)
#   CMC/GEM family (2026-06-17) — repped ONLY by the HRDPS 2.5km North-America nest. The GDPS
#   ~15km global (gem_global) was DROPPED in the coarse-global removal, so OUTSIDE N-America CMC
#   has NO rep (structurally absent, not expected). In-domain the 2.5km nest carries the family.
GEM_FAMILY = (GEM_HRDPS_MODEL,)
PROVIDER_FAMILIES = (ICON_FAMILY, NCEP_FAMILY, UKMO_FAMILY, GEM_FAMILY)

# Alias dedup thresholds (spec §3: corr > 0.995 AND mean|delta| < eps).
ALIAS_CORR_THRESHOLD = 0.995
ALIAS_MEAN_ABS_DELTA_EPS = 0.05  # degC

# Which regional model each domain key in the polygon config governs.
# icon_eu has its OWN ICON-EU 7km-nest polygon (2026-06-09 fix) — it is domain-gated like the
# other regionals, not borrowing the icon_d2 Central-EU box.
_REGIONAL_DOMAIN_KEY = {
    "icon_d2": "icon_d2",
    "meteofrance_arome_france_hd": "meteofrance_arome_france_hd",
    ICON_EU_MODEL: ICON_EU_MODEL,
    # 2026-06-09 PROMOTION (operator-directed): ncep_nbm_conus is the domain-gated NCEP-family
    # CONUS blend (suppressed by gfs_hrrr when the 3km nest is present); ukmo_uk_deterministic_2km
    # is the London regional expert (UKMO-family rep in the UK). Both gated by their own polygons.
    "ncep_nbm_conus": "ncep_nbm_conus",
    "ukmo_uk_deterministic_2km": "ukmo_uk_deterministic_2km",
    # 2026-06-17 precision-input fix: high-res CONUS / N-America regional experts, each gated by
    # its own physical-domain polygon (config/model_domain_polygons.yaml). gfs_hrrr is the NOAA
    # 3km nest; gem_hrdps_continental is the CMC 2.5km nest.
    GFS_HRRR_MODEL: GFS_HRRR_MODEL,
    GEM_HRDPS_MODEL: GEM_HRDPS_MODEL,
}


@dataclass(frozen=True)
class DomainPolygon:
    model_name: str
    region_label: str
    max_lead_days: int
    ring: tuple[tuple[float, float], ...]  # (lon, lat) closed ring


@functools.lru_cache(maxsize=1)
def load_domain_polygons(path: str | None = None) -> dict[str, DomainPolygon]:
    """Load config/model_domain_polygons.yaml -> {model_name: DomainPolygon}.

    Fail-soft: a missing/unparseable config yields an empty map, which makes EVERY regional
    out-of-domain (ABSENT) — the conservative default (no leak). Never raises to the caller.
    """
    import yaml  # noqa: PLC0415

    cfg_path = Path(path) if path else POLYGON_CONFIG_PATH
    try:
        raw = yaml.safe_load(cfg_path.read_text())
    except Exception:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    domains = raw.get("domains")
    if not isinstance(domains, Mapping):
        return {}
    out: dict[str, DomainPolygon] = {}
    for _key, spec in domains.items():
        if not isinstance(spec, Mapping):
            continue
        model_name = str(spec.get("model_name") or "").strip()
        polygon = spec.get("polygon")
        if not model_name or not isinstance(polygon, Sequence):
            continue
        ring: list[tuple[float, float]] = []
        for pt in polygon:
            if isinstance(pt, Sequence) and len(pt) == 2:
                ring.append((float(pt[0]), float(pt[1])))
        if len(ring) < 4:
            continue
        out[model_name] = DomainPolygon(
            model_name=model_name,
            region_label=str(spec.get("region_label") or model_name),
            max_lead_days=int(spec.get("max_lead_days", 1)),
            ring=tuple(ring),
        )
    return out


def point_in_ring(lat: float, lon: float, ring: Sequence[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon. ring is (lon, lat) closed. Boundary counts as inside."""
    x, y = float(lon), float(lat)
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        # On-segment (boundary) -> inside.
        if min(xi, xj) - 1e-12 <= x <= max(xi, xj) + 1e-12 and min(yi, yj) - 1e-12 <= y <= max(yi, yj) + 1e-12:
            cross = (xj - xi) * (y - yi) - (yj - yi) * (x - xi)
            if abs(cross) < 1e-9:
                return True
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def regional_eligible(
    model_name: str,
    *,
    lat: float,
    lon: float,
    lead_days: int,
    polygons: Mapping[str, DomainPolygon] | None = None,
) -> bool:
    """Regional polygon + lead gate. True iff the city is INSIDE the model's domain polygon
    AND lead_days <= the domain's max_lead_days. Out-of-polygon or over-horizon -> ABSENT.

    This is the only gate that lets a regional expert into the fusion. Globals are never gated
    by polygon (they are global). A model with no polygon entry is treated as out-of-domain.
    """
    if model_name not in _REGIONAL_DOMAIN_KEY:
        return False
    poly_map = polygons if polygons is not None else load_domain_polygons()
    poly = poly_map.get(model_name)
    if poly is None:
        return False
    if int(lead_days) > poly.max_lead_days:
        return False
    return point_in_ring(lat, lon, poly.ring)


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    xa = list(a[:n])
    xb = list(b[:n])
    ma = sum(xa) / n
    mb = sum(xb) / n
    sxx = sum((v - ma) ** 2 for v in xa)
    syy = sum((v - mb) ** 2 for v in xb)
    sxy = sum((xa[i] - ma) * (xb[i] - mb) for i in range(n))
    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    return sxy / (sxx ** 0.5 * syy ** 0.5)


def is_alias(
    series_a: Sequence[float],
    series_b: Sequence[float],
    *,
    corr_threshold: float = ALIAS_CORR_THRESHOLD,
    mean_abs_delta_eps: float = ALIAS_MEAN_ABS_DELTA_EPS,
) -> bool:
    """Spec §3 alias test: two series are the same instrument iff corr > threshold AND
    mean|delta| < eps. Used to drop icon_seamless when it is bit-identical to icon_d2."""
    n = min(len(series_a), len(series_b))
    if n == 0:
        return False
    mean_abs_delta = sum(abs(series_a[i] - series_b[i]) for i in range(n)) / n
    if mean_abs_delta >= mean_abs_delta_eps:
        return False
    return _corr(series_a, series_b) > corr_threshold


@dataclass(frozen=True)
class SelectedModelSet:
    """Result of F4 selection — the ordered fusion set + provenance for the EMOS model_set_hash.

    ``likelihood_globals``: the decorrelated global reps present today (likelihood terms).
    ``regional_experts``: in-domain regional experts that passed the polygon + lead gate.
    ``anchor_present``: whether the ecmwf_ifs 0.1 anchor (prior) is available.
    ``dropped_aliases``: models removed by alias dedup (e.g. icon_seamless).
    ``excluded_regionals``: regionals dropped by the polygon/lead gate (out-of-domain).
    ``dropped_provider_dups``: present+otherwise-eligible models suppressed because another
        instrument of the SAME physical provider family already represents it in the fusion
        (spec §4 step 2 single-rep — e.g. icon_global/icon_eu suppressed when icon_d2 is the
        in-domain DWD-ICON rep). Distinct from ``dropped_aliases`` (bit-identical instrument)
        and ``excluded_regionals`` (out-of-polygon): these are real, distinct models dropped
        purely to keep one representative per provider family. Reported for the model_set_hash
        provenance so the fusion's Sigma is never fed two reps of one provider.
    """

    anchor_present: bool
    likelihood_globals: tuple[str, ...]
    regional_experts: tuple[str, ...]
    dropped_aliases: tuple[str, ...]
    excluded_regionals: tuple[str, ...]
    dropped_provider_dups: tuple[str, ...] = ()

    @property
    def used_models(self) -> tuple[str, ...]:
        anchor = (ANCHOR_MODEL,) if self.anchor_present else ()
        return anchor + self.likelihood_globals + self.regional_experts


def select_models(
    *,
    present_models: Mapping[str, float],
    lat: float,
    lon: float,
    lead_days: int,
    alias_series: Mapping[str, Sequence[float]] | None = None,
    polygons: Mapping[str, DomainPolygon] | None = None,
) -> SelectedModelSet:
    """Apply §4 steps (1)-(3): eligibility, decorrelated provider reps, alias dedup, regional
    polygon gate. ``present_models`` maps model_name -> today's value for the models that
    successfully fetched (fail-soft drop already applied upstream). ``alias_series`` maps
    model_name -> a short recent value series used for the icon_seamless==icon_d2 alias test.

    Selection order is deterministic (spec order) so the EMOS model_set_hash is stable.
    """
    present = set(present_models)
    series = dict(alias_series or {})

    anchor_present = ANCHOR_MODEL in present

    # ---- alias dedup: drop icon_seamless when it is an alias of icon_d2 ----
    dropped_aliases: list[str] = []
    if "icon_seamless" in present:
        d2_series = series.get("icon_d2")
        seam_series = series.get("icon_seamless")
        if d2_series is not None and seam_series is not None and is_alias(d2_series, seam_series):
            dropped_aliases.append("icon_seamless")
        elif "icon_d2" in present and d2_series is None and seam_series is None:
            # No series to test, but both present and spec declares them bit-identical in EU
            # -> conservatively dedup (never double-count the DWD-EU regional rep).
            dropped_aliases.append("icon_seamless")

    # ---- regional polygon + lead gate (run FIRST: an in-domain regional becomes its provider
    #      family's representative, so its eligibility decides which globals it suppresses) ----
    regional_experts: list[str] = []
    excluded_regionals: list[str] = []
    for rm in REGIONAL_MODELS:
        if rm not in present:
            continue
        if rm in dropped_aliases:
            continue
        if regional_eligible(rm, lat=lat, lon=lon, lead_days=lead_days, polygons=polygons):
            regional_experts.append(rm)
        else:
            excluded_regionals.append(rm)

    # ---- provider-family single representative (spec §4 step 2) for the DWD/ICON family ----
    # icon_d2 (2km nest), icon_eu (7km nest) and icon_global are the SAME physical provider at
    # different scopes; feeding two as independent triple-counts one error source (BLOCKER 9).
    # Keep the FIRST eligible member of ICON_FAMILY (most-specific-first) and suppress the rest.
    #   - icon_d2 (2km nest) is eligible only when it already qualified as a regional expert
    #     above (in Central-EU polygon + lead ok). It carries the family inside the EU box.
    #   - icon_eu (7km nest) is eligible inside its OWN ICON-EU domain (config polygon 'icon_eu',
    #     Europe + W-Asia/Middle East) at lead<=3. 2026-06-09 FIX: this previously borrowed the
    #     TIGHTENED icon_d2 Central-EU box, so for EU-edge cities (Madrid/Moscow/Istanbul/Ankara/
    #     Helsinki/Tel Aviv/Warsaw) where icon_d2 is absent but icon_eu has real data + Exp-O
    #     uplift, icon_eu_in_eu_domain was False -> icon_global (13km) became the rep and the
    #     better 7km icon_eu was dropped as a provider_dup. With its own polygon, icon_eu wins the
    #     ICON-family rep contest in those cities (icon_d2 absent, icon_eu eligible before
    #     icon_global). In Central-EU icon_d2 still wins (most-specific-first), so icon_eu is
    #     correctly dropped there. Out of the ICON-EU domain entirely, icon_global remains the rep.
    #   - icon_global is always eligible (global scope) and is the conservative default rep
    #     used outside the ICON-EU domain (spec §3: "use icon_d2 in-EU, icon_global out").
    # 2026-06-09 GENERALIZATION (one mechanism, K<<N): the family single-rep contest now runs
    # for EVERY declared provider family (ICON, NCEP, UKMO) with one eligibility rule:
    #   - a REGIONAL family member (icon_d2, ukmo_uk_2km) is eligible iff it already qualified
    #     as a regional expert above (own polygon + lead ok);
    #   - a DOMAIN-GATED global-scope member (icon_eu 7km nest, ncep_nbm CONUS blend) is
    #     eligible iff present AND inside its own config polygon at this lead;
    #   - a pure global member is eligible whenever present.
    # The FIRST eligible member (most-specific-first) is the family rep; every other PRESENT
    # global-scope member of the family is suppressed as a provider duplicate — a second
    # eligible rep AND a present-but-ineligible member both count (e.g. icon_eu out-of-EU must
    # never ride alongside icon_global; ncep_nbm must never ride alongside gfs_hrrr in-CONUS).
    # Regional non-reps are simply absent (out-of-polygon -> excluded_regionals), never "global dups".
    def _family_member_eligible(member: str) -> bool:
        if member in dropped_aliases:
            return False
        if member in REGIONAL_MODELS:
            return member in regional_experts  # in-domain + lead ok (already gated above)
        if member in _REGIONAL_DOMAIN_KEY:
            return member in present and regional_eligible(
                member, lat=lat, lon=lon, lead_days=lead_days, polygons=polygons
            )
        return member in present  # pure global: always eligible when present

    dropped_provider_dups: list[str] = []
    for family in PROVIDER_FAMILIES:
        family_rep: str | None = next(
            (m for m in family if _family_member_eligible(m)), None
        )
        dropped_provider_dups.extend(
            m
            for m in GLOBAL_LIKELIHOOD_MODELS
            if m in family and m in present and m != family_rep and m not in dropped_aliases
        )

    # ---- decorrelated global likelihood reps (spec order), minus aliases and provider dups ----
    suppressed_globals = set(dropped_provider_dups)
    likelihood_globals = tuple(
        m
        for m in GLOBAL_LIKELIHOOD_MODELS
        if m in present and m not in dropped_aliases and m not in suppressed_globals
    )

    return SelectedModelSet(
        anchor_present=anchor_present,
        likelihood_globals=likelihood_globals,
        regional_experts=tuple(regional_experts),
        dropped_aliases=tuple(dropped_aliases),
        excluded_regionals=tuple(excluded_regionals),
        dropped_provider_dups=tuple(dropped_provider_dups),
    )
