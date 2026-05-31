# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: fix/edli-stage-readiness-2026-05-31 — EDLI-mode release-gate
#   source_health freshness surface. ROOT: write_source_health emitted only
#   top-level "written_at", but the gate's _fresh() recognizes a different
#   canonical key set (generated_at|updated_at|observed_at|captured_at), so a
#   genuinely-fresh source_health.json was read as missing_timestamp.
#
# Relationship invariant (forecast-live writer -> release gate, cross-module):
#   write_source_health() MUST emit a top-level gate-canonical freshness key,
#   and a file it just wrote MUST be read as fresh by the gate's _fresh().

import json
from datetime import datetime, timezone

from scripts.check_live_release_gate import _fresh  # type: ignore
from src.data.source_health_probe import write_source_health


_GATE_FRESHNESS_KEYS = ("generated_at", "updated_at", "observed_at", "captured_at")


def test_write_source_health_emits_gate_recognized_freshness_key(tmp_path):
    out = write_source_health({"open_meteo_archive": {"consecutive_failures": 0}}, state_dir=tmp_path)
    payload = json.loads(out.read_text())

    present = [k for k in _GATE_FRESHNESS_KEYS if payload.get(k)]
    assert present, (
        "source_health payload exposes no gate-canonical freshness key "
        f"(any of {_GATE_FRESHNESS_KEYS}); gate _fresh() would return "
        f"missing_timestamp. Top-level keys: {sorted(payload.keys())}"
    )
    # written_at preserved for legacy consumers.
    assert payload.get("written_at"), "written_at must remain for existing consumers"


def test_fresh_source_health_read_as_fresh_by_release_gate(tmp_path):
    out = write_source_health({"hko": {"consecutive_failures": 0}}, state_dir=tmp_path)
    payload = json.loads(out.read_text())
    now = datetime.now(timezone.utc)
    ok, detail = _fresh(payload, max_age_seconds=15 * 60, now=now)
    assert ok, f"freshly-written source_health read as stale by gate: {detail}"


def test_generated_at_equals_written_at(tmp_path):
    out = write_source_health({"wu_pws": {"consecutive_failures": 0}}, state_dir=tmp_path)
    payload = json.loads(out.read_text())
    assert payload.get("generated_at") == payload.get("written_at"), (
        "generated_at and written_at must be the same instant — they describe the "
        "single moment this health snapshot was written"
    )
