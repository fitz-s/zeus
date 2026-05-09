# Created: 2026-04-23
# Last reused/audited: 2026-05-08
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T4.1b DecisionEvidence entry-event emission, D4 Option E); Wave31 D4 hard gate

"""T4.1b DecisionEvidence entry-event emission antibodies.

These tests pin the wire-format contract between the evaluator accept path
(src/engine/evaluator.py EdgeDecision(should_trade=True, ..., decision_evidence=...))
and the canonical position_events.payload_json sidecar:

- ENTRY_ORDER_POSTED payloads carry ``decision_evidence_envelope`` = verbatim
  output of ``DecisionEvidence.to_json()`` (string, not nested dict).
- POSITION_OPEN_INTENT and ENTRY_ORDER_FILLED payloads do NOT carry either
  sidecar key (scope boundary per T4.0 Option E design).
- Legacy backfill from src/execution/exit_lifecycle.py emits
  ``decision_evidence_reason="backfill_legacy_position"`` so the Wave31
  exit-side hard gate and post-hoc investigation can distinguish
  missing-because-legacy from missing-because-bug.
- build_entry_fill_only_canonical_write (fill_tracker path) never emits
  either key — fill-only detection runs after the decision frame has
  released, so there is no evidence to attach.
- Default call (no evidence, no reason) preserves the pre-slice payload
  key set byte-identically.

Read-side pattern demonstrated (T4.2/Wave31 readiness):
``json_extract(payload_json, '$.decision_evidence_envelope')`` returns the
envelope JSON string directly, ready for ``DecisionEvidence.from_json``
consumption — no dict-re-serialization step.
"""

from __future__ import annotations

import json

from src.contracts.decision_evidence import (
    DECISION_EVIDENCE_CONTRACT_VERSION,
    DecisionEvidence,
)
from src.engine.lifecycle_events import (
    _entry_event_payload,
    build_entry_canonical_write,
    build_entry_fill_only_canonical_write,
)
from src.state.portfolio import Position


def _runtime_position(
    *,
    state: str = "pending_tracked",
    exit_state: str = "",
    chain_state: str = "local_only",
) -> Position:
    return Position(
        trade_id="rt-pos-1",
        market_id="mkt-1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-03",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.5,
        p_posterior=0.6,
        edge=0.1,
        shares=20.0,
        cost_basis_usd=10.0,
        entered_at="2026-04-03T00:05:00Z" if state != "pending_tracked" else "",
        day0_entered_at="2026-04-03T00:06:00Z" if state == "day0_window" else "",
        decision_snapshot_id="snap-1",
        entry_method="ens_member_counting",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="update_reaction",
        state=state,
        order_id="ord-1",
        order_status="filled" if state != "pending_tracked" else "pending",
        order_posted_at="2026-04-03T00:00:00Z",
        chain_state=chain_state,
        exit_state=exit_state,
    )


def _entry_evidence() -> DecisionEvidence:
    return DecisionEvidence(
        evidence_type="entry",
        statistical_method="bootstrap_ci_bh_fdr",
        sample_size=5000,
        confidence_level=0.10,
        fdr_corrected=True,
        consecutive_confirmations=1,
    )


class TestEntryEventPayloadEnvelope:
    """ENTRY_ORDER_POSTED attaches envelope; siblings don't."""

    def test_accept_path_posts_envelope_on_entry_order_posted(self):
        evidence = _entry_evidence()
        events, _ = build_entry_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            decision_id="dec-42",
            source_module="src.engine.cycle_runtime",
            decision_evidence=evidence,
        )
        posted = [e for e in events if e["event_type"] == "ENTRY_ORDER_POSTED"][0]
        payload = json.loads(posted["payload_json"])
        assert "decision_evidence_envelope" in payload
        envelope = json.loads(payload["decision_evidence_envelope"])
        assert envelope["contract_version"] == DECISION_EVIDENCE_CONTRACT_VERSION
        assert envelope["fields"]["evidence_type"] == "entry"
        assert envelope["fields"]["sample_size"] == 5000
        assert envelope["fields"]["statistical_method"] == "bootstrap_ci_bh_fdr"
        assert envelope["fields"]["fdr_corrected"] is True

    def test_position_open_intent_does_not_carry_envelope(self):
        evidence = _entry_evidence()
        events, _ = build_entry_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            decision_id="dec-42",
            source_module="src.engine.cycle_runtime",
            decision_evidence=evidence,
        )
        intent = [e for e in events if e["event_type"] == "POSITION_OPEN_INTENT"][0]
        payload = json.loads(intent["payload_json"])
        assert "decision_evidence_envelope" not in payload
        assert "decision_evidence_reason" not in payload

    def test_entry_order_filled_does_not_carry_envelope(self):
        evidence = _entry_evidence()
        events, _ = build_entry_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            decision_id="dec-42",
            source_module="src.engine.cycle_runtime",
            decision_evidence=evidence,
        )
        filled = [e for e in events if e["event_type"] == "ENTRY_ORDER_FILLED"][0]
        payload = json.loads(filled["payload_json"])
        assert "decision_evidence_envelope" not in payload
        assert "decision_evidence_reason" not in payload

    def test_rejection_path_emits_no_envelope(self):
        """No evidence passed (rejection path / test fixture) → no sidecar key."""
        events, _ = build_entry_canonical_write(
            _runtime_position(state="pending_tracked"),
            decision_id="dec-reject",
            source_module="src.engine.cycle_runtime",
        )
        for event in events:
            payload = json.loads(event["payload_json"])
            assert "decision_evidence_envelope" not in payload
            assert "decision_evidence_reason" not in payload


