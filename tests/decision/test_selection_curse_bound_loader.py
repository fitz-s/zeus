# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/live_order_pathology/2026-06-23_selection_curse_*.md +
#   src/calibration/anchor_representativeness_debias.py loader pattern (state/<name>.json, cached,
#   fail-soft). Antibody: a loader that raises (instead of None) on a missing/malformed artifact
#   would turn "not yet fit / bad file" into a live fault rather than the identity no-op the
#   tighten-only + arm-with-monitoring posture requires.
"""The selection-curse bound loader is fail-soft: present->SelectionCurseBound, absent/bad->None."""
from __future__ import annotations

import json

from src.decision.selection_curse_bound import SelectionCurseBound
from src.decision.selection_curse_bound_loader import load_bound


def _write(p, obj):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


_GOOD = {
    "price_knots": [0.50, 0.70, 0.90, 0.97],
    "realized_lcb": [0.55, 0.66, 0.93, 1.00],
    "n_train": 900,
    "armed_sides": ["buy_no"],
    "artifact_hash": "abc",
    "built_at": "2026-06-23T00:00:00Z",
}


def test_load_present_round_trips(tmp_path):
    p = tmp_path / "selection_curse_bound.json"
    _write(p, _GOOD)
    b = load_bound(str(p))
    assert isinstance(b, SelectionCurseBound)
    assert b.price_knots == (0.50, 0.70, 0.90, 0.97)
    assert b.realized_lcb == (0.55, 0.66, 0.93, 1.00)
    assert b.armed_sides == frozenset({"buy_no"})
    assert b.n_train == 900


def test_load_missing_is_none(tmp_path):
    assert load_bound(str(tmp_path / "nope.json")) is None


def test_load_malformed_json_is_none(tmp_path):
    p = tmp_path / "bad.json"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert load_bound(str(p)) is None


def test_load_non_monotone_is_none(tmp_path):
    # A corrupt non-monotone band violates SelectionCurseBound.__post_init__ -> fail soft to None,
    # never raise into the live gate.
    bad = dict(_GOOD, realized_lcb=[0.9, 0.2, 0.3, 1.0])
    p = tmp_path / "nonmono.json"
    _write(p, bad)
    assert load_bound(str(p)) is None


def test_load_missing_field_is_none(tmp_path):
    bad = {k: v for k, v in _GOOD.items() if k != "price_knots"}
    p = tmp_path / "missing.json"
    _write(p, bad)
    assert load_bound(str(p)) is None
