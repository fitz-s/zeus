# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/scheduler_bindings/
"""
Tests for maintenance_worker.cli.scheduler_bindings.

Covers:
- launchd_plist_template: generate_plist produces valid XML, label/program/interval present,
  env_vars block, log paths, XML escaping
- in_process_scheduler: run_once sets MAINTENANCE_IN_PROCESS env var, calls run_tick,
  restores env after call; run_forever stops on KeyboardInterrupt; invalid interval raises
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maintenance_worker.cli.scheduler_bindings.launchd_plist_template import generate_plist
from maintenance_worker.cli.scheduler_bindings.in_process_scheduler import InProcessScheduler
from maintenance_worker.types.specs import EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> EngineConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return EngineConfig(
        repo_root=tmp_path,
        state_dir=state_dir,
        evidence_dir=evidence_dir,
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.md",
        live_default=False,
        scheduler="in_process",
        notification_channel="file",
    )


# ---------------------------------------------------------------------------
# generate_plist
# ---------------------------------------------------------------------------


def test_generate_plist_returns_string() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
    )
    assert isinstance(result, str)


def test_generate_plist_valid_xml() -> None:
    """Output must parse as valid XML."""
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
    )
    # Strip DOCTYPE declaration (may span 2 lines) before parsing;
    # ElementTree does not support DTDs.
    lines = result.splitlines()
    filtered: list[str] = []
    skip_next = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<!DOCTYPE"):
            skip_next = True  # DOCTYPE may continue on next line
            continue
        if skip_next and (stripped.startswith('"') or stripped.startswith("'")):
            skip_next = False
            continue
        skip_next = False
        filtered.append(line)
    xml_part = "\n".join(filtered)
    # Should not raise
    ET.fromstring(xml_part)


def test_generate_plist_contains_label() -> None:
    label = "com.example.maintenance-worker"
    result = generate_plist(
        label=label,
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
    )
    assert label in result


def test_generate_plist_contains_program_path() -> None:
    path = "/opt/venv/bin/python"
    result = generate_plist(
        label="com.example.mw",
        program_path=path,
        working_dir="/var/lib/mw",
    )
    assert path in result


def test_generate_plist_contains_interval() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
        interval_seconds=7200,
    )
    assert "7200" in result


def test_generate_plist_default_interval_3600() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
    )
    assert "3600" in result


def test_generate_plist_env_vars_present() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
        env_vars={"MAINTENANCE_SCHEDULER": "1", "FOO": "bar"},
    )
    assert "MAINTENANCE_SCHEDULER" in result
    assert "FOO" in result
    assert "bar" in result


def test_generate_plist_no_env_vars_when_none() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
        env_vars=None,
    )
    assert "EnvironmentVariables" not in result


def test_generate_plist_log_paths() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
        log_path="/var/log/mw.log",
        error_log_path="/var/log/mw.err",
    )
    assert "/var/log/mw.log" in result
    assert "/var/log/mw.err" in result
    assert "StandardOutPath" in result
    assert "StandardErrorPath" in result


def test_generate_plist_no_log_paths_when_none() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
    )
    assert "StandardOutPath" not in result
    assert "StandardErrorPath" not in result


def test_generate_plist_xml_escaping() -> None:
    """Special XML chars in label are escaped."""
    result = generate_plist(
        label="com.example.mw&test<>",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
    )
    assert "&amp;" in result or "&lt;" in result
    # The unescaped form should NOT appear in the XML content
    # (after the DOCTYPE line which we skip)
    xml_lines = [l for l in result.splitlines() if "<!DOCTYPE" not in l]
    xml_body = "\n".join(xml_lines)
    assert "mw&test" not in xml_body  # raw & must be escaped


def test_generate_plist_contains_working_dir() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/opt/maintenance",
    )
    assert "/opt/maintenance" in result


def test_generate_plist_run_at_load_true() -> None:
    result = generate_plist(
        label="com.example.mw",
        program_path="/usr/local/bin/mw",
        working_dir="/var/lib/mw",
    )
    assert "RunAtLoad" in result
    assert "<true/>" in result


# ---------------------------------------------------------------------------
# InProcessScheduler
# ---------------------------------------------------------------------------


def test_in_process_scheduler_invalid_interval() -> None:
    """interval_seconds < 1 raises ValueError."""
    with pytest.raises(ValueError, match="interval_seconds"):
        InProcessScheduler(MagicMock(), interval_seconds=0)


def test_in_process_scheduler_run_once_calls_run_tick(tmp_path: Path) -> None:
    """run_once invokes run_tick with the config."""
    config = _make_config(tmp_path)
    scheduler = InProcessScheduler(config, interval_seconds=60)

    mock_result = MagicMock()
    with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.run_tick", return_value=mock_result) as mock_run:
        result = scheduler.run_once()

    mock_run.assert_called_once_with(config)
    assert result is mock_result


def test_in_process_scheduler_run_once_sets_env_flag(tmp_path: Path) -> None:
    """run_once sets MAINTENANCE_IN_PROCESS=1 during the tick."""
    config = _make_config(tmp_path)
    scheduler = InProcessScheduler(config, interval_seconds=60)
    observed_env: list[str | None] = []

    def capture_env(cfg):
        observed_env.append(os.environ.get("MAINTENANCE_IN_PROCESS"))
        return MagicMock()

    with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.run_tick", side_effect=capture_env):
        scheduler.run_once()

    assert observed_env == ["1"], f"Expected MAINTENANCE_IN_PROCESS=1 during tick, got: {observed_env}"


def test_in_process_scheduler_run_once_restores_env(tmp_path: Path) -> None:
    """run_once restores MAINTENANCE_IN_PROCESS to its prior value after call."""
    # Ensure env var is unset before the test
    os.environ.pop("MAINTENANCE_IN_PROCESS", None)

    config = _make_config(tmp_path)
    scheduler = InProcessScheduler(config, interval_seconds=60)

    with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.run_tick", return_value=MagicMock()):
        scheduler.run_once()

    assert "MAINTENANCE_IN_PROCESS" not in os.environ, "Env var must be cleaned up after run_once"


def test_in_process_scheduler_run_once_restores_prior_env_value(tmp_path: Path) -> None:
    """If MAINTENANCE_IN_PROCESS was set before, it is restored after run_once."""
    os.environ["MAINTENANCE_IN_PROCESS"] = "prior_value"
    try:
        config = _make_config(tmp_path)
        scheduler = InProcessScheduler(config, interval_seconds=60)

        with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.run_tick", return_value=MagicMock()):
            scheduler.run_once()

        assert os.environ.get("MAINTENANCE_IN_PROCESS") == "prior_value"
    finally:
        os.environ.pop("MAINTENANCE_IN_PROCESS", None)


def test_in_process_scheduler_run_once_calls_callback(tmp_path: Path) -> None:
    """on_tick_complete callback is invoked with TickResult."""
    config = _make_config(tmp_path)
    callback_calls: list = []
    scheduler = InProcessScheduler(config, interval_seconds=60, on_tick_complete=callback_calls.append)

    mock_result = MagicMock()
    with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.run_tick", return_value=mock_result):
        scheduler.run_once()

    assert callback_calls == [mock_result]


def test_in_process_scheduler_run_forever_stops_on_keyboard_interrupt(tmp_path: Path) -> None:
    """run_forever returns cleanly on KeyboardInterrupt."""
    config = _make_config(tmp_path)
    scheduler = InProcessScheduler(config, interval_seconds=1)

    call_count = 0

    def raise_after_one(cfg):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.run_tick", side_effect=raise_after_one):
        with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.time.sleep"):
            # Should not raise — KeyboardInterrupt is caught
            scheduler.run_forever()

    assert call_count >= 1


def test_in_process_scheduler_run_forever_continues_after_tick_error(tmp_path: Path) -> None:
    """run_forever survives a tick exception and fires the next tick."""
    config = _make_config(tmp_path)
    scheduler = InProcessScheduler(config, interval_seconds=1)

    call_count = 0

    def fail_then_interrupt(cfg):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("tick error")
        raise KeyboardInterrupt

    with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.run_tick", side_effect=fail_then_interrupt):
        with patch("maintenance_worker.cli.scheduler_bindings.in_process_scheduler.time.sleep"):
            scheduler.run_forever()

    assert call_count == 2, f"Expected 2 tick calls (1 error + 1 interrupt), got {call_count}"
