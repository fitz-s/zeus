# Created: 2026-05-24
# Last reused/audited: 2026-10-07 (dense state-space v36/v35 revision; old v34/v33 and reverted v35 stay parseable)
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R2/R3 proof.

import pytest

from src.contracts.settlement_semantics import SettlementSemantics
from src.decision_kernel.canonicalization import stable_hash
from src.events.day0_authority import (
    Day0AuthorityError,
    Day0AuthorityEvidence,
    assert_live_day0_authority,
    observability_row_to_authority,
)
from src.events.forecast_completeness import ForecastSnapshotEvidence, classify_forecast_snapshot


def test_hko_observation_clock_revision_does_not_relabel_old_certificates():
    from src.events.day0_authority import (
        DAY0_PROBABILITY_SEMANTICS_REVISION,
        bind_day0_probability_semantics,
        day0_probability_semantics_revision,
    )

    current = bind_day0_probability_semantics("rebuilt-current-source-certificate")
    assert day0_probability_semantics_revision(current) == DAY0_PROBABILITY_SEMANTICS_REVISION
    for previous in (
        "day0_settlement_channel_revision_model_v27_diurnal_mixture_v1",
        "day0_resolver_terminal_composition_v26_diurnal_mixture_v1",
        "day0_settlement_channel_revision_model_v28_hko_observation_clock_v1",
        "day0_resolver_terminal_composition_v27_hko_observation_clock_v1",
        "day0_settlement_channel_revision_model_v28_smooth_center_bias_v1",
        "day0_resolver_terminal_composition_v27_smooth_center_bias_v1",
        "day0_settlement_channel_revision_model_v29_smooth_center_bias_observation_clock_v1",
        "day0_resolver_terminal_composition_v28_smooth_center_bias_observation_clock_v1",
        "day0_settlement_channel_revision_model_v29_instrument_variance_owner_v1",
        "day0_resolver_terminal_composition_v28_instrument_variance_owner_v1",
        "day0_settlement_channel_revision_model_v30_smooth_center_bias_observation_clock_city_instrument_variance_v1",
        "day0_resolver_terminal_composition_v29_smooth_center_bias_observation_clock_city_instrument_variance_v1",
        "day0_settlement_channel_revision_model_v31_smooth_center_bias_observation_clock_city_instrument_native_boundary_v1",
        "day0_resolver_terminal_composition_v30_smooth_center_bias_observation_clock_city_instrument_native_boundary_v1",
        "day0_settlement_channel_revision_model_v32_smooth_center_bias_observation_clock_city_instrument_native_boundary_v1",
        "day0_resolver_terminal_composition_v31_smooth_center_bias_observation_clock_city_instrument_native_boundary_v1",
        "day0_settlement_channel_revision_model_v33_unshifted_remaining_observation_clock_city_instrument_native_boundary_v1",
        "day0_resolver_terminal_composition_v32_unshifted_remaining_observation_clock_city_instrument_native_boundary_v1",
    ):
        old = f"day0-semrev:{previous}:immutable-entry-certificate"
        assert day0_probability_semantics_revision(old) != DAY0_PROBABILITY_SEMANTICS_REVISION
        assert bind_day0_probability_semantics(old) == old


def test_unshifted_joint_revision_preserves_clock_instrument_and_native_boundary(monkeypatch):
    from src.events import day0_authority as authority

    suffix = "observation_clock_city_instrument_native_boundary_v1"
    assert authority.DAY0_PROBABILITY_SEMANTICS_REVISION_SURVIVAL == f"day0_settlement_channel_revision_model_v36_dense_state_space_page_only_boundary_{suffix}"
    assert authority.DAY0_PROBABILITY_SEMANTICS_REVISION_RESOLVER == f"day0_resolver_terminal_composition_v35_dense_state_space_page_only_boundary_{suffix}"
    for current_revision, previous_revision in (
        (authority.DAY0_PROBABILITY_SEMANTICS_REVISION_SURVIVAL, f"day0_settlement_channel_revision_model_v34_unmixed_unshifted_remaining_{suffix}"),
        (authority.DAY0_PROBABILITY_SEMANTICS_REVISION_RESOLVER, f"day0_resolver_terminal_composition_v33_unmixed_unshifted_remaining_{suffix}"),
        (authority.DAY0_PROBABILITY_SEMANTICS_REVISION_SURVIVAL, "day0_native_domain_roles_v35_point_interval_confidence_v1"),
    ):
        monkeypatch.setattr(authority, "DAY0_PROBABILITY_SEMANTICS_REVISION", current_revision)
        old = f"day0-semrev:{previous_revision}:immutable-source-certificate"
        assert authority.bind_day0_probability_semantics(old) == old
        assert authority.day0_probability_semantics_revision(old) == previous_revision
        rebuilt = authority.bind_day0_probability_semantics("immutable-source-certificate")
        assert authority.day0_probability_semantics_revision(rebuilt) == current_revision
        assert rebuilt != old


