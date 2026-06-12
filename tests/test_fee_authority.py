# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=2026-06-12
# Purpose: antibody for the fee-schedule-as-realized-fee data-semantics error —
#   the taker fee fraction in EV math must come from realized-fill evidence,
#   degrading to the venue schedule only when evidence is thin/stale/absent.
# Reuse: incident 2026-06-12 — CLOB base_fee=1000bps (schedule CAP) consumed as
#   the actual fee, a ~10% phantom tax per EV, while 42/42 realized fills carried
#   trade-level fee_rate_bps=0 and cost basis reconciled to price*shares exactly.
# Last reused/audited: 2026-06-12
# Authority basis: calibration authority Task 2.3 + operator no-unfitted-hardcode law.
"""Tests for src/contracts/fee_authority.py + the reconciler artifact contract."""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest

import src.contracts.fee_authority as fa


@pytest.fixture()
def artifact_path(tmp_path, monkeypatch):
    p = tmp_path / "fee_reconciliation.json"
    monkeypatch.setattr(fa, "ARTIFACT_PATH", p)
    fa._cache["mtime"] = None
    fa._cache["artifact"] = None
    return p


def _write(p: Path, **kw):
    base = {
        "schema": "fee_reconciliation",
        "fitted_at": "2026-06-12T23:00:00+00:00",
        "n_fills": 42,
        "observed_max_fee_fraction": 0.0,
    }
    base.update(kw)
    p.write_text(json.dumps(base))


def test_licensed_zero_evidence_overrides_schedule(artifact_path):
    _write(artifact_path)
    fraction, source = fa.resolve_taker_fee_fraction(0.10)
    assert fraction == 0.0
    assert source.startswith("realized_fills_n=42")


def test_no_artifact_falls_back_to_schedule(artifact_path):
    fraction, source = fa.resolve_taker_fee_fraction(0.10)
    assert fraction == 0.10
    assert "no_reconciliation_artifact" in source


def test_thin_evidence_falls_back_to_schedule(artifact_path):
    _write(artifact_path, n_fills=3)
    fraction, source = fa.resolve_taker_fee_fraction(0.10)
    assert fraction == 0.10
    assert "insufficient_fills" in source


def test_nonzero_observed_fee_is_used_but_never_exceeds_schedule(artifact_path):
    _write(artifact_path, observed_max_fee_fraction=0.02)
    fraction, _ = fa.resolve_taker_fee_fraction(0.10)
    assert fraction == 0.02
    # observed above schedule: schedule (the venue's own cap) wins
    _write(artifact_path, observed_max_fee_fraction=0.50)
    fa._cache["mtime"] = None
    fraction2, _ = fa.resolve_taker_fee_fraction(0.10)
    assert fraction2 == 0.10


def test_stale_evidence_degrades_to_schedule(artifact_path, monkeypatch):
    _write(artifact_path)
    old = time.time() - (fa.MAX_EVIDENCE_AGE_DAYS + 5) * 86400
    import os
    os.utime(artifact_path, (old, old))
    fa._cache["mtime"] = None
    fraction, source = fa.resolve_taker_fee_fraction(0.10)
    assert fraction == 0.10
    assert "evidence_stale" in source


def test_reconciler_excludes_schedule_envelope_fields():
    """The reconciler must read TRADE-level fee fields only — fee_details.* is the
    venue schedule CAP, the exact confusion this artifact exists to kill."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "recon", Path(__file__).resolve().parent.parent / "scripts" / "reconcile_realized_fees.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    payload = {
        "trade_fact_proof": {"trade": {"fee_rate_bps": "0"}},
        "submit_result": {"_venue_submission_envelope": {"fee_details": {"fee_rate_bps": 1000.0}}},
    }
    fields = mod._scan_fee_fields(payload)
    realized = {k: v for k, v in fields.items() if "fee_details" not in k}
    assert list(realized.values()) == ["0"]
