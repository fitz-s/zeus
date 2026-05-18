# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/TRIVIAL_BATCH_NOTES.md (F89 RETRACT 3213fc2c),
#   docs/operations/task_2026-05-16_post_pr126_audit/RUN_15_track3_f91_f86_observability.md §F101
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F89 semantic retract + F101 heartbeat schema registry documentation antibody.
# Reuse: Run directly; no setup required. Update HEARTBEAT_SCHEMA_REGISTRY when new writers are added.
"""F89 + F101 antibodies.

F89 (RETRACT): heartbeat-sensor plist PID='-' between firings is CORRECT for
StartCalendarInterval / StartInterval plists. launchd shows PID='-' when a
calendar-triggered job is not currently executing. This is NOT a daemon crash.
The antibody documents this semantic to prevent future misreads.

F101 (schema drift): 5 heartbeat writers use 5 different payload schemas.
Unification is deferred (excluded surface); this test documents each writer's
declared payload keys so a generic checker can be written without reverse-
engineering from runtime behavior. No runtime behavior changed.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# F101: declared schema per heartbeat writer
# ---------------------------------------------------------------------------

HEARTBEAT_SCHEMA_REGISTRY: dict[str, dict] = {
    "daemon-heartbeat.json": {
        "writer": "src/main.py",
        "declared_fields": ["alive", "timestamp", "mode"],
        "note": "3-field alive signal written by _write_heartbeat() in live-trading daemon.",
    },
    "daemon-heartbeat-ingest.json": {
        "writer": "src/ingest_main.py",
        "declared_fields": ["daemon", "alive_at", "pid"],
        "note": "3-field alive signal; different key names than HB-1 (alive_at vs timestamp).",
    },
    "forecast-live-heartbeat.json": {
        "writer": "src/ingest/forecast_live_daemon.py",
        "declared_fields": ["cadence_seconds", "daemon", "jobs", "pid", "status", "timestamp", "written_at"],
        "note": "7-field rich heartbeat; written every 30s + on start/scheduler_ready/stopping.",
    },
    "venue-heartbeat-keeper.json": {
        "writer": "src/control/heartbeat_supervisor.py",
        "declared_fields": [
            "consecutive_failures", "consecutive_successes", "health", "heartbeat_id",
            "last_error", "last_success_at", "last_failure_at", "lease_continuous_since",
            "lease_gap_suspected_until", "owner", "resting_order_safe", "schema_version",
            "written_at",
        ],
        "note": "13-field rich health detail; schema_version field present.",
    },
    "oracle_error_rates.heartbeat.json": {
        "writer": "scripts/bridge_oracle_to_calibration.py",
        "declared_fields": ["sha256", "written_at"],
        "note": "Artifact-derived sidecar; keys are artifact metadata + sha256.",
    },
}


class TestF89StartCalendarIntervalPidBehavior:
    """Probe 1: heartbeat-sensor plist uses StartCalendarInterval (not KeepAlive)."""

    def test_heartbeat_sensor_plist_uses_start_calendar_interval(self):
        """F89 antibody: heartbeat-sensor is a cron-triggered job, not a daemon.

        PID='-' in `launchctl list | grep heartbeat-sensor` between firings is
        CORRECT behavior — it means the job is not currently executing.
        This differs from PID='-' on a KeepAlive daemon, which means it crashed.

        Verify the plist uses StartCalendarInterval or StartInterval (confirming
        it is a one-shot trigger, not a persistent daemon).
        """
        plist = Path.home() / "Library" / "LaunchAgents" / "com.zeus.heartbeat-sensor.plist"
        if not plist.exists():
            # Plist not yet installed; check proposed plist
            proposed = Path.home() / "Library" / "LaunchAgents" / "com.zeus.heartbeat-sensor.plist.proposed"
            if not proposed.exists():
                # Neither installed — skip; document intent only
                import pytest
                pytest.skip(
                    "com.zeus.heartbeat-sensor.plist not installed. "
                    "F89 documents that PID='-' is correct for StartCalendarInterval plists."
                )
            plist = proposed

        content = plist.read_text()
        has_calendar = "StartCalendarInterval" in content
        has_interval = "StartInterval" in content
        assert has_calendar or has_interval, (
            "heartbeat-sensor plist must use StartCalendarInterval or StartInterval — "
            "confirming it is a cron-triggered job. PID='-' between firings is CORRECT. "
            "F89 RETRACTED: not a daemon crash (see TRIVIAL_BATCH_NOTES.md commit 3213fc2c)."
        )
        # Must NOT use KeepAlive (which would make PID='-' a crash signal)
        assert "KeepAlive" not in content or "<false/>" in content.split("KeepAlive")[1][:50], (
            "heartbeat-sensor plist must not use KeepAlive=true — it is a one-shot trigger"
        )


class TestF101HeartbeatSchemaRegistry:
    """F101 antibody: declare each writer's payload keys so a generic checker can be written.

    Unification is deferred; this registry makes the 5 schemas visible to CI.
    """

    def test_all_schema_entries_have_declared_fields(self):
        """Probe 1: every registry entry must declare at least one field."""
        for artifact, entry in HEARTBEAT_SCHEMA_REGISTRY.items():
            assert "declared_fields" in entry, (
                f"{artifact}: missing declared_fields in HEARTBEAT_SCHEMA_REGISTRY (F101)"
            )
            assert len(entry["declared_fields"]) > 0, (
                f"{artifact}: declared_fields is empty (F101)"
            )

    def test_five_writers_have_different_schemas(self):
        """Probe 2: schemas differ across writers — unification gap is visible."""
        field_sets = [
            frozenset(entry["declared_fields"])
            for entry in HEARTBEAT_SCHEMA_REGISTRY.values()
        ]
        # At least 3 distinct schemas (they actually have 5, but HB-1/HB-2 share field count)
        unique_schemas = set(field_sets)
        assert len(unique_schemas) >= 3, (
            "Expected ≥3 distinct heartbeat schemas across 5 writers (F101 schema drift). "
            f"Got {len(unique_schemas)} distinct schemas."
        )

    def test_written_at_field_naming_inconsistency_documented(self):
        """Probe 3: document that 'timestamp' vs 'written_at' vs 'alive_at' drift exists.

        HB-1 uses 'timestamp', HB-2 uses 'alive_at', HB-3/HB-4 use 'written_at'.
        A unified envelope would use one canonical time field.
        """
        time_field_names: set[str] = set()
        time_synonyms = {"timestamp", "written_at", "alive_at", "last_success_at"}
        for entry in HEARTBEAT_SCHEMA_REGISTRY.values():
            for field in entry["declared_fields"]:
                if field in time_synonyms:
                    time_field_names.add(field)

        assert len(time_field_names) >= 2, (
            "Expected ≥2 distinct time-field names across writers to document drift (F101). "
            f"Got: {time_field_names}"
        )
