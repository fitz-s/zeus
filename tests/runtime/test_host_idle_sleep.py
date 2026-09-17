"""The daemons' sleep-prevention assertion must be real, idempotent, and self-releasing."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from src.runtime.host_idle_sleep import hold_system_awake

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="IOKit power assertions are macOS-only")


@darwin_only
def test_assertion_is_visible_to_the_operating_system():
    """The kernel — not our own bookkeeping — must confirm the assertion is held.

    A held-flag we set ourselves would pass a test while the host slept anyway, which is
    exactly the failure this module exists to prevent, so the proof is pmset's own listing.
    """
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess, os;"
            "from src.runtime.host_idle_sleep import hold_system_awake;"
            "assert hold_system_awake('zeus pytest assertion') is True;"
            "print(subprocess.run(['pmset','-g','assertions'],capture_output=True,text=True).stdout)",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": REPO_ROOT},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 0, child.stderr
    held = [line for line in child.stdout.splitlines() if "zeus pytest assertion" in line]
    assert held, f"pmset did not list the assertion:\n{child.stdout[:2000]}"
    assert "PreventUserIdleSystemSleep" in held[0]


@darwin_only
def test_assertion_dies_with_the_process_that_justified_it():
    """No daemon's assertion may outlive the daemon; the kernel owns the release."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.runtime.host_idle_sleep import hold_system_awake;"
            "hold_system_awake('zeus pytest orphan check')",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": REPO_ROOT},
        check=True,
        timeout=60,
    )
    listing = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
    assert "zeus pytest orphan check" not in listing


@darwin_only
def test_second_call_is_a_noop_not_a_second_assertion():
    assert hold_system_awake("zeus pytest idempotence") is True
    assert hold_system_awake("zeus pytest idempotence") is True
    listing = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
    assert listing.count("zeus pytest idempotence") == 1


def test_non_darwin_degrades_without_raising(monkeypatch):
    """Losing sleep prevention degrades freshness; refusing to start loses everything."""
    import src.runtime.host_idle_sleep as mod

    monkeypatch.setattr(mod, "_assertion_id", None)
    monkeypatch.setattr(mod.sys, "platform", "linux")
    assert mod.hold_system_awake("zeus pytest portability") is False
