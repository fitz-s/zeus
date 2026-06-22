# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: per-city historical settlement-skill gate
#   (team-lead approved (a) 2026-06-22; live_order_pathology 2026-06-22). The DATA-PRECISION /
#   grid-distance hypothesis was FALSIFIED (settlement-verified: corr(forecast_error, d_eff_m) =
#   −0.52, wrong sign — Karachi is the closest grid yet the worst city). What separates the +edge
#   from −edge cities is forecast ACCURACY itself (Brier-vs-market), whose only pre-trade form is a
#   per-city HISTORICAL settlement-skill track record. This gate consumes that track record.
"""Per-city historical settlement-skill gate — the runtime serving rule (city selector, no de-bias).

Pairs with src/decision/selection_calibrator.py: the skill gate decides WHICH cities to trade (where
our forecast reliably beat the market on prior settled days); the calibrator blocks the
adversely-selected toxic tail WITHIN them.

THE SIGNAL: each city's PRIOR settlement-skill = mean(market_Brier − our_Brier) over that city's rows
settled STRICTLY before the decision time T (walk-forward, no leak). Positive => our forecast beat
the market for that city historically.

THE RULE: ADMIT a city only when BOTH hold:
    * prior_n >= min_track_record   (enough history — the noisy-middle, which flips edge sign at the
                                      current n~91, ABSTAINS until it earns a track record)
    * prior_skill >  skill_floor    (reliably positive — reliably-bad cities have skill<0 and are
                                      BLOCKED; cities below the floor ABSTAIN)
otherwise NO new entries from that city. ``min_track_record`` and ``skill_floor`` are LEARNED by
walk-forward (scripts/fit_city_skill_gate.py), never hard-coded. The gate decides admit/abstain from
SKILL ONLY — it never alters q (it is a city selector, not a probability authority), and price never
enters (no anchoring/cap).

ARTIFACT (versioned, FAIL-CLOSED — absent is NOT inert): read from ``state/city_skill_gate.json``
(fit ONLY by the fitter). A missing/malformed/stale/unknown-city artifact emits NO new entries for
that city. The artifact carries the posterior version it was fit under; a mismatch is stale ->
fail-closed.

HONEST MAGNITUDE (settlement-verified 2026-06-22, n=91): at the current sample only the few reliably
-skilled extremes admit (Tokyo robustly; Hong Kong marginally); reliably-bad (Karachi/Houston/Shanghai)
are blocked; the noisy-middle abstains. The admitted-set walk-forward after-cost EV is POSITIVE but
THIN (n~3-4). It grows as settlement accrues. This gate captures what is robust and abstains the rest;
it does NOT claim the in-sample +10.1% (that was a look-ahead artifact).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Mapping, Optional


# Defaults (the FITTER overrides these in the artifact _meta; they are conservative fallbacks only).
DEFAULT_MIN_TRACK_RECORD: int = 4
DEFAULT_SKILL_FLOOR: float = 0.0
DEFAULT_POSTERIOR_VERSION: str = "openmeteo_ecmwf_ifs9_bayes_fusion"

_CITY_SKILL_GATE_PATH: str = "state/city_skill_gate.json"
_CITY_SKILL_GATE_LIVE_ENV: str = "ZEUS_CITY_SKILL_GATE_LIVE"

_ARTIFACT_CACHE: Optional[dict] = None
_ARTIFACT_LOADED: bool = False


def _artifact_path() -> str:
    path = _CITY_SKILL_GATE_PATH
    if os.path.isabs(path):
        return path
    filename = path[len("state/"):] if path.startswith("state/") else path
    try:
        from src.config import state_path

        return str(state_path(filename))
    except Exception:  # noqa: BLE001
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(repo, path)


def load_artifact() -> Optional[dict]:
    """Load the city-skill-gate artifact (one-shot, cached). None when absent/unreadable so the
    serving rule fails closed (absence is NOT inert)."""
    global _ARTIFACT_CACHE, _ARTIFACT_LOADED
    if _ARTIFACT_LOADED:
        return _ARTIFACT_CACHE
    path = _artifact_path()
    out: Optional[dict] = None
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                parsed = json.load(fh)
            if isinstance(parsed, dict) and isinstance(parsed.get("cities"), dict):
                out = parsed
    except Exception:  # noqa: BLE001
        out = None
    _ARTIFACT_CACHE = out
    _ARTIFACT_LOADED = True
    return out


def reset_artifact_cache() -> None:
    global _ARTIFACT_CACHE, _ARTIFACT_LOADED
    _ARTIFACT_CACHE = None
    _ARTIFACT_LOADED = False


@dataclass(frozen=True)
class CitySkillVerdict:
    """Per-city gate verdict. ``admit`` True only when the city is reliably skilled with enough
    track record. ``abstained`` True on every no-admit path (block / thin / below-floor / fail-closed)
    so the caller emits no new entry for that city."""

    admit: bool
    abstained: bool
    city: str
    prior_skill: float
    prior_n: int
    basis: str


def _no(city: str, basis: str, skill: float = 0.0, n: int = 0) -> CitySkillVerdict:
    return CitySkillVerdict(admit=False, abstained=True, city=city, prior_skill=skill, prior_n=n, basis=basis)


def apply_city_skill_gate(
    *,
    city: str,
    artifact: Optional[Mapping] = None,
    expected_posterior_version: str = DEFAULT_POSTERIOR_VERSION,
    require_stable_bad_to_block: bool = False,
) -> CitySkillVerdict:
    """Apply the per-city historical-skill gate. Fail-closed everywhere; admit only a reliably-skilled
    city with enough prior track record. Decides from skill ONLY — never alters q, never uses price.

    ``require_stable_bad_to_block`` (LOSS-REDUCTION mode, the deployable-today posture): a city is
    HARD-BLOCKED only when its record confirms it is a TEMPORALLY-STABLE loser (negative skill in BOTH
    time halves, ``stable_bad=True``). A city merely negative in aggregate but NOT confirmed
    stable-bad still never ADMITS (negative skill), but its basis is ``CITY_SKILL_NEGATIVE_UNCONFIRMED``
    so the loss-reduction gate does not LIST it as a confirmed block (insufficient two-half evidence).
    This is the team-lead's "confirm each holds negative skill in BOTH halves before listing it"."""
    art = artifact if artifact is not None else load_artifact()
    if not isinstance(art, Mapping):
        return _no(city, "FAIL_CLOSED_NO_ARTIFACT")
    cities = art.get("cities")
    meta = art.get("_meta") if isinstance(art.get("_meta"), Mapping) else {}
    if not isinstance(cities, Mapping) or not cities:
        return _no(city, "FAIL_CLOSED_MALFORMED")

    art_version = str(meta.get("posterior_version", "")) if isinstance(meta, Mapping) else ""
    if art_version and expected_posterior_version and art_version != expected_posterior_version:
        return _no(city, "FAIL_CLOSED_STALE_VERSION")

    min_track = int(meta.get("min_track_record", DEFAULT_MIN_TRACK_RECORD)) if isinstance(meta, Mapping) else DEFAULT_MIN_TRACK_RECORD
    skill_floor = float(meta.get("skill_floor", DEFAULT_SKILL_FLOOR)) if isinstance(meta, Mapping) else DEFAULT_SKILL_FLOOR

    cell = cities.get(city)
    if not isinstance(cell, Mapping):
        return _no(city, "CITY_SKILL_UNKNOWN_CITY")
    try:
        prior_skill = float(cell.get("prior_skill"))
        prior_n = int(cell.get("prior_n", 0))
    except (TypeError, ValueError):
        return _no(city, "FAIL_CLOSED_MALFORMED")
    if not (math.isfinite(prior_skill) and prior_n >= 0):
        return _no(city, "FAIL_CLOSED_MALFORMED")

    # Reliably bad -> BLOCK (distinct basis for observability).
    if prior_skill <= 0.0:
        if require_stable_bad_to_block:
            # Loss-reduction mode: hard-block ONLY a CONFIRMED two-half stable loser.
            if bool(cell.get("stable_bad", False)):
                return _no(city, "CITY_SKILL_BLOCKED_STABLE_BAD", prior_skill, prior_n)
            # Negative in aggregate but not two-half-confirmed -> no admit, marked unconfirmed.
            return _no(city, "CITY_SKILL_NEGATIVE_UNCONFIRMED", prior_skill, prior_n)
        return _no(city, "CITY_SKILL_BLOCKED_NEGATIVE", prior_skill, prior_n)
    # Positive but too short a track record -> ABSTAIN (the noisy middle).
    if prior_n < min_track:
        return _no(city, "CITY_SKILL_THIN_TRACK", prior_skill, prior_n)
    # Positive, enough history, but below the learned floor -> ABSTAIN.
    if prior_skill <= skill_floor:
        return _no(city, "CITY_SKILL_BELOW_FLOOR", prior_skill, prior_n)
    return CitySkillVerdict(
        admit=True, abstained=False, city=city, prior_skill=prior_skill, prior_n=prior_n,
        basis="CITY_SKILL_ADMIT",
    )