@pytest.mark.parametrize("edli", (False, True))
@pytest.mark.parametrize("bias,accepted", (
    (0, True), (0.0, True), (-0.0, True),
    (None, False), (True, False), ("0", False),
    (float("nan"), False), (float("inf"), False), (10 ** 400, False), (0.2, False),
))
def test_declared_live_center_policy_requires_true_finite_numeric_zero(edli, bias, accepted):
    from src.events.day0_authority import current_day0_remaining_center_policy_has_authority

    prefix = "_edli_" if edli else ""
    payload = {prefix + "day0_remaining_center_policy": "unshifted_live_v1",
               prefix + "day0_remaining_center_bias_c": bias}
    assert current_day0_remaining_center_policy_has_authority(payload, edli=edli) is accepted


def test_old_zero_carrier_is_not_restamped_and_noncarrier_remains_ordinary():
    from src.events.day0_authority import current_day0_remaining_center_policy_has_authority
    from src.data.replacement_forecast_bundle_reader import _day0_carrier_identity_reason

    ordinary = {"q_shape": "fused_normal_direct"}
    assert current_day0_remaining_center_policy_has_authority(ordinary)
    assert _day0_carrier_identity_reason(ordinary) is None
    old = {"day0_remaining_carrier_content_identity": "old-unshifted-content",
           "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
           "day0_remaining_center_bias_c": 0.0}
    original = dict(old)
    assert not current_day0_remaining_center_policy_has_authority(old)
    assert _day0_carrier_identity_reason(old) == "REPLACEMENT_DAY0_REMAINING_CENTER_POLICY_NOT_CURRENT"
    assert old == original


@pytest.mark.parametrize("partial", (
    {"_edli_day0_remaining_content_identity": "old-shared-content"},
    {"_edli_day0_probability_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2"},
    {"_edli_day0_probability_operator": "typed_remaining_and_final_extreme_gaussian_v3"},
    {"_edli_day0_probability_operator": "resolver_graded_terminal_composition_v1"},
    {"_edli_day0_remaining_carrier_q": [0.5, 0.5]},
))
def test_partial_shared_carriers_still_require_current_unshifted_policy(partial):
    from src.events.day0_authority import current_day0_remaining_center_policy_has_authority

    assert not current_day0_remaining_center_policy_has_authority(partial, edli=True)


@pytest.mark.parametrize("edli", (False, True))
@pytest.mark.parametrize("field", (
    "day0_diurnal_mixture", "day0_diurnal_mixture_status", "day0_diurnal_mixture_weight",
    "day0_diurnal_mixture_k", "day0_diurnal_mixture_anchor", "day0_diurnal_mixture_artifact",
    "day0_diurnal_mixture_identity", "day0_diurnal_base_q",
))
@pytest.mark.parametrize("value", (None, 0.0, float("nan"), "artifact_unavailable"))
def test_exact_old_diurnal_declarations_cannot_authorize_current_q(edli, field, value):
    from src.events.day0_authority import (
        DAY0_PROBABILITY_MIXTURE_POLICY, current_day0_probability_mixture_policy_has_authority,
    )

    prefix = "_edli_" if edli else ""
    candidate = {prefix + "day0_probability_mixture_policy": DAY0_PROBABILITY_MIXTURE_POLICY}
    assert current_day0_probability_mixture_policy_has_authority(candidate, edli=edli)
    candidate[prefix + field] = value
    assert not current_day0_probability_mixture_policy_has_authority(candidate, edli=edli)


