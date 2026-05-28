# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC review Blocker H + F (SD4). fit_signature_hash must
#   include gate_set_hash + coverage + source-row digest so two rows under different gate
#   generations cannot collide on signature; pair batches must record domain identity.
"""Relationship tests for SD4: fit-signature domain identity (Blocker H).

The pair-batch manifest tests (Blocker F) live alongside once the calibration_pair_batch
table lands. Here we pin the signature invariant: a change to the gate set, the coverage
scope, or the underlying source residuals MUST change fit_signature_hash; identical inputs
MUST map to one signature.
"""
from __future__ import annotations

from scripts.fit_full_transport_error_models import _fit_signature_hash


def _sig(**over):
    base = dict(
        city="NYC", metric="high", season="MAM", live_dv="dvL", prior_dv="dvP",
        kappa=1.0, n_tig=2, n_opd=1,
        gate_set_hash="G1", coverage_months="3,4,5",
        tig_residuals=[1.0, 2.0], opd_residuals=[0.5],
    )
    base.update(over)
    return _fit_signature_hash(
        base["city"], base["metric"], base["season"], base["live_dv"], base["prior_dv"],
        base["kappa"], base["n_tig"], base["n_opd"],
        gate_set_hash=base["gate_set_hash"], coverage_months=base["coverage_months"],
        tig_residuals=base["tig_residuals"], opd_residuals=base["opd_residuals"],
    )


def test_signature_is_deterministic():
    assert _sig() == _sig()
    assert len(_sig()) == 16


def test_gate_set_change_changes_signature():
    assert _sig() != _sig(gate_set_hash="G2")


def test_coverage_change_changes_signature():
    assert _sig() != _sig(coverage_months="5")


def test_source_residual_change_changes_signature():
    assert _sig() != _sig(tig_residuals=[1.0, 2.1])
    assert _sig() != _sig(opd_residuals=[0.6])


def test_same_n_different_values_do_not_collide():
    # Two fits with identical (city, metric, season, dv, kappa, n) but different source
    # residuals must NOT share a signature — the pre-SD4 hole.
    a = _sig(tig_residuals=[1.0, 2.0])
    b = _sig(tig_residuals=[3.0, 4.0])
    assert a != b


# ---- Blocker F: pair-batch immutable manifest -------------------------------

import sqlite3  # noqa: E402

from src.calibration.ens_bias_repo import (  # noqa: E402
    build_pair_batch_manifest,
    write_pair_batch_manifest,
)

_ROWS = [
    {"city": "NYC", "season": "MAM", "metric": "high",
     "live_data_version": "dvL", "fit_signature_hash": "sigA", "gate_set_hash": "G1"},
    {"city": "LA", "season": "MAM", "metric": "high",
     "live_data_version": "dvL", "fit_signature_hash": "sigB", "gate_set_hash": "G1"},
]


def _manifest(**over):
    base = dict(error_model_family="full_transport_v1", gate_set_hash="G1",
                generator_commit="abc123", n_mc=1000, scope={"cities": ["NYC", "LA"]})
    base.update(over)
    return build_pair_batch_manifest(_ROWS, **base)


def test_manifest_records_all_required_domain_fields():
    m = _manifest()
    for field in ("error_model_family", "gate_set_hash", "fit_signature_hashes",
                  "generator_commit", "n_mc", "source_db_snapshot_hash",
                  "manifest_hash", "pair_batch_id"):
        assert field in m, f"manifest missing required field {field}"
    assert m["fit_signature_hashes"] == ["sigA", "sigB"]
    assert m["pair_batch_id"] == m["manifest_hash"]  # content-addressed


def test_manifest_is_content_addressed():
    assert _manifest()["pair_batch_id"] == _manifest()["pair_batch_id"]
    assert _manifest()["pair_batch_id"] != _manifest(gate_set_hash="G2")["pair_batch_id"]
    assert _manifest()["pair_batch_id"] != _manifest(n_mc=5000)["pair_batch_id"]


def test_manifest_different_source_rows_change_id():
    other = build_pair_batch_manifest(
        [{**_ROWS[0], "fit_signature_hash": "sigX"}],
        error_model_family="full_transport_v1", gate_set_hash="G1",
        generator_commit="abc123", n_mc=1000, scope={"cities": ["NYC"]},
    )
    assert other["pair_batch_id"] != _manifest()["pair_batch_id"]


def test_write_pair_batch_manifest_is_immutable():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE zeus_meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    m = _manifest()
    pbid = write_pair_batch_manifest(conn, m)
    # Re-write the SAME batch + a tampered copy under the same id -> must NOT overwrite.
    write_pair_batch_manifest(conn, m)
    write_pair_batch_manifest(conn, {**m, "n_mc": 999999})
    rows = conn.execute(
        "SELECT value FROM zeus_meta WHERE key = ?", (f"pair_batch:{pbid}",)
    ).fetchall()
    assert len(rows) == 1, "manifest must be append-only (one row per pair_batch_id)"
    import json
    assert json.loads(rows[0][0])["n_mc"] == 1000, "existing manifest must never be overwritten"
