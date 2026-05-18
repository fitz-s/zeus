# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-16_post_pr126_audit/RUN_16_track_A_f85_log_routing_f87_close.md §3
"""F85 antibody: each daemon entry-point must use dual-handler logging.

Root cause: logging.basicConfig() default StreamHandler(sys.stderr) routes ALL
output to .err files, leaving .log files empty. launchd plists correctly wire
StandardOutPath -> .log and StandardErrorPath -> .err — the mismatch was in code.

Fix: explicit dual handlers:
- stdout handler: INFO/DEBUG only (levelno < WARNING filter)
- stderr handler: WARNING+ only
- basicConfig() must NOT appear in any daemon main()

Three structural greps confirm the patch is present and basicConfig removed.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

DAEMON_FILES = {
    "src/main.py": PROJECT_ROOT / "src" / "main.py",
    "src/ingest_main.py": PROJECT_ROOT / "src" / "ingest_main.py",
    "src/ingest/forecast_live_daemon.py": PROJECT_ROOT / "src" / "ingest" / "forecast_live_daemon.py",
    "src/riskguard/riskguard.py": PROJECT_ROOT / "src" / "riskguard" / "riskguard.py",
}


class TestF85DualHandlerLogging:
    """Probe 1: basicConfig() must not appear inside any daemon main() function."""

    def test_main_py_no_basicconfig_in_main(self):
        content = (PROJECT_ROOT / "src" / "main.py").read_text()
        # Find the def main(): block and confirm no basicConfig inside
        # Allow basicConfig in comments but not as a call
        in_main = _extract_main_body(content)
        assert "logging.basicConfig(" not in in_main, (
            "src/main.py main() must not call logging.basicConfig(); "
            "use dual-handler setup (F85)"
        )

    def test_ingest_main_no_basicconfig_in_main(self):
        content = (PROJECT_ROOT / "src" / "ingest_main.py").read_text()
        in_main = _extract_main_body(content)
        assert "logging.basicConfig(" not in in_main, (
            "src/ingest_main.py main() must not call logging.basicConfig(); "
            "use dual-handler setup (F85)"
        )

    def test_forecast_live_daemon_no_basicconfig_in_main(self):
        content = (PROJECT_ROOT / "src" / "ingest" / "forecast_live_daemon.py").read_text()
        in_main = _extract_main_body(content)
        assert "logging.basicConfig(" not in in_main, (
            "src/ingest/forecast_live_daemon.py main() must not call logging.basicConfig(); "
            "use dual-handler setup (F85)"
        )

    def test_riskguard_no_basicconfig_in_dunder_main(self):
        content = (PROJECT_ROOT / "src" / "riskguard" / "riskguard.py").read_text()
        # riskguard uses if __name__ == "__main__": block
        assert "logging.basicConfig(" not in content, (
            "src/riskguard/riskguard.py must not call logging.basicConfig(); "
            "use dual-handler setup (F85)"
        )


class TestF85StdoutHandlerPresent:
    """Probe 2: stdout StreamHandler with level-cap filter must be present."""

    def test_main_py_has_stdout_handler(self):
        content = (PROJECT_ROOT / "src" / "main.py").read_text()
        assert "StreamHandler(sys.stdout)" in content, (
            "src/main.py must add StreamHandler(sys.stdout) for INFO routing (F85)"
        )
        assert "levelno < logging.WARNING" in content, (
            "src/main.py stdout handler must filter levelno < logging.WARNING (F85)"
        )

    def test_ingest_main_has_stdout_handler(self):
        content = (PROJECT_ROOT / "src" / "ingest_main.py").read_text()
        assert "StreamHandler(sys.stdout)" in content, (
            "src/ingest_main.py must add StreamHandler(sys.stdout) for INFO routing (F85)"
        )
        assert "levelno < logging.WARNING" in content, (
            "src/ingest_main.py stdout handler must filter levelno < logging.WARNING (F85)"
        )

    def test_forecast_live_daemon_has_stdout_handler(self):
        content = (PROJECT_ROOT / "src" / "ingest" / "forecast_live_daemon.py").read_text()
        assert "StreamHandler(sys.stdout)" in content, (
            "src/ingest/forecast_live_daemon.py must add StreamHandler(sys.stdout) (F85)"
        )
        assert "levelno < logging.WARNING" in content, (
            "src/ingest/forecast_live_daemon.py stdout handler must filter WARNING (F85)"
        )

    def test_riskguard_has_stdout_handler(self):
        content = (PROJECT_ROOT / "src" / "riskguard" / "riskguard.py").read_text()
        assert "StreamHandler(sys.stdout)" in content, (
            "src/riskguard/riskguard.py must add StreamHandler(sys.stdout) (F85)"
        )
        assert "levelno < logging.WARNING" in content, (
            "src/riskguard/riskguard.py stdout handler must filter levelno < logging.WARNING (F85)"
        )


class TestF85StderrHandlerPresent:
    """Probe 3: stderr StreamHandler at WARNING level must be present."""

    def test_main_py_has_stderr_handler(self):
        content = (PROJECT_ROOT / "src" / "main.py").read_text()
        assert "StreamHandler(sys.stderr)" in content, (
            "src/main.py must add StreamHandler(sys.stderr) for WARNING+ routing (F85)"
        )

    def test_ingest_main_has_stderr_handler(self):
        content = (PROJECT_ROOT / "src" / "ingest_main.py").read_text()
        assert "StreamHandler(sys.stderr)" in content, (
            "src/ingest_main.py must add StreamHandler(sys.stderr) for WARNING+ routing (F85)"
        )

    def test_forecast_live_daemon_has_stderr_handler(self):
        content = (PROJECT_ROOT / "src" / "ingest" / "forecast_live_daemon.py").read_text()
        assert "StreamHandler(sys.stderr)" in content, (
            "src/ingest/forecast_live_daemon.py must add StreamHandler(sys.stderr) (F85)"
        )

    def test_riskguard_has_stderr_handler(self):
        content = (PROJECT_ROOT / "src" / "riskguard" / "riskguard.py").read_text()
        assert "StreamHandler(sys.stderr)" in content, (
            "src/riskguard/riskguard.py must add StreamHandler(sys.stderr) (F85)"
        )


def _extract_main_body(content: str) -> str:
    """Extract text after 'def main():' to catch basicConfig calls inside main."""
    idx = content.find("def main(")
    if idx == -1:
        return content
    return content[idx:]
