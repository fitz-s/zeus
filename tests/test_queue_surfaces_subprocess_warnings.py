# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: 2026-06-09 K3-invisibility finding — each materialization runs as a
#   subprocess with capture_output=True, so the K3 fusion-degradation WARNINGs (decorrelated-
#   provider INCOMPLETE, fired 19/40 recent cells) landed ONLY in per-request sidecar JSONs and
#   reached NEITHER zeus-forecast-live.log NOR .err. A degradation antibody that warns into a
#   void is structurally deaf.
"""ANTI-SILENT-SINK antibody: subprocess WARNING/ERROR lines must surface at the queue level.

Relationship pinned (materializer subprocess -> daemon log boundary): any WARNING/ERROR line a
materialization subprocess emits is re-logged by the queue processor under the daemon's logging
config, so a fusion degradation can never again be invisible to the operator's log."""
from __future__ import annotations

import logging
import subprocess

from src.data.replacement_forecast_shadow_materialization_queue import (
    _surface_subprocess_warnings,
)


def _completed(stderr: str = "", stdout: str = "", rc: int = 0):
    return subprocess.CompletedProcess(args=["x"], returncode=rc, stdout=stdout, stderr=stderr)


def test_subprocess_warning_lines_are_relogged(caplog) -> None:
    k3_line = (
        "2026-06-09 12:56:13,000 [zeus.replacement_u0r_fusion] WARNING: replacement_0_1 U0R "
        "fusion decorrelated-provider INCOMPLETE for Wuhan high: served 3/4, missing "
        "['CMC/gem_global']"
    )
    with caplog.at_level(logging.WARNING, logger="zeus.replacement_shadow_materialization_queue"):
        _surface_subprocess_warnings("Wuhan.json", _completed(stderr=k3_line + "\nplain info line"))
    surfaced = [r.message for r in caplog.records]
    assert any("decorrelated-provider INCOMPLETE" in m for m in surfaced), (
        "a K3 degradation WARNING emitted inside the materialization subprocess must be "
        "re-logged at the queue level — warning into a sidecar-only void is the failure mode"
    )
    assert any("Wuhan.json" in m for m in surfaced)  # attributable to the request


def test_error_lines_also_surfaced_and_info_lines_not(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="zeus.replacement_shadow_materialization_queue"):
        _surface_subprocess_warnings(
            "x.json",
            _completed(stdout="something ERROR: boom\n2026-06-09 [zeus.foo] INFO: routine line"),
        )
    msgs = [r.message for r in caplog.records]
    assert any("ERROR: boom" in m for m in msgs)
    assert not any("routine line" in m for m in msgs)


def test_surfacing_never_raises_on_garbage() -> None:
    _surface_subprocess_warnings("x.json", _completed(stderr=None))  # type: ignore[arg-type]
    _surface_subprocess_warnings("x.json", object())  # type: ignore[arg-type]
