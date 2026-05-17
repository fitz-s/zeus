# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/authority/ARCHIVAL_RULES.md
#   §"Exemption Checks (ALL must pass to archive)" check #0
#   architecture/artifact_authority_status.yaml (runtime registry)
"""
archival_check_0 — Authority Status Registry check (Check #0).

Per ARCHIVAL_RULES.md §"Exemption Checks" item 0:
  Runs FIRST before the 8 heuristic checks (1–8).
  Looks up the candidate path in artifact_authority_status.yaml.
  If found AND status is NOT in {ARCHIVED, CURRENT_HISTORICAL+archival_ok}:
    → LOAD_BEARING (skip remaining checks)
  If found AND status is ARCHIVED:
    → ARCHIVABLE (may proceed to checks 1–8)
  If found AND status is CURRENT_HISTORICAL AND archival_ok=true:
    → ARCHIVABLE (may proceed to checks 1–8)
  If registry file absent or unreadable:
    → WARN_REGISTRY_ABSENT + log WARNING (do NOT treat as "not registered")
  If path not in registry:
    → ARCHIVABLE (heuristic checks 1–8 apply)

Interface:
  check_authority_status(path: Path, registry_path: Path) -> ArchivalCheckResult
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, Optional

import yaml

logger = logging.getLogger(__name__)

# Status values that allow archival (per ARCHIVAL_RULES §0)
_ARCHIVABLE_STATUSES = frozenset({"ARCHIVED"})
# CURRENT_HISTORICAL is archivable only when archival_ok=true; handled in logic.

# Status values that are NOT any of the archivable set → LOAD_BEARING
# Covers: CURRENT_LOAD_BEARING, STALE_REWRITE_NEEDED, DEMOTE, QUARANTINE
_ALWAYS_LOAD_BEARING_STATUSES = frozenset({
    "CURRENT_LOAD_BEARING",
    "STALE_REWRITE_NEEDED",
    "DEMOTE",
    "QUARANTINE",
})


@dataclass(frozen=True)
class ArchivalCheckResult:
    """
    Result of Check #0 (authority status registry lookup).

    verdict:
      LOAD_BEARING         — path is in registry with a non-archivable status;
                             skip checks 1–8 and classify as LOAD_BEARING_DESPITE_AGE.
      ARCHIVABLE           — either path not in registry, or status/archival_ok allow
                             archival; proceed to heuristic checks 1–8.
      WARN_REGISTRY_ABSENT — registry file absent or unreadable; WARNING logged;
                             caller should proceed to heuristic checks 1–8.
    reason:   human-readable rationale for the verdict.
    status_row: the raw registry row dict if found, else None.
    """

    verdict: Literal["LOAD_BEARING", "ARCHIVABLE", "WARN_REGISTRY_ABSENT"]
    reason: str
    status_row: Optional[dict] = None


@cache
def _load_registry(registry_path: str) -> Optional[list[dict]]:
    """
    Load and return the entries list from artifact_authority_status.yaml.

    Cached per-process via functools.cache (keyed on string path for hashability).
    Returns None if file absent or unreadable.
    """
    p = Path(registry_path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return None
        return data.get("entries", []) or []
    except Exception:
        return None


def _resolve_candidate(path: Path) -> str:
    """
    Return a normalised string form of the candidate path for registry lookup.

    Tries realpath first; falls back to absolute path if realpath fails
    (e.g., path does not yet exist on disk — archival candidates may be
    planned-but-not-yet-moved).
    Registry paths are stored relative to repo root; we match by suffix too.
    """
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def _path_matches_row(candidate: Path, row_path_str: str) -> bool:
    """
    Return True if candidate matches the row's path field.

    Registry rows store paths relative to repo root OR as absolute paths.
    We match if:
      1. candidate's string ends with the row path (relative match), OR
      2. row path ends with candidate's name parts (reverse relative), OR
      3. exact match after normalization.
    """
    cand_str = str(candidate)
    # Expand ~ so resolved absolute paths match literal-~ registry entries (and vice versa)
    cand_norm = os.path.expanduser(cand_str).replace("\\", "/")
    row_norm = os.path.expanduser(row_path_str).replace("\\", "/").rstrip("/")

    # Exact match
    if cand_norm == row_norm or cand_norm.rstrip("/") == row_norm:
        return True
    # Candidate path ends with the registry row path (e.g., row="AGENTS.md")
    if cand_norm.endswith("/" + row_norm) or cand_norm == row_norm:
        return True
    # Registry row ends with candidate (e.g., row is absolute, candidate relative)
    if row_norm.endswith("/" + cand_norm) or row_norm == cand_norm:
        return True
    return False


def check_authority_status(path: Path, registry_path: Path) -> ArchivalCheckResult:
    """
    Check #0: Authority Status Registry lookup.

    Args:
        path:          The candidate path to look up (file or directory).
        registry_path: Path to architecture/artifact_authority_status.yaml.

    Returns:
        ArchivalCheckResult with verdict LOAD_BEARING, ARCHIVABLE, or WARN_REGISTRY_ABSENT.
    """
    # Load registry (cached per-process)
    entries = _load_registry(str(registry_path))

    if entries is None:
        logger.warning(
            "archival_check_0: registry file absent or unreadable at %s; "
            "falling through to heuristic checks 1–8",
            registry_path,
        )
        return ArchivalCheckResult(
            verdict="WARN_REGISTRY_ABSENT",
            reason=f"Registry file absent or unreadable: {registry_path}",
            status_row=None,
        )

    # Search for matching row
    matched_row: Optional[dict] = None
    for row in entries:
        row_path = row.get("path", "")
        if _path_matches_row(path, row_path):
            matched_row = row
            break

    if matched_row is None:
        return ArchivalCheckResult(
            verdict="ARCHIVABLE",
            reason=f"Path not found in registry; heuristic checks 1–8 apply.",
            status_row=None,
        )

    status = matched_row.get("status", "")
    archival_ok = matched_row.get("archival_ok", False)

    # ARCHIVED → always archivable
    if status == "ARCHIVED":
        return ArchivalCheckResult(
            verdict="ARCHIVABLE",
            reason=f"Registry status is ARCHIVED.",
            status_row=matched_row,
        )

    # CURRENT_HISTORICAL with explicit archival_ok=True → archivable
    if status == "CURRENT_HISTORICAL" and archival_ok is True:
        return ArchivalCheckResult(
            verdict="ARCHIVABLE",
            reason=f"Registry status is CURRENT_HISTORICAL with archival_ok=true.",
            status_row=matched_row,
        )

    # Any other status (CURRENT_LOAD_BEARING, STALE_REWRITE_NEEDED, DEMOTE,
    # QUARANTINE, CURRENT_HISTORICAL without archival_ok) → LOAD_BEARING
    return ArchivalCheckResult(
        verdict="LOAD_BEARING",
        reason=(
            f"Registry status is {status!r}"
            + ("" if archival_ok else " (archival_ok not set)")
            + "; classified as LOAD_BEARING_DESPITE_AGE."
        ),
        status_row=matched_row,
    )
