"""Deterministic synthetic checks; no network, live DB, or repo conftest required.

Run: python test_reference.py --json-out synthetic_results.json
The emitted evidence supports the declared finite-state synthetic model only.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
from decimal import Decimal
from itertools import product
import json
from pathlib import Path
import platform
import sys
import time
import unittest

import numpy as np
import scipy
from scipy.special import logsumexp

from reference import (
    Evidence, FiniteMarkedModel, InconsistentEvidence, Mark, ReportKernel,
    SettlementBin, State, WrhRow, _log_probabilities, causal_evidence,
    dense_evidence, fit_interval_pairs, infer, infer_metar_only, infer_mixture,
    infer_optional_dense, interval_evidence, local_day_utc_bounds,
    log_normal_interval, nonreceipt_evidence, ou_grid_transition,
    raw_report_predictor_value, report_fact, round_half_up,
    routine_instants_for_local_day, temperature_report_kernel, wrh_row_value,
    receipt_density_evidence, fit_finite_persistence, dispatch_optional_dense,
)

SYNTHETIC_RESULTS: dict[str, object] = {}


def binary_model(*, name="binary", initial=(0.5, 0.5), steps=2) -> FiniteMarkedModel:
    states = (State(0.0, "calm"), State(1.0, "storm"))
    no_reports = temperature_report_kernel(states, routine=False)
    final = temperature_report_kernel(states, routine=True)
    return FiniteMarkedModel(tuple(float(i) for i in range(steps)), states,
                             _log_probabilities(initial),
                             tuple(_log_probabilities(np.eye(2)) for _ in range(steps - 1)),
                             tuple([no_reports] * (steps - 1) + [final]), name=name)


def brute_force(model: FiniteMarkedModel, observations, decision, metric):
    """Independent enumeration of complete state and mark paths, no recursion."""
    selected = causal_evidence(model, observations, decision)
    total = {}
    for states in product(range(len(model.states)), repeat=len(model.times)):
        prior = model.initial_log_probabilities[states[0]]
        for i in range(1, len(states)):
            prior += model.log_transitions[i - 1][states[i - 1], states[i]]
        for marks in product(*(range(len(r.marks)) for r in model.reports)):
            weight = prior
            observed_values = []
            for i, j in enumerate(marks):
                kernel = model.reports[i]
                weight += kernel.log_probabilities[states[i], j]
                value = kernel.marks[j].settlement_value
                if value is not None:
                    observed_values.append(value)
            for e in selected:
                if e.log_state_likelihood is not None:
                    weight += e.log_state_likelihood[states[e.event_index]]
                if e.log_report_likelihood is not None:
                    a = e.log_report_likelihood
                    weight += a[marks[e.event_index]] if a.ndim == 1 else a[states[e.event_index], marks[e.event_index]]
            value = (max if metric == "high" else min)(observed_values) if observed_values else None
            total[value] = np.logaddexp(total.get(value, -np.inf), weight)
    normalizer = logsumexp(list(total.values()))
    return {k: v - normalizer for k, v in total.items()}, normalizer


class SettlementTests(unittest.TestCase):
    def test_integer_bin_topology_rejects_fractional_and_nonfinite_endpoints(self):
        for bad in (True, False, 0.5, 1.0, np.nan, np.inf, -np.inf):
            with self.assertRaises(ValueError):
                SettlementBin("bad", lower=bad)
        for bad_name in ("", " ", None):
            with self.assertRaises(ValueError):
                SettlementBin(bad_name)
        with self.assertRaises(ValueError):
            SettlementBin("reversed", lower=2, upper=1)
        result = infer(binary_model(), [], decision_at=0.5)
        with self.assertRaisesRegex(ValueError, "gap"):
            result.bin_vector((SettlementBin("left", upper=0, receives_no_data=True),
                               SettlementBin("right", lower=2)))

    def test_negative_half_ties_and_decoder_order(self):
        self.assertEqual([round_half_up(x) for x in (-2.5, -1.5, -0.5, 0.5, 1.5)],
                         [-2, -1, 0, 1, 2])
        self.assertEqual(raw_report_predictor_value(body_integer_c=20, t_group_c=Decimal("20.4"),
                                                     use_t_group=True, unit="F"), 69)
        self.assertEqual(raw_report_predictor_value(body_integer_c=20, t_group_c=Decimal("20.4"),
                                                     use_t_group=False, unit="F"), 68)

    def test_native_feed_overrides_tgroup_predictor_and_hourly_includes_speci(self):
        # A source feed cell of 68F wins over a raw 20.4C T-group suggesting 69F.
        row = WrhRow(68.0, "F", None, "KJFK 071230Z 18010KT 10SM CLR 20/10 A3000 T02040100")
        self.assertEqual(wrh_row_value(row, "KJFK", "hourly", "F"), 68)
        self.assertIsNone(wrh_row_value(replace(row, raw_metar="METAR KJFK ..."), "KJFK", "hourly", "F"))
        self.assertEqual(wrh_row_value(replace(row, sea_level_pressure=1013.2), "KJFK", "hourly", "F"), 68)
        self.assertEqual(wrh_row_value(replace(row, raw_metar=None), "KJFK", "all", "F"), 68)
        with self.assertRaises(ValueError):
            wrh_row_value(row, "KJFK", "all", "C")

    def test_local_calendar_half_open_dst_day(self):
        for day, hours in ((date(2026, 3, 8), 23), (date(2026, 11, 1), 25)):
            start, end = local_day_utc_bounds(day, "America/Chicago")
            self.assertEqual((end - start) / 3600, hours)
            slots = routine_instants_for_local_day(day, "America/Chicago", (20, 50))
            self.assertEqual(len(slots), hours * 2)
            self.assertTrue(all(start <= t < end for t in slots))
            self.assertEqual(len(set(slots)), len(slots))

    def test_interval_censor_edges_are_half_open(self):
        states = (State(-1.5), State(-0.5), State(0.5))
        model = FiniteMarkedModel((0,), states, _log_probabilities([1/3] * 3), (),
                                  (temperature_report_kernel(states, routine=True),))
        evidence = interval_evidence(model, 0, 0, receipt_at=0.1, observation_id="integer")
        np.testing.assert_array_equal(np.isfinite(evidence.log_state_likelihood), [False, True, False])


class ExactRecursionTests(unittest.TestCase):
    def test_against_full_path_enumeration_both_metrics_and_weather_marks(self):
        states = (State(-0.2, "calm"), State(1.2, "storm"))
        trans = _log_probabilities([[0.83, 0.17], [0.26, 0.74]])
        reports = (
            temperature_report_kernel(states, routine=True),
            temperature_report_kernel(states, routine=False,
                                      speci_probability_by_weather={"calm": 0.07, "storm": 0.8}),
            temperature_report_kernel(states, routine=True),
        )
        model = FiniteMarkedModel((0, 1, 2), states, _log_probabilities([0.6, 0.4]),
                                  (trans, trans), reports)
        observations = [dense_evidence(model, 1, 0.8, receipt_at=1.1, observation_id="dense", sigma_c=0.35)]
        for metric in ("high", "low"):
            actual = infer(model, observations, decision_at=1.5, metric=metric)
            expected, normalizer = brute_force(model, observations, 1.5, metric)
            for value in actual.values:
                np.testing.assert_allclose(actual.log_probability_of_value(value), expected.get(value, -np.inf), atol=2e-13)
            self.assertAlmostEqual(actual.log_evidence, normalizer, places=13)

    def test_weather_driven_speci_and_station_dark_mass(self):
        states = (State(0.0, "calm"), State(0.0, "storm"))
        report = temperature_report_kernel(states, routine=False,
                                            speci_probability_by_weather={"calm": 0.1, "storm": 0.9})
        model = FiniteMarkedModel((0,), states, _log_probabilities([0.5, 0.5]), (), (report,))
        result = infer(model, [], decision_at=0.5)
        self.assertAlmostEqual(np.exp(result.log_probability_of_value(None)), 0.5)
        bins = result.bin_vector((SettlementBin("lowest", upper=-1, receives_no_data=True),
                                  SettlementBin("zero", lower=0, upper=0),
                                  SettlementBin("upper", lower=1)))
        self.assertAlmostEqual(bins["lowest"].linear_for_display(), 0.5)
        self.assertFalse(bins["lowest"].settlement_forced_one)
        zero_speci = replace(model, reports=(temperature_report_kernel(states, routine=False),))
        self.assertEqual(infer(zero_speci, [], decision_at=0.5).log_probability_of_value(None), 0.0)
        self.assertEqual(infer(zero_speci, [], decision_at=0.5, metric="low").log_probability_of_value(None), 0.0)

    def test_informative_nonreceipt_survival_factor(self):
        states = (State(0.0, "calm"), State(1.0, "storm"))
        report = temperature_report_kernel(states, routine=False,
                                            speci_probability_by_weather={"calm": 0.2, "storm": 0.8})
        model = FiniteMarkedModel((0,), states, _log_probabilities([0.5, 0.5]), (), (report,))
        e = nonreceipt_evidence(model, 0, checked_at=1.0, rates=np.array([[1.0], [4.0]]),
                                observation_id="no-arrival-snapshot")
        result = infer(model, [e], decision_at=1.1)
        no_data = 0.5
        normalizer = no_data + 0.5 * 0.2 * np.exp(-1) + 0.5 * 0.8 * np.exp(-4)
        self.assertAlmostEqual(np.exp(result.log_probability_of_value(None)), no_data / normalizer)

    def test_informative_receipt_density_and_lifecycle_deduplication(self):
        model = binary_model(steps=1)
        rates = np.array([[1.0], [4.0]])
        received = receipt_density_evidence(model, 0, receipt_at=0.5, rates=rates, observation_id="receipt")
        result = infer(model, [received], decision_at=0.6)
        expected = (4 * np.exp(-2)) / (np.exp(-0.5) + 4 * np.exp(-2))
        self.assertAlmostEqual(np.exp(result.log_probability_of_value(1)), expected)
        earlier = nonreceipt_evidence(model, 0, checked_at=0.2, rates=rates, observation_id="earlier")
        with self.assertRaisesRegex(ValueError, "canonical arrival"):
            infer(model, [earlier, received], decision_at=0.6)
        fact = report_fact(model, 0, 1, receipt_at=0.1, observation_id="source")
        with self.assertRaisesRegex(ValueError, "contradicts"):
            infer(model, [earlier, fact], decision_at=0.6)
        later_fact = replace(fact, receipt_at=0.5)
        with self.assertRaisesRegex(ValueError, "contradicts"):
            infer(model, [earlier, later_fact], decision_at=0.6)

    def test_ensemble_members_are_reweighted_by_evidence(self):
        a, b = binary_model(name="a", initial=(0.9, 0.1)), binary_model(name="b", initial=(0.1, 0.9))
        evidence = dense_evidence(a, 0, 1.0, receipt_at=0.1, observation_id="d", sigma_c=0.2)
        actual = infer_mixture([a, b], [0.5, 0.5], [[evidence], [evidence]], decision_at=0.5)
        ea = infer(a, [evidence], decision_at=0.5).log_evidence
        eb = infer(b, [evidence], decision_at=0.5).log_evidence
        posterior_b = np.exp(eb - np.logaddexp(ea, eb))
        self.assertAlmostEqual(actual.component_posteriors["b"], posterior_b, places=13)
        self.assertGreater(posterior_b, 0.89)
        self.assertGreater(np.exp(actual.log_probability_of_value(1)), 0.9999)

    def test_known_source_extreme_is_only_hard_boundary(self):
        model = binary_model(steps=3)
        model = replace(model, reports=(temperature_report_kernel(model.states, routine=True),
                                        *model.reports[1:]))
        for metric, observed, impossible in (("high", 1, SettlementBin("below", upper=0)),
                                             ("low", 0, SettlementBin("above", lower=1))):
            kernel = model.reports[0]
            j = next(i for i, m in enumerate(kernel.marks) if m.settlement_value == observed)
            fact = report_fact(model, 0, j, receipt_at=0.1, observation_id="source")
            absurd_dense = dense_evidence(model, 1, -100.0 if observed else 100.0,
                                           receipt_at=1.1, observation_id="dense", sigma_c=0.05)
            result = infer(model, [fact, absurd_dense], decision_at=1.5, metric=metric)
            probability = result.bin_probability(impossible)
            self.assertEqual(probability.log_probability, -np.inf)
            self.assertFalse(probability.model_supported)
            self.assertTrue(probability.settlement_forced_zero)

    def test_dense_full_support_and_log_complement_not_false_certainty(self):
        model = binary_model()
        baseline = infer(model, [], decision_at=0.5)
        for kwargs in ({"sigma_c": 0.01}, {"sigma_c": 0.01, "quantum_c": 0.1},
                       {"sigma_c": 0.01, "outlier_weight": 0.05, "outlier_scale_c": 1.0}):
            reading = dense_evidence(model, 0, 100.0, receipt_at=0.1, observation_id="d", **kwargs)
            result = infer(model, [reading], decision_at=0.5)
            np.testing.assert_array_equal(result.model_support, baseline.model_support)
            probability = result.bin_probability(SettlementBin("one", lower=1, upper=1))
            self.assertTrue(np.isfinite(probability.log_complement))
            self.assertTrue(probability.model_complement_supported)
            self.assertFalse(probability.settlement_forced_one)
            self.assertFalse(result.serving_authorized)

    def test_dense_may_not_inject_a_support_mask(self):
        model = binary_model()
        bad = Evidence("dense", 0, 0.1, "dense", np.array([0.0, -np.inf]))
        with self.assertRaisesRegex(ValueError, "structural"):
            infer(model, [bad], decision_at=0.5)

    def test_model_zero_is_not_certified_semantic_zero(self):
        result = infer(binary_model(), [], decision_at=0.5)
        p = result.bin_probability(SettlementBin("outside-toy-grid", lower=10))
        self.assertFalse(p.model_supported)
        self.assertFalse(p.settlement_forced_zero)


class CausalityTests(unittest.TestCase):
    def test_absent_dense_dispatch_returns_identical_legacy_object(self):
        legacy = {"probabilities": np.array([0.2, 0.8]), "certificate_id": "immutable"}
        before = legacy["probabilities"].copy()
        calls = []

        def old():
            calls.append("legacy")
            return legacy

        def new(observations):
            calls.append("dense")
            return {"observations": observations}

        for optional in (None, [], [Evidence("future", 999, 5.0, "dense", np.zeros(7))]):
            result = dispatch_optional_dense(optional, decision_at=1, legacy_callback=old, dense_callback=new)
            self.assertIs(result, legacy)
        self.assertEqual(calls, ["legacy"] * 3)
        np.testing.assert_array_equal(before, legacy["probabilities"])

    def test_future_payload_is_inert_until_its_receipt_cutoff(self):
        model = binary_model()
        malformed_future = Evidence("", 999, 1.0, "dense", np.array([np.nan]))
        before = infer(model, [], decision_at=0.5)
        after = infer(model, [malformed_future], decision_at=0.5)
        np.testing.assert_array_equal(before.log_probabilities, after.log_probabilities)
        with self.assertRaises(ValueError):
            infer(model, [malformed_future], decision_at=1.1)

    def test_model_and_evidence_detach_input_buffers(self):
        model = binary_model()
        likelihood = np.array([0.0, -1.0])
        evidence = Evidence("d", 0, 0.1, "dense", likelihood)
        likelihood[0] = -100
        self.assertEqual(evidence.log_state_likelihood[0], 0)
        with self.assertRaises(ValueError):
            evidence.log_state_likelihood[0] = -100
        with self.assertRaises(ValueError):
            model.initial_log_probabilities[0] = -100

    def test_receipt_strictness_delayed_past_rows_and_no_dense_identity(self):
        model = binary_model(steps=3)
        model = replace(model, reports=(model.reports[0],
                                        temperature_report_kernel(model.states, routine=True),
                                        model.reports[2]))
        j = next(i for i, m in enumerate(model.reports[1].marks) if m.settlement_value == 1)
        pending_fact = report_fact(model, 1, j, receipt_at=1.5, observation_id="delayed")
        baseline = infer(model, [], decision_at=1.5)
        equal_receipt = infer(model, [pending_fact], decision_at=1.5)
        np.testing.assert_array_equal(baseline.log_probabilities, equal_receipt.log_probabilities)
        self.assertGreater(np.exp(baseline.log_probability_of_value(0)), 0)
        received = infer(model, [pending_fact], decision_at=1.5001)
        self.assertEqual(received.log_probability_of_value(0), -np.inf)
        no_dense_a = infer_metar_only(model, [pending_fact], decision_at=1.7)
        no_dense_b = infer_optional_dense(model, [pending_fact], decision_at=1.7)
        np.testing.assert_array_equal(no_dense_a.log_probabilities, no_dense_b.log_probabilities)
        self.assertEqual(no_dense_a.log_evidence, no_dense_b.log_evidence)

    def test_future_observation_cannot_be_received_early(self):
        model = binary_model()
        future = Evidence("future", 1, 0.1, "dense", np.zeros(2))
        with self.assertRaisesRegex(ValueError, "before"):
            infer(model, [future], decision_at=0.5)
        with self.assertRaisesRegex(ValueError, "model snapshot"):
            infer(replace(model, available_at=0.5), [], decision_at=0.5)

    def test_duplicate_lineage_once_and_conflict_rejected(self):
        model = binary_model()
        e = dense_evidence(model, 0, 0.2, receipt_at=0.1, observation_id="one-reading")
        once = infer(model, [e], decision_at=0.5)
        duplicate = infer(model, [e, replace(e, receipt_at=0.2)], decision_at=0.5)
        np.testing.assert_array_equal(once.log_probabilities, duplicate.log_probabilities)
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            infer(model, [e, replace(e, log_state_likelihood=np.zeros(2))], decision_at=0.5)

    def test_noncoincident_dense_grid_informs_and_old_data_not_timer_erased(self):
        model = binary_model(steps=4)
        model = replace(model, times=(0.0, 10.0, 20.0, 30.0),
                         log_transitions=tuple(_log_probabilities([[0.95, 0.05], [0.05, 0.95]]) for _ in range(3)))
        e = dense_evidence(model, 2, 0.95, receipt_at=20.1, observation_id="dense-20", sigma_c=0.2)
        a, b = infer(model, [], decision_at=25), infer(model, [e], decision_at=25)
        self.assertGreater(b.log_probability_of_value(1), a.log_probability_of_value(1))
        np.testing.assert_array_equal(a.model_support, b.model_support)
        old = infer(model, [e], decision_at=1000)
        np.testing.assert_array_equal(old.log_probabilities, b.log_probabilities)
        self.assertTrue(all(model.reports[2].marks[j].settlement_value is None
                            for j in range(len(model.reports[2].marks))
                            if np.any(np.isfinite(model.reports[2].log_probabilities[:, j]))))


class StatisticalTests(unittest.TestCase):
    def test_exact_expected_log_score_information_gain(self):
        model = binary_model()
        likelihood = np.array([[0.9, 0.1], [0.1, 0.9]])  # state x observed dense symbol
        baseline = infer(model, [], decision_at=0.5)
        loss_a, loss_b = 0.0, 0.0
        for x, y in product(range(2), repeat=2):
            probability = 0.5 * likelihood[x, y]
            e = Evidence(f"dense-symbol-{y}", 0, 0.1, "dense", np.log(likelihood[:, y]))
            posterior = infer(model, [e], decision_at=0.5)
            loss_a -= probability * baseline.log_probability_of_value(x)
            loss_b -= probability * posterior.log_probability_of_value(x)
        entropy = -(0.9 * np.log(0.9) + 0.1 * np.log(0.1))
        self.assertAlmostEqual(loss_a, np.log(2), places=14)
        self.assertAlmostEqual(loss_b, entropy, places=14)
        self.assertGreater(loss_a - loss_b, 0.36)
        SYNTHETIC_RESULTS["exact_information_enumeration"] = {
            "metar_only_expected_log_loss": loss_a,
            "dense_expected_log_loss": loss_b,
            "conditional_mutual_information_nats": loss_a - loss_b,
            "meaning": "Exact finite enumeration under the correct declared toy model.",
        }

    def test_joint_interval_pair_parameter_recovery(self):
        rng = np.random.default_rng(824105)
        n = 6000
        f = np.linspace(-4, 18, n) + np.sin(np.arange(n) / 19)
        truth = {"bias_c": 0.18, "dense_sigma_c": 0.16, "latent_sigma_c": 0.9}
        latent = f + rng.normal(0, truth["latent_sigma_c"], n)
        k = np.floor(latent + 0.5).astype(int)
        y = latent + truth["bias_c"] + rng.normal(0, truth["dense_sigma_c"], n)
        fit = fit_interval_pairs(k, y, f)
        self.assertLess(abs(fit.bias_c - truth["bias_c"]), 0.025)
        self.assertLess(abs(fit.dense_sigma_c - truth["dense_sigma_c"]), 0.025)
        self.assertLess(abs(fit.latent_sigma_c - truth["latent_sigma_c"]), 0.045)
        self.assertTrue(fit.converged)
        SYNTHETIC_RESULTS["interval_pair_parameter_recovery"] = {
            "seed": 824105, "training_pairs": n, "truth": truth,
            "fitted": {"bias_c": fit.bias_c, "dense_sigma_c": fit.dense_sigma_c,
                       "latent_sigma_c": fit.latent_sigma_c},
            "optimizer_converged": fit.converged,
            "meaning": "Joint iid paired-data MLE; does not validate temporal parameters or real sensor identity.",
        }

    def test_temporal_persistence_recovery_from_censored_marginal_likelihood(self):
        rng = np.random.default_rng(172119)
        n, true_tau = 2200, 12.0
        temperatures = np.array([-0.8, -0.2, 0.2, 0.8])
        pi = np.array([0.15, 0.35, 0.35, 0.15])
        times = np.cumsum(rng.choice([1.0, 2.0], n))
        state = rng.choice(4, p=pi)
        hidden = np.empty(n)
        for i in range(n):
            if i and rng.random() > np.exp(-(times[i] - times[i - 1]) / true_tau):
                state = rng.choice(4, p=pi)
            hidden[i] = temperatures[state]
        metar = np.full(n, np.nan)
        dense = np.full(n, np.nan)
        metar[::3] = np.floor(hidden[::3] + 0.5)
        dense[1::2] = hidden[1::2] + 0.13 + rng.normal(0, 0.16, len(dense[1::2]))
        fit = fit_finite_persistence(times, temperatures, pi, metar, dense, bias_c=0.13, dense_sigma_c=0.16)
        self.assertTrue(fit.converged)
        self.assertLess(abs(fit.tau / true_tau - 1), 0.25)
        SYNTHETIC_RESULTS["finite_hmm_persistence_recovery"] = {
            "seed": 172119, "training_instants": n, "true_tau": true_tau,
            "fitted_tau": fit.tau, "optimizer_converged": fit.converged,
            "meaning": "Censored/missing-observation marginal MLE for a finite reset HMM with anchored sensor parameters; not OU parameter recovery.",
        }

    def test_dense_beats_metar_only_on_heldout_simulated_paths(self):
        rng = np.random.default_rng(701926)
        states = tuple(State(v) for v in (-1.0, 0.0, 1.0, 2.0))
        initial = _log_probabilities([1, 0, 0, 0])
        # A first weather innovation, then persistent residual state.
        jump = np.tile([0.1, 0.35, 0.4, 0.15], (4, 1))
        persist = 0.92 * np.eye(4) + 0.08 * np.tile([0.1, 0.35, 0.4, 0.15], (4, 1))
        kernels = tuple(temperature_report_kernel(states, routine=i in (0, 2, 3)) for i in range(4))
        model = FiniteMarkedModel((0, 1, 2, 3), states, initial,
                                  tuple(_log_probabilities(p) for p in (jump, persist, persist)), kernels)
        j0 = next(j for j, mark in enumerate(kernels[0].marks) if mark.settlement_value == -1)
        fact = report_fact(model, 0, j0, receipt_at=0.1, observation_id="initial-source")
        baseline = infer_metar_only(model, [fact], decision_at=2.5)
        loss_a, loss_b, pit_a, pit_b, confident_a, confident_b = [], [], [], [], [], []
        days = 800
        for day in range(days):
            state1 = rng.choice(4, p=jump[0])
            state2 = rng.choice(4, p=persist[state1])
            state3 = rng.choice(4, p=persist[state2])
            truth = int(max(-1, states[state2].temperature_c, states[state3].temperature_c))
            y = states[state2].temperature_c + rng.normal(0, 0.18)
            dense = dense_evidence(model, 2, y, receipt_at=2.1, observation_id=f"dense-{day}", sigma_c=0.18)
            posterior = infer_optional_dense(model, [fact, dense], decision_at=2.5)
            loss_a.append(-baseline.log_probability_of_value(truth))
            loss_b.append(-posterior.log_probability_of_value(truth))
            for result, pits, confident in ((baseline, pit_a, confident_a), (posterior, pit_b, confident_b)):
                p = np.exp(result.log_probabilities)
                target = result.values.index(truth)
                pits.append(float(np.sum(p[:target]) + rng.random() * p[target]))
                best = int(np.argmax(p))
                if p[best] >= 0.99:
                    confident.append(result.values[best] != truth)
            np.testing.assert_array_equal(posterior.model_support, baseline.model_support)
            self.assertEqual(posterior.log_probability_of_value(None), -np.inf)
        gains = np.asarray(loss_a) - loss_b
        se = float(np.std(gains, ddof=1) / np.sqrt(days))
        self.assertGreater(float(np.mean(gains)), 5 * se)
        for pits in (pit_a, pit_b):
            self.assertLess(abs(np.mean(pits) - 0.5), 0.04)
            self.assertLess(abs(np.var(pits) - 1/12), 0.02)
        SYNTHETIC_RESULTS["heldout_finite_markov_paths"] = {
            "seed": 701926, "days": days,
            "metar_only_mean_log_loss": float(np.mean(loss_a)),
            "dense_mean_log_loss": float(np.mean(loss_b)),
            "paired_mean_improvement_nats": float(np.mean(gains)),
            "paired_standard_error": se,
            "improvement_approx_95pct_interval": [float(np.mean(gains) - 1.96 * se), float(np.mean(gains) + 1.96 * se)],
            "randomized_pit_means": {"metar_only": float(np.mean(pit_a)), "dense": float(np.mean(pit_b))},
            "q_ge_0_99_wrong_and_total": {"metar_only": [sum(confident_a), len(confident_a)],
                                            "dense": [sum(confident_b), len(confident_b)]},
            "meaning": "Oracle parameters are fixed independently of heldout days; not an A/B test on Zeus or real stations.",
        }

    def test_bias_misspecification_can_make_dense_worse(self):
        model = binary_model()
        a = infer(model, [], decision_at=0.5)
        loss_a = loss_b = loss_corrected = 0.0
        for truth in (0, 1):
            reading = truth + 1.0  # unmodeled sensor bias
            bad = dense_evidence(model, 0, reading, receipt_at=0.1, observation_id="bad", sigma_c=0.15)
            good = dense_evidence(model, 0, reading, receipt_at=0.1, observation_id="good", sigma_c=0.15, bias_c=1.0)
            loss_a -= 0.5 * a.log_probability_of_value(truth)
            loss_b -= 0.5 * infer(model, [bad], decision_at=0.5).log_probability_of_value(truth)
            loss_corrected -= 0.5 * infer(model, [good], decision_at=0.5).log_probability_of_value(truth)
        self.assertGreater(loss_b, loss_a + 1)
        self.assertLess(loss_corrected, loss_a)
        SYNTHETIC_RESULTS["deliberate_bias_misspecification"] = {
            "metar_only_log_loss": loss_a, "wrong_dense_model_log_loss": loss_b,
            "known_correct_bias_log_loss": loss_corrected,
            "meaning": "Counterexample: adding data is not guaranteed to help a misspecified likelihood.",
        }

    def test_ou_cell_approximation_retains_tail_mass_and_is_labelled(self):
        centers, edges = [-2, 0, 2], [-np.inf, -1, 1, np.inf]
        transition = ou_grid_transition(centers, edges, dt=10, tau=30, stationary_sd=1)
        np.testing.assert_allclose(logsumexp(transition, axis=1), 0, atol=1e-14)
        self.assertTrue(np.all(np.isfinite(transition)))
        self.assertAlmostEqual(transition[1, 0], transition[1, 2], places=14)
        self.assertIn("APPROXIMATION", ou_grid_transition.__doc__)

    def test_log_interval_preserves_remote_tail_mass(self):
        value = float(log_normal_interval(100, 100.1))
        self.assertTrue(np.isfinite(value))
        self.assertLess(value, -5000)
        self.assertAlmostEqual(float(log_normal_interval(-100.1, -100)), value, places=12)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path)
    args, rest = parser.parse_known_args()
    start = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    elapsed = time.perf_counter() - start
    report = {
        "schema": "dense_obs_theory.synthetic.v1", "passed": result.wasSuccessful(),
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "elapsed_seconds": elapsed,
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__},
        "scope": "Exact finite-state reference and synthetic experiments only. No live calibration or continuous-process certification.",
        "results": SYNTHETIC_RESULTS,
    }
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