@pytest.mark.parametrize("edli", (False, True))
@pytest.mark.parametrize("policy", (None, True, 0, "", "fitted_live_v1", "unmixed_live_v1"))
def test_current_mixture_policy_is_explicit_and_ordinary_telemetry_is_not_a_carrier(edli, policy):
    from src.events.day0_authority import current_day0_probability_mixture_policy_has_authority

    prefix = "_edli_" if edli else ""
    assert current_day0_probability_mixture_policy_has_authority({
        prefix + "day0_diurnal_diagnostic": "offline-only",
        prefix + "day0_process_sigma_native": 0.5,
    }, edli=edli)
    old_zero = {prefix + "day0_remaining_center_policy": "unshifted_live_v1"}
    assert not current_day0_probability_mixture_policy_has_authority(old_zero, edli=edli)
    candidate = {**old_zero, prefix + "day0_probability_mixture_policy": policy}
    assert current_day0_probability_mixture_policy_has_authority(candidate, edli=edli) is (
        policy == "unmixed_live_v1"
    )


@pytest.mark.parametrize("cache_kind", ("prepared_entry", "prepared_held", "prepared_exit", "ineligible"))
@pytest.mark.parametrize("revision_kind", ("fast_route", "mixture_policy", "current_width_route"))
def test_fast_consumer_route_invalidates_both_process_caches_and_reuses_new_namespace(
    monkeypatch, cache_kind, revision_kind,
):
    """Cache-mechanism proof only; canonical FAST authority is tested separately."""
    import hashlib
    import sqlite3
    from datetime import UTC, datetime, timedelta

    import numpy as np

    from src.engine import event_reactor_adapter as era
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
    from src.events.day0_authority import bind_day0_probability_semantics
    from src.solve.solver import (
        JointOutcomeProbabilityWitness, OutcomeTokenBinding,
        joint_probability_witness_identity,
    )

    monkeypatch.setattr(era, "_GLOBAL_PROBABILITY_FAMILY_CACHE_NAMESPACE", None)
    monkeypatch.setattr(era, "_GLOBAL_PROBABILITY_FAMILY_CACHE", {})
    monkeypatch.setattr(era, "_GLOBAL_PROBABILITY_FAMILY_INELIGIBLE_CACHE", {})
    conn = sqlite3.connect(":memory:")
    try:
        cut = datetime(2026, 10, 1, 8, 25, tzinfo=UTC)
        databases = tuple((str(row[1]), f"memory:{id(conn)}")
                          for row in conn.execute("PRAGMA database_list"))
        if revision_kind == "fast_route":
            old_namespace = hashlib.sha256(
                repr((cut.date().isoformat(), (databases,))).encode("utf-8")
            ).hexdigest()
        elif revision_kind == "mixture_policy":
            from src.events import day0_authority as authority

            with monkeypatch.context() as old_policy:
                old_policy.setattr(authority, "DAY0_PROBABILITY_MIXTURE_POLICY", "fitted_diurnal_live_v1")
                old_namespace = era._global_probability_family_cache_namespace((conn,), decision_time=cut)
        else:
            from src.events.day0_authority import DAY0_PROBABILITY_MIXTURE_POLICY

            # Exact pre-width-route namespace, not a fictional old value for
            # the new field. Existing process entries must miss after reload.
            old_namespace = hashlib.sha256(repr((cut.date().isoformat(), (databases,),
                era._DAY0_FAST_CARRIER_CONSUMER_ROUTE_REVISION,
                DAY0_PROBABILITY_MIXTURE_POLICY)).encode("utf-8")).hexdigest()
        current_namespace = era._global_probability_family_cache_namespace(
            (conn,), decision_time=cut,
        )
        assert current_namespace != old_namespace
        assert current_namespace == era._global_probability_family_cache_namespace(
            (conn,), decision_time=cut + timedelta(seconds=1),
        )
        family_key = "Chicago|2026-10-01|low"
        common = dict(family_key=family_key, event_id="same-source-event")
        if cache_kind == "ineligible":
            revision = era._global_probability_family_cache_revision((conn,))
            receipt = era.EventSubmissionReceipt(False, "same-source-event", "same-cut", reason=(
                "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:"
                f"{era._FAMILY_AUTHORITY_UNAVAILABLE}:"
                "GLOBAL_DAY0_PROVISIONAL_REVISION_LIKELIHOOD_UNAVAILABLE"
            ))
            kwargs = dict(**common, causal_snapshot_id="same-cut", revision=revision)
            era._store_global_probability_family_ineligible_cache(
                old_namespace, **kwargs, receipt=receipt,
            )
            assert era._probe_global_probability_family_ineligible_cache(
                old_namespace, **kwargs,
            ) is receipt
            assert era._probe_global_probability_family_ineligible_cache(
                current_namespace, **kwargs,
            ) is None
            era._store_global_probability_family_ineligible_cache(
                current_namespace, **kwargs, receipt=receipt,
            )
            assert era._probe_global_probability_family_ineligible_cache(
                current_namespace, **kwargs,
            ) is receipt
        else:
            probability_use = {
                "prepared_entry": era._CurrentProbabilityUse.ENTRY,
                "prepared_held": era._CurrentProbabilityUse.HELD_MONITOR,
                "prepared_exit": era._CurrentProbabilityUse.REDUCE_ONLY_EXIT,
            }[cache_kind]
            samples = np.tile(np.array([[0.4, 0.6]]), (500, 1))
            witness_fields = dict(
                family_key=family_key,
                bindings=(OutcomeTokenBinding("lower", "c1", None, None),
                          OutcomeTokenBinding("upper", "c2", None, None)),
                yes_point_q=np.mean(samples, axis=0), yes_q_samples=samples,
                q_version=bind_day0_probability_semantics("cache-component"),
                resolution_identity="resolution", topology_identity="topology",
                posterior_identity_hash="component-posterior",
                source_truth_identity="component-source",
                authority_certificate_hash="component-certificate",
                band_alpha=0.05, band_basis="component-band", captured_at_utc=cut,
            )
            witness = JointOutcomeProbabilityWitness(
                **witness_fields, max_age=timedelta(minutes=3),
                witness_identity=joint_probability_witness_identity(**witness_fields),
            )
            prepared = PreparedGlobalFamily("component-decision", witness, ())
            store = dict(**common, family_binding_hash="component-binding",
                         prepared=prepared, probability_use=probability_use)
            probe = dict(**common, causal_snapshot_id="new-cut", captured_at_utc=cut,
                         probability_use=probability_use)
            era._store_global_probability_family_cache(old_namespace, **store)
            assert era._probe_global_probability_family_cache(old_namespace, **probe) is not None
            assert era._probe_global_probability_family_cache(current_namespace, **probe) is None
            era._store_global_probability_family_cache(current_namespace, **store)
            reused = era._probe_global_probability_family_cache(current_namespace, **probe)
            assert reused is not None
            assert reused.probability_witness.probability_content_identity == witness.probability_content_identity
            assert reused.probability_witness.q_version == witness.q_version
            np.testing.assert_array_equal(reused.probability_witness.yes_q_samples, samples)
    finally:
        conn.close()


