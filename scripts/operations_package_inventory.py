# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/authority/ARCHIVAL_RULES.md; architecture/docs_registry.yaml; PR-T4 topology brief
"""
Operations packet inventory: classify every docs/operations/task_* directory into
exactly one of six advisory classes so archiving (T5) is operator-approved and safe.

CLASSIFICATION PRECEDENCE (highest wins):
  1. LOAD_BEARING_DESPITE_AGE  — inbound ref from src/scripts/architecture/tests/docs/authority
     OR authority_status_registry CURRENT_LOAD_BEARING
  2. RUNTIME_GATING_EVIDENCE   — packet contains TIGGE ingest decision, live-gating evidence
  3. CURRENT_PACKAGE_INPUT     — modified in last 30 days (active window), or docs_registry active
  4. MONITORING_SURFACE        — PLAN.md/README.md mentions observation/shadow/monitor keyword
  5. ARCHIVE_CANDIDATE         — modified 60+ days ago, no authority status, no inbound refs
  6. UNKNOWN_OPERATOR_DECISION — cannot classify without human input

Exit code: always 0 (advisory only).
CLI: python3 scripts/operations_package_inventory.py [--json] [--repo-root PATH]

Self-poison prevention: packet names are discovered at runtime via glob, never
hardcoded in this source. Test files use synthetic packet names.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

CLASSES = Literal[
    "CURRENT_PACKAGE_INPUT",
    "LOAD_BEARING_DESPITE_AGE",
    "ARCHIVE_CANDIDATE",
    "MONITORING_SURFACE",
    "RUNTIME_GATING_EVIDENCE",
    "UNKNOWN_OPERATOR_DECISION",
]

# Inbound-ref grep search roots (relative to repo root).
# Deliberately excludes docs/operations/** to avoid packet↔packet false positives
# and docs/reports/** to avoid self-poisoning the inventory report.
INBOUND_REF_ROOTS = [
    "src",
    "scripts",
    "architecture",
    "tests",
    "docs/authority",
]

# Keywords that signal a RUNTIME_GATING_EVIDENCE packet.
RUNTIME_GATING_KEYWORDS = {
    "tigge_ingest_decision",
    "tigge ingest decision",
    "runtime-gating",
    "runtime_gating",
    "live-gating",
    "ingest decision",
    "gate: open",
    "gate: closed",
    "LIVE-GATING",
}

# Keywords that signal MONITORING_SURFACE.
MONITORING_KEYWORDS = {
    "observation",
    "shadow",
    "monitor",
    "monitoring",
    "_observation/",
}

ACTIVE_WINDOW_DAYS = 30
ARCHIVE_THRESHOLD_DAYS = 60


# ---------------------------------------------------------------------------
# Signal bundle — pure data captured from the repo
# ---------------------------------------------------------------------------

@dataclass
class SignalBundle:
    slug: str                          # e.g. "task_2026-05-15_p1_topology_v_next_additive"
    last_modified_date: Optional[datetime.date]
    days_since_modified: Optional[int]
    inbound_ref_count: int             # files referencing this slug in INBOUND_REF_ROOTS
    inbound_ref_files: list[str]       # for reporting
    authority_status: Optional[str]    # from artifact_authority_status.yaml if registered
    registry_lifecycle: Optional[str]  # from docs_registry.yaml lifecycle_state
    has_authority_status_text: bool    # True if PLAN.md/README first 50 lines has AUTHORITY/ACTIVE_LAW
    is_runtime_gating_evidence: bool   # True if any file in packet mentions gating keywords
    is_monitoring_surface: bool        # True if plan mentions monitoring keywords
    proposed_new_home: str             # for CURRENT_PACKAGE_INPUT only


@dataclass
class PacketRecord:
    slug: str
    classification: str
    reason: str
    inbound_ref_count: int
    proposed_new_home: str


# ---------------------------------------------------------------------------
# Pure classification function (testable without repo I/O)
# ---------------------------------------------------------------------------

def classify(signals: SignalBundle) -> PacketRecord:
    """
    Pure function: given a SignalBundle, return a PacketRecord with one
    of the six advisory classifications.

    Precedence (highest wins):
      1. LOAD_BEARING_DESPITE_AGE
      2. RUNTIME_GATING_EVIDENCE
      3. CURRENT_PACKAGE_INPUT
      4. MONITORING_SURFACE
      5. ARCHIVE_CANDIDATE
      6. UNKNOWN_OPERATOR_DECISION
    """
    slug = signals.slug

    # Check 1: LOAD_BEARING_DESPITE_AGE
    # Any inbound reference from src/scripts/architecture/tests/docs/authority
    # OR authority_status_registry says CURRENT_LOAD_BEARING
    if signals.inbound_ref_count > 0:
        reason = (
            f"inbound refs from authoritative roots: "
            f"{signals.inbound_ref_count} file(s) reference this packet slug"
        )
        return PacketRecord(
            slug=slug,
            classification="LOAD_BEARING_DESPITE_AGE",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home="",
        )

    if signals.authority_status == "CURRENT_LOAD_BEARING":
        reason = "artifact_authority_status.yaml status=CURRENT_LOAD_BEARING"
        return PacketRecord(
            slug=slug,
            classification="LOAD_BEARING_DESPITE_AGE",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home="",
        )

    if signals.has_authority_status_text:
        reason = "PLAN.md/README.md first 50 lines contains Status: AUTHORITY or ACTIVE_LAW"
        return PacketRecord(
            slug=slug,
            classification="LOAD_BEARING_DESPITE_AGE",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home="",
        )

    # Check 2: RUNTIME_GATING_EVIDENCE
    if signals.is_runtime_gating_evidence:
        reason = "packet contains runtime-gating evidence (TIGGE ingest decision or live-gate keyword)"
        return PacketRecord(
            slug=slug,
            classification="RUNTIME_GATING_EVIDENCE",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home="",
        )

    # Check 3: CURRENT_PACKAGE_INPUT
    if signals.days_since_modified is not None and signals.days_since_modified <= ACTIVE_WINDOW_DAYS:
        reason = f"modified {signals.days_since_modified}d ago (within {ACTIVE_WINDOW_DAYS}d active window)"
        return PacketRecord(
            slug=slug,
            classification="CURRENT_PACKAGE_INPUT",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home=signals.proposed_new_home,
        )

    if signals.registry_lifecycle in ("active", "durable"):
        reason = f"docs_registry lifecycle_state={signals.registry_lifecycle}"
        return PacketRecord(
            slug=slug,
            classification="CURRENT_PACKAGE_INPUT",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home=signals.proposed_new_home,
        )

    # Check 4: MONITORING_SURFACE
    if signals.is_monitoring_surface:
        reason = "PLAN.md/README.md contains observation/shadow/monitor keyword"
        return PacketRecord(
            slug=slug,
            classification="MONITORING_SURFACE",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home="",
        )

    # Check 5: ARCHIVE_CANDIDATE
    if signals.days_since_modified is not None and signals.days_since_modified > ARCHIVE_THRESHOLD_DAYS:
        reason = (
            f"modified {signals.days_since_modified}d ago (>{ARCHIVE_THRESHOLD_DAYS}d), "
            "no inbound refs, no authority status"
        )
        return PacketRecord(
            slug=slug,
            classification="ARCHIVE_CANDIDATE",
            reason=reason,
            inbound_ref_count=signals.inbound_ref_count,
            proposed_new_home="",
        )

    # Check 6: UNKNOWN_OPERATOR_DECISION
    reason = "insufficient signals to classify; operator decision required"
    return PacketRecord(
        slug=slug,
        classification="UNKNOWN_OPERATOR_DECISION",
        reason=reason,
        inbound_ref_count=signals.inbound_ref_count,
        proposed_new_home="",
    )


# ---------------------------------------------------------------------------
# I/O layer — gather signals from the real repo
# ---------------------------------------------------------------------------

def _git_last_modified_date(packet_path: Path, repo_root: Path) -> Optional[datetime.date]:
    """Return the date of the most recent git commit that touched the packet dir."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%ai", "--", str(packet_path.relative_to(repo_root))],
            capture_output=True, text=True, cwd=str(repo_root), timeout=10,
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        if not lines:
            return None
        # Format: "2026-05-21 12:26:10 -0500"
        date_str = lines[0].split()[0]
        return datetime.date.fromisoformat(date_str)
    except Exception:
        return None


