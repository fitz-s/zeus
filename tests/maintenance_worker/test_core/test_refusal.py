# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/refusal.py
"""
Tests for maintenance_worker.core.refusal.

Covers:
- refuse_fatal: exits non-zero, unique codes per RefusalReason, writes errors.tsv
- refuse_fatal: does NOT write SELF_QUARANTINE (C2 / Path A invariant)
- skip_tick: returns normally (no sys.exit), writes errors.tsv
- exit code uniqueness across all RefusalReasons
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from maintenance_worker.core.refusal import (
    _exit_code_for,
    refuse_fatal,
    skip_tick,
)
from maintenance_worker.types.modes import RefusalReason
from maintenance_worker.types.specs import EngineConfig, TickContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path) -> TickContext:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=evidence_dir,
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.md",
        live_default=False,
        scheduler="launchd",
        notification_channel="file",
    )
    return TickContext(
        run_id="test-run-00000000",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="MANUAL_CLI",
    )


# ---------------------------------------------------------------------------
# refuse_fatal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        RefusalReason.KILL_SWITCH,
        RefusalReason.DIRTY_REPO,
        RefusalReason.ACTIVE_REBASE,
        RefusalReason.LOW_DISK,
        RefusalReason.INFLIGHT_PR,
        RefusalReason.SELF_QUARANTINED,
        RefusalReason.FORBIDDEN_PATH_VIOLATION,
        RefusalReason.FORBIDDEN_OPERATION_VIOLATION,
    ],
)
def test_refuse_fatal_exits_nonzero(reason: RefusalReason, tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        refuse_fatal(reason, ctx)
    assert exc_info.value.code != 0


def test_refuse_fatal_exit_code_unique_per_reason(tmp_path: Path) -> None:
    """Each RefusalReason gets a unique exit code."""
    codes = [_exit_code_for(r) for r in RefusalReason]
    assert len(codes) == len(set(codes)), "Duplicate exit codes detected"


def test_refuse_fatal_writes_errors_tsv(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(SystemExit):
        refuse_fatal(RefusalReason.KILL_SWITCH, ctx, "test kill switch")
    errors_tsv = ctx.config.evidence_dir / "errors.tsv"
    assert errors_tsv.exists()
    content = errors_tsv.read_text()
    assert "KILL_SWITCH" in content
    assert "test kill switch" in content


def test_refuse_fatal_does_not_write_self_quarantine(tmp_path: Path) -> None:
    """
    Critical C2 invariant (Path A): refuse_fatal must NOT write SELF_QUARANTINE.
    SELF_QUARANTINE is written ONLY by post_mutation_detector (Path B).
    """
    ctx = _make_ctx(tmp_path)
    state_dir = ctx.config.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(SystemExit):
        refuse_fatal(RefusalReason.FORBIDDEN_PATH_VIOLATION, ctx)
    quarantine_file = state_dir / "SELF_QUARANTINE"
    assert not quarantine_file.exists(), (
        "refuse_fatal must NOT write SELF_QUARANTINE (Path A invariant violated)"
    )


def test_refuse_fatal_distinct_codes_for_each_hard_reason(tmp_path: Path) -> None:
    """Hard-guard reasons produce distinct exit codes from each other."""
    hard_reasons = [
        RefusalReason.KILL_SWITCH,
        RefusalReason.DIRTY_REPO,
        RefusalReason.ACTIVE_REBASE,
        RefusalReason.LOW_DISK,
        RefusalReason.INFLIGHT_PR,
        RefusalReason.SELF_QUARANTINED,
    ]
    codes = {_exit_code_for(r) for r in hard_reasons}
    assert len(codes) == len(hard_reasons)


# ---------------------------------------------------------------------------
# skip_tick
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [RefusalReason.MAINTENANCE_PAUSED, RefusalReason.ONCALL_QUIET],
)
def test_skip_tick_returns_normally(reason: RefusalReason, tmp_path: Path) -> None:
    """skip_tick must NOT call sys.exit — soft refusal returns normally."""
    ctx = _make_ctx(tmp_path)
    result = skip_tick(reason, ctx)
    assert result is None


def test_skip_tick_writes_errors_tsv(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    skip_tick(RefusalReason.MAINTENANCE_PAUSED, ctx, "paused for deploy")
    errors_tsv = ctx.config.evidence_dir / "errors.tsv"
    assert errors_tsv.exists()
    content = errors_tsv.read_text()
    assert "MAINTENANCE_PAUSED" in content


def test_skip_tick_does_not_exit(tmp_path: Path) -> None:
    """Confirm no SystemExit raised by skip_tick."""
    ctx = _make_ctx(tmp_path)
    try:
        skip_tick(RefusalReason.ONCALL_QUIET, ctx)
    except SystemExit:
        pytest.fail("skip_tick raised SystemExit — must return normally")
