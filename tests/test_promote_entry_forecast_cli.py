# Created: 2026-05-12
# Last reused/audited: 2026-05-12
# Authority basis: Tests for src/control/cli/promote_entry_forecast.py operator CLI.
"""Tests for the promote_entry_forecast operator CLI.

Isolated via tmp_path + monkeypatch — no production state is mutated.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from src.config import EntryForecastConfig, EntryForecastRolloutMode, settings as global_settings
from src.control.cli import promote_entry_forecast as cli
from src.control.entry_forecast_promotion_evidence_io import (
    DEFAULT_PROMOTION_EVIDENCE_PATH,
    clear_evidence_read_cache,
    write_promotion_evidence,
)
from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
from src.data.live_entry_status import LiveEntryForecastStatus


@pytest.fixture
def isolated_evidence_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "state" / "entry_forecast_promotion_evidence.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)
    # Same module-global is captured separately by the IO layer; clear cache so the
    # module-level lru_cache doesn't return stale parses across tests.
    clear_evidence_read_cache()
    yield target
    clear_evidence_read_cache()


@pytest.fixture
def patched_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect cli.state_path to write under tmp_path/state."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    def _state_path(filename: str) -> Path:
        return state_dir / filename

    monkeypatch.setattr(cli, "state_path", _state_path)
    return state_dir


@pytest.fixture
def fake_cfg(monkeypatch: pytest.MonkeyPatch) -> EntryForecastConfig:
    cfg = cli.entry_forecast_config()
    monkeypatch.setattr(cli, "entry_forecast_config", lambda: cfg)
    return cfg


@pytest.fixture
def fake_status_snapshot(monkeypatch: pytest.MonkeyPatch) -> LiveEntryForecastStatus:
    snapshot = LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=42,
        producer_readiness_count=10,
        producer_live_eligible_count=10,
    )

    def _build(_conn, *, config, now_utc=None):  # noqa: ARG001
        return snapshot

    monkeypatch.setattr(cli, "build_live_entry_forecast_status", _build)
    return snapshot


@pytest.fixture
def fake_db(tmp_path: Path) -> Path:
    """Create an empty sqlite db so _open_db_readonly() succeeds."""
    db = tmp_path / "fake.db"
    conn = sqlite3.connect(db)
    conn.close()
    return db


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_subcommand_no_evidence_file_present(
    isolated_evidence_path: Path,
    fake_cfg: EntryForecastConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert not isolated_evidence_path.exists()
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "<no evidence file present>" in out
    assert "rollout_decision" in out


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def test_propose_dry_run_does_not_write(
    isolated_evidence_path: Path,
    fake_cfg: EntryForecastConfig,
    fake_status_snapshot: LiveEntryForecastStatus,
    fake_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    g1 = tmp_path / "g1_evidence.txt"
    g1.write_text("ok")
    rc = cli.main(
        [
            "propose",
            "--operator-approval-id",
            "OPS-2026-05-12-test-001",
            "--g1-evidence-id",
            str(g1),
            "--canary-success-evidence-id",
            "CANARY-OK",
            "--db",
            str(fake_db),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert not isolated_evidence_path.exists()


def test_propose_commit_writes_atomically(
    isolated_evidence_path: Path,
    fake_cfg: EntryForecastConfig,
    fake_status_snapshot: LiveEntryForecastStatus,
    fake_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    g1 = tmp_path / "g1_evidence.txt"
    g1.write_text("ok")
    rc = cli.main(
        [
            "propose",
            "--operator-approval-id",
            "OPS-2026-05-12-test-002",
            "--g1-evidence-id",
            str(g1),
            "--canary-success-evidence-id",
            "CANARY-OK",
            "--db",
            str(fake_db),
            "--evidence-path",
            str(isolated_evidence_path),
            "--commit",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "WROTE" in out
    assert isolated_evidence_path.exists()
    payload = json.loads(isolated_evidence_path.read_text())
    assert payload["operator_approval_id"] == "OPS-2026-05-12-test-002"
    assert payload["g1_evidence_id"] == str(g1)
    assert payload["canary_success_evidence_id"] == "CANARY-OK"
    assert payload["calibration_promotion_approved"] is True
    assert payload["status_snapshot"]["status"] == "LIVE_ELIGIBLE"


def test_propose_rejects_bad_operator_approval_id(
    isolated_evidence_path: Path,
    fake_cfg: EntryForecastConfig,
    fake_status_snapshot: LiveEntryForecastStatus,
    fake_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    g1 = tmp_path / "g1.txt"
    g1.write_text("x")
    rc = cli.main(
        [
            "propose",
            "--operator-approval-id",
            "BAD-FORMAT",
            "--g1-evidence-id",
            str(g1),
            "--db",
            str(fake_db),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "operator-approval-id" in err


# ---------------------------------------------------------------------------
# flip-mode
# ---------------------------------------------------------------------------


def _force_cfg_mode(monkeypatch: pytest.MonkeyPatch, mode: EntryForecastRolloutMode) -> None:
    base = cli.entry_forecast_config()
    from dataclasses import replace as dc_replace

    patched = dc_replace(base, rollout_mode=mode)
    monkeypatch.setattr(cli, "entry_forecast_config", lambda: patched)


def test_flip_mode_to_live_requires_canary_evidence(
    isolated_evidence_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _force_cfg_mode(monkeypatch, EntryForecastRolloutMode.CANARY)
    # write evidence WITHOUT canary_success_evidence_id
    snapshot = LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=1,
        producer_readiness_count=1,
        producer_live_eligible_count=1,
    )
    write_promotion_evidence(
        EntryForecastPromotionEvidence(
            operator_approval_id="OPS-2026-05-12-x",
            g1_evidence_id="g1",
            status_snapshot=snapshot,
            calibration_promotion_approved=True,
            canary_success_evidence_id=None,
        ),
        path=isolated_evidence_path,
    )
    rc = cli.main(["flip-mode", "live", "--evidence-path", str(isolated_evidence_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "canary_success_evidence_id" in err


def test_flip_mode_to_canary_from_shadow_ok(
    isolated_evidence_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _force_cfg_mode(monkeypatch, EntryForecastRolloutMode.SHADOW)
    # write evidence so the predicted decision under canary is CANARY_ELIGIBLE
    snapshot = LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=1,
        producer_readiness_count=1,
        producer_live_eligible_count=1,
    )
    write_promotion_evidence(
        EntryForecastPromotionEvidence(
            operator_approval_id="OPS-2026-05-12-y",
            g1_evidence_id="g1",
            status_snapshot=snapshot,
            calibration_promotion_approved=True,
            canary_success_evidence_id="CAN-OK",
        ),
        path=isolated_evidence_path,
    )
    rc = cli.main(["flip-mode", "canary", "--evidence-path", str(isolated_evidence_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ZEUS_ENTRY_FORECAST_ROLLOUT_MODE=canary" in out
    assert "launchctl kickstart" in out
    assert "NOT EXECUTED" in out


# ---------------------------------------------------------------------------
# unarm
# ---------------------------------------------------------------------------


def test_unarm_dry_run(
    patched_state_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cutover = patched_state_dir / "cutover_guard.json"
    cutover.write_text(json.dumps({"state": "LIVE_ENABLED", "transitions": []}))
    rc = cli.main(["unarm"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    # untouched
    assert json.loads(cutover.read_text())["state"] == "LIVE_ENABLED"
    assert not (patched_state_dir / "auto_pause_failclosed.tombstone").exists()


def test_unarm_commit_writes(
    patched_state_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cutover = patched_state_dir / "cutover_guard.json"
    cutover.write_text(json.dumps({"state": "LIVE_ENABLED", "transitions": []}))
    rc = cli.main(["unarm", "--commit"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(cutover.read_text())
    assert payload["state"] == "NORMAL"
    assert payload["transitions"][-1]["to"] == "NORMAL"
    assert (patched_state_dir / "auto_pause_failclosed.tombstone").exists()
    assert "NOT executed" in out



# ---------------------------------------------------------------------------
# Env override (Task D part 1) + flip-mode --commit (Task D part 2)
# ---------------------------------------------------------------------------


def test_env_override_takes_precedence_over_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """ZEUS_ENTRY_FORECAST_ROLLOUT_MODE overrides settings.json value."""
    from src.config import entry_forecast_config
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_MODE", "shadow")
    cfg = entry_forecast_config()
    assert cfg.rollout_mode.value == "shadow"


def test_env_override_unset_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import entry_forecast_config, settings as global_settings
    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_ROLLOUT_MODE", raising=False)
    cfg = entry_forecast_config()
    assert cfg.rollout_mode.value == global_settings["entry_forecast"]["rollout_mode"]


def test_env_override_invalid_value_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import entry_forecast_config
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_MODE", "BOGUS")
    with pytest.raises(ValueError, match="ZEUS_ENTRY_FORECAST_ROLLOUT_MODE"):
        entry_forecast_config()


def test_env_override_empty_string_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import entry_forecast_config, settings as global_settings
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_MODE", "")
    cfg = entry_forecast_config()
    assert cfg.rollout_mode.value == global_settings["entry_forecast"]["rollout_mode"]


def test_flip_mode_commit_rewrites_settings_json(
    isolated_evidence_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """flip-mode --commit atomically rewrites settings.json rollout_mode."""
    _force_cfg_mode(monkeypatch, EntryForecastRolloutMode.SHADOW)
    snapshot = LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=1,
        producer_readiness_count=1,
        producer_live_eligible_count=1,
    )
    write_promotion_evidence(
        EntryForecastPromotionEvidence(
            operator_approval_id="OPS-2026-05-12-z",
            g1_evidence_id="g1",
            status_snapshot=snapshot,
            calibration_promotion_approved=True,
            canary_success_evidence_id="CAN-OK",
        ),
        path=isolated_evidence_path,
    )

    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text(json.dumps({
        "entry_forecast": {"rollout_mode": "shadow", "source_id": "x"},
        "other_block": {"keep": "me"},
    }, indent=2))

    rc = cli.main([
        "flip-mode", "canary",
        "--evidence-path", str(isolated_evidence_path),
        "--commit",
        "--settings-path", str(fake_settings),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WROTE" in out
    payload = json.loads(fake_settings.read_text())
    assert payload["entry_forecast"]["rollout_mode"] == "canary"
    assert payload["entry_forecast"]["source_id"] == "x"  # preserved
    assert payload["other_block"]["keep"] == "me"  # preserved


def test_flip_mode_commit_no_op_when_already_target(
    isolated_evidence_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _force_cfg_mode(monkeypatch, EntryForecastRolloutMode.CANARY)
    snapshot = LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=1,
        producer_readiness_count=1,
        producer_live_eligible_count=1,
    )
    write_promotion_evidence(
        EntryForecastPromotionEvidence(
            operator_approval_id="OPS-2026-05-12-zz",
            g1_evidence_id="g1",
            status_snapshot=snapshot,
            calibration_promotion_approved=True,
            canary_success_evidence_id="CAN-OK",
        ),
        path=isolated_evidence_path,
    )
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text(json.dumps({
        "entry_forecast": {"rollout_mode": "canary"},
    }))
    rc = cli.main([
        "flip-mode", "canary",
        "--evidence-path", str(isolated_evidence_path),
        "--commit",
        "--settings-path", str(fake_settings),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "NO-OP" in out


def test_flip_mode_dry_run_does_not_write_settings(
    isolated_evidence_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _force_cfg_mode(monkeypatch, EntryForecastRolloutMode.SHADOW)
    snapshot = LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=1,
        producer_readiness_count=1,
        producer_live_eligible_count=1,
    )
    write_promotion_evidence(
        EntryForecastPromotionEvidence(
            operator_approval_id="OPS-2026-05-12-zzz",
            g1_evidence_id="g1",
            status_snapshot=snapshot,
            calibration_promotion_approved=True,
            canary_success_evidence_id="CAN-OK",
        ),
        path=isolated_evidence_path,
    )
    fake_settings = tmp_path / "settings.json"
    original = json.dumps({"entry_forecast": {"rollout_mode": "shadow"}})
    fake_settings.write_text(original)
    rc = cli.main([
        "flip-mode", "canary",
        "--evidence-path", str(isolated_evidence_path),
        "--settings-path", str(fake_settings),
    ])
    assert rc == 0
    assert fake_settings.read_text() == original
