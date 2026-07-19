"""Source-clock update probe for live replacement downloads."""

from __future__ import annotations

import hashlib
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
from src.events.event_writer import EventWriter
from src.events.opportunity_event import SourceRunArrivedPayload, make_source_run_arrived_event
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
_SOURCE_CURSOR_COMMIT_STATUSES = frozenset(
    {
        "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED",
        "SOURCE_CLOCK_SOURCE_NO_TARGETS",
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
    emitted_event_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "model_count": self.model_count,
            "updated_sources": list(self.updated_sources),
            "affected_cities": list(self.affected_cities),
            "model_updates_path": self.model_updates_path,
            "cursor_path": self.cursor_path,
            "error": self.error,
            "emitted_event_ids": list(self.emitted_event_ids),
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


def _source_route_identity(model: str) -> str:
    cities = affected_cities_for_source_updates((model,))
    payload = "\0".join(cities).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cursor_for_updates(updates: tuple[OpenMeteoModelUpdate, ...]) -> dict[str, str]:
    return {
        update.model: (
            f"v2:{update.last_run_availability_time.isoformat()}:"
            f"{_source_route_identity(update.model)}"
        )
        for update in updates
    }


def probe_openmeteo_source_clock_updates(
    *,
    model_updates_path: str | Path = DEFAULT_MODEL_UPDATES_JSONL,
    cursor_path: str | Path = DEFAULT_CURSOR_JSON,
    endpoint_url: str | None = None,
    use_network: bool = True,
    advance_cursor: bool = True,
    event_writer: EventWriter | None = None,
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
    emitted_event_ids: tuple[str, ...] = ()
    if usable_changed and event_writer is not None:
        emitted_event_ids = _emit_source_run_arrived_events(
            usable_changed,
            update_by_model=update_by_model,
            event_writer=event_writer,
            received_at=now,
        )
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
        emitted_event_ids=emitted_event_ids,
    )


def _emit_source_run_arrived_events(
    usable_changed: list[str],
    *,
    update_by_model: Mapping[str, OpenMeteoModelUpdate],
    event_writer: EventWriter,
    received_at: datetime,
) -> tuple[str, ...]:
    """Emit one SOURCE_RUN_ARRIVED event per newly-usable model run.

    ``available_at`` is the run's own publicly-usable time (not this call's wall
    clock), and ``entity_key`` encodes the run's own cycle time. Re-polling the SAME
    undelivered run (this function can be called again before the cursor commits —
    see ``advance_cursor=False`` callers) reproduces an identical idempotency key, so
    ``EventWriter.write`` no-ops on the replay instead of stacking duplicate rows.
    """

    event_ids: list[str] = []
    for model in usable_changed:
        update = update_by_model[model]
        run = update.to_source_run_clock()
        source_cycle_time = update.last_run_initialisation_time.astimezone(UTC).isoformat()
        detected_at = source_publicly_usable_at(run).isoformat()
        payload = SourceRunArrivedPayload(
            source=model,
            affected_cities=list(affected_cities_for_source_updates([model])),
            source_cycle_time=source_cycle_time,
            detected_at=detected_at,
        )
        event = make_source_run_arrived_event(
            entity_key=f"{model}|{source_cycle_time}",
            source=model,
            observed_at=source_cycle_time,
            available_at=detected_at,
            received_at=received_at.isoformat(),
            payload=payload,
        )
        result = event_writer.write(event)
        event_ids.append(result.event_id)
    return tuple(event_ids)


def source_clock_scoped_download_allows_cursor_advance(report: Mapping[str, object] | None) -> bool:
    if not isinstance(report, Mapping):
        return False
    return str(report.get("status") or "") in _DOWNLOAD_CURSOR_COMMIT_STATUSES


def source_clock_scoped_download_cursor_sources(
    report: Mapping[str, object] | None,
) -> tuple[str, ...]:
    if not isinstance(report, Mapping):
        return ()
    source_results = report.get("source_results")
    if isinstance(source_results, Mapping):
        return tuple(
            sorted(
                str(source)
                for source, result in source_results.items()
                if isinstance(result, Mapping)
                and str(result.get("status") or "") in _SOURCE_CURSOR_COMMIT_STATUSES
            )
        )
    if not source_clock_scoped_download_allows_cursor_advance(report):
        return ()
    return tuple(
        sorted(
            str(source)
            for source in (
                report.get("updated_sources")
                or report.get("source_clock_updated_sources")
                or ()
            )
            if str(source)
        )
    )


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
