# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F3.yaml
"""Dormant TIGGE ingest stub for R3 F3.

This module wires the TIGGE forecast-source class without performing external
TIGGE archive I/O. Construction is intentionally safe with the operator gate
closed. ``fetch()`` checks the dual gate before any payload loading; when the
gate is open it reads only an operator-approved local JSON payload configured
by constructor, environment, or decision artifact. Missing payload
configuration fails closed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.forecast_ingest_protocol import (
    ForecastBundle,
    ForecastSourceHealth,
)


SOURCE_ID = "tigge"
AUTHORITY_TIER = "FORECAST"
ENV_FLAG_NAME = "ZEUS_TIGGE_INGEST_ENABLED"
PAYLOAD_PATH_ENV = "ZEUS_TIGGE_PAYLOAD_PATH"


class TIGGEIngestNotEnabled(RuntimeError):
    """Raised when TIGGE fetch is attempted while the operator gate is closed."""


class TIGGEIngestFetchNotConfigured(RuntimeError):
    """Raised when the gate is open but no operator-approved payload is configured."""


PayloadFetcher = Callable[[datetime, tuple[int, ...]], object]


class TIGGEIngest:
    """ForecastIngestProtocol-compatible TIGGE adapter stub.

    F3 deliberately avoids real HTTP/GRIB implementation. Downstream tests and
    future packet-approved ingest code can inject ``payload_fetcher``; absent
    that, an open-gate fetch reads only an operator-approved local JSON payload
    and still fails closed rather than fabricating data when no payload is
    configured.
    """

    source_id = SOURCE_ID
    authority_tier = AUTHORITY_TIER

    def __init__(
        self,
        api_key: str | None = None,
        *,
        root: Path | None = None,
        environ: Mapping[str, str] | None = None,
        payload_path: str | Path | None = None,
        payload_fetcher: PayloadFetcher | None = None,
        city: object | None = None,
    ) -> None:
        self._api_key = api_key
        self._root = root
        self._environ = environ
        self._payload_path = Path(payload_path) if payload_path is not None else None
        self._payload_fetcher = payload_fetcher
        # Live-blockers 2026-05-01: when invoked from the trading-side
        # _fetch_registered_ingest_ensemble, the city is forwarded so this
        # adapter can read directly from ensemble_snapshots_v2 (populated by
        # the data-ingest daemon) instead of expecting a pre-staged JSON
        # payload. JSON-payload mode (env var or artifact-declared) still
        # wins when both are configured, preserving the original contract.
        self._city = city

    def fetch(
        self,
        run_init_utc: datetime,
        lead_hours: Sequence[int],
    ) -> ForecastBundle:
        """Return a source-stamped TIGGE bundle, or fail closed before I/O."""

        if not _operator_gate_open(root=self._root, environ=self._environ):
            raise TIGGEIngestNotEnabled(_gate_closed_message())

        payload = self._fetch_payload(run_init_utc, tuple(int(h) for h in lead_hours))
        if isinstance(payload, ForecastBundle):
            if payload.source_id != self.source_id:
                raise ValueError(
                    f"TIGGE payload returned source_id={payload.source_id!r}, "
                    f"expected {self.source_id!r}"
                )
            return payload

        from src.data.forecast_source_registry import stable_payload_hash

        members: Sequence[Any] = ()
        if isinstance(payload, Mapping):
            maybe_members = payload.get("ensemble_members", ())
            if isinstance(maybe_members, Sequence) and not isinstance(maybe_members, (str, bytes)):
                members = maybe_members

        return ForecastBundle(
            source_id=self.source_id,
            run_init_utc=run_init_utc,
            lead_hours=tuple(int(h) for h in lead_hours),
            captured_at=datetime.now(timezone.utc),
            raw_payload_hash=stable_payload_hash(payload),
            authority_tier=self.authority_tier,
            ensemble_members=tuple(members),
            raw_payload=payload,
        )

    def health_check(self) -> ForecastSourceHealth:
        """Report gate health without touching the external TIGGE archive."""

        ok = _operator_gate_open(root=self._root, environ=self._environ)
        return ForecastSourceHealth(
            source_id=self.source_id,
            ok=ok,
            checked_at=datetime.now(timezone.utc),
            message="TIGGE operator gate open" if ok else _gate_closed_message(),
        )

    def _fetch_payload(self, run_init_utc: datetime, lead_hours: tuple[int, ...]) -> object:
        if self._payload_fetcher is not None:
            return self._payload_fetcher(run_init_utc, lead_hours)
        payload_path = self._resolve_payload_path()
        if payload_path is not None:
            return _load_json_payload(payload_path)
        if self._city is not None:
            payload = _fetch_db_payload(self._city, run_init_utc)
            if payload is not None:
                return payload
        raise TIGGEIngestFetchNotConfigured(
            "TIGGE gate is open but no operator-approved payload is configured. "
            f"Set {PAYLOAD_PATH_ENV}=<json path> or add `payload_path: <json path>` "
            "to the tigge_ingest_decision evidence artifact, or ensure the "
            "data-ingest daemon has populated ensemble_snapshots_v2 with fresh "
            "tigge_* rows for the requested city."
        )

    def _resolve_payload_path(self) -> Path | None:
        root = self._root or _default_project_root()
        env = self._environ or os.environ
        candidates: list[str | Path] = []
        if self._payload_path is not None:
            candidates.append(self._payload_path)
        env_path = str(env.get(PAYLOAD_PATH_ENV, "")).strip()
        if env_path:
            candidates.append(env_path)
        artifact_path = _operator_payload_path_from_latest_artifact(root=root)
        if artifact_path is not None:
            candidates.append(artifact_path)
        for candidate in candidates:
            path = Path(candidate)
            if not path.is_absolute():
                path = root / path
            if path.exists() and path.is_file():
                return path
        return None


def _operator_gate_open(
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return True only when the registry's TIGGE dual gate is open."""

    from src.data.forecast_source_registry import SourceNotEnabled, gate_source

    try:
        gate_source(SOURCE_ID, root=root, environ=environ)
    except SourceNotEnabled:
        return False
    return True


