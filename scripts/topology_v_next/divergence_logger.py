# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md
#                  §1.1 (public API), §4 (schema), §4.3 (concurrency contract),
#                  §4.4 (OLD_STATUS_TO_NEW_SEVERITY), §4.5 (classify_divergence),
#                  §9.1 (datetime.now(UTC) — NOT utcnow(), deprecated since 3.12)
"""
Concurrency-safe append-only JSONL writer for per-call divergence records.

Public API (SCAFFOLD §1.1):
    DivergenceRecord  -- frozen dataclass, all §4.1 schema fields
    log_divergence(record, *, root="evidence/topology_v_next_shadow") -> None
    compute_event_type(*, old_status, new_severity, companion_skip_used) -> str
    classify_divergence(record: DivergenceRecord) -> str
    daily_path(*, root=..., today=None) -> Path
    OLD_STATUS_TO_NEW_SEVERITY  -- dict mapping 6 old statuses to Severity

Concurrency: O_APPEND + O_CREAT, single os.write per record. Multi-process-safe
on POSIX (SCAFFOLD §4.3). Never raises on I/O failure — stderr + continue.

Codex-importable: stdlib + .dataclasses (P1 types) only. No anthropic SDK.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, UTC
from pathlib import Path
from typing import Any

from scripts.topology_v_next.dataclasses import Severity


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1"

# Per SCAFFOLD §4.2 — single-syscall write cap.
# Records exceeding this byte length trigger truncation at the files field.
_MAX_RECORD_BYTES = 8 * 1024  # 8 KiB

# Per SCAFFOLD §4.4 — maps old run_navigation admission status strings to
# Severity equivalents. HARD_STOP is intentionally absent: current admission
# has no kernel concept, so every v_next HARD_STOP is automatic
# DISAGREE_HARD_STOP.  This absence is load-bearing — do not add a mapping.
OLD_STATUS_TO_NEW_SEVERITY: dict[str, Severity] = {
    "admitted":                  Severity.ADMIT,       # green
    "advisory_only":             Severity.ADVISORY,    # green-with-conditions; UNIVERSAL §11
    "blocked":                   Severity.SOFT_BLOCK,  # current returns blocked → v_next SOFT_BLOCK
    "scope_expansion_required":  Severity.SOFT_BLOCK,  # composition conflict equivalent
    "route_contract_conflict":   Severity.SOFT_BLOCK,  # contract violation = soft block
    "ambiguous":                 Severity.SOFT_BLOCK,  # caller-supplied disambiguation needed
    # HARD_STOP has no current equivalent — current admission has no kernel concept;
    # any v_next HARD_STOP is automatic DISAGREE_HARD_STOP (escalated severity)
}


# ---------------------------------------------------------------------------
# DivergenceRecord — per-call schema (SCAFFOLD §4.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DivergenceRecord:
    """
    Frozen dataclass capturing one shadow-call divergence observation.

    All fields per SCAFFOLD §4.1. Serialised to a single JSONL line by
    _serialize_record(); stored in evidence/topology_v_next_shadow/.
    """
    # Temporal
    ts: str                                  # ISO-8601 UTC ms-precision, Z suffix
    schema_version: str                      # literal "1" for P3; bump on breaking change

    # Classification
    event_type: str                          # "divergence_observation"|"companion_skip_honored"|"agree"
    agreement_class: str                     # AGREE|DISAGREE_*|SKIP_HONORED|ERROR

    # Old-admission side
    profile_resolved_old: str | None         # old admission's resolved profile_id; None if absent
    old_admit_status: str                    # one of the 6 canonical old-side status strings

    # New (v_next) side
    profile_resolved_new: str | None         # v_next.admit's profile_matched; None on exception
    new_admit_severity: str | None           # ADMIT|ADVISORY|SOFT_BLOCK|HARD_STOP; None on exception
    new_admit_ok: bool | None                # AdmissionDecision.ok; None on exception

    # Intent
    intent_typed: str                        # validated Intent enum value (post intent_resolver)
    intent_supplied: str | None              # raw caller-supplied intent string; None if not supplied

    # Files
    files: tuple[str, ...]                   # verbatim copy of files argument

    # P2 carry-forward
    missing_companion: tuple[str, ...]       # MISSING_COMPANION issue paths; empty if none
    companion_skip_used: bool                # True iff v_next emitted companion_skip_token_used

    # Diagnostics
    friction_pattern_hit: str | None         # FrictionPattern enum value if detected; else None
    closest_rejected_profile: str | None     # carried from AdmissionDecision
    kernel_alert_count: int                  # len(AdmissionDecision.kernel_alerts)
    friction_budget_used: int                # AdmissionDecision.friction_budget_used

    # Grouping / audit
    task_hash: str                           # sha256(task)[:16] — for grouping; NEVER routing

    # Error
    error: str | None                        # None on success; exception type+msg on failure


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_path(root: Path | str, today: date | None) -> Path:
    """Return the daily JSONL path for the given date (UTC today by default)."""
    if today is None:
        today = datetime.now(UTC).date()
    root = Path(root)
    return root / f"divergence_{today.isoformat()}.jsonl"


def _serialize_record(record: DivergenceRecord) -> str:
    """
    Produce a single-line JSON string for *record*.

    Serialises to canonical dict, sorts keys deterministically, and asserts
    that no embedded newline has crept into any field value.  The trailing
    newline terminator is added here so callers can just encode + write.
    """
    raw: dict[str, Any] = asdict(record)
    # asdict converts tuples to lists — JSON arrays are fine.
    line = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    # Guard: assert no embedded newline (would break JSONL line-per-record contract)
    if "\n" in line:
        # Sanitise rather than raise — caller must not fail admission.
        line = line.replace("\n", "\\n")
    return line + "\n"


def _maybe_truncate(record: DivergenceRecord) -> DivergenceRecord:
    """
    Return *record* unchanged unless its serialised size exceeds 8 KiB.

    On overflow, replaces the files list with ["__TRUNCATED__"] and sets
    error to "record_size_exceeded" (per SCAFFOLD §4.2).
    """
    candidate = _serialize_record(record)
    if len(candidate.encode("utf-8")) <= _MAX_RECORD_BYTES:
        return record

    # Rebuild with truncated files + error flag.
    # Can't mutate frozen dataclass; use object.__setattr__ workaround by
    # replacing via dataclass replace pattern.
    import dataclasses
    return dataclasses.replace(
        record,
        files=("__TRUNCATED__",),
        error=record.error or "record_size_exceeded",
    )


# ---------------------------------------------------------------------------
# Public: compute_event_type
# ---------------------------------------------------------------------------

def compute_event_type(
    *,
    old_status: str,
    new_severity: Severity | str,
    companion_skip_used: bool,
) -> str:
    """
    Classify into one of three event_type strings (SCAFFOLD §1.1, §4.1).

    Returns
    -------
    "companion_skip_honored"   when companion_skip_used is True
    "agree"                    when old and new severity map to the same tier
    "divergence_observation"   otherwise
    """
    if companion_skip_used:
        return "companion_skip_honored"

    if isinstance(new_severity, str):
        try:
            new_severity = Severity(new_severity)
        except ValueError:
            return "divergence_observation"

    mapped = OLD_STATUS_TO_NEW_SEVERITY.get(old_status)
    if mapped is None:
        # old_status unknown or HARD_STOP sentinel — always a divergence
        return "divergence_observation"

    if mapped == new_severity:
        return "agree"
    return "divergence_observation"


# ---------------------------------------------------------------------------
# Public: classify_divergence
# ---------------------------------------------------------------------------

def classify_divergence(record: DivergenceRecord) -> str:
    """
    Return the agreement class for *record* (SCAFFOLD §4.5).

    Possible return values:
        AGREE                 old and new admit at same severity tier, same profile
        DISAGREE_SEVERITY     severity mismatch (new non-HARD_STOP)
        DISAGREE_PROFILE      severity agrees but profile_matched differs
        DISAGREE_COMPANION    MISSING_COMPANION caught by v_next; old side missed it
        DISAGREE_HARD_STOP    v_next escalated to HARD_STOP (current has no equivalent)
        SKIP_HONORED          companion skip token was used; excluded from agreement-%
        ERROR                 v_next raised an exception; excluded from agreement-%
    """
    # Defensive guard: error envelope may have None severity (v_next raised before
    # severity was set).  ERROR records are excluded from the agreement-% denominator
    # by the analyzer.
    if record.new_admit_severity is None or record.error is not None:
        return "ERROR"

    old_severity_equiv = OLD_STATUS_TO_NEW_SEVERITY.get(record.old_admit_status)
    if old_severity_equiv is None:
        # Unknown old_admit_status — treat as disagree to avoid false AGREE.
        return "DISAGREE_SEVERITY"

    try:
        new_severity = Severity(record.new_admit_severity)
    except ValueError:
        return "DISAGREE_SEVERITY"

    # SKIP_HONORED is a P2-integration case: v_next emitted companion_skip_token_used.
    # Per §6 below, these are excluded from agreement-% denominator.
    if record.companion_skip_used:
        return "SKIP_HONORED"

    # Hard escalation — v_next added a HARD_STOP the old side cannot express
    if new_severity == Severity.HARD_STOP:
        return "DISAGREE_HARD_STOP"

    # MISSING_COMPANION (P2.a) — old side has no companion check
    if record.missing_companion and old_severity_equiv == Severity.ADMIT:
        return "DISAGREE_COMPANION"  # v_next caught a P2 drift the old side missed

    # Severity mismatch
    if old_severity_equiv != new_severity:
        return "DISAGREE_SEVERITY"

    # Profile mismatch (severities agree)
    if record.profile_resolved_old != record.profile_resolved_new:
        return "DISAGREE_PROFILE"

    # Intent was normalised; informational only — counts as AGREE if severity+profile match
    # (per §4.5: intent mismatch is a pass-through, not a separate class)

    return "AGREE"


# ---------------------------------------------------------------------------
# Public: daily_path
# ---------------------------------------------------------------------------

def daily_path(
    *,
    root: Path | str = "evidence/topology_v_next_shadow",
    today: date | None = None,
) -> Path:
    """
    Return the JSONL path for the given day (UTC today by default).

    Pure function; no I/O.  Exposed for tests and cross-midnight probes.
    """
    return _resolve_path(root, today)


# ---------------------------------------------------------------------------
# Public: log_divergence
# ---------------------------------------------------------------------------

def log_divergence(
    record: DivergenceRecord,
    *,
    root: Path | str = "evidence/topology_v_next_shadow",
) -> None:
    """
    Append *record* as one JSONL line to the daily file under *root*.

    Uses O_APPEND|O_CREAT + single os.write() for POSIX multi-process safety
    (SCAFFOLD §4.3).  Never raises — errors are written to stderr so that
    the shadow logger never breaks the admission call that triggered it.

    Path: {root}/divergence_{YYYY-MM-DD}.jsonl  (UTC day boundary)
    """
    try:
        record = _maybe_truncate(record)
        line = _serialize_record(record)
        encoded = line.encode("utf-8")

        path = _resolve_path(root, None)
        path.parent.mkdir(parents=True, exist_ok=True)

        # O_APPEND ensures atomic position-to-EOF before each write (POSIX).
        # Single os.write() for the complete line → no interleaving with
        # concurrent writers.  0o644 permissions match repo convention.
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)

    except Exception as exc:  # noqa: BLE001
        # SCAFFOLD §1.1: "Never raises on disk-full or permission errors —
        # logs to stderr via sys.stderr and continues."
        sys.stderr.write(
            f"[divergence_logger] log_divergence failed: {type(exc).__name__}: {exc}\n"
        )
