"""Open-Meteo model-update metadata client.

Open-Meteo's model-update metadata exposes each model's run initialisation time,
API availability time, update interval, and temporal resolution. The trading
clock uses ``last_run_availability_time + 10 minutes`` as the public-availability
boundary; callers may inject or configure the endpoint because Open-Meteo serves
the metadata links from the model-updates page rather than a static endpoint in
the repository.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

import requests

from src.strategy.live_inference.source_clock_vnext import (
    SourceRunClock,
    provider_family_for_source,
)


ENV_MODEL_UPDATES_ENDPOINT = "ZEUS_OPENMETEO_MODEL_UPDATES_URL"
ENV_MODEL_UPDATES_MAX_WORKERS = "ZEUS_OPENMETEO_MODEL_UPDATES_MAX_WORKERS"
DEFAULT_MODEL_UPDATES_ENDPOINT = "https://api.open-meteo.com/data/{model}/static/meta.json"
DEFAULT_MODEL_UPDATES_MAX_WORKERS = 8

OPENMETEO_MODEL_METADATA_IDS: Mapping[str, str] = {
    "dmi_harmonie_europe": "dmi_harmonie_arome_europe",
    "gem_hrdps_continental": "cmc_gem_hrdps",
    "gfs_hrrr": "ncep_hrrr_conus",
    "icon_d2": "dwd_icon_d2",
    "icon_eu": "dwd_icon_eu",
    "icon_global": "dwd_icon",
    "italiameteo_icon_2i": "italia_meteo_arpae_icon_2i",
    "knmi_harmonie_netherlands": "knmi_harmonie_arome_netherlands",
    "met_nordic": "metno_nordic_pp",
    "nam_conus": "ncep_nam_conus",
}


@dataclass(frozen=True)
class OpenMeteoModelUpdate:
    model: str
    last_run_initialisation_time: datetime
    last_run_availability_time: datetime
    last_run_modification_time: datetime | None = None
    update_interval_seconds: int | None = None
    temporal_resolution_seconds: int | None = None
    raw: Mapping[str, Any] | None = None

    def to_source_run_clock(self) -> SourceRunClock:
        return SourceRunClock(
            source_id=self.model,
            provider_family=provider_family_for_source(self.model),
            run_initialisation_time=self.last_run_initialisation_time,
            run_availability_time=self.last_run_availability_time,
            update_interval_seconds=self.update_interval_seconds,
            temporal_resolution_seconds=self.temporal_resolution_seconds,
            api_surface="openmeteo_model_updates",
            freshness_state="FRESH",
        )

    def to_json_row(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "last_run_initialisation_time",
            "last_run_availability_time",
            "last_run_modification_time",
        ):
            value = payload.get(key)
            if isinstance(value, datetime):
                payload[key] = value.isoformat()
        return payload


def _coerce_utc(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(float(value), tz=UTC)
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():
            parsed = datetime.fromtimestamp(float(text), tz=UTC)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} is required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _coerce_optional_utc(value: Any, *, field_name: str) -> datetime | None:
    if value is None or value == "":
        return None
    return _coerce_utc(value, field_name=field_name)


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return None
    return out if out >= 0 else None


def parse_model_update(model: str, payload: Mapping[str, Any]) -> OpenMeteoModelUpdate:
    return OpenMeteoModelUpdate(
        model=str(payload.get("model") or payload.get("model_id") or model).strip(),
        last_run_initialisation_time=_coerce_utc(
            payload.get("last_run_initialisation_time")
            or payload.get("run_initialisation_time"),
            field_name="last_run_initialisation_time",
        ),
        last_run_availability_time=_coerce_utc(
            payload.get("last_run_availability_time")
            or payload.get("run_availability_time")
            or payload.get("availability_time"),
            field_name="last_run_availability_time",
        ),
        last_run_modification_time=_coerce_optional_utc(
            payload.get("last_run_modification_time"),
            field_name="last_run_modification_time",
        ),
        update_interval_seconds=_coerce_int(payload.get("update_interval_seconds")),
        temporal_resolution_seconds=_coerce_int(payload.get("temporal_resolution_seconds")),
        raw=dict(payload),
    )


def parse_model_updates_payload(payload: Any) -> tuple[OpenMeteoModelUpdate, ...]:
    rows: list[OpenMeteoModelUpdate] = []
    if isinstance(payload, Mapping):
        if "models" in payload and isinstance(payload["models"], Sequence):
            for item in payload["models"]:
                if isinstance(item, Mapping):
                    model = str(item.get("model") or item.get("model_id") or "")
                    if model:
                        rows.append(parse_model_update(model, item))
        elif "data" in payload and isinstance(payload["data"], Sequence):
            for item in payload["data"]:
                if isinstance(item, Mapping):
                    model = str(item.get("model") or item.get("model_id") or "")
                    if model:
                        rows.append(parse_model_update(model, item))
        else:
            model = str(payload.get("model") or payload.get("model_id") or "").strip()
            if model:
                rows.append(parse_model_update(model, payload))
            else:
                for key, value in payload.items():
                    if isinstance(value, Mapping):
                        try:
                            rows.append(parse_model_update(str(key), value))
                        except ValueError:
                            continue
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for item in payload:
            if isinstance(item, Mapping):
                model = str(item.get("model") or item.get("model_id") or "")
                if model:
                    rows.append(parse_model_update(model, item))
    return tuple(rows)


def _endpoint_url(base_url: str, models: Sequence[str]) -> str:
    clean = [str(model).strip() for model in models if str(model).strip()]
    if not clean:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{urlencode({'models': ','.join(clean)})}"


def metadata_model_id(model: str) -> str:
    clean = str(model).strip()
    return OPENMETEO_MODEL_METADATA_IDS.get(clean, clean)


def _metadata_url(template_url: str, model: str) -> str:
    return template_url.format(model=metadata_model_id(model))


def _model_update_worker_count(
    models: Sequence[str],
    *,
    configured_workers: int | None,
    session: requests.Session | None,
) -> int:
    clean_count = len([model for model in models if str(model).strip()])
    if clean_count <= 1:
        return 1
    if configured_workers is not None:
        return max(1, min(int(configured_workers), clean_count))
    if session is not None:
        return 1
    try:
        env_workers = int(os.environ.get(ENV_MODEL_UPDATES_MAX_WORKERS, ""))
    except ValueError:
        env_workers = DEFAULT_MODEL_UPDATES_MAX_WORKERS
    if env_workers <= 0:
        env_workers = DEFAULT_MODEL_UPDATES_MAX_WORKERS
    return max(1, min(env_workers, clean_count))


def fetch_model_updates(
    models: Sequence[str],
    *,
    endpoint_url: str | None = None,
    timeout_seconds: float = 30.0,
    session: requests.Session | None = None,
    max_workers: int | None = None,
) -> tuple[OpenMeteoModelUpdate, ...]:
    base = endpoint_url or os.environ.get(ENV_MODEL_UPDATES_ENDPOINT) or DEFAULT_MODEL_UPDATES_ENDPOINT
    client = session or requests
    if "{model}" in base:
        clean_models = tuple(str(model).strip() for model in models if str(model).strip())

        def _fetch_one(clean_model: str) -> OpenMeteoModelUpdate:
            response = client.get(_metadata_url(base, clean_model), timeout=timeout_seconds)
            response.raise_for_status()
            return parse_model_update(clean_model, response.json())

        workers = _model_update_worker_count(
            clean_models,
            configured_workers=max_workers,
            session=session,
        )
        if workers <= 1:
            return tuple(_fetch_one(model) for model in clean_models)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return tuple(executor.map(_fetch_one, clean_models))

    url = _endpoint_url(base, models)
    response = client.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    return parse_model_updates_payload(response.json())


def write_model_updates_jsonl(path: str | Path, updates: Sequence[OpenMeteoModelUpdate]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for update in updates:
            fh.write(json.dumps(update.to_json_row(), sort_keys=True, default=str) + "\n")


def read_model_updates_jsonl(path: str | Path) -> tuple[OpenMeteoModelUpdate, ...]:
    in_path = Path(path)
    rows: list[OpenMeteoModelUpdate] = []
    if not in_path.exists():
        return ()
    with in_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, Mapping):
                rows.append(parse_model_update(str(payload.get("model") or ""), payload))
    return tuple(rows)
