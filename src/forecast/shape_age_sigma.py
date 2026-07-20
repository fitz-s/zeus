# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult P2-B: anomaly transport prices the center; the REMAINING risk of an aged
#   shape is a fitted variance term gamma_g * shape_lag_hours/6 added to the transported
#   predictive variance. Operator order 2026-07-17: fit from existing archives, strictly
#   walk-forward; serving is fail-open dormant until the artifact is installed.
"""Fitted shape-age sigma lookup for the transported ENS evidence shape.

``gamma_for(metric)`` returns the fitted excess-variance slope (degC² per 6h of
shape lag) written by ``scripts/fit_shape_age_sigma.py``
(state/shape_age_sigma/ACTIVE.json -> shape_age_sigma_<YYYYMMDD>.json). The consumer
(``_current_evidence_shape_from_values``) multiplies it by ``shape_lag_hours/6`` on the
transported branch ONLY — the fit's covariate is the same carrier-minus-ENS-cycle lag,
so fit and serving share one unit.

FAIL-OPEN HARD INVARIANT (mirror of src/forecast/staleness_variance.v_for): artifact
absent, pointer/sha mismatch, unknown metric, non-finite/negative fitted value, any
exception -> 0.0 — the added variance term vanishes and serving is byte-identical.
This module never raises and never reduces availability.
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

DEFAULT_SHAPE_AGE_SIGMA_DIR = PROJECT_ROOT / "state" / "shape_age_sigma"
ENV_SHAPE_AGE_SIGMA_DIR = "ZEUS_SHAPE_AGE_SIGMA_ARTIFACT_DIR"
ACTIVE_POINTER_NAME = "ACTIVE.json"


def _artifact_dir() -> Path:
    override = os.environ.get(ENV_SHAPE_AGE_SIGMA_DIR)
    if override and override.strip():
        return Path(override).expanduser()
    return DEFAULT_SHAPE_AGE_SIGMA_DIR


def _load_active_artifact(artifact_dir_text: str) -> Mapping[str, object] | None:
    """mtime-keyed wrapper: a weekly refit that rewrites ACTIVE.json is picked up by
    long-lived daemons WITHOUT a restart; an unchanged pointer stays a pure cache hit.
    Fail-soft contract of the inner loader is unchanged."""
    pointer_path = Path(artifact_dir_text) / ACTIVE_POINTER_NAME
    try:
        mtime_ns = pointer_path.stat().st_mtime_ns
    except OSError:
        return None
    return _load_active_artifact_at(artifact_dir_text, mtime_ns)


@lru_cache(maxsize=8)
def _load_active_artifact_at(
    artifact_dir_text: str, pointer_mtime_ns: int
) -> Mapping[str, object] | None:
    """Read+integrity-check ACTIVE.json -> the referenced artifact JSON; None on any fault."""
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


# Test-compat: reset via _load_active_artifact.cache_clear().
_load_active_artifact.cache_clear = _load_active_artifact_at.cache_clear  # type: ignore[attr-defined]


def gamma_for(metric: str) -> float:
    """Fitted shape-age variance slope (degC² per 6h of shape lag); 0.0 fail-open."""
    try:
        artifact = _load_active_artifact(str(_artifact_dir()))
        if not artifact:
            return 0.0
        entry = (artifact.get("metrics") or {}).get(str(metric))
        if not entry:
            return 0.0
        gamma = float(entry.get("gamma_per_6h", 0.0))
        if not math.isfinite(gamma) or gamma <= 0.0:
            return 0.0
        return gamma
    except Exception:
        return 0.0
