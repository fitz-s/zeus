# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §4a (staleness
#   degrade ladder, 2026-07-17). AMBER-band sigma inflation is priced in ERROR VARIANCE
#   fitted from settled history, never a guessed haircut — mirror of the mixed-cycle
#   staleness-variance discipline (consult v2 (b)).
"""Fitted POSTERIOR-AGE inflation lookup for the AMBER staleness band.

``v_for(metric, age_hours)`` returns the fitted additive predictive-variance inflation
(degC²) for serving a posterior whose age (decision_time − source_cycle_time) falls in a
stale band. The artifact is written by ``scripts/fit_posterior_age_inflation.py``
(state/posterior_age_inflation/ACTIVE.json -> posterior_age_inflation_<YYYYMMDD>.json).

SEAM DISTINCTION (prevents double-counting with src/forecast/staleness_variance.py):
  * staleness_variance prices INSTRUMENT cycle-lag INSIDE the fused center — how much a
    single model value substituted from an older cycle inflates that model's residual
    second moment in the precision weights (a per-model, materialization-time term).
  * posterior_age_inflation prices the POSTERIOR's age AT ADMISSION — how much the whole
    served belief's center-vs-settlement error grows once the posterior itself is stale
    (a posterior-level, decision-time term applied to the served predictive sigma).
  Different seams, different history slices (per-model residuals vs whole-posterior
  center error), applied at different times — they do not overlap.

AGE -> BAND: the fit measures the paired aged-vs-fresh center-error variance increment
keyed on the posterior age band (``band = floor(age_hours / band_hours) * band_hours``).
The loader returns the fitted v for the band CONTAINING ``age_hours``; between/beyond
fitted bands it clamps to the LARGEST fitted band ≤ the queried band (v is monotone in
age, so this is the measured lower bound — never an extrapolation).

FAIL-OPEN HARD INVARIANT: artifact absent, pointer/sha mismatch, unknown metric,
non-finite/negative age, any exception -> 0.0, which callers add to the base predictive
variance (a +0.0 no-op) — byte-identical serving. This module never raises and never
reduces availability. Until the orchestrator activates a fitted artifact post-merge the
loader returns 0.0 everywhere, so the ladder ships inert on the AMBER-inflation axis
(the RED entry-isolation axis is independent and does not depend on this artifact).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from src.config import PROJECT_ROOT

DEFAULT_POSTERIOR_AGE_INFLATION_DIR = PROJECT_ROOT / "state" / "posterior_age_inflation"
ENV_POSTERIOR_AGE_INFLATION_DIR = "ZEUS_POSTERIOR_AGE_INFLATION_ARTIFACT_DIR"
ACTIVE_POINTER_NAME = "ACTIVE.json"


def _artifact_dir() -> Path:
    override = os.environ.get(ENV_POSTERIOR_AGE_INFLATION_DIR)
    if override and override.strip():
        return Path(override).expanduser()
    return DEFAULT_POSTERIOR_AGE_INFLATION_DIR


@lru_cache(maxsize=8)
def _load_active_artifact(artifact_dir_text: str) -> Mapping[str, object] | None:
    """Read+integrity-check ACTIVE.json -> the referenced artifact JSON.

    Fail-soft mirror of staleness_variance._load_active_artifact: a missing
    pointer/artifact, a sha256 mismatch, or any parse error returns ``None`` — never
    raises.
    """
    artifact_dir = Path(artifact_dir_text)
    pointer_path = artifact_dir / ACTIVE_POINTER_NAME
    if not pointer_path.exists():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        artifact_path = artifact_dir / str(pointer["artifact"])
        raw = artifact_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(pointer.get("sha256", "")):
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def v_for(metric: str, age_hours: float) -> float:
    """Fitted posterior-age variance inflation (degC²) for ``age_hours``; 0.0 fail-open."""
    try:
        age = float(age_hours)
        if not math.isfinite(age) or age <= 0.0:
            return 0.0
        artifact = _load_active_artifact(str(_artifact_dir()))
        if not artifact:
            return 0.0
        entry = (artifact.get("metrics") or {}).get(str(metric))
        if not entry:
            return 0.0
        band_hours = float(artifact.get("band_hours") or 6.0)
        if band_hours <= 0.0:
            return 0.0
        v_by_band = {
            int(band): float(v)
            for band, v in (entry.get("v_by_age_band") or {}).items()
        }
        if not v_by_band:
            return 0.0
        band = int(age // band_hours) * int(band_hours)
        eligible = [b for b in v_by_band if b <= band]
        if not eligible:
            return 0.0
        v = v_by_band[max(eligible)]
        if not math.isfinite(v) or v <= 0.0:
            return 0.0
        return v
    except Exception:
        return 0.0
