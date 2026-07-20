# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (b): mixed-cycle fusion is priced in ERROR VARIANCE, never age haircuts —
#   per-model v_m(cycle-lag) fitted from strictly-prior settlements is ADDED to the model's
#   residual second moment in the precision weights. Stale instruments are DOWNWEIGHTED,
#   never excluded (E4: exclusion measured +0.152C worse).
"""Fitted staleness-variance lookup for the serving precision weights.

``v_for(model, metric, age_hours)`` returns the fitted error-variance inflation (degC²)
for serving a value whose issuing cycle lags ``age_hours`` behind the decision's selected
cycle. The artifact is written by ``scripts/fit_model_staleness_variance.py``
(state/staleness_variance/ACTIVE.json -> staleness_variance_<YYYYMMDD>.json).

LAG -> BUCKET (the fit defines the unit): the archive measures residual m2 per
``lead_days`` (one archived run per day of issuance lag), so the serving lag maps to
``bucket = freshest_fitted_lead + floor(age_hours / 24)``. Between fitted buckets the
LARGEST fitted bucket <= the mapped bucket is used (v is monotone, so this is the
measured lower bound — never an extrapolation); beyond the largest fitted bucket the lag
CLAMPS to the largest fitted v. WHY cycle-lag, not capture-lag: the history residuals
that price a served value are queried at the DECISION lead, i.e. they assume the
decision's selected cycle. A row substituted from an older cycle carries information the
decision-lead history did not price — exactly ``selected_cycle − served_cycle`` of extra
issuance lag. (``ServedInstrumentValue.age_hours`` — capture-lag after the row's OWN
cycle — is near-zero for a promptly-captured stale cycle and would leave the mechanism
dead; the materializer therefore passes cycle-lag hours.)

FAIL-OPEN HARD INVARIANT: artifact absent, pointer/sha mismatch, unknown model/metric,
non-finite lag, any exception -> 0.0, which callers add to raw_m2 (a +0.0 no-op) —
byte-identical serving. This module never raises and never reduces availability.
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

DEFAULT_STALENESS_VARIANCE_DIR = PROJECT_ROOT / "state" / "staleness_variance"
ENV_STALENESS_VARIANCE_DIR = "ZEUS_STALENESS_VARIANCE_ARTIFACT_DIR"
ACTIVE_POINTER_NAME = "ACTIVE.json"


def _artifact_dir() -> Path:
    override = os.environ.get(ENV_STALENESS_VARIANCE_DIR)
    if override and override.strip():
        return Path(override).expanduser()
    return DEFAULT_STALENESS_VARIANCE_DIR


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
    """Read+integrity-check ACTIVE.json -> the referenced artifact JSON.

    Fail-soft mirror of source_clock_city_weights._load_active_artifact: a missing
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


# Test-compat: reset via _load_active_artifact.cache_clear().
_load_active_artifact.cache_clear = _load_active_artifact_at.cache_clear  # type: ignore[attr-defined]


def v_for(model: str, metric: str, age_hours: float) -> float:
    """Fitted staleness variance (degC²) for ``age_hours`` of cycle-lag; 0.0 fail-open."""
    try:
        age = float(age_hours)
        if not math.isfinite(age) or age <= 0.0:
            return 0.0
        artifact = _load_active_artifact(str(_artifact_dir()))
        if not artifact:
            return 0.0
        entry = (
            (artifact.get("models") or {}).get(str(model), {}).get(str(metric))
        )
        if not entry:
            return 0.0
        freshest = int(entry["freshest_lead"])
        v_by_lead = {
            int(lead): float(v) for lead, v in (entry.get("v_by_lead") or {}).items()
        }
        if not v_by_lead:
            return 0.0
        bucket = freshest + int(age // 24.0)
        eligible = [lead for lead in v_by_lead if lead <= bucket]
        if not eligible:
            return 0.0
        v = v_by_lead[max(eligible)]
        if not math.isfinite(v) or v <= 0.0:
            return 0.0
        return v
    except Exception:
        return 0.0
