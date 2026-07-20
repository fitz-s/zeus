# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (f) — CP bound on ~51 DEPENDENT ENS members treated as independent
#   binomial trials is overconfident; honest form = effective-n from measured member
#   dependence. Fail-open: missing artifact ⇒ rho 0.0 ⇒ byte-identical serving.
"""Fitted ECMWF-ENS member-dependence (design-effect) artifact loader.

``scripts/fit_ens_member_dependence.py`` measures the intraclass correlation
(rho) of ensemble member settlement-preimage hit indicators over the settled
archive, strictly walk-forward, and writes a versioned artifact under
``state/ens_member_dependence/`` behind an ``ACTIVE.json`` pointer
(artifact filename + sha256), mirroring the source-clock weight artifact
pattern (src/strategy/live_inference/source_clock_city_weights.py).

``member_dependence_rho(metric)`` is the ONLY serving-side read. Contract:

- artifact absent / unreadable / sha256 mismatch / no finite rho ⇒ 0.0
  (rho=0 makes the Clopper-Pearson effective-n correction the exact integer
  identity — byte-identical to pre-artifact behavior; never reduces posterior
  availability).
- metric fitted ⇒ its rho, clamped to [0, 1].
- metric ``None`` or unfitted ⇒ the MAX fitted rho (pooled conservative
  fallback: a larger rho only shrinks n_eff, i.e. only WIDENS the UCB).

The loader never raises (fail-soft ``None`` internally) and caches by artifact
directory; tests point ``ZEUS_ENS_MEMBER_DEPENDENCE_ARTIFACT_DIR`` at their own
(or an absent) directory and call ``_load_active_artifact.cache_clear()``.
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

DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "state" / "ens_member_dependence"
ENV_ARTIFACT_DIR = "ZEUS_ENS_MEMBER_DEPENDENCE_ARTIFACT_DIR"
ACTIVE_POINTER_NAME = "ACTIVE.json"


def _artifact_dir() -> Path:
    override = os.environ.get(ENV_ARTIFACT_DIR)
    if override and override.strip():
        return Path(override).expanduser()
    return DEFAULT_ARTIFACT_DIR


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
    """Read+integrity-check the ACTIVE.json pointer -> the referenced artifact JSON.

    Fail-soft: a missing pointer/artifact, a sha256 mismatch, or any parse error
    returns ``None`` (the caller serves rho=0.0 — the exact identity) — this
    loader never raises.
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


def member_dependence_rho(metric: str | None) -> float:
    """Fitted member-dependence rho for ``metric`` ('high'/'low'); 0.0 = identity.

    Conservative-only by construction: the returned rho is clamped to [0, 1]
    and an unknown/None metric maps to the LARGEST fitted rho (widest bound).
    """
    artifact = _load_active_artifact(str(_artifact_dir()))
    if not artifact:
        return 0.0
    rhos: dict[str, float] = {}
    metrics = artifact.get("metrics")
    if isinstance(metrics, Mapping):
        for name, cell in metrics.items():
            try:
                value = float((cell or {}).get("rho"))  # type: ignore[union-attr]
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                rhos[str(name).strip().lower()] = min(max(value, 0.0), 1.0)
    if not rhos:
        return 0.0
    key = str(metric or "").strip().lower()
    if key in rhos:
        return rhos[key]
    return max(rhos.values())
