# Created: 2026-09-26
# Last audited: 2026-09-26
# Authority basis: design review REQ-20260925-223704 §4, §5 and §10 behavioral
#   acceptance (folds, leakage, duplication, adversarial licensing cases);
#   release thresholds in docs/operations/current/plans/day0_probability_repair_2026-09-25.md.
"""Tests for scripts/fit_candidate_calibration.py (offline release harness)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from scripts import fit_candidate_calibration as fcc
from src.calibration import market_anchored_family as maf

K = 7
SPECS = ((maf.MARKET_NULL, maf.LOG_LOSS), (maf.ARITHMETIC, maf.LOG_LOSS))
DAY = 86400.0


def _synthetic(kind: str, seed: int, n_dates: int = 24, n_cities: int = 10, per_day: int = 2,
               storm_share: float = 0.15) -> fcc.Corpus:
    """City-day families with a shared truth; ``kind`` picks how P and Q
    relate to it. Instants of one city-day share its settled bin."""

    rng = np.random.default_rng(seed)
    start = date(2026, 8, 1)
    city, tdate, metric, at, label_at, P, Q, y, lane, hours = ([] for _ in range(10))
    grid = np.arange(K)
    for d in range(n_dates):
        td = start + timedelta(days=d)
        end = datetime.combine(td + timedelta(days=1), time(0), timezone.utc).timestamp()
        for c in range(n_cities):
            mu, sd = rng.uniform(1.0, 5.0), rng.uniform(0.7, 1.4)
            truth = np.exp(-0.5 * ((grid - mu) / sd) ** 2)
            truth /= truth.sum()
            yy = int(rng.choice(K, p=truth))
            storm = rng.uniform() < storm_share
            for h in range(per_day):
                t = end - 30 * 3600 + 6 * 3600 * h
                if kind == "market_true":
                    p, q = truth, rng.dirichlet(np.full(K, 0.3))
                elif kind == "shrink":  # vague market, sharp but overconfident q
                    p = 0.3 * truth + 0.7 / K
                    q = truth ** 3 / np.sum(truth ** 3)
                elif kind == "harm":  # q helps on calm days, is confidently wrong on storms
                    if storm:
                        p = truth
                        q = 0.05 / K + 0.95 * np.eye(K)[int(np.argmin(truth))]
                    else:
                        p, q = 0.3 * truth + 0.7 / K, truth
                else:
                    raise ValueError(kind)
                city.append(f"C{c}")
                tdate.append(td.isoformat())
                metric.append("high")
                at.append(t)
                label_at.append(end + 72 * 3600)
                P.append(p / p.sum())
                Q.append(q / q.sum())
                y.append(yy)
                lane.append(maf.FORECAST)
                hours.append((end - t) / 3600)
    n = len(y)
    P, Q, y = np.vstack(P), np.vstack(Q), np.array(y)
    leg_i = np.repeat(np.arange(n), 2)
    leg_b = np.stack([np.argmax(P, axis=1), np.argmin(P, axis=1)], axis=1).ravel()
    leg_s = np.tile(np.array(["YES", "NO"], dtype=object), n)
    _, leg_y = maf.held_side(np.zeros(2 * n), y[leg_i] == leg_b, leg_s)
    p_h, _ = maf.held_side(P[leg_i, leg_b], leg_y, leg_s)
    feats = maf.FamilyFeatures(
        np.array(metric, dtype=object), np.array(lane, dtype=object), np.array(hours),
        np.full(n, "C1:wmo_half_up", dtype=object), np.array(city, dtype=object),
        np.full(n, "e", dtype=object),
    )
    return fcc.Corpus(fcc.Instants(city, tdate, metric, at), np.array(label_at), y, P, Q, feats,
                      leg_i, leg_b, leg_s, np.clip(p_h + 0.01, 0.001, 0.999), leg_y)


def _duplicate(c: fcc.Corpus, times: int) -> fcc.Corpus:
    idx = np.repeat(np.arange(len(c.inst)), times)
    legs = np.repeat(np.arange(len(c.leg_instant)), times)
    inverse = np.repeat(np.arange(len(c.inst)), times)
    first = {i: j for j, i in reversed(list(enumerate(inverse)))}
    return fcc.Corpus(
        fcc.Instants(c.inst.city[idx], c.inst.target_date[idx], c.inst.metric[idx], c.inst.decision_at[idx]),
        c.label_at[idx], c.y[idx], c.P[idx], c.Q[idx], c.features.take(idx),
        np.array([first[i] for i in c.leg_instant[legs]]), c.leg_bin[legs], c.leg_side[legs],
        c.leg_p0[legs], c.leg_y[legs],
    )


# ---------------------------------------------------------------------------
# Folds and leakage.
# ---------------------------------------------------------------------------


def test_city_day_never_crosses_folds_and_inner_folds_nest_inside_outer_training():
    c = _synthetic("shrink", 1)
    folds = fcc.build_folds(c)
    assert len(folds) >= 2
    cd = [tuple(x) for x in zip(c.inst.city, c.inst.target_date)]
    owner = {}
    for f in folds:
        test_cd = {cd[i] for i in np.flatnonzero(f.outer.test)}
        train_cd = {cd[i] for i in np.flatnonzero(f.outer.train)}
        assert not test_cd & train_cd
        for key in test_cd:
            assert owner.setdefault(key, f.index) == f.index
        # every instant of a tested city-day is in that fold's test set
        members = np.array([k in test_cd for k in cd])
        assert np.array_equal(members, f.outer.test)
        if f.inner is not None:
            assert not np.any(f.inner.train & ~f.outer.train)
            assert not np.any(f.inner.test & ~f.outer.train)
            assert not {cd[i] for i in np.flatnonzero(f.inner.test)} & {cd[i] for i in np.flatnonzero(f.inner.train)}


def test_future_labels_cannot_enter_training():
    c = _synthetic("shrink", 2)
    late = (c.inst.target_date == "2026-08-05")
    c.label_at[late] = datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp()
    folds = fcc.build_folds(c)
    for f in folds:
        for split in filter(None, (f.outer, f.inner)):
            assert not np.any(split.train & late)
            if split.train.any():
                assert c.label_at[split.train].max() < split.cutoff
                assert c.inst.decision_at[split.test].min() == split.cutoff
    # predictions on every other row are invariant to those unavailable labels
    base = fcc.run_procedure(c, SPECS, folds)
    y = np.where(late, (c.y + 3) % K, c.y)
    _, leg_y = maf.held_side(np.zeros(len(c.leg_bin)), y[c.leg_instant] == c.leg_bin, c.leg_side)
    moved = fcc.Corpus(c.inst, c.label_at, y, c.P, c.Q, c.features, c.leg_instant, c.leg_bin,
                       c.leg_side, c.leg_p0, leg_y)
    after = fcc.run_procedure(moved, SPECS, fcc.build_folds(moved))
    assert base.tested[~late].any()
    assert np.allclose(base.served[~late & base.tested], after.served[~late & after.tested], atol=1e-9)


def test_training_waits_for_the_label_lag_even_for_earlier_dates():
    c = _synthetic("shrink", 3)
    f = fcc.build_folds(c)[0]
    first_test = min(f.outer.test_dates)
    train_dates = sorted(set(c.inst.target_date[f.outer.train]))
    # decisions start 30 h before the test day's end; labels need 72 h past day end
    assert train_dates and max(train_dates) <= (date.fromisoformat(first_test) - timedelta(days=4)).isoformat()


# ---------------------------------------------------------------------------
# Dependence: duplication cannot manufacture evidence.
# ---------------------------------------------------------------------------


def test_city_day_weights_sum_to_one_per_city_day():
    c = _synthetic("shrink", 4, per_day=3)
    w = c.inst.weights()
    assert np.allclose(np.bincount(c.inst.cd, weights=w), 1.0)


def test_duplicating_rows_does_not_tighten_the_bounds_or_raise_evidence():
    c = _synthetic("shrink", 5, n_dates=30, n_cities=12)
    d = _duplicate(c, 5)
    assert np.allclose(np.bincount(d.inst.cd, weights=d.inst.weights()), 1.0)
    base = fcc.evaluate_release(c, SPECS, replicates=40, blocks=(3,), seed=7)
    dup = fcc.evaluate_release(d, SPECS, replicates=40, blocks=(3,), seed=7)
    key = fcc.key(fcc.ALL, "family_ll_vs_market")
    b0 = base["by_block"]["3"]["bounds"]["endpoints"][key]
    b1 = dup["by_block"]["3"]["bounds"]["endpoints"][key]
    assert (b1["city_days"], b1["dates"]) == (b0["city_days"], b0["dates"])
    assert b1["theta"] == pytest.approx(b0["theta"], abs=1e-6)
    assert b1["se"] == pytest.approx(b0["se"], rel=1e-3)
    assert b1["upper"] >= b0["upper"] - 1e-6


# ---------------------------------------------------------------------------
# Adversarial licensing cases.
# ---------------------------------------------------------------------------


def test_market_as_true_generator_never_licenses():
    c = _synthetic("market_true", 6, n_dates=36, n_cities=12)
    res = fcc.evaluate_release(c, SPECS, replicates=60, blocks=(3,), seed=8)
    block = res["by_block"]["3"]
    fam = block["bounds"]["endpoints"][fcc.key(fcc.ALL, "family_ll_vs_market")]
    # the evidence floor is met, so the denial is a verdict, not missing data
    assert fam["city_days"] >= fcc.FLOOR_CITY_DAYS and fam["dates"] >= fcc.FLOOR_DATES
    assert fam["status"] != "PASS"
    assert all(v["verdict"] != "LICENSE" for v in block["verdicts"].values())
    assert block["verdicts"][fcc.ALL]["verdict"] == "DENY"
    trained = [f for f in res["folds"] if "OUTER_TRAIN" not in f["selection_reason"]
               and "INNER_TRAIN" not in f["selection_reason"]]
    assert trained and all(f["selected"] == fcc.spec_name(SPECS[0]) for f in trained)
    w = block["parameters"][f"{fcc.spec_name(SPECS[1])}|w|{fcc.ALL}"]
    assert w["theta"] < 0.05


def _gated(n_endpoints, theta_in_se, seed=0, n_rep=4000):
    """Independent endpoints with replicate sd 1 and point estimates at
    ``theta_in_se`` standard errors below zero."""

    rng = np.random.default_rng(seed)
    keys = [fcc.key(f"S{i}", "family_ll_vs_market") for i in range(n_endpoints)]
    point = {k: theta_in_se for k in keys}
    reps = theta_in_se + rng.standard_normal((n_rep, n_endpoints))
    evidence = {k: (fcc.FLOOR_CITY_DAYS, fcc.FLOOR_DATES) for k in keys}
    return fcc.simultaneous_upper(point, reps, keys, evidence, keys)


def test_a_negative_point_estimate_inside_its_noise_does_not_pass():
    out = _gated(1, -1.0)
    e = out["endpoints"][fcc.key("S0", "family_ll_vs_market")]
    assert e["theta"] < 0.0 and e["upper"] > 0.0 and e["status"] == "FAIL"


def test_bounds_are_simultaneous_over_the_endpoint_family():
    # -2 se clears one one-sided 95% bound (1.645) but not a max over 20 (~2.6)
    single = _gated(1, -2.0)
    assert single["critical_value"] == pytest.approx(1.645, abs=0.08)
    assert single["endpoints"][fcc.key("S0", "family_ll_vs_market")]["status"] == "PASS"
    family = _gated(20, -2.0)
    assert family["critical_value"] > 2.3
    assert all(e["status"] == "FAIL" for e in family["endpoints"].values())


def test_evidence_floor_and_empty_resamples_deny():
    keys = [fcc.key("S", "family_ll_vs_market"), fcc.key("T", "family_ll_vs_market")]
    reps = -5.0 + np.random.default_rng(1).standard_normal((500, 2))
    reps[3, 1] = np.nan  # one empty resample
    out = fcc.simultaneous_upper({k: -5.0 for k in keys}, reps, keys,
                                 {keys[0]: (fcc.FLOOR_CITY_DAYS - 1, 30), keys[1]: (500, 30)}, keys)
    assert [out["endpoints"][k]["status"] for k in keys] == ["INSUFFICIENT_EVIDENCE"] * 2


def test_useful_but_overconfident_q_earns_interior_shrinkage_and_beats_the_market():
    c = _synthetic("shrink", 9, n_dates=36, n_cities=40)
    res = fcc.evaluate_release(c, SPECS, replicates=60, blocks=(3,), seed=10)
    block = res["by_block"]["3"]
    w = block["parameters"][f"{fcc.spec_name(SPECS[1])}|w|{fcc.ALL}"]
    assert 0.05 < w["theta"] < 0.95
    assert w["p05"] > 0.0 and w["p975"] < 1.0
    # the fitted arithmetic candidate beats the market out of sample under the
    # simultaneous date-block bound, and inner folds select it once trainable
    arith = block["candidate_bounds"]["endpoints"][
        f"{fcc.spec_name(SPECS[1])}#{fcc.key(fcc.ALL, 'family_ll_vs_market')}"]
    assert arith["status"] == "PASS" and arith["upper"] < 0.0
    assert [f["selected"] for f in res["folds"] if f["selection_reason"] == "ONE_SE_RULE"] == [
        fcc.spec_name(SPECS[1])] * 3
    assert block["slope_at_market"][f"slope|w0|{fcc.ALL}"]["upper"] < 0.0


def test_pooled_gain_cannot_carry_harm_in_the_largest_disagreement_band():
    c = _synthetic("harm", 11, n_dates=36, n_cities=40, storm_share=0.12)
    res = fcc.evaluate_release(c, SPECS, replicates=60, blocks=(3,), seed=10)
    block = res["by_block"]["3"]
    ends = block["bounds"]["endpoints"]
    pooled = ends[fcc.key(fcc.ALL, "family_ll_vs_market")]
    assert pooled["status"] == "PASS" and pooled["upper"] < 0.0  # the pooled gain is real
    harm = ends[fcc.key(fcc.ALL, "family_ll_vs_market", "band:>0.50")]
    assert harm["theta"] > 0.01 and harm["status"] == "FAIL"
    verdict = block["verdicts"][fcc.ALL]
    assert "band:>0.50" in verdict["blocked_children"]
    assert "band:>0.50" not in verdict["licensed_children"]


def test_scope_below_the_date_floor_is_insufficient_evidence_not_a_pass():
    # plenty of city-days, too few settlement dates: only the date floor binds
    c = _synthetic("shrink", 13, n_dates=18, n_cities=20)
    res = fcc.evaluate_release(c, SPECS, replicates=30, blocks=(3,), seed=14)
    block = res["by_block"]["3"]
    fam = block["bounds"]["endpoints"][fcc.key(fcc.ALL, "family_ll_vs_market")]
    assert fam["city_days"] >= fcc.FLOOR_CITY_DAYS and fam["dates"] < fcc.FLOOR_DATES
    assert "nonfinite_replicates" not in fam
    assert block["verdicts"][fcc.ALL]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_untrained_outer_fold_is_not_scored():
    c = _synthetic("shrink", 15)
    folds = fcc.build_folds(c)
    ev = fcc.run_procedure(c, SPECS, folds)
    for f, reason in zip(folds, ev.reasons):
        if "OUTER_TRAIN_BELOW_MIN_CITY_DAYS" in reason:
            assert not ev.tested[f.outer.test].any()


# ---------------------------------------------------------------------------
# Live-DB loader on a fixture (read path, dedup, NO complement, topology).
# ---------------------------------------------------------------------------


def _fixture_dbs(tmp_path, rows):
    trade = sqlite3.connect(tmp_path / "trade.db")
    trade.execute(
        "CREATE TABLE tier0_candidate_set_provenance (selection_epoch_identity TEXT, "
        "decision_at_utc TEXT, city TEXT, target_date TEXT, family_key TEXT, side TEXT, "
        "action TEXT, p0 REAL, market_key TEXT, settled_y INTEGER)")
    trade.executemany("INSERT INTO tier0_candidate_set_provenance VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    trade.commit()
    fc = sqlite3.connect(tmp_path / "forecast.db")
    fc.execute("CREATE TABLE market_events (condition_id TEXT, city TEXT, target_date TEXT, "
               "temperature_metric TEXT, range_low REAL, range_high REAL, range_label TEXT)")
    fc.executemany("INSERT INTO market_events VALUES (?,?,?,?,?,?,?)", [
        ("0xa", "Tel Aviv", "2026-09-20", "high", None, 29.0, "29 or below"),
        ("0xb", "Tel Aviv", "2026-09-20", "high", 30.0, 30.0, "30"),
        ("0xc", "Tel Aviv", "2026-09-20", "high", 31.0, None, "31 or higher"),
    ])
    fc.execute("CREATE TABLE settlement_outcomes (city TEXT, target_date TEXT, temperature_metric TEXT, "
               "settlement_value REAL, settlement_unit TEXT, recorded_at TEXT, authority TEXT)")
    fc.execute("INSERT INTO settlement_outcomes VALUES ('Tel Aviv','2026-09-20','high',30.0,'C',"
               "'2026-09-21T12:00:00+00:00','VERIFIED')")
    fc.commit()
    return trade, fc


def test_loader_dedups_twins_complements_no_legs_and_normalizes_the_ask_vector(tmp_path):
    from src.config import runtime_cities_by_name

    at = "2026-09-19T10:00:00+00:00"
    base = ("e1", at, "Tel Aviv", "2026-09-20", "f1")
    rows = [
        base + ("YES", "BUY", 0.20, "0xa", 0),
        base + ("YES", "BUY", 0.60, "0xb", 1),
        base + ("YES", "BUY", 0.40, "0xc", 0),
        base + ("YES", "BUY", 0.40, "0xc", 0),  # maker/taker twin of the same leg
        base + ("NO", "BUY", 0.85, "0xa", 1),   # NO on a losing bin wins
        base + ("NO", "BUY", 0.50, "0xb", 1),   # wrong held-side label: rejected
    ]
    trade, fc = _fixture_dbs(tmp_path, rows)
    live = fcc.load_live_corpus(trade, fc, runtime_cities_by_name(), "2026-09-01", "2026-09-30")
    counts = live.manifest["counts"]
    assert counts["duplicate_leg_rows"] == 1
    assert counts["unique_buy_legs"] == 5
    assert counts["legs_label_mismatch"] == 1
    c = live.corpus
    assert c is not None and len(c.inst) == 1
    assert c.y.tolist() == [1]
    assert c.P[0] == pytest.approx(np.array([0.2, 0.6, 0.4]) / 1.2)
    assert c.features.lane[0] == maf.FORECAST
    tz = ZoneInfo(runtime_cities_by_name()["Tel Aviv"].timezone)
    end = datetime.combine(date(2026, 9, 21), time(0), tz).timestamp()
    decided = datetime.fromisoformat(at).timestamp()
    assert c.features.hours_to_end[0] == pytest.approx((end - decided) / 3600)
    assert c.label_at[0] == pytest.approx(end + fcc.LABEL_LAG.total_seconds())
    no = c.leg_side == "NO"
    assert c.leg_y[no].tolist() == [1.0] and c.leg_bin[no].tolist() == [0]


def test_loader_drops_conflicting_twins_and_incomplete_reference(tmp_path):
    from src.config import runtime_cities_by_name

    base = ("e1", "2026-09-19T10:00:00+00:00", "Tel Aviv", "2026-09-20", "f1")
    rows = [
        base + ("YES", "BUY", 0.20, "0xa", 0),
        base + ("YES", "BUY", 0.60, "0xb", 1),
        base + ("YES", "BUY", 0.65, "0xb", 1),  # conflicting twin: both dropped
        base + ("YES", "BUY", 0.40, "0xc", 0),
    ]
    trade, fc = _fixture_dbs(tmp_path, rows)
    live = fcc.load_live_corpus(trade, fc, runtime_cities_by_name(), "2026-09-01", "2026-09-30")
    assert live.manifest["counts"]["duplicate_leg_conflicts"] == 1
    assert live.corpus is None  # no complete YES-ask vector: no market reference, no fit row


def test_out_directory_under_state_is_refused(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(SystemExit):
        fcc.main(["--diagnostic", "price_only", "--state", str(state), "--out", str(state / "x")])


def test_content_addressed_write_is_immutable(tmp_path):
    p1, sha = fcc.write_content_addressed(tmp_path, "a", ".json", b"{}")
    p2, _ = fcc.write_content_addressed(tmp_path, "a", ".json", b"{}")
    assert p1 == p2 and sha[:16] in p1.name
    p1.write_bytes(b"tampered")
    with pytest.raises(RuntimeError):
        fcc.write_content_addressed(tmp_path, "a", ".json", b"{}")