def _count_inbound_refs(slug: str, repo_root: Path) -> tuple[int, list[str]]:
    """
    Count files in INBOUND_REF_ROOTS that reference the packet slug.
    Returns (count, list_of_matching_files).
    """
    existing_roots = [
        root for root in INBOUND_REF_ROOTS
        if (repo_root / root).exists()
    ]
    if not existing_roots:
        return 0, []

    try:
        result = subprocess.run(
            ["git", "grep", "-l", slug, "--"] + existing_roots,
            capture_output=True, text=True, cwd=str(repo_root), timeout=30,
        )
        files = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        return len(files), files
    except Exception:
        return 0, []


def _check_authority_status_text(packet_path: Path) -> bool:
    """Check first 50 lines of PLAN.md / README.md for authority status keywords."""
    authority_keywords = {"Status: AUTHORITY", "Status: ACTIVE_LAW", "Status: AUTHORITATIVE"}
    for name in ("PLAN.md", "README.md"):
        candidate = packet_path / name
        if candidate.is_file():
            try:
                with candidate.open(encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f):
                        if i >= 50:
                            break
                        if any(kw in line for kw in authority_keywords):
                            return True
            except Exception:
                pass
    return False


def _check_runtime_gating_evidence(packet_path: Path) -> bool:
    """Check if any file inside the packet mentions runtime-gating evidence keywords."""
    for fpath in packet_path.rglob("*"):
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
            if any(kw.lower() in text.lower() for kw in RUNTIME_GATING_KEYWORDS):
                return True
        except Exception:
            pass
    return False


