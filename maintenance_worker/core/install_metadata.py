# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Dry-run floor enforcement"
"""
install_metadata — InstallMetadata schema, writer, and dry-run floor enforcement.

install_metadata.json is written ONCE on first run. Subsequent writes raise
ImmutableMetadataError. The dry-run floor (30 days elapsed since first_run_at)
is enforced by enforce_dry_run_floor(). Two hardcoded exempt task IDs bypass
the floor unconditionally.

FLOOR_EXEMPT_TASK_IDS is a frozenset (not YAML-configurable) to prevent
catalog drift from silently widening exemptions. SCAFFOLD §4.

Stdlib only. No imports from maintenance_worker.core (no circular deps).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

METADATA_FILENAME = "install_metadata.json"
SCHEMA_VERSION = 1

# Hardcoded — not config-read. TaskRegistry cross-checks at load. SCAFFOLD §4.
FLOOR_EXEMPT_TASK_IDS: frozenset[str] = frozenset(
    {
        "zero_byte_state_cleanup",
        "agent_self_evidence_archival",
    }
)

DRY_RUN_FLOOR_DAYS: int = 30


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ImmutableMetadataError(RuntimeError):
    """Raised when install_metadata.json is written more than once."""


class MetadataSchemaError(ValueError):
    """Raised when install_metadata.json has an unknown schema_version."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallMetadata:
    """
    Immutable record written once on first tick.

    first_run_at: UTC datetime — floor countdown starts here.
    agent_version: semver string.
    install_run_id: UUID4 from evidence trail id of first tick.
    allowed_remote_urls: git remote origin URLs pinned at install.
    repo_root_at_install: absolute path to repo root at install time.
    """

    schema_version: int
    first_run_at: datetime
    agent_version: str
    install_run_id: str
    allowed_remote_urls: tuple[str, ...] = field(default_factory=tuple)
    repo_root_at_install: str = ""


@dataclass(frozen=True)
class DryRunFloor:
    """
    Configuration for the 30-day dry-run floor.

    floor_days: days that must elapse since first_run_at (default 30).
    override_ack_file: path whose existence bypasses the floor entirely.
    """

    floor_days: int = DRY_RUN_FLOOR_DAYS
    override_ack_file: Path = Path("")

    def __post_init__(self) -> None:
        # Validate floor_days is positive
        if self.floor_days < 1:
            raise ValueError(f"floor_days must be >= 1, got {self.floor_days}")


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def write_install_metadata(state_dir: Path, metadata: InstallMetadata) -> Path:
    """
    Write install_metadata.json to state_dir.

    Raises ImmutableMetadataError if the file already exists.
    Returns the path written.
    """
    target = state_dir / METADATA_FILENAME
    if target.exists():
        raise ImmutableMetadataError(
            f"install_metadata.json already exists at {target}; "
            "it is immutable after first write. Human must update explicitly."
        )
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": metadata.schema_version,
        "first_run_at": metadata.first_run_at.isoformat(),
        "agent_version": metadata.agent_version,
        "install_run_id": metadata.install_run_id,
        "allowed_remote_urls": list(metadata.allowed_remote_urls),
        "repo_root_at_install": metadata.repo_root_at_install,
    }
    # Atomic write: tmp then replace
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def read_install_metadata(state_dir: Path) -> InstallMetadata:
    """
    Read and deserialize install_metadata.json from state_dir.

    Raises FileNotFoundError if absent.
    Raises MetadataSchemaError on schema_version mismatch.
    """
    target = state_dir / METADATA_FILENAME
    raw = json.loads(target.read_text(encoding="utf-8"))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise MetadataSchemaError(
            f"install_metadata.json schema_version={raw.get('schema_version')!r}, "
            f"expected {SCHEMA_VERSION}. Manual update required."
        )
    return InstallMetadata(
        schema_version=raw["schema_version"],
        first_run_at=datetime.fromisoformat(raw["first_run_at"]),
        agent_version=raw["agent_version"],
        install_run_id=raw["install_run_id"],
        allowed_remote_urls=tuple(raw.get("allowed_remote_urls", [])),
        repo_root_at_install=raw.get("repo_root_at_install", ""),
    )


# ---------------------------------------------------------------------------
# Dry-run floor enforcement
# ---------------------------------------------------------------------------


def enforce_dry_run_floor(
    task_id: str,
    install_meta: InstallMetadata,
    floor_cfg: DryRunFloor,
) -> str:
    """
    Return a ValidatorResult-compatible string for the dry-run floor check.

    Returns "ALLOWED" or "ALLOWED_BUT_DRY_RUN_ONLY".

    Returns a string (not the ValidatorResult enum) to avoid importing
    from types.results in this foundational module. Callers convert to
    ValidatorResult. SCAFFOLD §4 pseudocode is implemented exactly.

    Decision tree (SCAFFOLD §4):
    1. FLOOR_EXEMPT_TASK_IDS → ALLOWED unconditionally (hardcoded, not YAML).
    2. override_ack_file present → ALLOWED (human override).
    3. elapsed < floor_days → ALLOWED_BUT_DRY_RUN_ONLY.
    4. elapsed >= floor_days → ALLOWED.
    """
    # Step 1: hardcoded exemption (cannot be widened via YAML)
    if task_id in FLOOR_EXEMPT_TASK_IDS:
        return "ALLOWED"

    # Step 2: human override (ack file existence bypasses floor)
    # Check override_ack_file only when it's a meaningful path
    if floor_cfg.override_ack_file != Path("") and floor_cfg.override_ack_file.exists():
        return "ALLOWED"

    # Step 3: elapsed time check
    now = datetime.now(tz=timezone.utc)
    # Ensure install_meta.first_run_at is tz-aware
    first_run = install_meta.first_run_at
    if first_run.tzinfo is None:
        first_run = first_run.replace(tzinfo=timezone.utc)
    elapsed = now - first_run
    if elapsed < timedelta(days=floor_cfg.floor_days):
        return "ALLOWED_BUT_DRY_RUN_ONLY"

    # Step 4: floor elapsed — allow
    return "ALLOWED"
