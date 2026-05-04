# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md Phase B1 atomic JSON I/O for EntryForecastPromotionEvidence.
"""Atomic JSON read/write for ``EntryForecastPromotionEvidence``.

DAEMON ACTIVATION: NOT YET WIRED. This module is importable but is not
imported from any daemon hot-path file (``src/main.py``,
``src/ingest_main.py``, ``src/engine/*``, ``src/execution/*``,
``src/state/db.py`` runtime callers, ``scripts/healthcheck.py``
``result["healthy"]`` predicate). Phase C will register a single import
site behind an operator-controlled feature flag. See
``docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md``.

The promotion evidence carries the operator approval, G1 attestation,
calibration promotion approval, and canary-success attestation that
``evaluate_entry_forecast_rollout_gate`` requires before authorizing
canary or live entry-forecast orders. Storage on disk so an operator
script can populate it atomically without taking the daemon down.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
from src.data.live_entry_status import LiveEntryForecastStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMOTION_EVIDENCE_PATH = PROJECT_ROOT / "state" / "entry_forecast_promotion_evidence.json"

PROMOTION_EVIDENCE_SCHEMA_VERSION = 1


class PromotionEvidenceCorruption(ValueError):
    """Raised when the on-disk promotion evidence file fails strict parsing.

    The caller is expected to treat this as ``EVIDENCE_MISSING`` for
    rollout-gate purposes — never as ``EVIDENCE_PRESENT_AND_VALID``.
    """


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _serialize_status(status: LiveEntryForecastStatus) -> dict[str, Any]:
    return status.to_dict()


def _deserialize_status(raw: object) -> LiveEntryForecastStatus:
    if not isinstance(raw, dict):
        raise PromotionEvidenceCorruption(
            "status_snapshot must be a dict; got " + type(raw).__name__
        )
    required = {
        "status",
        "blockers",
        "executable_row_count",
        "producer_readiness_count",
        "producer_live_eligible_count",
    }
    missing = required - set(raw)
    if missing:
        raise PromotionEvidenceCorruption(
            "status_snapshot missing fields: " + ", ".join(sorted(missing))
        )
    if not isinstance(raw["status"], str) or not raw["status"]:
        raise PromotionEvidenceCorruption("status_snapshot.status must be non-empty string")
    blockers = raw["blockers"]
    if not isinstance(blockers, list) or not all(isinstance(b, str) for b in blockers):
        raise PromotionEvidenceCorruption("status_snapshot.blockers must be list[str]")
    for field in ("executable_row_count", "producer_readiness_count", "producer_live_eligible_count"):
        if not isinstance(raw[field], int) or isinstance(raw[field], bool):
            raise PromotionEvidenceCorruption(f"status_snapshot.{field} must be int")
    return LiveEntryForecastStatus(
        status=raw["status"],
        blockers=tuple(blockers),
        executable_row_count=raw["executable_row_count"],
        producer_readiness_count=raw["producer_readiness_count"],
        producer_live_eligible_count=raw["producer_live_eligible_count"],
    )


def write_promotion_evidence(
    evidence: EntryForecastPromotionEvidence,
    *,
    path: Path | None = None,
) -> None:
    target = path or DEFAULT_PROMOTION_EVIDENCE_PATH
    payload = {
        "schema_version": PROMOTION_EVIDENCE_SCHEMA_VERSION,
        "operator_approval_id": evidence.operator_approval_id,
        "g1_evidence_id": evidence.g1_evidence_id,
        "calibration_promotion_approved": evidence.calibration_promotion_approved,
        "canary_success_evidence_id": evidence.canary_success_evidence_id,
        "status_snapshot": _serialize_status(evidence.status_snapshot),
    }
    _atomic_write_json(target, payload)


def read_promotion_evidence(
    *,
    path: Path | None = None,
) -> EntryForecastPromotionEvidence | None:
    """Return parsed promotion evidence, or ``None`` if the file is absent.

    Strict parsing: any structural defect raises
    :class:`PromotionEvidenceCorruption`. Callers must treat corruption
    as ``EVIDENCE_MISSING`` for rollout-gate purposes — never silently
    accept a malformed payload as valid evidence.
    """

    target = path or DEFAULT_PROMOTION_EVIDENCE_PATH
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise PromotionEvidenceCorruption(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise PromotionEvidenceCorruption("payload root must be object")
    schema = raw.get("schema_version")
    if schema != PROMOTION_EVIDENCE_SCHEMA_VERSION:
        raise PromotionEvidenceCorruption(
            f"unsupported schema_version={schema!r}; "
            f"expected {PROMOTION_EVIDENCE_SCHEMA_VERSION}"
        )
    for nullable in ("operator_approval_id", "g1_evidence_id", "canary_success_evidence_id"):
        value = raw.get(nullable)
        if value is not None and not isinstance(value, str):
            raise PromotionEvidenceCorruption(f"{nullable} must be string or null")
    approved = raw.get("calibration_promotion_approved")
    if not isinstance(approved, bool):
        raise PromotionEvidenceCorruption(
            "calibration_promotion_approved must be bool (no truthy coercion)"
        )
    return EntryForecastPromotionEvidence(
        operator_approval_id=raw.get("operator_approval_id"),
        g1_evidence_id=raw.get("g1_evidence_id"),
        status_snapshot=_deserialize_status(raw.get("status_snapshot")),
        calibration_promotion_approved=approved,
        canary_success_evidence_id=raw.get("canary_success_evidence_id"),
    )


def evidence_to_dict(evidence: EntryForecastPromotionEvidence) -> dict[str, Any]:
    """Serialize for log/audit purposes only — not the on-disk format."""

    payload = asdict(evidence)
    payload["status_snapshot"] = evidence.status_snapshot.to_dict()
    return payload
