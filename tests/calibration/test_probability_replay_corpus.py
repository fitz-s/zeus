# Lifecycle: created=2026-09-25; last_reviewed=2026-09-25; last_reused=never
# Purpose: Current-recipe replay corpus antibodies: old-revision Day0 states become
#   current-recipe rows, future evidence is refused, origins stay verbatim, replay
#   generations cannot inflate n, resolver changes cannot pool, and the fit adapter
#   selects replay rows by evaluation_recipe_id.
# Reuse: Inspect src/calibration/probability_replay_corpus.py and the Day0 carrier
#   rebuild in src/engine/event_reactor_adapter.py before relying on these fixtures.
# Authority basis: operator directive 2026-09-24 (current-recipe replay).
"""Tests for src/calibration/probability_replay_corpus.py."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.calibration import probability_replay_corpus as replay
from src.calibration.market_anchored_live_fit import CanonicalCorpusCache
from src.config import runtime_cities_by_name
from src.contracts.payoff_q_correction import CalibrationFitScope
from src.contracts.probability_validation import (
    EXECUTED_ORDER,
    IDENTITY_VALIDATED,
    SETTLEMENT_STATE,
    ProbabilityValidationCertificate,
    ValidatedIdentity,
)
from src.decision_kernel.canonicalization import stable_hash
from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION as RECIPE

CUT = datetime(2026, 9, 20, 5, 40, tzinfo=timezone.utc)
NOW = CUT + timedelta(days=3)
TARGET = "2026-09-20"
OLD_REVISION = "day0_hourly_ens_source_clock_carrier_v15"
CONDITIONS = ("0xa", "0xb", "0xc", "0xd")
BOUNDS = ((None, 8.0), (9.0, 9.0), (10.0, 10.0), (11.0, None))
LABELS = ("8C or below", "9C", "10C", "11C or higher")
SELECTED = 1
MOSCOW = runtime_cities_by_name()["Moscow"]
CONTRACT = replay.settlement_contract_id(MOSCOW, TARGET)


def _likelihood() -> dict:
    identity = {
        "semantics": "same_station_preliminary_report_survival_likelihood_v2",
        "cutoff": (CUT - timedelta(minutes=5)).isoformat(),
        "successes": [], "failures": [], "unconfirmed_awc_ids": [],
        "alpha": 22.5, "beta": 0.5, "station_id": "UUWW",
        "source_channel_pair": {"awc": "aviationweather_metar", "ogimet": "ogimet_metar_uuww"},
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**identity, "boundary_survival_probability": 22.5 / 23.0, "identity_hash": digest}


def _observation(**changes) -> dict:
    """An archived Day0 carrier state, as a live ENTRY certificate seals it."""

    observation = {
        "settlement_source": "ogimet_metar_uuww", "evidence_finality": "MONOTONE_SETTLEMENT_BOUND",
        "rounded_value": 9, "low_so_far": 9.0, "settlement_unit": "C",
        "observation_time": (CUT - timedelta(hours=3)).isoformat(),
        "observation_available_at": (CUT - timedelta(hours=3)).isoformat(),
        "_edli_day0_decision_carrier_rebuild_basis": replay.DAY0_ENTRY_CARRIER_BASIS,
        "_edli_day0_remaining_carrier_probability_cutoff_utc": CUT.isoformat(),
        "_edli_day0_remaining_carrier_future_extremes_c": [13.8, 13.6, 13.7],
        "_edli_day0_remaining_vector_witness": {
            "actual_models": ["ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km"],
            "capture_times_by_model_utc": {"ecmwf_ifs": (CUT - timedelta(hours=1)).isoformat()},
        },
        "_edli_day0_provisional_revision_likelihood": _likelihood(),
        "_edli_day0_source_clock_predictive_sigma_native": 0.84,
        "_edli_day0_current_temperature_native": 13.0,
        "_edli_day0_current_temperature_observed_at_utc": (CUT - timedelta(minutes=10)).isoformat(),
        "_edli_day0_current_temperature_source": "aviationweather_metar",
        # The old operator's point estimate that the live decision priced.
        "_edli_day0_remaining_carrier_q": [0.0, 0.9742, 0.0018, 0.024],
        "_edli_day0_lcb_transform": {
            "mask": [1.0, 1.0, 0.0, 1.0],
            "no_lcb_by_condition": {condition: 0.5 for condition in CONDITIONS},
        },
        "_edli_day0_remaining_probability_samples": [[0.25, 0.25, 0.25, 0.25]] * 3,
    }
    observation.update(changes)
    return observation


def _origin_q(observation: dict) -> float:
    q = observation["_edli_day0_remaining_carrier_q"]
    masked = [value * mask for value, mask in zip(q, observation["_edli_day0_lcb_transform"]["mask"])]
    return masked[SELECTED] / sum(masked)


def _replay_state(observation: dict, **changes):
    arguments = dict(
        city="Moscow", target_date=TARGET, metric="low", condition_ids=CONDITIONS, bounds=BOUNDS,
        condition_id=CONDITIONS[SELECTED], side="YES", decision_time=CUT + timedelta(seconds=3),
        origin_raw_q=_origin_q(observation),
    )
    arguments.update(changes)
    return replay.replay_day0_state(observation, **arguments)


def test_old_revision_state_produces_a_current_recipe_row():
    observation = _observation()
    q, reason, information_set = _replay_state(observation)
    assert reason is None and len(information_set) == 64
    # The current recipe re-integrates the same archive; it is not the old q.
    assert 0.0 < q < 1.0 and abs(q - _origin_q(observation)) > 1e-6
    world, trade, forecast = _databases(observation)
    corpus = replay.load_replay_corpus(world, trade, forecast, training_cutoff=NOW, generated_at=NOW)
    state, = [row for row in corpus.rows if row.population == SETTLEMENT_STATE]
    assert state.evaluation_recipe_id == RECIPE != OLD_REVISION == state.origin_live_revision
    assert state.recomputed_raw_q == pytest.approx(q, abs=1e-12)
    assert state.origin_raw_q == pytest.approx(_origin_q(observation), abs=1e-12)
    assert state.settlement_contract_id == CONTRACT and state.payout == 1.0
    assert state.historical_market_features["p0"] == 0.42


@pytest.mark.parametrize("field, value, reason", [
    ("_edli_day0_current_temperature_observed_at_utc",
     (CUT + timedelta(minutes=1)).isoformat(), "FUTURE_EVIDENCE:current_temperature_observed_at"),
    ("_edli_day0_remaining_vector_witness",
     {"actual_models": ["a", "b", "c"], "capture_times_by_model_utc": {"a": (CUT + timedelta(seconds=1)).isoformat()}},
     "FUTURE_EVIDENCE:vector_witness.capture_times_by_model_utc"),
    ("observation_available_at", "not-a-clock", "FUTURE_EVIDENCE:observation_available_at"),
    ("_edli_day0_remaining_carrier_probability_cutoff_utc",
     (CUT + timedelta(minutes=5)).isoformat(), "FUTURE_EVIDENCE:carrier_cutoff"),
])
def test_future_available_evidence_is_rejected(field, value, reason):
    q, got, _ = _replay_state(_observation(**{field: value}))
    assert q is None and got == reason


def test_final_extreme_center_published_after_the_cutoff_is_rejected():
    observation = _observation(_edli_day0_remaining_carrier_final_extremes_c=[14.0])
    late = (("final_extreme.source_available_at", (CUT + timedelta(minutes=2)).isoformat()),)
    assert _replay_state(observation, final_extreme_clocks=late)[1] == "FUTURE_EVIDENCE:final_extreme.source_available_at"
    assert _replay_state(observation)[1] == "FINAL_EXTREME_PROVENANCE_UNBOUND"


def test_replay_never_overwrites_the_original_prediction():
    observation = _observation()
    world, trade, forecast = _databases(observation)
    before = world.execute("SELECT payload_json, payload_hash FROM decision_certificates").fetchall()
    changes = [conn.total_changes for conn in (world, trade, forecast)]
    archive = copy.deepcopy(observation)
    corpus = replay.load_replay_corpus(world, trade, forecast, training_cutoff=NOW, generated_at=NOW)
    replay.replay_day0_state(observation, city="Moscow", target_date=TARGET, metric="low",
                             condition_ids=CONDITIONS, bounds=BOUNDS, condition_id=CONDITIONS[SELECTED],
                             side="YES", decision_time=CUT, origin_raw_q=_origin_q(observation))
    assert observation == archive
    assert world.execute("SELECT payload_json, payload_hash FROM decision_certificates").fetchall() == before
    assert [conn.total_changes for conn in (world, trade, forecast)] == changes
    for row in corpus.rows:
        assert row.origin_live_revision == OLD_REVISION
        assert row.origin_raw_q == pytest.approx(_origin_q(observation), abs=1e-12)
        assert row.recomputed_raw_q != row.origin_raw_q


def test_duplicate_replay_generations_do_not_increase_effective_n():
    world, trade, forecast = _databases(_observation())
    first = replay.load_replay_corpus(world, trade, forecast, training_cutoff=NOW, generated_at=NOW)
    second = replay.load_replay_corpus(world, trade, forecast, training_cutoff=NOW,
                                       generated_at=NOW + timedelta(hours=1))
    merged = replay.ReplayCorpus(rows=first.rows + second.rows, unavailable={})
    assert len(merged.rows) == len(first.rows) == 2
    fit = merged.fit_corpus(population=SETTLEMENT_STATE, current_contracts=frozenset({CONTRACT}),
                            training_cutoff=NOW)
    rows = fit.fit_rows(**_fit_kwargs())
    assert len(rows) == 1 and sum(row.w for row in rows) == pytest.approx(1.0)
    # A second decision on the same settlement is a new state, not a new outcome.
    twin = replace(first.rows[0], origin_evidence_id="another-decision")
    clustered = replay.ReplayCorpus(rows=first.rows + (twin,), unavailable={}).fit_corpus(
        population=SETTLEMENT_STATE, current_contracts=frozenset({CONTRACT}), training_cutoff=NOW)
    assert sum(row.w for row in clustered.fit_rows(**_fit_kwargs())) == pytest.approx(1.0)
    assert clustered.funnel(_scope())[-1] == ("lead_bucket", 2, 1)
    with pytest.raises(ValueError, match="REPLAY_GENERATION_CONFLICT"):
        replay.ReplayCorpus(rows=(first.rows[0], replace(
            first.rows[0], recomputed_raw_q=0.5, replay_generated_at="2026-09-30T00:00:00+00:00",
        )), unavailable={})


def test_changed_resolver_contract_prevents_pooling(monkeypatch):
    # Moscow switched resolver the day after the historical target date.
    switched = replace(MOSCOW, settlement_source_type="noaa", previous_settlement_source_type="wu_icao",
                       settlement_source_type_effective_date="2026-09-21")
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Moscow": switched})
    world, trade, forecast = _databases(_observation())
    corpus = replay.load_replay_corpus(world, trade, forecast, training_cutoff=NOW, generated_at=NOW)
    old_contract = replay.settlement_contract_id(switched, TARGET)
    today_contracts = replay.current_settlement_contracts({"Moscow": switched}, NOW.date())
    assert old_contract.startswith("wu_icao:") and old_contract not in today_contracts
    assert {row.settlement_contract_id for row in corpus.rows} == {old_contract}
    fit = corpus.fit_corpus(population=SETTLEMENT_STATE, current_contracts=today_contracts, training_cutoff=NOW)
    assert fit.fit_rows(**_fit_kwargs()) == []
    assert fit.funnel(_scope())[4] == ("current_settlement_contract", 0, 0)
    pooled = corpus.fit_corpus(population=SETTLEMENT_STATE, current_contracts=frozenset({old_contract}),
                               training_cutoff=NOW)
    assert len(pooled.fit_rows(**_fit_kwargs())) == 1


def test_fitter_adapter_selects_replay_rows_by_evaluation_recipe_id():
    world, trade, forecast = _databases(_observation())
    provider = replay.ReplayMarketAnchoredFitProvider(
        lambda: (world, trade, forecast), city_timezones={"Moscow": "Europe/Moscow"},
        population=SETTLEMENT_STATE, current_contracts=frozenset({CONTRACT}), min_train_rows=1,
        corpus_cache=CanonicalCorpusCache(),
    )
    current, old = _scope(), _scope(OLD_REVISION)
    assert provider.artifact(scope=current, now=NOW) is not None
    assert provider.insufficient_support(scope=current, now=NOW) is False
    # The live revision at collection time selects nothing; only the recipe does.
    assert provider.artifact(scope=old, now=NOW) is None
    assert provider.insufficient_support(scope=old, now=NOW) is True
    assert provider.replay_unavailable(scope=old, now=NOW) == {replay.NO_REPLAY_EVIDENCE: 0}
    assert provider.calibration_policy.input_revision.startswith(replay.REPLAY_CORPUS_REVISION)
    executed = replay.ReplayMarketAnchoredFitProvider(
        lambda: (world, trade, forecast), city_timezones={"Moscow": "Europe/Moscow"},
        population=EXECUTED_ORDER, current_contracts=frozenset({CONTRACT}), min_train_rows=1,
        corpus_cache=CanonicalCorpusCache(),
    )
    corpus = replay.load_replay_corpus(world, trade, forecast, training_cutoff=NOW, generated_at=NOW)
    for population in (SETTLEMENT_STATE, EXECUTED_ORDER):
        fit = corpus.fit_corpus(population=population, current_contracts=frozenset({CONTRACT}),
                                training_cutoff=NOW)
        assert fit.funnel(current)[2] == ("population", 1, 1)
    taker = CalibrationFitScope(metric="low", execution_mode="TAKER_LIMIT",
                                execution_contract="FOK_FULL_OR_ZERO", raw_probability_revision=RECIPE)
    # The fill was a resting maker order; it is never evidence for a taker contract.
    assert executed.artifact(scope=current, now=NOW) is not None
    assert executed.artifact(scope=taker, now=NOW) is None


def test_validated_identity_requires_a_covering_certificate():
    world, trade, forecast = _databases(_observation())
    corpus = replay.load_replay_corpus(world, trade, forecast, training_cutoff=NOW, generated_at=NOW)
    fit = corpus.fit_corpus(population=SETTLEMENT_STATE, current_contracts=frozenset({CONTRACT}),
                            training_cutoff=NOW)
    provider = replay.ReplayMarketAnchoredFitProvider(
        lambda: (world, trade, forecast), city_timezones={"Moscow": "Europe/Moscow"},
        population=SETTLEMENT_STATE, current_contracts=frozenset({CONTRACT}),
    )
    certificate = ProbabilityValidationCertificate(
        recipe_id=RECIPE, fit_scope=_scope(), settlement_contracts=(CONTRACT,),
        execution_population=SETTLEMENT_STATE, feature_ranges=(("p0", 0.05, 0.95), ("q_raw", 0.0, 1.0)),
        training_policy=provider.calibration_policy, training_manifest=fit.training_manifest(scope=_scope()),
        validation_results=(("log_loss_delta_vs_raw", 0.0),), verdict=IDENTITY_VALIDATED,
    )
    assert ProbabilityValidationCertificate.from_payload(certificate.as_payload()) == certificate
    policy = ValidatedIdentity(family_key="f", bin_id="b", side="YES", token_id="t", raw_q=0.9, p0=0.42,
                               settlement_contract_id=CONTRACT, certificate=certificate)
    assert policy.corrected_q == 0.9 and policy.as_cert_fields()["policy"] == "VALIDATED_IDENTITY_V1"
    with pytest.raises(ValueError, match="does not cover"):
        ValidatedIdentity(family_key="f", bin_id="b", side="YES", token_id="t", raw_q=0.9, p0=0.99,
                          settlement_contract_id=CONTRACT, certificate=certificate)


def _scope(recipe: str = RECIPE) -> CalibrationFitScope:
    return CalibrationFitScope(metric="low", execution_mode="MAKER_REST", execution_contract="MAKER_REST",
                               raw_probability_revision=recipe)


def _fit_kwargs() -> dict:
    return dict(metric="low", execution_mode="MAKER_REST", execution_contract="MAKER_REST",
                probability_revision=RECIPE)


def _databases(observation: dict):
    """Three private DBs holding one filled MAKER ENTRY on an old-revision Day0 state."""

    world, trade, forecast = (sqlite3.connect(":memory:") for _ in range(3))
    capture = {"p0_held": 0.42, "p0_basis": "GROSS_NATIVE_TOKEN_PRICE", "token_id": "yes-b", "side": "YES",
               "condition_id": CONDITIONS[SELECTED], "execution_mode": "MAKER_REST",
               "book_snapshot_id": "book", "raw_q_held": _origin_q(observation)}
    payload = {
        "q_source": "day0_remaining_day", "probability_semantics_revision": OLD_REVISION,
        "temperature_metric": "low", "city": "Moscow", "target_date": TARGET,
        "condition_id": CONDITIONS[SELECTED], "token_id": "yes-b", "direction": "buy_yes",
        "bin_label": LABELS[SELECTED], "executable_snapshot_id": "book",
        "qkernel_execution_economics": {"global_execution_mode": "MAKER_REST", "raw_calibration_input": capture},
        "day0_probability_authority": {"global_current_observation_payload": observation},
    }
    decided = (CUT + timedelta(seconds=3)).isoformat()
    world.execute("""CREATE TABLE decision_certificates (certificate_hash TEXT, certificate_type TEXT,
        mode TEXT, verifier_status TEXT, decision_time TEXT, persisted_at TEXT, payload_json TEXT,
        payload_hash TEXT)""")
    world.execute("INSERT INTO decision_certificates VALUES (?,?,?,?,?,?,?,?)", (
        "cert-1", "ActionableTradeCertificate", "LIVE", "VERIFIED", decided, decided,
        json.dumps(payload), stable_hash(payload)))
    forecast.execute("""CREATE TABLE market_events (city TEXT, target_date TEXT, temperature_metric TEXT,
        condition_id TEXT, range_label TEXT, range_low REAL, range_high REAL)""")
    forecast.executemany("INSERT INTO market_events VALUES (?,?,?,?,?,?,?)", [
        ("Moscow", TARGET, "low", condition, label, low, high)
        for condition, label, (low, high) in zip(CONDITIONS, LABELS, BOUNDS)])
    forecast.execute("CREATE TABLE forecast_posteriors (posterior_id INTEGER, computed_at TEXT, provenance_json TEXT)")
    trade.executescript("""
      CREATE TABLE venue_commands(command_id TEXT, token_id TEXT, created_at TEXT, venue_order_id TEXT,
        intent_kind TEXT, side TEXT, price REAL, size REAL, envelope_id TEXT);
      CREATE TABLE position_decision_attribution(command_id TEXT, decision_certificate_hash TEXT,
        intent_kind TEXT, created_at TEXT);
      CREATE TABLE venue_submission_envelopes(envelope_id TEXT, order_type TEXT, post_only INTEGER);
      CREATE TABLE venue_trade_facts(trade_fact_id INTEGER PRIMARY KEY, command_id TEXT, trade_id TEXT,
        venue_order_id TEXT, state TEXT, filled_size REAL, tx_hash TEXT, observed_at TEXT,
        ingested_at TEXT, venue_timestamp TEXT, local_sequence INTEGER, raw_payload_json TEXT);
      CREATE TABLE executable_market_snapshots(snapshot_id TEXT, condition_id TEXT, yes_token_id TEXT,
        no_token_id TEXT, token_map_json TEXT, orderbook_top_bid TEXT, orderbook_top_ask TEXT, captured_at TEXT);
      CREATE TABLE payout_observations(id INTEGER PRIMARY KEY, condition_id TEXT, outcome_index INTEGER,
        payout_numerator INTEGER, payout_denominator INTEGER, state TEXT, source TEXT,
        block_number INTEGER, block_hash TEXT, observed_at TEXT, superseded_by INTEGER);
    """)
    trade.execute("INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
                  ("cmd-1", "yes-b", decided, "order-1", "ENTRY", "BUY", 0.42, 10.0, "env-1"))
    trade.execute("INSERT INTO position_decision_attribution VALUES (?,?,?,?)", ("cmd-1", "cert-1", "ENTRY", decided))
    trade.execute("INSERT INTO venue_submission_envelopes VALUES (?,?,?)", ("env-1", "GTC", 1))
    filled = CUT + timedelta(minutes=2)
    trade.execute("INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
        1, "cmd-1", "trade-1", "order-1", "CONFIRMED", 10.0, "0xtx", filled.isoformat(),
        filled.strftime("%Y-%m-%d %H:%M:%S"), filled.isoformat(), 1, "{}"))
    trade.execute("INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?,?)", (
        "book", CONDITIONS[SELECTED], "yes-b", "no-b", json.dumps({"YES": "yes-b", "NO": "no-b"}),
        "0.41", "0.43", (CUT - timedelta(seconds=30)).isoformat()))
    for index in (0, 1):
        trade.execute("INSERT INTO payout_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            index + 1, CONDITIONS[SELECTED], index, 1 - index, 1,
            "RESOLVED_NONZERO" if index == 0 else "RESOLVED_ZERO", "chain_rpc_finalized_v1",
            100, "0x" + "aa" * 32, (CUT + timedelta(days=1)).isoformat(), None))
    return world, trade, forecast
