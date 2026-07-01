"""Source-clock update probe for live replacement downloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from src.config import STATE_DIR
from src.data.openmeteo_model_updates import (
    OpenMeteoModelUpdate,
    fetch_model_updates,
    read_model_updates_jsonl,
    write_model_updates_jsonl,
)
from src.data.bayes_precision_fusion_download import (
    source_clock_metadata_run_is_single_runs_served,
)
from src.strategy.live_inference.source_clock_city_weights import (
    affected_cities_for_source_updates,
    all_configured_source_ids,
)
from src.strategy.live_inference.source_clock_vnext import source_publicly_usable_at


DEFAULT_MODEL_UPDATES_JSONL = STATE_DIR / "source_updates" / "open_meteo_model_updates.jsonl"
DEFAULT_CURSOR_JSON = STATE_DIR / "source_updates" / "open_meteo_model_updates_cursor.json"
_DOWNLOAD_CURSOR_COMMIT_STATUSES = frozenset(
    {
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
        "SOURCE_CLOCK_BPF_SCOPED_NO_AFFECTED_CITIES",
        "SOURCE_CLOCK_BPF_SCOPED_NO_TARGETS",
    }
)


@dataclass(frozen=True)
class SourceClockUpdateProbeReport:
    status: str
    model_count: int
    updated_sources: tuple[str, ...]
    affected_cities: tuple[str, ...]
    model_updates_path: str
    cursor_path: str
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "model_count": self.model_count,
            "updated_sources": list(self.updated_sources),
            "affected_cities": list(self.affected_cities),
            "model_updates_path": self.model_updates_path,
            "cursor_path": self.cursor_path,
            "error": self.error,
        }


def _read_cursor(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return {str(k): str(v) for k, v in payload.items()}


def _write_cursor(path: Path, cursor: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(cursor.items())), indent=2) + "\n", encoding="utf-8")


def _cursor_for_updates(updates: tuple[OpenMeteoModelUpdate, ...]) -> dict[str, str]:
    return {
        update.model: update.last_run_availability_time.isoformat()
        for update in updates
    }


def probe_openmeteo_source_clock_updates(
    *,
    model_updates_path: str | Path = DEFAULT_MODEL_UPDATES_JSONL,
    cursor_path: str | Path = DEFAULT_CURSOR_JSON,
    endpoint_url: str | None = None,
    use_network: bool = True,
    advance_cursor: bool = True,
) -> SourceClockUpdateProbeReport:
    models = all_configured_source_ids()
    updates_path = Path(model_updates_path)
    cursor = Path(cursor_path)
    updates: tuple[OpenMeteoModelUpdate, ...]
    try:
        if use_network:
            updates = fetch_model_updates(models, endpoint_url=endpoint_url)
            write_model_updates_jsonl(updates_path, updates)
        else:
            updates = read_model_updates_jsonl(updates_path)
    except Exception as exc:  # fail-soft: cached metadata may still be usable
        cached = read_model_updates_jsonl(updates_path)
        if not cached:
            return SourceClockUpdateProbeReport(
                status="SOURCE_CLOCK_MODEL_UPDATES_UNAVAILABLE",
                model_count=len(models),
                updated_sources=(),
                affected_cities=(),
                model_updates_path=str(updates_path),
                cursor_path=str(cursor),
                error=str(exc),
            )
        updates = cached
    old = _read_cursor(cursor)
    new = _cursor_for_updates(updates)
    changed = tuple(sorted(model for model, ts in new.items() if old.get(model) != ts))
    now = datetime.now(tz=UTC)
    usable_changed: list[str] = []
    update_by_model = {u.model: u for u in updates}
    for model in changed:
        run = update_by_model[model].to_source_run_clock()
        if now >= source_publicly_usable_at(run):
            init = update_by_model[model].last_run_initialisation_time.astimezone(UTC)
            if not source_clock_metadata_run_is_single_runs_served(model, init.hour):
                continue
            usable_changed.append(model)
    if usable_changed and advance_cursor:
        next_cursor = dict(old)
        for model in usable_changed:
            next_cursor[model] = new[model]
        _write_cursor(cursor, next_cursor)
    return SourceClockUpdateProbeReport(
        status=(
            "SOURCE_CLOCK_UPDATES_CHANGED"
            if usable_changed
            else "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE"
        ),
        model_count=len(models),
        updated_sources=tuple(sorted(usable_changed)),
        affected_cities=affected_cities_for_source_updates(usable_changed),
        model_updates_path=str(updates_path),
        cursor_path=str(cursor),
        error=None,
    )


def source_clock_scoped_download_allows_cursor_advance(report: Mapping[str, object] | None) -> bool:
    if not isinstance(report, Mapping):
        return False
    return str(report.get("status") or "") in _DOWNLOAD_CURSOR_COMMIT_STATUSES


def advance_source_clock_cursor(
    source_clock_report: SourceClockUpdateProbeReport | Mapping[str, object],
    *,
    sources: tuple[str, ...] | list[str] | None = None,
) -> tuple[str, ...]:
    payload = (
        source_clock_report.as_dict()
        if hasattr(source_clock_report, "as_dict")
        else dict(source_clock_report)
    )
    requested = tuple(
        str(source).strip()
        for source in (sources if sources is not None else payload.get("updated_sources") or ())
        if str(source).strip()
    )
    if not requested:
        return ()
    updates_path = Path(str(payload.get("model_updates_path") or DEFAULT_MODEL_UPDATES_JSONL))
    cursor_path = Path(str(payload.get("cursor_path") or DEFAULT_CURSOR_JSON))
    updates = read_model_updates_jsonl(updates_path)
    next_by_model = _cursor_for_updates(updates)
    old = _read_cursor(cursor_path)
    committed: list[str] = []
    for model in requested:
        ts = next_by_model.get(model)
        if ts is None:
            continue
        old[model] = ts
        committed.append(model)
    if committed:
        _write_cursor(cursor_path, old)
    return tuple(sorted(committed))
