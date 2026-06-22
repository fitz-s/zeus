# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: would-admit shadow logger
#   (team-lead approved (a) step 3, 2026-06-22; live_order_pathology 2026-06-22). STEP-1 found NO
#   current-regime (bayes_fusion) would-admit population exists. This logger accrues it: every live
#   candidate's would-admit decision + features, joined later to settlement, so a real
#   forward-positive gate/calibrator can be validated once a few hundred labelled rows accrue.
"""RED-first tests for the would-admit shadow logger (append-only, off the live decision path).

It records, per evaluated side-candidate: decision_time, city, target_date, condition_id, bin_id,
side, raw_side_prob, q_lcb_side, own_side_cost, admission_margin, admit0 (= native_quote_available
AND quote_fresh AND q_lcb_side_old > own_side_cost), city_skill_admit, posterior_version. It NEVER
reads back into any gate (observability only) and is flag-gated default OFF (no live change).
"""
from __future__ import annotations

import json

import pytest

from src.decision import shadow_admit_logger as sal


def test_build_record_has_all_would_admit_fields():
    rec = sal.build_shadow_record(
        decision_time="2026-06-22T12:00:00+00:00", city="Tokyo", target_date="2026-06-23",
        condition_id="0xabc", bin_id="18C", side="NO", raw_side_prob=0.87, q_lcb_side=0.83,
        own_side_cost=0.70, native_quote_available=True, quote_fresh=True,
        posterior_version="openmeteo_ecmwf_ifs9_bayes_fusion",
    )
    for k in ("decision_time", "city", "target_date", "condition_id", "bin_id", "side",
              "raw_side_prob", "q_lcb_side", "own_side_cost", "admission_margin", "admit0",
              "posterior_version"):
        assert k in rec
    # admit0 = quote available AND fresh AND q_lcb_side > own_side_cost (0.83 > 0.70 -> True).
    assert rec["admit0"] is True
    assert abs(rec["admission_margin"] - (0.83 - 0.70)) < 1e-9


def test_admit0_false_when_lcb_below_cost():
    rec = sal.build_shadow_record(
        decision_time="2026-06-22T12:00:00+00:00", city="Tokyo", target_date="2026-06-23",
        condition_id="0xabc", bin_id="18C", side="NO", raw_side_prob=0.87, q_lcb_side=0.65,
        own_side_cost=0.70, native_quote_available=True, quote_fresh=True,
        posterior_version="v",
    )
    assert rec["admit0"] is False  # 0.65 < 0.70


def test_admit0_false_when_quote_unavailable_or_stale():
    base = dict(
        decision_time="2026-06-22T12:00:00+00:00", city="Tokyo", target_date="2026-06-23",
        condition_id="0xabc", bin_id="18C", side="NO", raw_side_prob=0.87, q_lcb_side=0.83,
        own_side_cost=0.70, posterior_version="v",
    )
    assert sal.build_shadow_record(native_quote_available=False, quote_fresh=True, **base)["admit0"] is False
    assert sal.build_shadow_record(native_quote_available=True, quote_fresh=False, **base)["admit0"] is False


def test_append_and_read_roundtrip(tmp_path):
    path = tmp_path / "shadow.jsonl"
    r1 = sal.build_shadow_record(
        decision_time="2026-06-22T12:00:00+00:00", city="Tokyo", target_date="2026-06-23",
        condition_id="0xabc", bin_id="18C", side="NO", raw_side_prob=0.87, q_lcb_side=0.83,
        own_side_cost=0.70, native_quote_available=True, quote_fresh=True, posterior_version="v",
    )
    sal.append_shadow_record(r1, path=str(path))
    sal.append_shadow_record(r1, path=str(path))
    rows = sal.read_shadow_log(path=str(path))
    assert len(rows) == 2
    assert rows[0]["city"] == "Tokyo"


def test_logger_flag_gated_default_off(tmp_path, monkeypatch):
    # Default OFF -> maybe_log is a no-op (writes nothing), so wiring it in is inert.
    monkeypatch.delenv("ZEUS_SHADOW_ADMIT_LOG", raising=False)
    path = tmp_path / "shadow.jsonl"
    wrote = sal.maybe_log_candidate(
        path=str(path), decision_time="t", city="Tokyo", target_date="2026-06-23",
        condition_id="0xabc", bin_id="18C", side="NO", raw_side_prob=0.87, q_lcb_side=0.83,
        own_side_cost=0.70, native_quote_available=True, quote_fresh=True, posterior_version="v",
    )
    assert wrote is False
    assert not path.exists()


def test_logger_flag_on_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_SHADOW_ADMIT_LOG", "1")
    path = tmp_path / "shadow.jsonl"
    wrote = sal.maybe_log_candidate(
        path=str(path), decision_time="t", city="Tokyo", target_date="2026-06-23",
        condition_id="0xabc", bin_id="18C", side="NO", raw_side_prob=0.87, q_lcb_side=0.83,
        own_side_cost=0.70, native_quote_available=True, quote_fresh=True, posterior_version="v",
    )
    assert wrote is True
    assert path.exists()
    assert len(sal.read_shadow_log(path=str(path))) == 1


def test_logger_never_raises_on_bad_path(monkeypatch):
    # Observability must NEVER break the decision path: a bad path is swallowed (returns False).
    monkeypatch.setenv("ZEUS_SHADOW_ADMIT_LOG", "1")
    wrote = sal.maybe_log_candidate(
        path="/nonexistent_dir_zzz/shadow.jsonl", decision_time="t", city="Tokyo",
        target_date="2026-06-23", condition_id="0xabc", bin_id="18C", side="NO",
        raw_side_prob=0.87, q_lcb_side=0.83, own_side_cost=0.70,
        native_quote_available=True, quote_fresh=True, posterior_version="v",
    )
    assert wrote is False  # swallowed, did not raise