def _check_monitoring_surface(packet_path: Path) -> bool:
    """Check if PLAN.md/README.md mentions monitoring keywords."""
    for name in ("PLAN.md", "README.md"):
        candidate = packet_path / name
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8", errors="ignore").lower()
                if any(kw in text for kw in {k.lower() for k in MONITORING_KEYWORDS}):
                    return True
            except Exception:
                pass
    return False


def _load_artifact_authority_status(repo_root: Path) -> dict[str, str]:
    """
    Parse architecture/artifact_authority_status.yaml entries into a
    {slug: status} mapping. Slug is extracted from the path field.
    """
    yaml_path = repo_root / "architecture" / "artifact_authority_status.yaml"
    if not yaml_path.is_file():
        return {}
    try:
        import yaml  # type: ignore
        with yaml_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        result: dict[str, str] = {}
        for entry in data.get("entries", []):
            path = entry.get("path", "")
            status = entry.get("status", "")
            # Extract task_* slug from the path
            for part in Path(path).parts:
                if part.startswith("task_"):
                    result[part] = status
                    break
        return result
    except Exception:
        return {}


def _load_registry_lifecycle(repo_root: Path) -> dict[str, str]:
    """
    Parse architecture/docs_registry.yaml entries into a {slug: lifecycle_state} mapping.
    """
    yaml_path = repo_root / "architecture" / "docs_registry.yaml"
    if not yaml_path.is_file():
        return {}
    try:
        import yaml  # type: ignore
        with yaml_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        result: dict[str, str] = {}
        for entry in data.get("entries", []):
            path = entry.get("path", "")
            lifecycle = entry.get("lifecycle_state", "")
            for part in Path(path).parts:
                if part.startswith("task_"):
                    result[part] = lifecycle
                    break
        return result
    except Exception:
        return {}


