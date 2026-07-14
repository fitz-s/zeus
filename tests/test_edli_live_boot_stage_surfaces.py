# Created: 2026-05-31
# Last reused or audited: 2026-07-14
# Authority basis: live-only EDLI stage readiness; code identity is deployment
#   observability while source/status freshness remains runtime authority.
#
# Lifecycle: created=2026-05-31; last_reviewed=2026-07-14; last_reused=2026-07-14
# Purpose: Prove live mode fails closed on absent stage surfaces and resolves
#   relative state/* paths through ZEUS_PRIMARY_ROOT/state.
# Reuse: Verify _assert_edli_stage_readiness and the world-connection helper names
#   are unchanged before reusing.
#
# Relationship invariant (_assert_edli_stage_readiness -> daemon boot):
#   For edli_live, absent or stale stage surfaces MUST cause a RuntimeError; valid
#   stage surfaces under the canonical runtime root allow boot.

import sqlite3
import json
from datetime import datetime, timezone

import pytest

import src.config as zeus_config
import src.main as zeus_main
from src.main import _assert_edli_stage_readiness

_VALID_TEST_SHA = "a" * 40


def _empty_world_conn():
    """In-memory SQLite with the tables _assert_edli_stage_readiness probes."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE edli_live_order_projection (pending_reconcile INTEGER)"
    )
    conn.execute(
        "CREATE TABLE edli_live_cap_usage (reservation_status TEXT)"
    )
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def patch_world_connection(monkeypatch):
    """Redirect world DB reads to an empty in-memory DB for every test here."""
    conn = _empty_world_conn()
    monkeypatch.setattr(
        zeus_main, "_edli_stage_world_connection", lambda path: conn, raising=True
    )
    yield
    conn.close()


def test_live_boot_blocks_on_absent_stage_surfaces(tmp_path):
    """Gate must NOT be relaxed globally: edli_live MUST raise when stage surfaces
    are absent. Only shadow gets the deferred-surface treatment. (Wave-2 item 5:
    canary collapsed into edli_live — the boot-block readiness path is unchanged.)"""
    live_cfg = {
        "live_execution_mode": "edli_live",
        "edli_stage_loaded_sha_file": str(tmp_path / "loaded_sha.json"),
        "edli_stage_source_health_json": str(tmp_path / "source_health.json"),
        "edli_stage_status_json": str(tmp_path / "status_summary.json"),
        "edli_live_promotion_artifact_path": str(tmp_path / "promotion.json"),
    }
    # No surfaces exist.
    with pytest.raises(RuntimeError, match="EDLI_LIVE_READINESS_FAIL"):
        _assert_edli_stage_readiness(live_cfg)


def test_live_stage_loaded_sha_invalid_shape_is_observed(tmp_path):
    loaded = tmp_path / "loaded_sha.json"
    loaded.write_text(json.dumps({"loaded_sha": "abc123"}))

    reasons = zeus_main._edli_stage_loaded_sha_observations(str(loaded))

    assert reasons == ["EDLI_STAGE_LOADED_SHA_INVALID_VALUE:abc123"]


def test_live_stage_loaded_sha_is_not_a_readiness_gate(tmp_path, caplog):
    now = datetime.now(timezone.utc).isoformat()
    source = tmp_path / "source_health.json"
    status = tmp_path / "status_summary.json"
    source.write_text(json.dumps({"generated_at": now, "sources": {}}))
    status.write_text(json.dumps({"generated_at": now}))

    with caplog.at_level("WARNING", logger="zeus"):
        result = _assert_edli_stage_readiness(
            {
                "live_execution_mode": "edli_live",
                "edli_stage_loaded_sha_file": str(tmp_path / "missing_loaded_sha.json"),
                "edli_stage_source_health_json": str(source),
                "edli_stage_status_json": str(status),
                "edli_live_promotion_artifact_path": str(tmp_path / "promotion.json"),
            }
        )

    assert result.status == zeus_main.EDLI_STAGE_PASS
    assert result.submit_allowed is True
    assert "EDLI_STAGE_LOADED_SHA_MISSING" in caplog.text


def test_live_stage_relative_state_paths_resolve_against_runtime_root(tmp_path, monkeypatch):
    """Relative state/* stage surfaces must use ZEUS_PRIMARY_ROOT state, not cwd.

    Live launchd runs from the deploy worktree but points ZEUS_PRIMARY_ROOT at the
    canonical runtime root. A relative config value like state/loaded_sha.json is
    therefore a runtime-state path, never a worktree-local artifact.
    """
    runtime_state = tmp_path / "runtime" / "state"
    runtime_state.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    (runtime_state / "loaded_sha.json").write_text(json.dumps({"loaded_sha": _VALID_TEST_SHA, "generated_at": now}))
    (runtime_state / "source_health.json").write_text(json.dumps({"generated_at": now, "sources": {}}))
    (runtime_state / "status_summary.json").write_text(json.dumps({"generated_at": now}))

    monkeypatch.setattr(zeus_config, "RUNTIME_ROOT", tmp_path / "runtime")
    monkeypatch.setattr(zeus_config, "STATE_DIR", runtime_state)
    monkeypatch.setitem(zeus_main._BOOT_STATE, "sha", _VALID_TEST_SHA)

    result = _assert_edli_stage_readiness(
        {
            "live_execution_mode": "edli_live",
            "edli_stage_loaded_sha_file": "state/loaded_sha.json",
            "edli_stage_source_health_json": "state/source_health.json",
            "edli_stage_status_json": "state/status_summary.json",
            "edli_stage_readiness_max_age_seconds": 900,
            "edli_live_promotion_artifact_path": "state/edli_live_promotion_artifact.json",
        }
    )

    assert result.stage == "edli_live"
    assert result.status == zeus_main.EDLI_STAGE_PASS
    assert result.submit_allowed is True