def confirmed_blocked_cities(*, artifact: Optional[Mapping] = None) -> list[str]:
    """The cities the loss-reduction gate hard-blocks: those confirmed TEMPORALLY-STABLE losers
    (``stable_bad=True``). Sorted. The honest, forward-valid block list."""
    art = artifact if artifact is not None else load_artifact()
    if not isinstance(art, Mapping):
        return []
    cities = art.get("cities")
    if not isinstance(cities, Mapping):
        return []
    out = [c for c, v in cities.items() if isinstance(v, Mapping) and bool(v.get("stable_bad", False))]
    return sorted(out)


# ---------------------------------------------------------------------------
# Seam helper (flag-gated; DEFAULT OFF — does NOT change live behavior).
# ---------------------------------------------------------------------------

def city_skill_gate_live_enabled() -> bool:
    return os.environ.get(_CITY_SKILL_GATE_LIVE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def city_skill_gate_admits(
    *,
    city: str,
    artifact: Optional[Mapping] = None,
    expected_posterior_version: str = DEFAULT_POSTERIOR_VERSION,
) -> bool:
    """The seam boolean: True = this city may emit new entries. DEFAULT OFF -> always True (no-op)
    so wiring it into the admission path is inert until the orchestrator promotes the artifact and
    flips ``ZEUS_CITY_SKILL_GATE_LIVE``. When LIVE: returns the gate's admit decision (fail-closed)."""
    if not city_skill_gate_live_enabled():
        return True
    return apply_city_skill_gate(
        city=city, artifact=artifact, expected_posterior_version=expected_posterior_version
    ).admit
