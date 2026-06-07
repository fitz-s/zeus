"""Plan and optionally install replacement forecast refit handoff artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE
from src.data.replacement_forecast_refit_handoff import refit_decision_from_handoff_payload


@dataclass(frozen=True)
class ReplacementForecastRefitHandoffInstallPlan:
    status: str
    reason_codes: tuple[str, ...]
    source_path: str
    target_path: str
    source_sha256: str | None
    target_sha256: str | None
    target_exists: bool
    same_content: bool
    write_requested: bool
    wrote_target: bool
    live_root_written: bool

    @property
    def ready(self) -> bool:
        return self.status in {"REFIT_HANDOFF_INSTALL_READY", "REFIT_HANDOFF_INSTALLED"}

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "source_path": self.source_path,
            "target_path": self.target_path,
            "source_sha256": self.source_sha256,
            "target_sha256": self.target_sha256,
            "target_exists": self.target_exists,
            "same_content": self.same_content,
            "write_requested": self.write_requested,
            "wrote_target": self.wrote_target,
            "live_root_written": self.live_root_written,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_handoff_payload(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("refit handoff artifact must decode to a JSON object")
    refit_decision_from_handoff_payload(payload)
    return payload


def plan_replacement_forecast_refit_handoff_install(
    *,
    live_root: Path | str,
    refit_handoff_json: Path | str,
    target_relative_path: str = REFIT_HANDOFF_FILE,
    write: bool = False,
) -> ReplacementForecastRefitHandoffInstallPlan:
    """Validate a handoff artifact and optionally place it under the live root."""

    root = Path(live_root)
    source = Path(refit_handoff_json)
    target = root / target_relative_path
    reasons: list[str] = []
    source_sha: str | None = None
    target_sha: str | None = None
    try:
        _read_handoff_payload(source)
        source_sha = _sha256(source)
    except FileNotFoundError:
        reasons.append("REPLACEMENT_REFIT_HANDOFF_INSTALL_SOURCE_MISSING")
    except Exception:
        reasons.append("REPLACEMENT_REFIT_HANDOFF_INSTALL_SOURCE_INVALID")

    target_exists = target.exists()
    if target_exists:
        try:
            target_sha = _sha256(target)
            _read_handoff_payload(target)
        except Exception:
            reasons.append("REPLACEMENT_REFIT_HANDOFF_INSTALL_TARGET_INVALID")
    same_content = bool(source_sha and target_sha and source_sha == target_sha)
    wrote_target = False
    live_root_written = False
    if write and not reasons and not same_content:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        wrote_target = True
        live_root_written = True
        target_exists = True
        target_sha = _sha256(target)
        same_content = source_sha == target_sha
    status = "REFIT_HANDOFF_INSTALL_BLOCKED"
    if not reasons:
        status = "REFIT_HANDOFF_INSTALLED" if wrote_target else "REFIT_HANDOFF_INSTALL_READY"
    return ReplacementForecastRefitHandoffInstallPlan(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or ["REPLACEMENT_REFIT_HANDOFF_INSTALL_READY"])),
        source_path=str(source),
        target_path=str(target),
        source_sha256=source_sha,
        target_sha256=target_sha,
        target_exists=target_exists,
        same_content=same_content,
        write_requested=write,
        wrote_target=wrote_target,
        live_root_written=live_root_written,
    )
