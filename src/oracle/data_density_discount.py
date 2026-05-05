# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/reference/zeus_oracle_density_discount_reference.md (v2 redesign)
"""Data Density Discount (DDD) v2 — Two-Rail trigger + continuous linear curve.

Design spec: docs/reference/zeus_oracle_density_discount_reference.md §6 (v2)
Phase 1 analysis: docs/operations/task_2026-05-03_ddd_implementation_plan/
                   phase1_results/MATH_REALITY_OPTIMUM_ANALYSIS.md

Two-Rail logic:
  Rail 1 — Absolute hard kill: cov < 0.35 AND window_elapsed > 0.50
  Rail 2 — Continuous linear discount: D = min(0.09, 0.20 × shortfall)
            with 1.25× amplification when N_platt_samples < N_star

σ is a diagnostic telemetry value only — it does NOT enter the trigger or
floor selection. See §X of the reference doc for the redesign rationale.

Asymmetric loss preferences (Denver, Paris) belong in the Kelly multiplier
layer, NOT in the floor. See docs/reference/zeus_kelly_asymmetric_loss_handoff.md.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal, NamedTuple

logger = logging.getLogger(__name__)

# ── config paths ──────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_FLOORS_PATH = (
    _REPO_ROOT
    / "docs"
    / "operations"
    / "task_2026-05-03_ddd_implementation_plan"
    / "phase1_results"
    / "p2_1_FINAL_v2_per_city_floors.json"
)
_DEFAULT_NSTAR_PATH = (
    _REPO_ROOT
    / "docs"
    / "operations"
    / "task_2026-05-03_ddd_implementation_plan"
    / "phase1_results"
    / "p2_5_small_sample_floor.json"
)

# ── constants ─────────────────────────────────────────────────────────────────
ABSOLUTE_KILL_FLOOR: float = 0.35      # Rail 1: below this + >50% window elapsed → HALT
WINDOW_ELAPSED_THRESHOLD: float = 0.50 # Rail 1 and Rail 2: only evaluate after window half-done
LINEAR_ALPHA: float = 0.20             # Rail 2 curve: D = min(0.09, 0.20 × shortfall)
MAX_DISCOUNT: float = 0.09             # 9% cap — stays in CAUTION, never BLACKLIST
SMALL_SAMPLE_AMPLIFIER: float = 1.25   # multiplier applied when N < N_star
SAFETY_MINIMUM_FLOOR: float = 0.35     # absolute floor for any city (max(p05, 0.35))


# ── result type ───────────────────────────────────────────────────────────────
class DDDResult(NamedTuple):
    """Result of Data Density Discount evaluation.

    action:     'HALT'     — Rail 1 fired; stop trading for the day
                'DISCOUNT' — Rail 2 discount applied (may be 0.0 if cov >= floor)
    discount:   float in [0.0, 0.09]; 0.0 for HALT (trading blocked separately)
    rail:       1 for absolute kill, 2 for relative discount, None if too early
    diagnostic: dict with σ (if provided), shortfall, city_floor, N_star,
                small_sample_amp, final_discount_pre_mismatch
                σ is logged here for monitoring but never enters the trigger.
    """

    action: Literal["HALT", "DISCOUNT"]
    discount: float
    rail: int | None
    diagnostic: dict


# ── config loading ────────────────────────────────────────────────────────────

def load_city_floors(floors_path: Path | None = None) -> dict:
    """Load per-city floor config.

    Returns the full JSON dict. Raises on missing file (fail-CLOSED).
    """
    path = floors_path or _DEFAULT_FLOORS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"DDD floors config not found at {path}. "
            "Cannot evaluate DDD without floor data (fail-CLOSED)."
        )
    with path.open() as f:
        return json.load(f)


def load_nstar_config(nstar_path: Path | None = None) -> dict:
    """Load per-(city, metric) N_star config.

    Returns the full JSON dict. Raises on missing file (fail-CLOSED).
    """
    path = nstar_path or _DEFAULT_NSTAR_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"DDD N_star config not found at {path}. "
            "Cannot evaluate DDD without N_star data (fail-CLOSED)."
        )
    with path.open() as f:
        return json.load(f)


def get_city_floor(city: str, city_floors_config: dict) -> float:
    """Extract the active floor for a city.

    Raises KeyError if the city is missing from the config.
    Raises ValueError if the city has NO_TRAIN_DATA or EXCLUDED status.
    """
    per_city = city_floors_config.get("per_city", city_floors_config)
    if city not in per_city:
        raise KeyError(
            f"City '{city}' not found in DDD floors config (fail-CLOSED). "
            "Add the city to the floors file before enabling DDD."
        )
    entry = per_city[city]
    if isinstance(entry, dict) and "status" in entry:
        status = entry["status"]
        raise ValueError(
            f"City '{city}' has status '{status}' in floors config — "
            "DDD cannot be evaluated (fail-CLOSED)."
        )
    final_floor = entry.get("final_floor")
    if final_floor is None:
        raise ValueError(
            f"City '{city}' has null final_floor in floors config (fail-CLOSED)."
        )
    return float(final_floor)


def get_n_star(city: str, track: str, nstar_config: dict) -> int | None:
    """Extract N_star for a (city, metric/track) pair.

    Returns None if status is N_STAR_NOT_FOUND (conservative: treat as N < N_star).
    Raises KeyError if the key is completely absent.
    """
    key = f"{city}_{track}"
    per_city_metric = nstar_config.get("per_city_metric", nstar_config)
    if key not in per_city_metric:
        raise KeyError(
            f"N_star key '{key}' not found in DDD N_star config (fail-CLOSED). "
            "Add the city/track pair before enabling DDD."
        )
    entry = per_city_metric[key]
    if entry.get("status") == "N_STAR_NOT_FOUND":
        return None  # conservative: treat as small-sample
    return entry.get("N_star")


# ── core evaluation ───────────────────────────────────────────────────────────

def evaluate_ddd(
    city: str,
    track: str,
    current_cov: float,
    window_elapsed: float,
    N_platt_samples: int,
    mismatch_rate: float,
    city_floors_config: dict,
    n_star_config: dict,
    sigma_diagnostic: float | None = None,
    cycle: str | None = None,
    source_id: str | None = None,
    horizon_profile: str | None = None,
) -> DDDResult:
    """Evaluate the v2 Two-Rail Data Density Discount.

    Pure function — no DB writes.

    Parameters
    ----------
    city:               City name (must match floors config key exactly)
    track:              Track name, typically 'high' or 'low'
    current_cov:        Current directional coverage [0.0, 1.0]
    window_elapsed:     Fraction of the observation window elapsed [0.0, 1.0]
    N_platt_samples:    Number of Platt training samples for (city, track)
    mismatch_rate:      Oracle policy mismatch rate from oracle_penalty. For
                        posterior-backed artifacts this is the beta-binomial
                        95% upper bound, not raw mismatches / n.
    city_floors_config: Loaded floors JSON dict (from load_city_floors)
    n_star_config:      Loaded N_star JSON dict (from load_nstar_config)
    sigma_diagnostic:   σ of historical coverage (for monitoring only, NOT trigger)
    cycle:              Forecast cycle hour string, e.g. '00' or '12'. Stored in
                        diagnostic for monitoring; not a trigger input.
    source_id:          Forecast source identifier, e.g. 'tigge_mars' or
                        'ecmwf_open_data'. Stored in diagnostic only.
    horizon_profile:    Horizon profile, e.g. 'full' or 'short'. Stored in
                        diagnostic only.

    Returns
    -------
    DDDResult with action, discount, rail, and diagnostic dict.

    Raises
    ------
    KeyError:   city or city/track not found in config (fail-CLOSED)
    ValueError: city has NO_TRAIN_DATA or null floor (fail-CLOSED)
    """
    city_floor = get_city_floor(city, city_floors_config)
    n_star = get_n_star(city, track, n_star_config)

    diagnostic: dict = {
        "city": city,
        "track": track,
        "current_cov": current_cov,
        "window_elapsed": window_elapsed,
        "city_floor": city_floor,
        "N_platt_samples": N_platt_samples,
        "N_star": n_star,
        "mismatch_rate": mismatch_rate,
        # σ is tracked for monitoring/dashboards but never enters the trigger
        "sigma_diagnostic": sigma_diagnostic,
        "small_sample_amp_applied": False,
        # Source-cycle provenance — monitoring/audit only, NOT trigger inputs.
        # Surfaces INV-17 risk: DDD trained on 00z TIGGE history must not be
        # silently applied to a 12z OpenData live forecast without visibility.
        "cycle": cycle,
        "source_id": source_id,
        "horizon_profile": horizon_profile,
    }

    # ── RAIL 1: Absolute hard kill ─────────────────────────────────────────
    # Physics: below 0.35, no probability claim about daily extreme is defensible.
    if current_cov < ABSOLUTE_KILL_FLOOR and window_elapsed > WINDOW_ELAPSED_THRESHOLD:
        diagnostic["rail_fired"] = 1
        diagnostic["shortfall"] = None
        diagnostic["final_discount_pre_mismatch"] = None
        logger.warning(
            "DDD Rail 1 HALT: city=%s track=%s cov=%.3f window_elapsed=%.2f",
            city, track, current_cov, window_elapsed,
        )
        result = DDDResult(action="HALT", discount=0.0, rail=1, diagnostic=diagnostic)
        _emit_diagnostic_log(result)
        return result

    # ── RAIL 2: Continuous linear discount ────────────────────────────────
    shortfall = max(0.0, city_floor - current_cov)
    discount = min(MAX_DISCOUNT, LINEAR_ALPHA * shortfall)

    # Small-sample amplification: 1.25× when N < N_star
    # (N_star=None means stability point not found → treat as small-sample)
    small_sample = n_star is None or N_platt_samples < n_star
    if small_sample:
        discount = min(MAX_DISCOUNT, discount * SMALL_SAMPLE_AMPLIFIER)
        diagnostic["small_sample_amp_applied"] = True

    diagnostic["rail_fired"] = 2
    diagnostic["shortfall"] = shortfall
    diagnostic["final_discount_pre_mismatch"] = discount

    final_discount = max(mismatch_rate, discount)

    logger.debug(
        "DDD Rail 2: city=%s track=%s cov=%.3f floor=%.3f shortfall=%.3f "
        "discount=%.4f mismatch=%.4f final=%.4f small_sample=%s",
        city, track, current_cov, city_floor, shortfall,
        discount, mismatch_rate, final_discount, small_sample,
    )

    result = DDDResult(
        action="DISCOUNT",
        discount=final_discount,
        rail=2,
        diagnostic=diagnostic,
    )
    _emit_diagnostic_log(result)
    return result


# ── structured diagnostic emission (σ sink) ──────────────────────────────────

def _emit_diagnostic_log(result: DDDResult) -> None:
    """Always-on structured diagnostic emission (σ sink, 2026-05-03).

    Emits one INFO-level structured log record per DDD evaluation with
    city/track/cov/floor/σ/action/discount/N/N_star. Logging-aggregator-
    friendly: ``extra={"ddd_diag": {...}}`` keeps the field bag opaque to the
    text formatter while making every value queryable downstream.

    This is the minimal-scope σ diagnostic sink from
    RERUN_PLAN_v2.md §5 P2 #7. σ is computed and stored in the diagnostic dict
    by callers; this emitter ensures it reaches the log stream so monitoring
    tools (Grafana, Loki, an operator dashboard) can detect regime shifts
    without any further wiring. Operator may later hook the same log key to a
    metrics emitter; the contract here is "always emit, never block".
    """
    d = result.diagnostic
    payload = {
        "city": d.get("city"),
        "track": d.get("track"),
        "cov": d.get("current_cov"),
        "floor": d.get("city_floor"),
        "shortfall": d.get("shortfall"),
        "sigma": d.get("sigma_diagnostic"),
        "window_elapsed": d.get("window_elapsed"),
        "N_platt": d.get("N_platt_samples"),
        "N_star": d.get("N_star"),
        "small_sample_amp": d.get("small_sample_amp_applied"),
        "mismatch_rate": d.get("mismatch_rate"),
        "action": result.action,
        "rail": result.rail,
        "discount": result.discount,
        # Source-cycle provenance fields (INV-17 audit surface)
        "cycle": d.get("cycle"),
        "source_id": d.get("source_id"),
        "horizon_profile": d.get("horizon_profile"),
    }
    logger.info("ddd_evaluated", extra={"ddd_diag": payload})


# ── convenience wrapper with file loading ────────────────────────────────────

def evaluate_ddd_from_files(
    city: str,
    track: str,
    current_cov: float,
    window_elapsed: float,
    N_platt_samples: int,
    mismatch_rate: float,
    floors_path: Path | None = None,
    nstar_path: Path | None = None,
    sigma_diagnostic: float | None = None,
    cycle: str | None = None,
    source_id: str | None = None,
    horizon_profile: str | None = None,
) -> DDDResult:
    """Load config from files and evaluate DDD.

    Convenience wrapper. For hot paths, load configs once and call
    evaluate_ddd() directly with pre-loaded dicts.
    """
    city_floors_config = load_city_floors(floors_path)
    n_star_config = load_nstar_config(nstar_path)
    return evaluate_ddd(
        city=city,
        track=track,
        current_cov=current_cov,
        window_elapsed=window_elapsed,
        N_platt_samples=N_platt_samples,
        mismatch_rate=mismatch_rate,
        city_floors_config=city_floors_config,
        n_star_config=n_star_config,
        sigma_diagnostic=sigma_diagnostic,
        cycle=cycle,
        source_id=source_id,
        horizon_profile=horizon_profile,
    )
