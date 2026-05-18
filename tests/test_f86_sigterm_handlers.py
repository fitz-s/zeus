# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-16_post_pr126_audit/RUN_15_track3_f91_f86_observability.md §TASK B
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: Antibody test — verify SIGTERM handlers present in all daemon entry-points and healthcheck reads last_exit_code.
# Reuse: Run directly; no setup required. Structural grep; does not invoke signal handlers at runtime.
"""F86 antibody: SIGTERM handlers and healthcheck.py last_exit_code surface.

Root cause: live-trading, riskguard, and venue-heartbeat daemons had no
signal.signal(SIGTERM, ...) handler. When launchd SIGTERMed them, the process
died silently — no forensic line in .err, no elapsed time, no PID trace.
healthcheck.py also never read `last exit code` from launchctl print output,
so the every-30-min dispatcher path never surfaced prior -15 exits.

Three probe classes:
1. signal.signal(signal.SIGTERM call present in each daemon entry-point
2. SIGTERM_RECEIVED error-log string present (confirms the handler logs)
3. healthcheck.py reads last_exit_code via _first_launchctl_field and flags
   non-zero exits as an issue
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


class TestF86SigtermHandlerPresent:
    """Probe 1: signal.signal(signal.SIGTERM present in each daemon entry-point."""

    def test_main_py_has_sigterm_handler(self):
        content = (PROJECT_ROOT / "src" / "main.py").read_text()
        assert "signal.signal(" in content, (
            "src/main.py must install a SIGTERM handler (F86)"
        )
        assert "signal.SIGTERM" in content, (
            "src/main.py SIGTERM handler must reference signal.SIGTERM (F86)"
        )

    def test_riskguard_has_sigterm_handler(self):
        content = (PROJECT_ROOT / "src" / "riskguard" / "riskguard.py").read_text()
        assert "signal.signal(" in content, (
            "src/riskguard/riskguard.py must install a SIGTERM handler (F86)"
        )
        assert "signal.SIGTERM" in content, (
            "src/riskguard/riskguard.py SIGTERM handler must reference signal.SIGTERM (F86)"
        )

    def test_heartbeat_supervisor_has_sigterm_handler(self):
        content = (PROJECT_ROOT / "src" / "control" / "heartbeat_supervisor.py").read_text()
        assert "signal.signal(" in content, (
            "src/control/heartbeat_supervisor.py must install a SIGTERM handler (F86)"
        )
        assert "signal.SIGTERM" in content, (
            "src/control/heartbeat_supervisor.py SIGTERM handler must reference signal.SIGTERM (F86)"
        )


class TestF86SigtermReceived:
    """Probe 2: handler must emit SIGTERM_RECEIVED error log (forensic trail)."""

    def test_main_py_sigterm_received_log(self):
        content = (PROJECT_ROOT / "src" / "main.py").read_text()
        assert "SIGTERM_RECEIVED" in content, (
            "src/main.py SIGTERM handler must log SIGTERM_RECEIVED (F86 forensic trail)"
        )

    def test_riskguard_sigterm_received_log(self):
        content = (PROJECT_ROOT / "src" / "riskguard" / "riskguard.py").read_text()
        assert "SIGTERM_RECEIVED" in content, (
            "src/riskguard/riskguard.py SIGTERM handler must log SIGTERM_RECEIVED (F86)"
        )

    def test_heartbeat_supervisor_sigterm_received_log(self):
        content = (PROJECT_ROOT / "src" / "control" / "heartbeat_supervisor.py").read_text()
        assert "SIGTERM_RECEIVED" in content, (
            "src/control/heartbeat_supervisor.py SIGTERM handler must log SIGTERM_RECEIVED (F86)"
        )


class TestF86HealthcheckLastExitCode:
    """Probe 3: healthcheck.py must surface last_exit_code from launchctl print."""

    def test_healthcheck_reads_last_exit_code(self):
        content = (PROJECT_ROOT / "scripts" / "healthcheck.py").read_text()
        assert '"last exit code"' in content or "'last exit code'" in content, (
            "scripts/healthcheck.py must call _first_launchctl_field(..., 'last exit code') "
            "to surface prior SIGTERM exits in the 30-min dispatcher path (F86)"
        )

    def test_healthcheck_includes_last_exit_code_in_item(self):
        content = (PROJECT_ROOT / "scripts" / "healthcheck.py").read_text()
        assert "last_exit_code" in content, (
            "scripts/healthcheck.py must include last_exit_code in the health item dict (F86)"
        )

    def test_healthcheck_flags_nonzero_exit_as_issue(self):
        content = (PROJECT_ROOT / "scripts" / "healthcheck.py").read_text()
        assert "loaded_prior_exit_code" in content, (
            "scripts/healthcheck.py must append 'loaded_prior_exit_code_*' to issues "
            "when last_exit_code is non-zero (F86)"
        )
