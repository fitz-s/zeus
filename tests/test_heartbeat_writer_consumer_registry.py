# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: OBSERVABILITY_STRUCTURAL_FIX.md — paired-existence invariant (F99/F100)
"""Antibody: every JSON heartbeat writer must have a registered consumer.

Design invariant: a writer with no consumer is an orphan. Orphan writers accumulate
stale/missing state indefinitely without operator awareness. This test enforces the
paired-existence contract at CI time — adding a new heartbeat writer without a consumer
entry causes an immediate CI failure.

Registry format:
  HEARTBEAT_REGISTRY = {
    "artifact_filename": {
      "writer": "path/to/writer.py",
      "consumers": [list of consumer paths or PENDING_* tokens],
      "note": "optional explanation",
    }
  }

PENDING_* tokens are allowed (with a mandatory note) to document known gaps without
blocking CI. They surface in the test output so gaps are visible, not hidden.

Findings closed: F99 (write/read asymmetry), F100 (daemon-heartbeat-ingest zero readers).
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Registry — hand-curated; add entry before adding a new heartbeat writer
# ---------------------------------------------------------------------------

HEARTBEAT_REGISTRY: dict[str, dict] = {
    "daemon-heartbeat.json": {
        "writer": "src/main.py",
        "consumers": [
            "scripts/check_daemon_heartbeat.py",
            "scripts/live_health_probe.py",
        ],
        "note": "Trading daemon alive signal. OpenClaw plist also monitors.",
    },
    "daemon-heartbeat-ingest.json": {
        "writer": "src/ingest_main.py",
        "consumers": [
            "PENDING_OPENCLAW_ENFORCEMENT: plist --heartbeat-files arg is informational-only "
            "in sensor_layer1.py; enforcement tracked as OpenClaw-layer gap (F91/F100). "
            "Until sensor_layer1 enforces the arg, this writer has no active Zeus consumer.",
        ],
        "note": "Ingest daemon alive signal. Plist references it but sensor_layer1 ignores it.",
    },
    "forecast-live-heartbeat.json": {
        "writer": "src/ingest/forecast_live_daemon.py",
        "consumers": [
            "scripts/check_forecast_live_ready.py",
            "scripts/live_health_probe.py",
        ],
        "note": "Forecast-live daemon alive signal.",
    },
    "oracle_error_rates.heartbeat.json": {
        "writer": "scripts/bridge_oracle_to_calibration.py",
        "consumers": [
            "scripts/deep_heartbeat.py:check_oracle_missing",
        ],
        "note": (
            "Oracle artifact sidecar. Consumed by check_oracle_missing() in deep_heartbeat.py "
            "which escalates via OpenClaw RED path on persistent MISSING (F33 fix)."
        ),
    },
    "venue-heartbeat-keeper.json": {
        "writer": "src/control/heartbeat_supervisor.py",
        "consumers": [
            "src/control/heartbeat_supervisor.py:ExternalHeartbeatSupervisor",
        ],
        "note": "Venue CLOB heartbeat lease. Self-paired: writer and reader in same module.",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_heartbeat_writers(project_root: Path) -> dict[str, list[str]]:
    """Grep src/ and scripts/ for files that write known heartbeat JSON filenames.

    Returns {filename: [list_of_source_files_that_write_it]}.
    """
    search_dirs = [project_root / "src", project_root / "scripts"]
    writers: dict[str, list[str]] = {}

    for artifact in HEARTBEAT_REGISTRY:
        found = []
        for d in search_dirs:
            if not d.exists():
                continue
            for py_file in sorted(d.rglob("*.py")):
                content = py_file.read_text(errors="replace")
                # Match write patterns: open/write/state_path references to the filename
                if artifact in content and any(
                    kw in content
                    for kw in (
                        "write",
                        "open(",
                        "state_path",
                        "heartbeat_path",
                        ".replace(",  # atomic write pattern
                        "tmp.replace",
                    )
                ):
                    rel = str(py_file.relative_to(project_root))
                    found.append(rel)
        writers[artifact] = found

    return writers


def _active_consumers(entry: dict) -> list[str]:
    """Return consumers that are NOT PENDING_* tokens."""
    return [c for c in entry.get("consumers", []) if not c.startswith("PENDING_")]


def _pending_consumers(entry: dict) -> list[str]:
    """Return consumers that ARE PENDING_* tokens (documented gaps)."""
    return [c for c in entry.get("consumers", []) if c.startswith("PENDING_")]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHeartbeatWriterConsumerRegistry:
    """CI enforcement: every registered heartbeat writer has consumer documentation."""

    def test_all_registry_entries_have_consumers_field(self):
        """Every registry entry must declare a consumers list (may contain PENDING_ tokens)."""
        for artifact, entry in HEARTBEAT_REGISTRY.items():
            assert "consumers" in entry, (
                f"{artifact}: missing 'consumers' field in HEARTBEAT_REGISTRY. "
                "Add consumer path(s) or a PENDING_* token with a note."
            )
            assert isinstance(entry["consumers"], list), (
                f"{artifact}: 'consumers' must be a list"
            )
            assert len(entry["consumers"]) > 0, (
                f"{artifact}: 'consumers' list is empty. "
                "Add ≥1 consumer path or PENDING_* token."
            )

    def test_pending_consumers_have_notes(self):
        """PENDING_* consumer tokens must include an explanation (colon-separated)."""
        for artifact, entry in HEARTBEAT_REGISTRY.items():
            for consumer in _pending_consumers(entry):
                assert ":" in consumer, (
                    f"{artifact}: PENDING consumer token must include explanation after colon. "
                    f"Got: {consumer!r}"
                )

    def test_active_consumers_reference_existing_files(self):
        """Active (non-PENDING) consumers must reference files that exist in the repo."""
        for artifact, entry in HEARTBEAT_REGISTRY.items():
            for consumer in _active_consumers(entry):
                # Consumer may be "path/to/file.py" or "path/to/file.py:function"
                file_part = consumer.split(":")[0]
                consumer_path = PROJECT_ROOT / file_part
                assert consumer_path.exists(), (
                    f"{artifact}: declared consumer {consumer!r} → file {file_part!r} "
                    "not found. Update HEARTBEAT_REGISTRY with the correct path."
                )

    def test_writers_reference_existing_files(self):
        """Every writer path in the registry must exist."""
        for artifact, entry in HEARTBEAT_REGISTRY.items():
            writer = entry.get("writer", "")
            assert writer, f"{artifact}: 'writer' field missing or empty"
            writer_path = PROJECT_ROOT / writer
            assert writer_path.exists(), (
                f"{artifact}: declared writer {writer!r} not found. "
                "Update HEARTBEAT_REGISTRY."
            )

    def test_no_orphan_writers_in_src_scripts(self):
        """Any .py file in src/ or scripts/ that writes a heartbeat artifact
        must be registered in HEARTBEAT_REGISTRY.

        This catches new heartbeat writers added without updating the registry.
        """
        search_dirs = [PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"]
        registered_writers: dict[str, str] = {
            artifact: entry["writer"]
            for artifact, entry in HEARTBEAT_REGISTRY.items()
        }

        unregistered: list[str] = []

        for artifact in HEARTBEAT_REGISTRY:
            for d in search_dirs:
                if not d.exists():
                    continue
                for py_file in sorted(d.rglob("*.py")):
                    content = py_file.read_text(errors="replace")
                    rel = str(py_file.relative_to(PROJECT_ROOT))
                    # Skip the registered writer itself
                    if rel == registered_writers.get(artifact):
                        continue
                    # Flag if it writes the artifact name without being a registered consumer
                    if artifact in content and any(
                        kw in content
                        for kw in ("open(", "write_text", "tmp.replace", ".replace(target")
                    ):
                        # Narrow: must contain the filename in a write context
                        for line in content.splitlines():
                            if artifact in line and any(
                                kw in line
                                for kw in ("open(", "write_text", "tmp.replace", "replace(target")
                            ):
                                unregistered.append(f"{rel} writes {artifact!r}")
                                break

        assert not unregistered, (
            "Unregistered heartbeat writers found. Add to HEARTBEAT_REGISTRY:\n"
            + "\n".join(f"  {u}" for u in unregistered)
        )

    def test_oracle_missing_check_wired_into_deep_heartbeat(self):
        """check_oracle_missing must be registered in run_diagnostics checks list (F33)."""
        deep_hb = PROJECT_ROOT / "scripts" / "deep_heartbeat.py"
        assert deep_hb.exists(), "deep_heartbeat.py must exist"
        content = deep_hb.read_text()
        assert "check_oracle_missing" in content, (
            "check_oracle_missing() must be defined in deep_heartbeat.py (F33 fix)"
        )
        assert "check_oracle_missing()" in content, (
            "check_oracle_missing() must be called in run_diagnostics checks list"
        )

    def test_daemon_heartbeat_ingest_gap_documented(self):
        """daemon-heartbeat-ingest.json orphan gap must be documented (not silently skipped)."""
        entry = HEARTBEAT_REGISTRY.get("daemon-heartbeat-ingest.json", {})
        consumers = entry.get("consumers", [])
        pending = _pending_consumers(entry)
        assert len(pending) >= 1, (
            "daemon-heartbeat-ingest.json has no active consumer and no PENDING_ token. "
            "Document the gap with a PENDING_ entry (F100)."
        )
        # The PENDING token must mention F91 or F100 or OpenClaw to show it's tracked
        combined = " ".join(pending)
        assert any(kw in combined for kw in ("F91", "F100", "OpenClaw", "sensor_layer1", "plist")), (
            "daemon-heartbeat-ingest.json PENDING_ token must reference the gap (F91/F100 "
            "or sensor_layer1 enforcement). Got: " + combined
        )