def _forecast(**overrides):
    values = dict(
        cycle_hour=0,
        target_step=6,
        expected_steps=(0, 3, 6),
        observed_steps=(0, 3, 6),
        observed_members=51,
        expected_members=51,
        min_members_floor=40,
        source_available_at="2026-05-24T10:00:00+00:00",
        issue_time="2026-05-24T00:00:00+00:00",
        executable_reader_live_eligible=True,
    )
    values.update(overrides)
    return ForecastSnapshotEvidence(**values)


def _semantics() -> SettlementSemantics:
    return SettlementSemantics(
        resolution_source="WU_KMDW",
        measurement_unit="F",
        precision=1.0,
        rounding_rule="wmo_half_up",
        finalization_time="12:00:00Z",
    )


def _day0(**overrides):
    provenance = {
        "city": "Chicago",
        "target_date": "2026-05-24",
        "metric": "high",
        "settlement_source": "wu_icao_history",
        "station_id": "KMDW",
        "configured_station_id": "KMDW",
        "raw_payload_sha256": "a" * 64,
        "observation_time": "2026-05-24T08:00:00+00:00",
        "observation_available_at": "2026-05-24T08:05:00+00:00",
    }
    values = dict(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_match_status="MATCH",
        station_match_status="MATCH",
        local_date_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
        observation_available_at="2026-05-24T08:05:00+00:00",
        observation_time="2026-05-24T08:00:00+00:00",
        raw_value=80.2,
        rounded_value=80,
        station_id=provenance["station_id"],
        configured_station_id=provenance["configured_station_id"],
        settlement_source=provenance["settlement_source"],
        raw_payload_sha256=provenance["raw_payload_sha256"],
        day0_observation_provenance_hash=stable_hash(provenance),
        settlement_semantics=_semantics(),
    )
    values.update(overrides)
    return Day0AuthorityEvidence(**values)


