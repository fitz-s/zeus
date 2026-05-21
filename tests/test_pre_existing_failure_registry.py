# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md P3-2
"""Guards for the central pre-existing failure registry."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml


REGISTRY_PATH = Path("architecture/pre_existing_failure_registry.yaml")


def test_pre_existing_failure_registry_entries_are_actionable() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text())
    assert registry["schema_version"] == 1
    assert registry["entries"], "registry must not exist as an empty placeholder"

    today = date(2026, 5, 21)
    forbidden_statuses = {"permanent", "ignored", "wontfix"}
    seen: set[str] = set()
    for entry in registry["entries"]:
        failure_id = entry["failure_id"]
        assert failure_id not in seen
        seen.add(failure_id)
        assert failure_id.startswith("PEF-")
        assert entry["status"] not in forbidden_statuses
        assert entry["owner"]
        assert entry["evidence"]
        assert entry["allowed_only_if"]
        assert date.fromisoformat(entry["first_observed"]) <= today
        assert date.fromisoformat(entry["review_by"]) > today
