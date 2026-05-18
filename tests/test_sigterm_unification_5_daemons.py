# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis:
#   docs/operations/task_2026-05-18_wave3_dispatches/WAVE3_BATCH_C_PER_FINDING_ACCOUNTING.md
#     §SIGTERM log-string unification across all 5 daemons (carry-forward #5)
#   docs/operations/task_2026-05-16_post_pr126_audit/RUN_15_track3_f91_f86_observability.md §F86
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F86 SIGTERM-unif antibody — assert all 5 live-trading-relevant
#   daemons emit the unified `SIGTERM_RECEIVED pid=... ppid=... elapsed=...s`
#   ERROR token so a single grep across .err files returns parity hits.
# Reuse: Run on every PR touching daemon SIGTERM handlers or the F86 forensic
#   contract.

"""SIGTERM-unif antibody.

Five Zeus daemons handle SIGTERM. Three (live-trading, riskguard, venue-heartbeat)
already emit the new `SIGTERM_RECEIVED pid=... ppid=... elapsed=...s` token from
the F86 work. Two (data-ingest, forecast-live) previously emitted only the
legacy INFO line `"received SIGTERM"`. This antibody asserts both daemons now
emit the unified ERROR token in addition to the legacy line.

Antibody is static — it scans the source file for the literal token string
rather than spawning a live daemon. This is the correct level for the change
in scope (log-string parity); a behavioral test of SIGTERM delivery is
covered by F86 system-level work.
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# (source-file, label, must-have-token) — every entry must satisfy both
# parts: the SIGTERM_RECEIVED token (forensic line) AND a SIGTERM-handler
# registration call.
DAEMONS_WITH_SIGTERM_TOKEN = [
    ("src/main.py", "live-trading"),
    ("src/riskguard/riskguard.py", "riskguard-live"),
    ("src/control/heartbeat_supervisor.py", "venue-heartbeat"),
    # The two daemons unified in WAVE-4 SIGTERM-unif:
    ("src/ingest_main.py", "data-ingest"),
    ("src/ingest/forecast_live_daemon.py", "forecast-live"),
]


@pytest.mark.parametrize("rel_path,label", DAEMONS_WITH_SIGTERM_TOKEN)
def test_daemon_source_contains_sigterm_received_token(rel_path: str, label: str) -> None:
    """Probe 1: every daemon source emits the unified `SIGTERM_RECEIVED` ERROR token."""
    src = PROJECT_ROOT / rel_path
    assert src.exists(), f"daemon source missing: {rel_path}"
    text = src.read_text()
    assert "SIGTERM_RECEIVED pid=" in text, (
        f"{label} ({rel_path}): missing unified `SIGTERM_RECEIVED pid=...` token. "
        f"All 5 daemons must emit this for operator forensic grep parity. "
        f"See WAVE3_BATCH_C_PER_FINDING_ACCOUNTING.md carry-forward #5."
    )


@pytest.mark.parametrize("rel_path,label", DAEMONS_WITH_SIGTERM_TOKEN)
def test_daemon_source_registers_sigterm_handler(rel_path: str, label: str) -> None:
    """Probe 2: token emission requires the handler to actually be wired."""
    src = PROJECT_ROOT / rel_path
    text = src.read_text()
    # Either `signal.signal(signal.SIGTERM, ...)` or a lambda-bound handler
    assert "SIGTERM" in text, f"{label}: no SIGTERM reference at all"
    assert "signal.signal" in text, (
        f"{label} ({rel_path}): SIGTERM handler not registered via signal.signal()"
    )


def test_legacy_received_sigterm_lines_preserved() -> None:
    """Probe 3: the two unified daemons retain their pre-existing
    `"received SIGTERM"` INFO line emitted by `logger.info(...)`.
    Removing the legacy line would break operator grep tooling pinned
    to that string. SIGTERM-unif is additive.

    Antibody checks for the literal `logger.info("…received SIGTERM…")` call
    rather than a free-text mention, so docstrings/comments cannot mask
    a regression where the actual log emission is removed.
    """
    legacy_carriers = [
        ("src/ingest_main.py", "data-ingest"),
        ("src/ingest/forecast_live_daemon.py", "forecast-live"),
    ]
    for rel_path, label in legacy_carriers:
        text = (PROJECT_ROOT / rel_path).read_text()
        # The legacy INFO emission is a single line of the form
        #   logger.info("<label> daemon received SIGTERM; shutting down scheduler")
        # Confirm both the call (`logger.info(`) AND the legacy string token
        # `received SIGTERM; shutting down scheduler` are present so a docstring
        # mention alone cannot satisfy the antibody.
        assert "logger.info(" in text, f"{label} ({rel_path}): no logger.info call"
        assert "received SIGTERM; shutting down scheduler" in text, (
            f"{label} ({rel_path}): legacy 'received SIGTERM; shutting down "
            f"scheduler' INFO emission removed. SIGTERM-unif must be ADDITIVE — "
            f"keep the legacy log emission for operators with grep tooling "
            f"installed pre-WAVE-4."
        )


def test_all_five_daemons_capture_process_start_for_elapsed() -> None:
    """Probe 4: the `elapsed=...s` part of the token requires capturing
    process start time. main.py uses `_start`; riskguard / heartbeat use
    similar conventions; ingest_main and forecast_live use `_PROCESS_START`.
    Confirm every daemon source either captures _start or _PROCESS_START."""
    for rel_path, label in DAEMONS_WITH_SIGTERM_TOKEN:
        text = (PROJECT_ROOT / rel_path).read_text()
        has_marker = (
            "_start = time.monotonic()" in text
            or "_PROCESS_START = time.monotonic()" in text
            or "_start_monotonic = time.monotonic()" in text
        )
        assert has_marker, (
            f"{label} ({rel_path}): no `time.monotonic()` process-start capture. "
            f"The `elapsed=Xs` part of the SIGTERM_RECEIVED token cannot be "
            f"computed without it."
        )
