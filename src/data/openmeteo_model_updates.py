"""Open-Meteo model-update metadata client.

Open-Meteo's model-update metadata exposes each model's run initialisation time,
API availability time, update interval, and temporal resolution. The trading
clock uses ``last_run_availability_time + 10 minutes`` as the public-availability
boundary; callers may inject or configure the endpoint because Open-Meteo serves
the metadata links from the model-updates page rather than a static endpoint in
the repository.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from src.data.openmeteo_client import fetch as _fetch_openmeteo
from src.data.openmeteo_quota import quota_tracker
from src.strategy.live_inference.source_clock_vnext import (
    SourceRunClock,
    provider_family_for_source,
)


ENV_MODEL_UPDATES_ENDPOINT = "ZEUS_OPENMETEO_MODEL_UPDATES_URL"
ENV_MODEL_UPDATES_MAX_WORKERS = "ZEUS_OPENMETEO_MODEL_UPDATES_MAX_WORKERS"
DEFAULT_MODEL_UPDATES_ENDPOINT = "https://api.open-meteo.com/data/{model}/static/meta.json"
DEFAULT_MODEL_UPDATES_MAX_WORKERS = 8
NATIVE_METADATA_ENTITY_KEY = "_native_http_entity"

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
        native = (payload.get("raw") or {}).get(NATIVE_METADATA_ENTITY_KEY)
        if isinstance(native, dict) and native.get("status") == "CAPTURED":
            native["recorded_at"] = datetime.now(UTC).isoformat()
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


def _official_metadata_api_url(url: str) -> bool:
    """Return whether Open-Meteo documents this URL as unmetered metadata."""

    parsed = urlsplit(url)
    parts = tuple(part for part in parsed.path.split("/") if part)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.open-meteo.com"
        and len(parts) == 4
        and parts[0] == "data"
        and bool(parts[1])
        and parts[2:] == ("static", "meta.json")
    )


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
    priority: bool = False,
    capture_metadata_response: Callable[[str, dict, bytes, float, Mapping[str, str]], None] | None = None,
) -> tuple[OpenMeteoModelUpdate, ...]:
    def _fetch_metadata(url: str, **kwargs: object) -> tuple[object, dict[str, object]]:
        # Transport callbacks supply original entity bytes and possession clocks.
        # Parsed payload fields and JSONL caches never manufacture native bytes.
        entities: list[tuple[bytes, float]] = []
        responses: list[tuple[bytes, float, Mapping[str, str]]] = []
        kwargs["capture_entity_body"] = lambda body, at: entities.append((body, at))
        kwargs["capture_network_response"] = lambda body, at, headers: responses.append((body, at, headers))
        payload = _fetch_openmeteo(url, {}, **kwargs)
        native: dict[str, object] = {"status": "UNKNOWN", "reason": "native_capture_missing", "response_role": "UNKNOWN"}
        if entities or responses:
            try:
                body, fetched_at = entities[-1] if entities else responses[-1][:2]
                headers: Mapping[str, str] = {}
                if responses:
                    network_body, network_at, headers = responses[-1]
                    if network_body != body or network_at != fetched_at:
                        raise ValueError("metadata entity and network capture differ")
                if json.loads(body) != payload:
                    raise ValueError("metadata native body differs from parsed response")
                parts = urlsplit(url)
                query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                         if not any(secret in key.lower() for secret in ("key", "token", "auth", "password"))]
                clean_url = urlunsplit((parts.scheme, parts.netloc.split("@")[-1], parts.path, urlencode(query), ""))
                safe_headers = {str(key).lower(): str(value) for key, value in headers.items()
                    if str(key).lower() in {"date", "etag", "last-modified", "content-type"}}
                native = {"status": "CAPTURED", "revision": "openmeteo_model_metadata_entity_v1",
                    "body_base64": base64.b64encode(body).decode("ascii"),
                    "body_sha256": hashlib.sha256(body).hexdigest(), "byte_size": len(body),
                    "request_url": clean_url, "request_params": {},
                    "captured_at": datetime.fromtimestamp(fetched_at, UTC).isoformat(),
                    "clock_role": "LOCAL_HTTP_ENTITY_POSSESSION", "publisher_issue_time": "UNKNOWN",
                    "response_role": "NETWORK_200_ENTITY" if responses else "CACHE_ENTITY",
                    "origin_response_role": "NETWORK_200_ENTITY" if responses else "UNKNOWN",
                    "headers_status": "CAPTURED" if responses else "UNKNOWN",
                    "http_response_headers": safe_headers}
                if capture_metadata_response is not None and responses:
                    capture_metadata_response(clean_url, {}, body, fetched_at, safe_headers)
            except (OSError, TypeError, ValueError, OverflowError):
                native = {"status": "UNKNOWN", "reason": "native_capture_invalid", "response_role": "UNKNOWN"}
                logging.getLogger(__name__).warning("OPENMETEO_METADATA_CAPTURE_UNKNOWN", exc_info=True)
        return payload, native

    def _attach(update: OpenMeteoModelUpdate, native: Mapping[str, object]) -> OpenMeteoModelUpdate:
        return replace(update, raw={**dict(update.raw or {}), NATIVE_METADATA_ENTITY_KEY: dict(native)})

    base = endpoint_url or os.environ.get(ENV_MODEL_UPDATES_ENDPOINT) or DEFAULT_MODEL_UPDATES_ENDPOINT
    if "{model}" in base:
        clean_models = tuple(str(model).strip() for model in models if str(model).strip())

        def _fetch_one(clean_model: str) -> OpenMeteoModelUpdate:
            url = _metadata_url(base, clean_model)
            quota_lane = quota_tracker.priority_lane() if priority else nullcontext()
            with quota_lane:
                payload, native = _fetch_metadata(
                    url,
                    timeout=timeout_seconds,
                    max_retries=1,
                    endpoint_label=f"source_clock_model_meta_{clean_model}",
                    client=session,
                    count_toward_quota=not _official_metadata_api_url(url),
                )
            return _attach(parse_model_update(clean_model, payload), native)

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
    quota_lane = quota_tracker.priority_lane() if priority else nullcontext()
    with quota_lane:
        payload, native = _fetch_metadata(
            url,
            timeout=timeout_seconds,
            max_retries=1,
            endpoint_label="source_clock_model_meta_batch",
            client=session,
        )
    return tuple(_attach(update, native) for update in parse_model_updates_payload(payload))


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
                raw = payload.get("raw")
                while isinstance(raw, Mapping) and isinstance(raw.get("raw"), Mapping):
                    raw = raw["raw"]
                update = parse_model_update(str(payload.get("model") or ""), payload)
                if isinstance(raw, Mapping):
                    update = replace(update, raw=dict(raw))
                rows.append(update)
    return tuple(rows)
