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
    ) -> None:
        self._api_key = api_key
        self._root = root
        self._environ = environ
        self._payload_path = Path(payload_path) if payload_path is not None else None
        self._payload_fetcher = payload_fetcher

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
        if payload_path is None:
            raise TIGGEIngestFetchNotConfigured(
                "TIGGE gate is open but no operator-approved payload is configured. "
                f"Set {PAYLOAD_PATH_ENV}=<json path> or add `payload_path: <json path>` "
                "to the tigge_ingest_decision evidence artifact. This loader reads "
                "local JSON only; it does not perform live TIGGE archive HTTP/GRIB I/O."
            )
        return _load_json_payload(payload_path)

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
