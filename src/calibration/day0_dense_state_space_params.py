# Created: 2026-10-07
# Last reused or audited: 2026-10-07
# Authority basis: docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md
#   (Implementation design); fitted by scripts/fit_day0_dense_state_space.py.
"""Fitted parameters of the Day0 dense state-space law (FORECAST-class artifact, read-only).

The artifact ``config/day0_dense_state_space_params.json`` is versioned and content-hashed.  A city
is qualified for a metric only when its block validates completely; anything malformed is absent,
and absence means the legacy operator.  Loading never raises into a caller.
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
SCHEMA_VERSION = 1
ARTIFACT_KIND = "day0_dense_state_space_params"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DenseCityParams:
    city: str
    station: str
    timezone: str
    metrics: frozenset[str]
    routine_minutes: tuple[int, ...]
    page_retention: float
    dense_channel: str | None
    dense_max_age_minutes: float
    provisional_route_channels: tuple[str, ...]
    model: DenseModel
    variants: tuple[DenseModel, ...]
    training_last_date: str
    params_hash: str


@dataclass(frozen=True)
class DenseParamsArtifact:
    content_hash: str
    data_version: str
    training_cutoff: str
    cities: Mapping[str, DenseCityParams]


def canonical_hash(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _model(block: Mapping[str, Any], speci_rate: float) -> DenseModel:
    lat, mean, noise = block["latent"], block["mean"], block.get("noise")
    return DenseModel(
        DenseLatent(float(lat["tau"]), float(lat["s2"]), float(lat.get("s2_static", 0.0))),
        None if noise is None else DenseNoise(
            tuple(float(v) for v in noise["b_hour"]), float(noise["s1"]), float(noise["s2"]),
            float(noise["pi"]), float(noise.get("quantum", 0.1)), float(noise.get("tau_e", 1.0)),
            float(noise.get("sd2", 0.0))),
        DenseMean(tuple(float(v) for v in mean["mu_hour"]), float(mean["beta"])),
        speci_rate,
    )


def _city(name: str, block: Mapping[str, Any]) -> DenseCityParams:
    speci = float(block["speci_rate_per_min"])
    metrics = frozenset(str(m) for m in block["metrics"])
    routine = tuple(int(m) for m in block["routine_minutes"])
    retention = float(block["page_retention"]["s"])
    dense = block.get("dense_channel")
    if (not metrics <= {"high", "low"} or not routine or any(not 0 <= m < 60 for m in routine)
            or not 0.0 < retention < 1.0 or not math.isfinite(speci) or speci < 0
            or (dense is not None and not isinstance(dense, str))):
        raise ValueError("DAY0_DENSE_CITY_PARAMS_INVALID")
    model = _model(block["model"], speci)
    if (model.noise is None) != (dense is None):
        raise ValueError("DAY0_DENSE_CITY_CHANNEL_NOISE_MISMATCH")
    variants = tuple(_model(v, speci) for v in block.get("variants", ()))
    params_hash = hashlib.sha256(json.dumps(block, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return DenseCityParams(
        city=name, station=str(block["station"]).upper(), timezone=str(block["timezone"]), metrics=metrics,
        routine_minutes=routine, page_retention=retention, dense_channel=dense,
        dense_max_age_minutes=float(block.get("dense_max_age_minutes", 25.0)),
        provisional_route_channels=tuple(str(c) for c in block.get("provisional_route_channels", ())),
        model=model, variants=variants,
        training_last_date=max(str(block["training"]["last"]), str(block["page_retention"]["last_local_date"])),
        params_hash=params_hash)


def parse_artifact(payload: Mapping[str, Any]) -> DenseParamsArtifact:
    if (payload.get("schema_version") != SCHEMA_VERSION or payload.get("artifact") != ARTIFACT_KIND
            or payload.get("content_hash") != canonical_hash(payload)):
        raise ValueError("DAY0_DENSE_PARAMS_ARTIFACT_INVALID")
    cities = {}
    for name, block in dict(payload["cities"]).items():
        try:
            cities[str(name)] = _city(str(name), block)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("DAY0_DENSE_CITY_PARAMS_REJECTED city=%s error=%s", name, exc)
    return DenseParamsArtifact(str(payload["content_hash"]), str(payload["data_version"]),
                               str(payload["training_cutoff"]), cities)


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int, size: int) -> DenseParamsArtifact | None:
    try:
        return parse_artifact(json.loads(Path(path).read_text()))
    except (OSError, KeyError, TypeError, ValueError) as exc:
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
    """The qualified city block for this family, or None (legacy).  Walk-forward: the target date
    must be after the training data."""
    artifact = load_dense_params(path)
    if artifact is None:
        return None
    params = artifact.cities.get(city)
    if params is None or metric not in params.metrics or not str(target_date)[:10] > params.training_last_date:
        return None
    return artifact, params