def gather_signals(packet_path: Path, repo_root: Path,
                   authority_status_map: dict[str, str],
                   registry_lifecycle_map: dict[str, str]) -> SignalBundle:
    """Gather all classification signals for a single packet directory."""
    slug = packet_path.name
    today = datetime.date.today()

    last_modified = _git_last_modified_date(packet_path, repo_root)
    if last_modified is not None:
        days_since = (today - last_modified).days
    else:
        days_since = None

    inbound_count, inbound_files = _count_inbound_refs(slug, repo_root)

    authority_status = authority_status_map.get(slug)
    registry_lifecycle = registry_lifecycle_map.get(slug)

    has_authority_text = _check_authority_status_text(packet_path)
    is_runtime_gating = _check_runtime_gating_evidence(packet_path)
    is_monitoring = _check_monitoring_surface(packet_path)

    # Proposed new home for CURRENT_PACKAGE_INPUT packets
    proposed = ""
    if days_since is not None and days_since <= ACTIVE_WINDOW_DAYS:
        bare_slug = slug[len("task_"):] if slug.startswith("task_") else slug
        # Strip the date prefix (YYYY-MM-DD_)
        parts = bare_slug.split("_", 3)
        if len(parts) >= 4:
            name_slug = "_".join(parts[3:])
        elif len(parts) == 3:
            name_slug = parts[2]
        else:
            name_slug = bare_slug
        proposed = f"docs/operations/current/plans/{name_slug}/"

    return SignalBundle(
        slug=slug,
        last_modified_date=last_modified,
        days_since_modified=days_since,
        inbound_ref_count=inbound_count,
        inbound_ref_files=inbound_files,
        authority_status=authority_status,
        registry_lifecycle=registry_lifecycle,
        has_authority_status_text=has_authority_text,
        is_runtime_gating_evidence=is_runtime_gating,
        is_monitoring_surface=is_monitoring,
        proposed_new_home=proposed,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_inventory(repo_root: Path) -> list[PacketRecord]:
    """Enumerate all task_* packets and classify each."""
    ops_dir = repo_root / "docs" / "operations"
    packets = sorted(
        p for p in ops_dir.iterdir()
        if p.is_dir() and p.name.startswith("task_")
    )

    authority_status_map = _load_artifact_authority_status(repo_root)
    registry_lifecycle_map = _load_registry_lifecycle(repo_root)

    records: list[PacketRecord] = []
    for packet_path in packets:
        signals = gather_signals(
            packet_path, repo_root,
            authority_status_map,
            registry_lifecycle_map,
        )
        record = classify(signals)
        records.append(record)
    return records


def _format_table(records: list[PacketRecord]) -> str:
    """Format records as a human-readable table."""
    header = f"{'PACKET':<55} {'CLASS':<30} {'REFS':>4}  REASON"
    sep = "-" * 120
    lines = [header, sep]
    for r in records:
        slug_short = r.slug if len(r.slug) <= 55 else r.slug[:52] + "..."
        reason_short = r.reason if len(r.reason) <= 60 else r.reason[:57] + "..."
        lines.append(
            f"{slug_short:<55} {r.classification:<30} {r.inbound_ref_count:>4}  {reason_short}"
        )
    lines.append(sep)
    # Summary row
    from collections import Counter
    counts = Counter(r.classification for r in records)
    lines.append(f"\nTotal packets: {len(records)}")
    for cls in [
        "CURRENT_PACKAGE_INPUT", "LOAD_BEARING_DESPITE_AGE", "ARCHIVE_CANDIDATE",
        "MONITORING_SURFACE", "RUNTIME_GATING_EVIDENCE", "UNKNOWN_OPERATOR_DECISION",
    ]:
        lines.append(f"  {cls}: {counts.get(cls, 0)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify docs/operations/task_* packets. Advisory only; exit 0 always.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Path to zeus repo root (default: auto-detect from this file's location)",
    )
    args = parser.parse_args()

    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        # Auto-detect: this script lives at <repo_root>/scripts/operations_package_inventory.py
        repo_root = Path(__file__).resolve().parents[1]

    records = run_inventory(repo_root)

    if args.json:
        output = [
            {
                "slug": r.slug,
                "classification": r.classification,
                "reason": r.reason,
                "inbound_ref_count": r.inbound_ref_count,
                "proposed_new_home": r.proposed_new_home,
            }
            for r in records
        ]
        print(json.dumps(output, indent=2))
    else:
        print(_format_table(records))

    sys.exit(0)


if __name__ == "__main__":
    main()
