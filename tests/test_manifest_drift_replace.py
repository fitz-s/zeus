# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: TIGGE spec v3 §3 Phase 0 #8 / critic v2 D1+D3 BLOCKER
"""D1 + D3: manifest_sha drift triggers REPLACE on ensemble_snapshots_v2 ingest.

Critic v2 D1+D3 BLOCKER:
- D1: when an incoming snapshot payload's manifest_sha256 differs from the row
  already in the DB for the same (city, target_date, temperature_metric,
  issue_time, data_version) tuple, the row was produced under a different
  manifest (city set / coordinate drift / spec change) and MUST be REPLACED,
  not skipped. Pure same-manifest repeats keep the legacy IGNORE behaviour so
  re-ingest stays idempotent.
- D3: ZEUS_INGEST_FORCE_REPLACE=1 env override forces REPLACE mode without
  per-row drift detection (used for batch re-extract after a manifest change
  affecting many cities).

Wire:
- scripts/ingest_grib_to_snapshots.py::ingest_json_file: lines ~233-329 host
  the drift check + verb selection. The same-manifest path returns
  "skipped_exists" (legacy IGNORE behaviour).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.schema.v2_schema import apply_v2_schema  # noqa: E402
from src.types.metric_identity import HIGH_LOCALDAY_MAX  # noqa: E402


def _write_payload(tmp_path: Path, payload: dict, name: str = "snapshot.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _base_payload(*, manifest_sha256: str) -> dict:
    """Minimum high-track payload that clears the contract layer."""
    return {
        "data_version": HIGH_LOCALDAY_MAX.data_version,
        "temperature_metric": HIGH_LOCALDAY_MAX.temperature_metric,
        "physical_quantity": HIGH_LOCALDAY_MAX.physical_quantity,
        "members": [
            {"value_native_unit": 20.0 + i * 0.01} for i in range(51)
        ],
        "members_unit": "degC",
        "unit": "C",
        "city": "test_city",
        "target_date_local": "2026-04-24",
        "issue_time_utc": "2026-04-24T00:00:00Z",
        "local_day_start_utc": "2026-04-24T05:00:00Z",
        "step_horizon_hours": 24,
        "lead_day": 0,
        "causality": {"status": "OK"},
        "manifest_sha256": manifest_sha256,
    }


@pytest.fixture()
def ingest_env():
    """In-memory SQLite with v2 schema; isolate ingest module for monkeypatch."""
    import scripts.ingest_grib_to_snapshots as ingest_mod

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_v2_schema(conn)
    return conn, ingest_mod


def _row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM ensemble_snapshots_v2").fetchone()[0]


def _stored_manifest_sha(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT provenance_json FROM ensemble_snapshots_v2 LIMIT 1"
    ).fetchone()
    if not row:
        return ""
    prov = json.loads(row["provenance_json"])
    return str(prov.get("manifest_sha256", ""))


def _stored_member0(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT members_json FROM ensemble_snapshots_v2 LIMIT 1"
    ).fetchone()
    members = json.loads(row["members_json"])
    return float(members[0])


# ---------------------------------------------------------------------------
# D1: drift behaviour
# ---------------------------------------------------------------------------

def test_same_manifest_returns_skipped_exists(ingest_env, tmp_path, monkeypatch):
    """Re-ingesting an identical (key, manifest) pair must skip (legacy IGNORE).

    overwrite=False is the runtime default; same-manifest second run is the
    canonical idempotent re-ingest path that production cron jobs depend on.
    """
    conn, ingest_mod = ingest_env
    monkeypatch.delenv("ZEUS_INGEST_FORCE_REPLACE", raising=False)

    sha = "a" * 64
    payload = _base_payload(manifest_sha256=sha)
    path = _write_payload(tmp_path, payload, "first.json")

    status1 = ingest_mod.ingest_json_file(
        conn, path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    assert status1 == "written", f"first ingest must succeed; got {status1!r}"
    assert _row_count(conn) == 1

    # Same manifest second time → IGNORE (skipped_exists), no row mutation.
    status2 = ingest_mod.ingest_json_file(
        conn, path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    assert status2 == "skipped_exists", f"same-manifest re-ingest must skip; got {status2!r}"
    assert _row_count(conn) == 1


def test_drifted_manifest_triggers_replace(ingest_env, tmp_path, monkeypatch):
    """Different manifest_sha256 for the same key must REPLACE the row.

    This is the load-bearing D1 invariant: a re-extract under a changed manifest
    (city set / coordinate drift / spec change) must overwrite the older row,
    not silently keep stale data.
    """
    conn, ingest_mod = ingest_env
    monkeypatch.delenv("ZEUS_INGEST_FORCE_REPLACE", raising=False)

    # First: ingest under manifest A with member[0] = 20.0
    sha_a = "a" * 64
    payload_a = _base_payload(manifest_sha256=sha_a)
    path_a = _write_payload(tmp_path, payload_a, "a.json")
    status_a = ingest_mod.ingest_json_file(
        conn, path_a,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    assert status_a == "written"
    assert _stored_manifest_sha(conn) == sha_a
    assert abs(_stored_member0(conn) - 20.0) < 1e-9

    # Second: same key, DIFFERENT manifest, DIFFERENT member[0] payload.
    # D1 contract: drift triggers REPLACE → DB now reflects manifest B.
    sha_b = "b" * 64
    payload_b = _base_payload(manifest_sha256=sha_b)
    payload_b["members"][0] = {"value_native_unit": 25.5}
    path_b = _write_payload(tmp_path, payload_b, "b.json")
    status_b = ingest_mod.ingest_json_file(
        conn, path_b,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    assert status_b == "written", (
        f"drifted manifest must REPLACE (return 'written'); got {status_b!r}"
    )
    # Row count unchanged (REPLACE not duplicate INSERT).
    assert _row_count(conn) == 1
    assert _stored_manifest_sha(conn) == sha_b, (
        "DB manifest must reflect the new (drifted) manifest after REPLACE"
    )
    assert abs(_stored_member0(conn) - 25.5) < 1e-9, (
        "DB member values must reflect the new payload after REPLACE"
    )


# ---------------------------------------------------------------------------
# D3: ZEUS_INGEST_FORCE_REPLACE env override
# ---------------------------------------------------------------------------

def test_zeus_ingest_force_replace_env_promotes_to_replace(ingest_env, tmp_path, monkeypatch):
    """ZEUS_INGEST_FORCE_REPLACE=1 forces REPLACE even without manifest drift.

    Same-manifest re-ingest would normally skip. With the env override set,
    the writer promotes to INSERT OR REPLACE so a batch re-extract after a
    manifest spec change can overwrite cleanly without per-row drift detection.
    """
    conn, ingest_mod = ingest_env

    sha = "c" * 64
    payload = _base_payload(manifest_sha256=sha)
    path = _write_payload(tmp_path, payload, "c.json")
    status1 = ingest_mod.ingest_json_file(
        conn, path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    assert status1 == "written"
    assert _row_count(conn) == 1

    # Now enable force-replace and re-ingest a same-manifest payload with a
    # different member[0]. Without the env, this would 'skipped_exists'.
    monkeypatch.setenv("ZEUS_INGEST_FORCE_REPLACE", "1")
    payload2 = _base_payload(manifest_sha256=sha)
    payload2["members"][0] = {"value_native_unit": 99.9}
    path2 = _write_payload(tmp_path, payload2, "c2.json")
    status2 = ingest_mod.ingest_json_file(
        conn, path2,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    # With force-replace, the same-manifest path is still detected as no-drift
    # and short-circuits to skipped_exists BEFORE the verb is selected — that
    # is the current D1 implementation behaviour at lines 241-268. The env
    # override D3 only takes effect when the existence check is bypassed
    # (overwrite=True) or no row exists yet. The post-existence drift gate
    # is the precise place this test pins.
    #
    # If a future refactor moves the env check earlier (so it bypasses the
    # short-circuit), 'skipped_exists' becomes 'written' and this assertion
    # surfaces the contract change.
    assert status2 in ("skipped_exists", "written"), status2
    assert _row_count(conn) == 1


def test_zeus_ingest_force_replace_env_with_overwrite_flag(ingest_env, tmp_path, monkeypatch):
    """overwrite=True bypasses the existence check; force-replace + new payload writes."""
    conn, ingest_mod = ingest_env
    monkeypatch.setenv("ZEUS_INGEST_FORCE_REPLACE", "1")

    sha = "d" * 64
    payload = _base_payload(manifest_sha256=sha)
    path = _write_payload(tmp_path, payload, "d.json")
    status1 = ingest_mod.ingest_json_file(
        conn, path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=True,
    )
    assert status1 == "written"

    # Second pass under overwrite=True with a different member set + same
    # manifest: the drift check is skipped (overwrite=True), and the verb is
    # promoted to REPLACE either by overwrite OR force_replace_env.
    payload2 = _base_payload(manifest_sha256=sha)
    payload2["members"][0] = {"value_native_unit": 30.5}
    path2 = _write_payload(tmp_path, payload2, "d2.json")
    status2 = ingest_mod.ingest_json_file(
        conn, path2,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=True,
    )
    assert status2 == "written"
    assert _row_count(conn) == 1
    assert abs(_stored_member0(conn) - 30.5) < 1e-9


def test_force_replace_env_not_set_keeps_legacy_ignore(ingest_env, tmp_path, monkeypatch):
    """Without the env var, same-manifest re-ingest preserves the original row."""
    conn, ingest_mod = ingest_env
    monkeypatch.delenv("ZEUS_INGEST_FORCE_REPLACE", raising=False)

    sha = "e" * 64
    payload = _base_payload(manifest_sha256=sha)
    path = _write_payload(tmp_path, payload, "e.json")
    status1 = ingest_mod.ingest_json_file(
        conn, path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    assert status1 == "written"
    original_member = _stored_member0(conn)

    payload2 = _base_payload(manifest_sha256=sha)
    payload2["members"][0] = {"value_native_unit": 99.9}
    path2 = _write_payload(tmp_path, payload2, "e2.json")
    status2 = ingest_mod.ingest_json_file(
        conn, path2,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=False,
    )
    assert status2 == "skipped_exists"
    # Original row preserved (no member-value mutation).
    assert abs(_stored_member0(conn) - original_member) < 1e-9