def _gate_closed_message() -> str:
    from src.data.forecast_source_registry import get_source

    spec = get_source(SOURCE_ID)
    return (
        "TIGGE ingest is operator-gated. Required: operator decision artifact at "
        f"{spec.operator_decision_artifact} AND env var {spec.env_flag_name}=1"
    )


def _default_project_root() -> Path:
    from src.data.forecast_source_registry import PROJECT_ROOT

    return PROJECT_ROOT


def _operator_payload_path_from_latest_artifact(*, root: Path) -> str | None:
    from src.data.forecast_source_registry import get_source

    spec = get_source(SOURCE_ID)
    if not spec.operator_decision_artifact:
        return None
    artifacts = sorted(root.glob(spec.operator_decision_artifact))
    for artifact in reversed(artifacts):
        payload_path = _extract_payload_path(artifact.read_text(errors="ignore"))
        if payload_path:
            return payload_path
    return None


def _extract_payload_path(text: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for key in ("payload_path", "tigge_payload_path"):
            prefix = f"{key}:"
            if line.lower().startswith(prefix):
                value = line[len(prefix):].strip().strip("'\"")
                return value or None
    return None


def _fetch_db_payload(city: object, fetch_time: datetime) -> dict | None:
    """Combine high+low TIGGE rows into a 51 x (24*N) hourly grid.

    Sonnet's per-metric ``tigge_db_fetcher.fetch_from_db`` broadcasts the
    daily aggregate across 24 hours of a single metric, which makes the
    resulting array's ``min()`` equal to the max (or vice-versa) — wrong
    for the half of markets keyed on the OTHER metric. To preserve the
    invariant that ``EnsembleSignal.member_maxes_for_target_date()`` returns
    daily HIGH and ``.member_mins_for_target_date()`` returns daily LOW,
    we fetch BOTH metrics from the DB and assemble a symmetric grid:
    morning (00:00-11:00 local) holds the daily LOW per member, afternoon
    (12:00-23:00 local) holds the daily HIGH per member. Per-hour mean
    becomes ~midpoint(low, high), which is acceptable because the
    entry_primary path uses extrema, not means.
    """

    from src.data.tigge_db_fetcher import fetch_from_db

    high_bundle = fetch_from_db(city, "high", fetch_time)  # type: ignore[arg-type]
    low_bundle = fetch_from_db(city, "low", fetch_time)  # type: ignore[arg-type]
    if high_bundle is None and low_bundle is None:
        return None

    high_payload = high_bundle.raw_payload if high_bundle is not None else None
    low_payload = low_bundle.raw_payload if low_bundle is not None else None

    def _by_date(payload: object) -> dict[str, list[list[float]]]:
        if not isinstance(payload, Mapping):
            return {}
        members_hourly = payload.get("members_hourly")
        times = payload.get("times")
        if not isinstance(members_hourly, list) or not isinstance(times, list):
            return {}
        if not members_hourly or len(members_hourly[0]) != len(times):
            return {}
        n_members = len(members_hourly)
        out: dict[str, list[list[float]]] = {}
        for col_idx, ts in enumerate(times):
            date_str = str(ts).split("T", 1)[0]
            if date_str not in out:
                col = [members_hourly[m][col_idx] for m in range(n_members)]
                out[date_str] = [col]  # one column per date is enough — daily aggregate is broadcast
        return out

    high_by_date = _by_date(high_payload)
    low_by_date = _by_date(low_payload)
    all_dates = sorted(set(high_by_date.keys()) | set(low_by_date.keys()))
    if not all_dates:
        return None

    n_members = 51
    for src in (high_by_date, low_by_date):
        for cols in src.values():
            if cols and isinstance(cols[0], list):
                n_members = len(cols[0])
                break

    times: list[str] = []
    members_grid: list[list[float]] = [[] for _ in range(n_members)]
    for date_str in all_dates:
        high_col = high_by_date.get(date_str, [None])[0] if high_by_date.get(date_str) else None
        low_col = low_by_date.get(date_str, [None])[0] if low_by_date.get(date_str) else None
        if high_col is None and low_col is None:
            continue
        for hour in range(24):
            times.append(f"{date_str}T{hour:02d}:00:00+00:00")
            use_high = hour >= 12
            chosen = (high_col if use_high else low_col) or low_col or high_col
            for i in range(n_members):
                if chosen is not None and i < len(chosen):
                    members_grid[i].append(float(chosen[i]))
                else:
                    members_grid[i].append(float("nan"))

    if not times:
        return None

    issue_time_str = ""
    if isinstance(high_payload, Mapping):
        issue_time_str = str(high_payload.get("issue_time", "") or "")
    if not issue_time_str and isinstance(low_payload, Mapping):
        issue_time_str = str(low_payload.get("issue_time", "") or "")

    return {
        "source_id": SOURCE_ID,
        "times": times,
        "members_hourly": members_grid,
        "issue_time": issue_time_str,
        "synthesised_from": "ensemble_snapshots_v2.high+low",
    }


def _load_json_payload(path: Path) -> object:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise TIGGEIngestFetchNotConfigured(
            f"TIGGE operator payload is not valid JSON: {path}"
        ) from exc
    if isinstance(payload, Mapping) and payload.get("source_id") not in (None, SOURCE_ID):
        raise ValueError(
            f"TIGGE operator payload source_id={payload.get('source_id')!r}, expected {SOURCE_ID!r}"
        )
    if isinstance(payload, list):
        return {"ensemble_members": payload}
    return payload
