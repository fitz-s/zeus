"""Runtime truth-file helpers and legacy-state deprecation tooling."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_mode, legacy_state_path, runtime_state_path

logger = logging.getLogger(__name__)


class RuntimeStateMismatchError(ValueError):
    """Raised when a runtime truth file carries a non-live state tag."""


LEGACY_STATE_FILES = (
    "status_summary.json",
    "positions.json",
    "strategy_tracker.json",
    "platt_models_low.json",
    "calibration_pairs_low.json",
)
_LOW_LANE_FILES: frozenset[str] = frozenset(
    f for f in LEGACY_STATE_FILES if "platt_models_low" in f or "calibration_pairs_low" in f
)
LEGACY_ARCHIVE_DIR = legacy_state_path("legacy_state_archive")


def current_runtime_state() -> str:
    return get_mode()


def build_truth_metadata(
    path: Path,
    *,
    runtime_state: str | None = None,
    generated_at: str | None = None,
    deprecated: bool = False,
    archived_to: str | None = None,
    authority: str = "UNVERIFIED",
    temperature_metric: str | None = None,
    data_version: str | None = None,
) -> dict[str, Any]:
    resolved_runtime_state = runtime_state or current_runtime_state()
    if resolved_runtime_state != "live" and not deprecated:
        raise RuntimeStateMismatchError(
            f"runtime truth metadata must be live, got runtime_state={resolved_runtime_state!r}"
        )
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    # Fail-closed: low-lane files stamped VERIFIED without temperature_metric silently
    # misidentify the metric. Downgrade to UNVERIFIED to enforce explicit tagging.
    resolved_authority = authority
    if authority == "VERIFIED" and temperature_metric is None and Path(path).name in _LOW_LANE_FILES:
        resolved_authority = "UNVERIFIED"
    meta: dict[str, Any] = {
        "runtime_state": resolved_runtime_state,
        "generated_at": generated_at,
        "source_path": str(path),
        "stale_age_seconds": 0.0,
        "deprecated": deprecated,
        "archived_to": archived_to,
        "authority": resolved_authority,
    }
    if temperature_metric is not None:
        meta["temperature_metric"] = temperature_metric
    if data_version is not None:
        meta["data_version"] = data_version
    return meta


def annotate_truth_payload(
    payload: dict[str, Any],
    path: Path,
    *,
    runtime_state: str | None = None,
    generated_at: str | None = None,
    authority: str = "UNVERIFIED",
    temperature_metric: str | None = None,
    data_version: str | None = None,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["truth"] = build_truth_metadata(
        path,
        runtime_state=runtime_state,
        generated_at=generated_at,
        authority=authority,
        temperature_metric=temperature_metric,
        data_version=data_version,
    )
    return enriched


def _parse_generated_at(payload: dict[str, Any]) -> str | None:
    truth = payload.get("truth")
    if isinstance(truth, dict) and truth.get("generated_at"):
        return str(truth["generated_at"])
    for key in ("timestamp", "updated_at"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def read_truth_json(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data = json.loads(path.read_text())
    generated_at = _parse_generated_at(data)
    stale_age_seconds = None
    if generated_at:
        try:
            gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            stale_age_seconds = max(
                0.0,
                (datetime.now(timezone.utc) - gen_dt).total_seconds(),
            )
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            # B079 [critic amendment]: narrow the silent-None fallback.
            # fromisoformat raises ValueError on malformed strings;
            # .replace/str coercion on a non-str value raises
            # AttributeError/TypeError; timedelta arithmetic with a
            # pathological datetime can raise OverflowError. These are
            # all *data* defects and warrant the None-fallback. Any
            # other exception (NameError, ImportError, etc.) is a code
            # defect and must propagate per SD-B.
            logger.warning(
                "TRUTH_GENERATED_AT_UNPARSEABLE: path=%s generated_at=%r error=%s",
                path,
                generated_at,
                exc,
            )
            stale_age_seconds = None
    truth = dict(data.get("truth", {})) if isinstance(data.get("truth"), dict) else {}
    legacy_mode_tag = truth.pop("mode", None)
    if legacy_mode_tag not in (None, "live", "deprecated"):
        raise RuntimeStateMismatchError(
            f"runtime truth file {path} carries retired state selector tag {legacy_mode_tag!r}"
        )
    truth.setdefault("runtime_state", "deprecated" if legacy_mode_tag == "deprecated" else "live")
    truth.setdefault("source_path", str(path))
    truth.setdefault("generated_at", generated_at)
    truth["stale_age_seconds"] = stale_age_seconds
    return data, truth


def read_runtime_truth_json(filename: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = runtime_state_path(filename)
    data, truth = read_truth_json(path)
    runtime_state = truth.get("runtime_state")
    if runtime_state != "live":
        raise RuntimeStateMismatchError(
            f"runtime truth file {path} must be tagged runtime_state='live', got {runtime_state!r}"
        )
    return data, truth


def legacy_tombstone_payload(
    filename: str,
    *,
    archived_to: str | None = None,
) -> dict[str, Any]:
    legacy_path = legacy_state_path(filename)
    return {
        "error": (
            f"{filename} is deprecated and must not be used as current truth. "
            "Use the live runtime state file instead."
        ),
        "truth": {
            **build_truth_metadata(
                legacy_path,
                runtime_state="deprecated",
                deprecated=True,
                archived_to=archived_to,
            ),
            "replacement_path": str(runtime_state_path(filename)),
        },
    }


def ensure_legacy_state_tombstone(filename: str) -> dict[str, Any]:
    path = legacy_state_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    archived_to = None

    if path.exists():
        try:
            current = json.loads(path.read_text())
        except Exception:
            current = None
        if isinstance(current, dict) and current.get("truth", {}).get("deprecated") is True:
            return {"path": str(path), "archived": False, "already_tombstoned": True}

        LEGACY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived_path = LEGACY_ARCHIVE_DIR / f"{filename}.{stamp}"
        os.replace(path, archived_path)
        archived_to = str(archived_path)

    path.write_text(json.dumps(legacy_tombstone_payload(filename, archived_to=archived_to), indent=2))
    return {"path": str(path), "archived": archived_to is not None, "archived_to": archived_to}


def deprecate_legacy_truth_files() -> list[dict[str, Any]]:
    return [ensure_legacy_state_tombstone(filename) for filename in LEGACY_STATE_FILES]


def backfill_runtime_truth_metadata(filename: str) -> dict[str, Any]:
    path = runtime_state_path(filename)
    if not path.exists():
        return {"path": str(path), "updated": False, "missing": True}

    data = json.loads(path.read_text())
    generated_at = _parse_generated_at(data)
    enriched = annotate_truth_payload(
        data,
        path,
        generated_at=generated_at,
    )
    path.write_text(json.dumps(enriched, indent=2))
    return {"path": str(path), "updated": True, "missing": False}


def backfill_truth_metadata() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for filename in LEGACY_STATE_FILES:
        reports.append(backfill_runtime_truth_metadata(filename))
    return reports
