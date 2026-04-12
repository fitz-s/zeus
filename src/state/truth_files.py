"""Mode-aware truth-file helpers and legacy-state deprecation tooling."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_mode, legacy_state_path, mode_state_path


LEGACY_STATE_FILES = (
    "status_summary.json",
    "positions.json",
    "strategy_tracker.json",
)
LEGACY_ARCHIVE_DIR = legacy_state_path("legacy_state_archive")


def current_mode(mode: str | None = None) -> str:
    return mode or get_mode()


def build_truth_metadata(
    path: Path,
    *,
    mode: str | None = None,
    generated_at: str | None = None,
    deprecated: bool = False,
    archived_to: str | None = None,
) -> dict[str, Any]:
    mode = current_mode(mode)
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "mode": mode,
        "generated_at": generated_at,
        "source_path": str(path),
        "stale_age_seconds": 0.0,
        "deprecated": deprecated,
        "archived_to": archived_to,
    }


def infer_mode_from_path(path: Path) -> str | None:
    stem = path.stem
    if stem.endswith("-live"):
        return "live"
    return None


def annotate_truth_payload(
    payload: dict[str, Any],
    path: Path,
    *,
    mode: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["truth"] = build_truth_metadata(
        path,
        mode=mode,
        generated_at=generated_at,
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
        except Exception:
            stale_age_seconds = None
    truth = dict(data.get("truth", {})) if isinstance(data.get("truth"), dict) else {}
    truth.setdefault("mode", infer_mode_from_path(path))
    truth.setdefault("source_path", str(path))
    truth.setdefault("generated_at", generated_at)
    truth["stale_age_seconds"] = stale_age_seconds
    return data, truth


def read_mode_truth_json(filename: str, *, mode: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    return read_truth_json(mode_state_path(filename, current_mode(mode)))


def legacy_tombstone_payload(
    filename: str,
    *,
    archived_to: str | None = None,
) -> dict[str, Any]:
    legacy_path = legacy_state_path(filename)
    return {
        "error": (
            f"{filename} is deprecated and must not be used as current truth. "
            "Use the mode-suffixed state files instead."
        ),
        "truth": {
            **build_truth_metadata(
                legacy_path,
                mode="deprecated",
                deprecated=True,
                archived_to=archived_to,
            ),
            "replacement_paths": {
                "live": str(mode_state_path(filename, "live")),
            },
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


def backfill_mode_truth_metadata(filename: str, *, mode: str) -> dict[str, Any]:
    path = mode_state_path(filename, mode)
    if not path.exists():
        return {"path": str(path), "updated": False, "missing": True}

    data = json.loads(path.read_text())
    generated_at = _parse_generated_at(data)
    enriched = annotate_truth_payload(
        data,
        path,
        mode=mode,
        generated_at=generated_at,
    )
    path.write_text(json.dumps(enriched, indent=2))
    return {"path": str(path), "updated": True, "missing": False}


def backfill_truth_metadata_for_modes(modes: tuple[str, ...] = ("live",)) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for mode in modes:
        for filename in LEGACY_STATE_FILES:
            reports.append(backfill_mode_truth_metadata(filename, mode=mode))
    return reports
