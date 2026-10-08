# Created: 2026-10-07
# Last reused or audited: 2026-10-08
# Authority basis: docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md
#   (D2 lifecycle, D6 qualification); fitted by scripts/fit_day0_dense_state_space.py.
"""Fitted parameters of the Day0 dense state-space law (FORECAST-class artifact, read-only).

The artifact ``config/day0_dense_state_space_params.json`` is versioned and content-hashed.  A city
block validates completely or is absent.  ``params_for_hash`` resolves one city block by its own
content hash, so a sealed dense certificate replays against exactly the parameters it used.
Eligibility (``metrics``) is bound to the qualification run by hash; an unqualified city serves
nothing.  Loading never raises into a caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping

from src.data.day0_dense_state_space import DenseLatent, DenseMean, DenseModel, DenseNoise

ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "config" / "day0_dense_state_space_params.json"
SCHEMA_VERSION = 2
ARTIFACT_KIND = "day0_dense_state_space_params"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Lifecycle:
    """Settlement-page lifecycle of a received provisional report at one station.

    ``kept`` / ``corrected`` / ``removed`` / ``gross`` are prior branch probabilities per report
    kind (routine, speci) for isolated reports; ``outage_prior`` is the prior probability that a
    day's unresolved reports fall inside a shared page outage (then removed but valid);
    ``delta`` the corrected-value offset distribution; ``visibility`` the posterior-predictive
    probability a(lag) that a kept report is already on a page fetch issued lag minutes after it,
    as (lag_upper_minutes, a) bins, increasing."""

    kept: Mapping[str, float]
    corrected: Mapping[str, float]
    removed: Mapping[str, float]
    gross: Mapping[str, float]
    outage_prior: float
    delta: tuple[tuple[int, float], ...]
    visibility: tuple[tuple[float, float], ...]

    def branches(self, kind: str) -> tuple[float, float, float, float]:
        return (self.kept[kind], self.corrected[kind], self.removed[kind], self.gross[kind])

    def a(self, lag_minutes: float) -> float:
        for upper, value in self.visibility:
            if lag_minutes <= upper:
                return value
        return self.visibility[-1][1]


@dataclass(frozen=True)
class DenseCityParams:
    city: str
    station: str
    timezone: str
    metrics: frozenset[str]
    routine_minutes: tuple[int, ...]
    speci_rate_per_min: float
    lifecycle: Lifecycle
    dense_channel: str | None
    dense_max_age_minutes: float
    model: DenseModel
    variants: tuple[DenseModel, ...]
    training_last_date: str
    params_hash: str


@dataclass(frozen=True)
class DenseParamsArtifact:
    content_hash: str
    data_version: str
    training_cutoff: str
    qualification_hash: str | None
    cities: Mapping[str, DenseCityParams]


def canonical_hash(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


ELIGIBILITY_KEYS = frozenset({"metrics", "qualification"})


def block_hash(block: Mapping[str, Any]) -> str:
    """Content address of a city's fitted law: every key except eligibility, so requalification
    alone never orphans a sealed certificate."""
    body = {k: v for k, v in block.items() if k not in ELIGIBILITY_KEYS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _iso_date(value: Any) -> str:
    from datetime import date

    return date.fromisoformat(str(value)).isoformat()


def _finite(value: Any) -> float:
    out = float(value)
    if not math.isfinite(out):
        raise ValueError("DAY0_DENSE_PARAMS_NONFINITE")
    return out


def _model(block: Mapping[str, Any]) -> DenseModel:
    lat, mean, noise = block["latent"], block["mean"], block.get("noise")
    return DenseModel(
        DenseLatent(_finite(lat["tau"]), _finite(lat["s2"]), _finite(lat.get("s2_static", 0.0))),
        None if noise is None else DenseNoise(
            tuple(_finite(v) for v in noise["b_hour"]), _finite(noise["s1"]), _finite(noise["s2"]),
            _finite(noise["pi"]), _finite(noise.get("quantum", 0.1)), _finite(noise.get("tau_e", 1.0)),
            _finite(noise.get("sd2", 0.0))),
        DenseMean(tuple(_finite(v) for v in mean["mu_hour"]), _finite(mean["beta"])),
    )


def _lifecycle(block: Mapping[str, Any]) -> Lifecycle:
    kinds = ("routine", "speci")
    out = {}
    for branch in ("kept", "corrected", "removed", "gross"):
        out[branch] = {k: _finite(block[branch][k]) for k in kinds}
    for k in kinds:
        values = [out[b][k] for b in ("kept", "corrected", "removed", "gross")]
        if any(not 0.0 <= v <= 1.0 for v in values) or not math.isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ValueError("DAY0_DENSE_LIFECYCLE_INVALID")
    delta = tuple((int(d), _finite(p)) for d, p in block["delta"])
    visibility = tuple((_finite(u), _finite(a)) for u, a in block["visibility"])
    outage = _finite(block["outage_prior"])
    if (not delta or not math.isclose(sum(p for _, p in delta), 1.0, abs_tol=1e-9) or any(d == 0 for d, _ in delta)
            or not visibility or any(not 0.0 <= a <= 1.0 for _, a in visibility)
            or [u for u, _ in visibility] != sorted(u for u, _ in visibility) or not 0.0 <= outage < 1.0):
        raise ValueError("DAY0_DENSE_LIFECYCLE_INVALID")
    return Lifecycle(out["kept"], out["corrected"], out["removed"], out["gross"], outage, delta, visibility)


def _city(name: str, block: Mapping[str, Any]) -> DenseCityParams:
    metrics = frozenset(str(m) for m in block["metrics"])
    routine = tuple(int(m) for m in block["routine_minutes"])
    speci = _finite(block["speci_rate_per_min"])
    dense = block.get("dense_channel")
    if (not metrics <= {"high", "low"} or not routine or any(not 0 <= m < 60 for m in routine)
            or speci < 0 or (dense is not None and not isinstance(dense, str))):
        raise ValueError("DAY0_DENSE_CITY_PARAMS_INVALID")
    model = _model(block["model"])
    if (model.noise is None) != (dense is None):
        raise ValueError("DAY0_DENSE_CITY_CHANNEL_NOISE_MISMATCH")
    variants = tuple(_model(v) for v in block.get("variants", ()))
    return DenseCityParams(
        city=name, station=str(block["station"]).upper(), timezone=str(block["timezone"]), metrics=metrics,
        routine_minutes=routine, speci_rate_per_min=speci, lifecycle=_lifecycle(block["lifecycle"]),
        dense_channel=dense, dense_max_age_minutes=_finite(block.get("dense_max_age_minutes", 25.0)),
        model=model, variants=variants,
        training_last_date=max(_iso_date(block["training"]["last"]), _iso_date(block["lifecycle"]["last_local_date"])),
        params_hash=block_hash(block))


def parse_artifact(payload: Mapping[str, Any]) -> DenseParamsArtifact:
    if (payload.get("schema_version") != SCHEMA_VERSION or payload.get("artifact") != ARTIFACT_KIND
            or payload.get("content_hash") != canonical_hash(payload)):
        raise ValueError("DAY0_DENSE_PARAMS_ARTIFACT_INVALID")
    cities = {}
    for name, block in dict(payload["cities"]).items():
        try:
            cities[str(name)] = _city(str(name), block)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            logger.warning("DAY0_DENSE_CITY_PARAMS_REJECTED city=%s error=%s", name, exc)
    qualification = payload.get("qualification_hash")
    return DenseParamsArtifact(str(payload["content_hash"]), str(payload["data_version"]),
                               str(payload["training_cutoff"]),
                               None if qualification is None else str(qualification), cities)


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int, size: int) -> DenseParamsArtifact | None:
    try:
        return parse_artifact(json.loads(Path(path).read_text()))
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        logger.warning("DAY0_DENSE_PARAMS_ARTIFACT_UNAVAILABLE path=%s error=%s", path, exc)
        return None


def load_dense_params(path: Path | None = None) -> DenseParamsArtifact | None:
    path = ARTIFACT_PATH if path is None else path
    try:
        stat = path.stat()
    except OSError:
        return None
    return _load(str(path), stat.st_mtime_ns, stat.st_size)


def dense_params_for(city: str, metric: str, target_date: str,
                     path: Path | None = None) -> tuple[DenseParamsArtifact, DenseCityParams] | None:
    """The qualified city block for this family, or None.  Walk-forward: the target date must be
    after the training data."""
    artifact = load_dense_params(path)
    if artifact is None:
        return None
    params = artifact.cities.get(city)
    if params is None or metric not in params.metrics or not str(target_date)[:10] > params.training_last_date:
        return None
    return artifact, params


def params_for_hash(city: str, params_hash: str, path: Path | None = None) -> DenseCityParams | None:
    """The city block whose content hash is ``params_hash``, whatever its eligibility today."""
    artifact = load_dense_params(path)
    if artifact is None:
        return None
    params = artifact.cities.get(city)
    return params if params is not None and params.params_hash == params_hash else None
