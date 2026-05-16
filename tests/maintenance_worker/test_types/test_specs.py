# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3, §4, §5 (P5.0a)
#                  SAFETY_CONTRACT.md §"Allowed Targets"
"""
Tests for maintenance_worker.types.specs — TaskSpec, EngineConfig,
TickContext, ProposalManifest, AckState.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from maintenance_worker.types.specs import (
    AckState,
    EngineConfig,
    ProposalManifest,
    TaskSpec,
    TickContext,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

REPO = Path("/repo/zeus")
STATE = REPO / "state/maintenance_state"
EVIDENCE = REPO / "state/maintenance_evidence"


def _config() -> EngineConfig:
    return EngineConfig(
        repo_root=REPO,
        state_dir=STATE,
        evidence_dir=EVIDENCE,
        task_catalog_path=REPO / ".maintenance/task_catalog.yaml",
        safety_contract_path=REPO / ".maintenance/safety_contract.md",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )


# ------------------------------------------------------------------
# TaskSpec
# ------------------------------------------------------------------

class TestTaskSpec:
    def test_defaults(self) -> None:
        ts = TaskSpec(task_id="zero_byte_state_cleanup", description="Remove 0-byte files", schedule="daily")
        assert ts.dry_run_floor_exempt is False
        assert ts.tags == ()

    def test_frozen(self) -> None:
        ts = TaskSpec(task_id="t1", description="d", schedule="daily")
        with pytest.raises((AttributeError, TypeError)):
            ts.task_id = "other"  # type: ignore[misc]

    def test_floor_exempt_flag(self) -> None:
        ts = TaskSpec(
            task_id="zero_byte_state_cleanup",
            description="desc",
            schedule="daily",
            dry_run_floor_exempt=True,
        )
        assert ts.dry_run_floor_exempt is True

    def test_tags_tuple(self) -> None:
        ts = TaskSpec(task_id="t", description="d", schedule="weekly", tags=("hygiene", "docs"))
        assert ts.tags == ("hygiene", "docs")


# ------------------------------------------------------------------
# EngineConfig
# ------------------------------------------------------------------

class TestEngineConfig:
    def test_all_paths_set(self) -> None:
        cfg = _config()
        assert cfg.repo_root == REPO
        assert cfg.state_dir == STATE
        assert cfg.evidence_dir == EVIDENCE

    def test_frozen(self) -> None:
        cfg = _config()
        with pytest.raises((AttributeError, TypeError)):
            cfg.live_default = True  # type: ignore[misc]

    def test_env_vars_default_empty(self) -> None:
        cfg = _config()
        assert cfg.env_vars == {}

    def test_env_vars_stored(self) -> None:
        cfg = EngineConfig(
            repo_root=REPO,
            state_dir=STATE,
            evidence_dir=EVIDENCE,
            task_catalog_path=REPO / ".maintenance/task_catalog.yaml",
            safety_contract_path=REPO / ".maintenance/safety_contract.md",
            live_default=True,
            scheduler="cron",
            notification_channel="file",
            env_vars={"ZEUS_REPO": str(REPO)},
        )
        assert cfg.env_vars["ZEUS_REPO"] == str(REPO)


# ------------------------------------------------------------------
# TickContext
# ------------------------------------------------------------------

class TestTickContext:
    def test_construction(self) -> None:
        cfg = _config()
        ctx = TickContext(
            run_id="abc-123",
            started_at=datetime(2026, 5, 15, 4, 30, tzinfo=timezone.utc),
            config=cfg,
            invocation_mode="SCHEDULED",
        )
        assert ctx.run_id == "abc-123"
        assert ctx.invocation_mode == "SCHEDULED"

    def test_frozen(self) -> None:
        cfg = _config()
        ctx = TickContext(
            run_id="x", started_at=datetime.now(tz=timezone.utc),
            config=cfg, invocation_mode="MANUAL_CLI",
        )
        with pytest.raises((AttributeError, TypeError)):
            ctx.run_id = "y"  # type: ignore[misc]


# ------------------------------------------------------------------
# ProposalManifest
# ------------------------------------------------------------------

class TestProposalManifest:
    def test_defaults(self) -> None:
        pm = ProposalManifest(task_id="archive_task")
        assert pm.proposed_moves == ()
        assert pm.proposed_deletes == ()
        assert pm.proposed_creates == ()
        assert pm.proposal_hash == ""

    def test_frozen(self) -> None:
        pm = ProposalManifest(task_id="t")
        with pytest.raises((AttributeError, TypeError)):
            pm.proposal_hash = "abc"  # type: ignore[misc]

    def test_moves_stored(self) -> None:
        src = REPO / "docs/operations/task_2026-05-01_old"
        dst = REPO / "docs/operations/archive/2026-Q2/task_2026-05-01_old"
        pm = ProposalManifest(
            task_id="archive_task",
            proposed_moves=((src, dst),),
            proposal_hash="sha256abc",
        )
        assert pm.proposed_moves[0] == (src, dst)
        assert pm.proposal_hash == "sha256abc"


# ------------------------------------------------------------------
# AckState
# ------------------------------------------------------------------

class TestAckState:
    def test_defaults(self) -> None:
        ack = AckState(task_id="t", proposal_hash="h")
        assert ack.acked is False
        assert ack.auto_ack_remaining == 0
        assert ack.acked_at is None

    def test_frozen(self) -> None:
        ack = AckState(task_id="t", proposal_hash="h")
        with pytest.raises((AttributeError, TypeError)):
            ack.acked = True  # type: ignore[misc]

    def test_acked_state(self) -> None:
        ts = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
        ack = AckState(task_id="t", proposal_hash="h", acked=True, acked_at=ts)
        assert ack.acked is True
        assert ack.acked_at == ts

    def test_auto_ack_n(self) -> None:
        ack = AckState(task_id="t", proposal_hash="h", auto_ack_remaining=3)
        assert ack.auto_ack_remaining == 3