def test_expected_steps_unknown_blocks():
    result = classify_forecast_snapshot(_forecast(cycle_hour=99, expected_steps=()))

    assert result.status == "PARTIAL_BLOCKED"
    assert result.live_eligible is False


def test_issue_time_not_availability():
    result = classify_forecast_snapshot(
        _forecast(source_available_at="2026-05-24T00:00:00+00:00", issue_time="2026-05-24T00:00:00+00:00")
    )

    assert result.reason == "issue_time_cannot_authorize_live"
    assert result.live_eligible is False


def test_partial_allowed_no_live_submit():
    result = classify_forecast_snapshot(_forecast(observed_members=45, expected_members=51))

    assert result.status == "PARTIAL_ALLOWED"
    assert result.live_eligible is False


def test_live_day0_authority_passes_with_settlement_semantics():
    assert_live_day0_authority(_day0(raw_value=80.2, rounded_value=80))


def test_observability_table_row_is_not_live_authority():
    with pytest.raises(Day0AuthorityError, match="not live authority"):
        observability_row_to_authority({"city": "Chicago", "live_authority_status": "OBSERVABILITY_ONLY"})


def test_pre_cutover_durable_live_authority_alias_is_read_as_live():
    row = {
        "city": "Chicago",
        "target_date": "2026-05-24",
        "metric": "high",
        "source_match_status": "MATCH",
        "station_match_status": "MATCH",
        "local_date_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "LIVE_AUTHORITY",
        "observation_available_at": "2026-05-24T08:05:00+00:00",
        "observation_time": "2026-05-24T08:00:00+00:00",
        "raw_value": 80.2,
        "rounded_value": 80,
        "station_id": "KMDW",
        "configured_station_id": "KMDW",
        "settlement_source": "wu_icao_history",
        "raw_payload_sha256": "a" * 64,
        "day0_observation_provenance_hash": stable_hash(
            {
                "city": "Chicago",
                "target_date": "2026-05-24",
                "metric": "high",
                "settlement_source": "wu_icao_history",
                "station_id": "KMDW",
                "configured_station_id": "KMDW",
                "raw_payload_sha256": "a" * 64,
                "observation_time": "2026-05-24T08:00:00+00:00",
                "observation_available_at": "2026-05-24T08:05:00+00:00",
            }
        ),
        "settlement_semantics": _semantics(),
    }

    evidence = observability_row_to_authority(row)

    assert evidence.live_authority_status == "live"
    assert_live_day0_authority(evidence)


def test_station_mismatch_blocks():
    with pytest.raises(Day0AuthorityError, match="station_match_status"):
        assert_live_day0_authority(_day0(station_match_status="MISMATCH"))


def test_dst_ambiguous_blocks():
    with pytest.raises(Day0AuthorityError, match="dst_status"):
        assert_live_day0_authority(_day0(dst_status="AMBIGUOUS"))


def test_settlement_semantics_only():
    with pytest.raises(Day0AuthorityError, match="SettlementSemantics"):
        assert_live_day0_authority(_day0(raw_value=80.6, rounded_value=80))


def test_missing_raw_observation_provenance_blocks():
    with pytest.raises(Day0AuthorityError, match="raw_payload_sha256"):
        assert_live_day0_authority(_day0(raw_payload_sha256=""))
