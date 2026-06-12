# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: fix/edli-stage-readiness-2026-05-31 — EDLI shadow boot
#   bootstrap-deadlock fix. CRITICAL: populating edli_stage_{source_health_json,
#   status_json} paths made _assert_edli_stage_readiness() hard-fail at boot when
#   those files are stale/absent (they populate AFTER the scheduler starts). This
#   blocked daemon boot in shadow mode indefinitely.
#
# Lifecycle: created=2026-05-31; last_reviewed=2026-05-31; last_reused=never
# Purpose: Prove the bootstrap deadlock is impossible — shadow mode must survive
#   absent stage surfaces at boot; canary/live must still block on them (the gate
#   must not be relaxed across the board).
# Reuse: Verify _assert_edli_stage_readiness, _EDLI_SHADOW_DEFERRED_REASON_PREFIXES,
#   and the world-connection helper names are unchanged before reusing.
#
# Relationship invariant (_assert_edli_stage_readiness -> daemon boot, cross-mode):
#   When edli_shadow_no_submit is active and stage-surface files are absent/stale,
#   _assert_edli_stage_readiness() MUST NOT raise — it logs a warning and returns.
#   For edli_live, the same absent surfaces MUST cause a RuntimeError.

import sqlite3
import pytest

import src.main as zeus_main
from src.main import _assert_edli_stage_readiness


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


def _shadow_cfg(tmp_path):
    """edli_cfg dict for edli_shadow_no_submit with stage paths absent."""
    return {
        "live_execution_mode": "edli_shadow_no_submit",
        # Stage surface paths point at non-existent files — simulates first boot.
        "edli_stage_loaded_sha_file": "",  # not required in shadow
        "edli_stage_source_health_json": str(tmp_path / "source_health.json"),
        "edli_stage_status_json": str(tmp_path / "status_summary.json"),
    }


def test_shadow_boot_survives_absent_stage_surfaces(tmp_path):
    """Bootstrap-deadlock fix: shadow mode must NOT raise when stage surfaces
    are absent (files do not exist at daemon boot — writers populate post-boot).

    Pre-fix: EDLI_STAGE_READINESS_FAILED was raised, bricking the daemon.
    Post-fix: warning is logged, _assert_edli_stage_readiness returns normally.
    """
    cfg = _shadow_cfg(tmp_path)
    # Surfaces do NOT exist — simulates first boot before scheduler runs.
    assert not (tmp_path / "source_health.json").exists()
    assert not (tmp_path / "status_summary.json").exists()

    # Must not raise.
    result = _assert_edli_stage_readiness(cfg)
    assert result.stage == "edli_shadow_no_submit"
    assert result.live_entries_allowed is False  # shadow never allows live entries


def test_shadow_boot_survives_stale_stage_surfaces(tmp_path):
    """Shadow mode must survive pre-populated but stale stage surfaces too
    (e.g. daemon restart with files older than max_age_seconds)."""
    import json
    from datetime import datetime, timezone, timedelta

    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    (tmp_path / "source_health.json").write_text(
        json.dumps({"generated_at": stale_ts, "sources": {}})
    )
    (tmp_path / "status_summary.json").write_text(
        json.dumps({"generated_at": stale_ts})
    )

    cfg = _shadow_cfg(tmp_path)
    # Must not raise even with stale surfaces.
    result = _assert_edli_stage_readiness(cfg)
    assert result.stage == "edli_shadow_no_submit"


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