class TestRoundTripRehydration:
    """Envelope round-trips through DecisionEvidence.from_json intact."""

    def test_envelope_rehydrates_to_equivalent_evidence(self):
        original = _entry_evidence()
        events, _ = build_entry_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            decision_id="dec-round-trip",
            source_module="src.engine.cycle_runtime",
            decision_evidence=original,
        )
        posted = [e for e in events if e["event_type"] == "ENTRY_ORDER_POSTED"][0]
        payload = json.loads(posted["payload_json"])
        rehydrated = DecisionEvidence.from_json(payload["decision_evidence_envelope"])
        assert rehydrated == original

    def test_read_side_pattern_simulates_json_extract(self):
        """Exit-side T4.2/Wave31 retrieval pattern in SQL:
          json_extract(payload_json, '$.decision_evidence_envelope')
          → DecisionEvidence.from_json(raw_string)

        Python analog: the stored value under the sidecar key is the JSON
        string verbatim from ``to_json()`` — NOT a re-serialized dict. This
        is the key invariant of Q2's string-value storage choice: no
        dict-to-string re-wrapping step required on the read path.
        """
        original = _entry_evidence()
        events, _ = build_entry_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            decision_id="dec-sql",
            source_module="src.engine.cycle_runtime",
            decision_evidence=original,
        )
        posted = [e for e in events if e["event_type"] == "ENTRY_ORDER_POSTED"][0]
        payload_json_str = posted["payload_json"]
        # Simulate SQL json_extract(payload_json, '$.decision_evidence_envelope')
        envelope_string = json.loads(payload_json_str)["decision_evidence_envelope"]
        assert isinstance(envelope_string, str)
        rehydrated = DecisionEvidence.from_json(envelope_string)
        assert rehydrated == original


class TestIdempotency:
    """Same decision_id + same evidence → byte-identical payloads across runs."""

    def test_same_decision_id_emits_byte_identical_posted_payload(self):
        evidence = _entry_evidence()
        runs = [
            build_entry_canonical_write(
                _runtime_position(state="entered", chain_state="unknown"),
                decision_id="dec-same",
                source_module="src.engine.cycle_runtime",
                decision_evidence=evidence,
            )
            for _ in range(2)
        ]
        posted = [
            [e for e in events if e["event_type"] == "ENTRY_ORDER_POSTED"][0]
            for events, _ in runs
        ]
        assert posted[0]["payload_json"] == posted[1]["payload_json"]


class TestBackfillReasonSentinel:
    """Legacy backfill emits decision_evidence_reason (not envelope)."""

    def test_backfill_emits_reason_sentinel_on_entry_order_posted(self):
        events, _ = build_entry_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            source_module="src.execution.exit_lifecycle:backfill",
            decision_evidence_reason="backfill_legacy_position",
        )
        posted = [e for e in events if e["event_type"] == "ENTRY_ORDER_POSTED"][0]
        payload = json.loads(posted["payload_json"])
        assert payload["decision_evidence_reason"] == "backfill_legacy_position"
        assert "decision_evidence_envelope" not in payload

    def test_backfill_reason_does_not_leak_to_sibling_events(self):
        events, _ = build_entry_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            source_module="src.execution.exit_lifecycle:backfill",
            decision_evidence_reason="backfill_legacy_position",
        )
        for event in events:
            if event["event_type"] != "ENTRY_ORDER_POSTED":
                payload = json.loads(event["payload_json"])
                assert "decision_evidence_reason" not in payload
                assert "decision_evidence_envelope" not in payload


class TestFillOnlyBuilderScopeBoundary:
    """build_entry_fill_only_canonical_write (fill_tracker path) never
    carries either sidecar key — enforces Q1 scope boundary."""

    def test_fill_only_builder_emits_no_decision_evidence_keys(self):
        events, _ = build_entry_fill_only_canonical_write(
            _runtime_position(state="entered", chain_state="unknown"),
            sequence_no=10,
            decision_id="dec-fill-only",
            source_module="src.execution.fill_tracker",
        )
        for event in events:
            payload = json.loads(event["payload_json"])
            assert "decision_evidence_envelope" not in payload
            assert "decision_evidence_reason" not in payload


class TestPayloadBackwardCompatibility:
    """Default call (no evidence, no reason) preserves pre-slice key set."""

    _EXPECTED_KEYS = frozenset({
        "city",
        "target_date",
        "bin_label",
        "direction",
        "unit",
        "size_usd",
        "shares",
        "entry_price",
        "order_status",
        "chain_state",
        "entry_method",
        "phase_after",
    })

    def test_default_call_preserves_pre_slice_key_set(self):
        events, _ = build_entry_canonical_write(
            _runtime_position(state="pending_tracked"),
            decision_id="dec-compat",
            source_module="src.engine.cycle_runtime",
        )
        for event in events:
            payload = json.loads(event["payload_json"])
            assert frozenset(payload.keys()) == self._EXPECTED_KEYS


class TestEntryEventPayloadUnitAccess:
    """Direct _entry_event_payload call — helper-layer unit coverage."""

    def test_payload_with_both_envelope_and_reason_keys(self):
        evidence = _entry_evidence()
        raw = _entry_event_payload(
            _runtime_position(state="entered"),
            phase_after="pending_entry",
            decision_evidence=evidence,
            decision_evidence_reason="diagnostic_dual_mode",
        )
        payload = json.loads(raw)
        assert "decision_evidence_envelope" in payload
        assert payload["decision_evidence_reason"] == "diagnostic_dual_mode"
        # envelope is a verbatim to_json() string
        rehydrated = DecisionEvidence.from_json(payload["decision_evidence_envelope"])
        assert rehydrated == evidence
