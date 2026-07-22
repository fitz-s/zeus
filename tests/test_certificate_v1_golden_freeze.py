# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: Golden-vector freeze on v1 decision-certificate serialization. A certificate's
#   payload_hash/certificate_hash/certificate_id and a NO_SUBMIT receipt's receipt_hash are
#   cryptographic commitments used downstream for settlement skill-attribution, EDLI redeem
#   proofs, and restart-preflight parent-chain verification. Changing the preimage bytes for
#   an already-written v1 row destroys its identity. This test pins concrete, real production
#   output as golden constants so any change to canonicalization (src/decision_kernel/
#   canonicalization.py), certificate assembly (src/decision_kernel/certificate.py), ledger
#   storage (src/decision_kernel/ledger.py), or no-submit receipt serialization
#   (src/events/no_submit_receipts.py) that would silently alter a v1 preimage fails loudly
#   here first. It does NOT reimplement serialization -- every hash below is produced by
#   calling the real production functions.
# Reuse: Re-run before landing any of: E1 (zlib_b64 -> BLOB column relocation), E4
#   (ActionableTradeCertificate opportunity_book summarization / certificate v2 envelope),
#   or any canonical_json/normalize change. A failure here means a v1 row's identity moved.
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/
#   implementation/certificate_v1_freeze.md (hash inventory + golden-vector harness spec);
#   .../certificate_e1e4_safety_trace.md (E1/E4 safety verdicts this freeze protects).
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import (
    CertificateHeader,
    ParentEdge,
    build_certificate,
    certificate_hash_for,
    certificate_payload_json,
)
from src.decision_kernel.ledger import DecisionCertificateLedger, _dt
from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger, _receipt_json
from src.events.reactor import EventSubmissionReceipt
from src.state.schema.edli_no_submit_receipts_schema import ensure_table as ensure_no_submit_receipts_table

GOLDEN_TS = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _cert(certificate_type: str, semantic_key: str, payload: dict, parents=(), *, mode: str = "NO_SUBMIT"):
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode=mode,
        decision_time=GOLDEN_TS,
        source_available_at=GOLDEN_TS,
        agent_received_at=GOLDEN_TS,
        persisted_at=GOLDEN_TS,
        payload=payload,
        parent_edges=tuple(parents),
        authority_id="golden-freeze",
        authority_version="v1",
        algorithm_id="golden-freeze",
        algorithm_version="v1",
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# CERT A -- trivial single-field payload, no parents. Smallest possible v1
# preimage: proves canonical_json's separators/sort_keys/ensure_ascii output
# and the payload_hash -> certificate_hash chain byte-for-byte.
# ---------------------------------------------------------------------------

GOLDEN_A_PAYLOAD_JSON = '{"mode":"NO_SUBMIT","note":"golden-freeze-a"}'
GOLDEN_A_PAYLOAD_HASH = "b4b6b4bf86c4b6ca4ce8708dd18dd8819cbb6cf4f14d2a81a390fe5279a71082"
GOLDEN_A_CERTIFICATE_HASH = "66f94842bf949708cc5a2b4461bc2df047e4b52ed7f0df7843bb2683e0f1fb03"
GOLDEN_A_CERTIFICATE_ID = "ClockModeCertificate:66f94842bf949708cc5a2b44"


def _cert_a():
    return _cert("ClockModeCertificate", "clock:golden-a", {"mode": "NO_SUBMIT", "note": "golden-freeze-a"})


def test_trivial_certificate_payload_json_is_pinned_and_stable():
    """payload_json/payload_hash/certificate_hash/certificate_id must equal the frozen
    v1 constants exactly. A mismatch means canonical_json or certificate_hash_for changed
    output for an unchanged input -- every v1 row's payload_hash/certificate_hash on disk
    is now unverifiable from stored payload_json."""
    cert = _cert_a()

    assert certificate_payload_json(cert) == GOLDEN_A_PAYLOAD_JSON
    assert cert.header.payload_hash == GOLDEN_A_PAYLOAD_HASH
    assert cert.header.certificate_hash == GOLDEN_A_CERTIFICATE_HASH
    assert cert.header.certificate_id == GOLDEN_A_CERTIFICATE_ID


def test_trivial_certificate_serialization_is_byte_stable_on_repeat():
    """Two independent build_certificate calls on identical inputs must produce identical
    bytes -- serialization must be a pure function of the payload/header fields, never of
    call order, object identity, or wall-clock time."""
    first = _cert_a()
    second = _cert_a()

    assert certificate_payload_json(first) == certificate_payload_json(second)
    assert first.header.payload_hash == second.header.payload_hash
    assert first.header.certificate_hash == second.header.certificate_hash
    assert first.header.certificate_id == second.header.certificate_id


# ---------------------------------------------------------------------------
# CERT B -- three parent edges. certificate_hash_for hashes header.parent_edges
# as an ordered tuple (canonicalization.py normalize() turns tuples into lists,
# which json.dumps serializes positionally) -- so edge ORDER is load-bearing,
# not just edge membership. This is the parent-chain verification surface
# restart-preflight and verifier.py:409-420 depend on.
# ---------------------------------------------------------------------------

GOLDEN_B_PAYLOAD_JSON = '{"family_id":"family-golden-b","note":"golden-freeze-b"}'
GOLDEN_B_PAYLOAD_HASH = "5e9f0fe87dd4fd58e4b5f217e3f5dcf5ee51b0990d674e071c781322a3dc25aa"
GOLDEN_B_CERTIFICATE_HASH = "67697d16c40541012afe2597ce32ecb4ec98f3500f5a6a0fb15aedf19521afaa"
GOLDEN_B_CERTIFICATE_ID = "ExecutionCommandCertificate:67697d16c40541012afe2597"
GOLDEN_B_CERTIFICATE_HASH_EDGES_REVERSED = "3685659cc24b772c9ee5fe2bde96c2084903010d597f59f8d8a4a41c0f22e95b"


def _cert_b_parents():
    parent_x = _cert("ForecastAuthorityCertificate", "forecast:golden-b", {"snapshot_id": "snap-golden-b"})
    parent_y = _cert("CalibrationCertificate", "cal:golden-b", {"calibrator_model_key": "model-golden-b"})
    parent_z = _cert("BeliefCertificate", "belief:golden-b", {"q": 0.42})
    return parent_x, parent_y, parent_z


def _cert_b(edges):
    return _cert(
        "ExecutionCommandCertificate",
        "exec-command:golden-b",
        {"family_id": "family-golden-b", "note": "golden-freeze-b"},
        edges,
    )


def test_multi_parent_certificate_hash_is_pinned_and_edge_order_sensitive():
    """The forward-ordered 3-parent-edge certificate must equal the frozen golden hash, and
    reversing edge order (same 3 parents, same roles, different tuple order) must produce a
    DIFFERENT certificate_hash. If this ever becomes order-insensitive, two certificates with
    swapped parent roles would collide -- exactly the corruption test_certificate_hashing.py's
    test_parent_role_swap_changes_hash also guards, pinned here as frozen v1 bytes."""
    parent_x, parent_y, parent_z = _cert_b_parents()
    edges_forward = (
        ParentEdge("forecast_authority", parent_x.certificate_hash, parent_x.certificate_type),
        ParentEdge("calibration", parent_y.certificate_hash, parent_y.certificate_type),
        ParentEdge("belief", parent_z.certificate_hash, parent_z.certificate_type),
    )
    cert_forward = _cert_b(edges_forward)

    assert certificate_payload_json(cert_forward) == GOLDEN_B_PAYLOAD_JSON
    assert cert_forward.header.payload_hash == GOLDEN_B_PAYLOAD_HASH
    assert cert_forward.header.certificate_hash == GOLDEN_B_CERTIFICATE_HASH
    assert cert_forward.header.certificate_id == GOLDEN_B_CERTIFICATE_ID

    cert_reversed = _cert_b(tuple(reversed(edges_forward)))
    assert cert_reversed.header.certificate_hash == GOLDEN_B_CERTIFICATE_HASH_EDGES_REVERSED
    assert cert_reversed.header.certificate_hash != cert_forward.header.certificate_hash


def test_certificate_ledger_round_trip_reproduces_stored_hash_and_edge_order():
    """Exercises the REAL storage path (DecisionCertificateLedger.insert_idempotent with the
    actual ledger._dt timestamp encoding and _persist_edges rowid-ordered edge table) rather
    than re-deriving stored form by hand. Two subtleties this must get right or the harness
    itself produces false failures (design doc certificate_v1_freeze.md section 3):
      1. decision_time is stored via ledger._dt as "...+00:00" but hashed by
         canonicalization.normalize() as "...Z" -- reconstruction must re-parse the stored
         string back into a datetime object, never hash the raw column string.
      2. parent_edges are order-sensitive and live in a separate table -- reconstruction must
         rebuild them ordered by rowid (insertion order == header.parent_edges tuple order,
         since ledger._persist_edges iterates the tuple in order).
    Also exercises DecisionCertificateLedger._audit_existing_payload_hash for real via a
    second idempotent insert -- this is the exact check production runs on every retry."""
    parent_x, parent_y, parent_z = _cert_b_parents()
    edges_forward = (
        ParentEdge("forecast_authority", parent_x.certificate_hash, parent_x.certificate_type),
        ParentEdge("calibration", parent_y.certificate_hash, parent_y.certificate_type),
        ParentEdge("belief", parent_z.certificate_hash, parent_z.certificate_type),
    )
    cert = _cert_b(edges_forward)
    conn = _conn()
    ledger = DecisionCertificateLedger(conn)
    for parent in (parent_x, parent_y, parent_z):
        ledger.insert_idempotent(parent, preverified=True)
    ledger.insert_idempotent(cert, preverified=True)

    row = conn.execute(
        """
        SELECT certificate_type, schema_version, canonicalization_version, semantic_key,
               claim_type, mode, decision_time, source_available_at, agent_received_at,
               persisted_at, authority_id, authority_version, algorithm_id, algorithm_version,
               config_hash, model_version_hash, payload_json, payload_hash, certificate_hash
        FROM decision_certificates WHERE certificate_id = ?
        """,
        (cert.certificate_id,),
    ).fetchone()
    assert row["payload_json"] == GOLDEN_B_PAYLOAD_JSON
    assert row["payload_hash"] == GOLDEN_B_PAYLOAD_HASH
    assert row["certificate_hash"] == GOLDEN_B_CERTIFICATE_HASH
    # mirrors DecisionCertificateLedger._audit_existing_payload_hash exactly
    assert stable_hash(json.loads(row["payload_json"])) == row["payload_hash"]

    edge_rows = conn.execute(
        """
        SELECT parent_role, parent_certificate_hash, parent_certificate_type, required
        FROM decision_certificate_edges WHERE child_certificate_id = ? ORDER BY rowid
        """,
        (cert.certificate_id,),
    ).fetchall()
    reconstructed_edges = tuple(
        ParentEdge(r["parent_role"], r["parent_certificate_hash"], r["parent_certificate_type"], bool(r["required"]))
        for r in edge_rows
    )
    reconstructed_header = CertificateHeader(
        certificate_id=cert.certificate_id,
        certificate_type=row["certificate_type"],
        schema_version=row["schema_version"],
        canonicalization_version=row["canonicalization_version"],
        semantic_key=row["semantic_key"],
        claim_type=row["claim_type"],
        mode=row["mode"],
        decision_time=datetime.fromisoformat(row["decision_time"]),
        source_available_at=datetime.fromisoformat(row["source_available_at"]) if row["source_available_at"] else None,
        agent_received_at=datetime.fromisoformat(row["agent_received_at"]) if row["agent_received_at"] else None,
        persisted_at=datetime.fromisoformat(row["persisted_at"]) if row["persisted_at"] else None,
        max_parent_source_available_at=None,
        max_parent_agent_received_at=None,
        max_parent_persisted_at=None,
        parent_edges=reconstructed_edges,
        authority_id=row["authority_id"],
        authority_version=row["authority_version"],
        algorithm_id=row["algorithm_id"],
        algorithm_version=row["algorithm_version"],
        config_hash=row["config_hash"],
        model_version_hash=row["model_version_hash"],
        payload_hash=row["payload_hash"],
        certificate_hash="",
        verifier_status="VERIFIED",
    )
    assert certificate_hash_for(reconstructed_header) == GOLDEN_B_CERTIFICATE_HASH

    # real _audit_existing_payload_hash path -- must not raise, must return the same id
    assert ledger.insert_idempotent(cert, preverified=True) == cert.certificate_id


def test_stored_timestamp_format_requires_datetime_reparse_not_raw_string():
    """Documents and proves the subtlety test_certificate_ledger_round_trip... relies on:
    ledger._dt stores "+00:00" but canonicalization.normalize() hashes datetimes as "Z".
    Hashing the raw stored column string (instead of re-parsing to a datetime first) silently
    produces a DIFFERENT hash than the one the certificate was minted with -- a naive
    replay/restart-preflight tool that string-compares stored timestamps instead of
    re-parsing them would falsely reject every v1 certificate."""
    stored = _dt(GOLDEN_TS)
    assert stored == "2026-07-20T12:00:00+00:00"

    naive_string_hash = stable_hash({"decision_time": stored})
    correct_datetime_hash = stable_hash({"decision_time": datetime.fromisoformat(stored)})
    assert naive_string_hash != correct_datetime_hash


# ---------------------------------------------------------------------------
# CERT C -- ActionableTradeCertificate carrying opportunity_book (the E4 hazard).
# opportunity_book is diagnostic-only (no verifier in src/decision_kernel/ reads
# it -- certificate_e1e4_safety_trace.md TRACE 2) but payload_hash = stable_hash
# of the ENTIRE payload dict, so it is hash-bound anyway. This is the concrete
# proof that summarizing/relocating opportunity_book in place -- without a v2
# envelope that excludes it from the identity preimage -- corrupts
# payload_hash -> certificate_hash -> certificate_id for every affected row.
# ---------------------------------------------------------------------------

GOLDEN_C_PAYLOAD_HASH = "6db8b56222a1de79fff78ed02fc75b60490b16a52694ffff0e142dc3bb37c91c"
GOLDEN_C_CERTIFICATE_HASH = "04f5c1781f76a6069b38b7e8e40c90ff357ccfc296d7cb9c4ee506273232d1db"
GOLDEN_C_CERTIFICATE_ID = "ActionableTradeCertificate:04f5c1781f76a6069b38b7e8"


def _opportunity_book(rejection_reason: str = "EDGE_BELOW_FLOOR") -> dict:
    """Representative shape of the diagnostic-only rejected-candidate book carried on
    ActionableTradeCertificate.payload["opportunity_book"] (production rows run ~92KB;
    this fixture keeps the same field shape at 3 candidates)."""
    return {
        "book_id": "book:event-1:family-1",
        "event_id": "event-1",
        "family_id": "family-1",
        "candidate_ids": ("family-1:yes-1", "family-1:no-1", "family-1:yes-2"),
        "selected_candidate_id": "family-1:yes-1",
        "candidates": [
            {
                "candidate_id": "family-1:yes-1",
                "condition_id": "condition-1",
                "token_id": "yes-1",
                "direction": "buy_yes",
                "selected": True,
                "q_live": 0.7,
                "cost": 0.4,
                "edge_lcb": 0.2,
                "rejection_reason": None,
            },
            {
                "candidate_id": "family-1:no-1",
                "condition_id": "condition-1",
                "token_id": "no-1",
                "direction": "buy_no",
                "selected": False,
                "q_live": 0.3,
                "cost": 0.35,
                "edge_lcb": -0.05,
                "rejection_reason": rejection_reason,
            },
            {
                "candidate_id": "family-1:yes-2",
                "condition_id": "condition-2",
                "token_id": "yes-2",
                "direction": "buy_yes",
                "selected": False,
                "q_live": 0.55,
                "cost": 0.5,
                "edge_lcb": 0.05,
                "rejection_reason": "BELOW_QUALITY_FLOOR",
            },
        ],
    }


def _actionable_payload_with_book(rejection_reason: str = "EDGE_BELOW_FLOOR", *, book: bool = True) -> dict:
    payload = {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "family_id": "family-1",
        "candidate_id": "family-1:yes-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "trade_score": 0.2,
        "action_score": 0.2,
        "native_quote_available": True,
    }
    payload["opportunity_book"] = _opportunity_book(rejection_reason) if book else None
    return payload


def _cert_c(rejection_reason: str = "EDGE_BELOW_FLOOR", *, book: bool = True):
    return _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:golden-c",
        _actionable_payload_with_book(rejection_reason, book=book),
        mode="LIVE",
    )


def test_actionable_trade_opportunity_book_payload_hash_is_pinned():
    """payload_hash/certificate_hash/certificate_id for the ActionableTradeCertificate
    carrying opportunity_book must equal the frozen v1 constants."""
    cert = _cert_c()

    assert cert.header.payload_hash == GOLDEN_C_PAYLOAD_HASH
    assert cert.header.certificate_hash == GOLDEN_C_CERTIFICATE_HASH
    assert cert.header.certificate_id == GOLDEN_C_CERTIFICATE_ID


def test_actionable_trade_opportunity_book_mutation_breaks_certificate_identity():
    """The E4 antibody. Changing ONE field (rejection_reason) on ONE rejected, non-selected
    candidate deep inside opportunity_book -- a field verifier.py never reads
    (certificate_e1e4_safety_trace.md TRACE 2: zero `opportunity_book` hits in
    src/decision_kernel/verifier.py) -- must still change payload_hash, certificate_hash,
    AND certificate_id. This is exactly why in-place opportunity_book summarization is
    UNSAFE (ledger.py:210-228 raises DECISION_CERTIFICATE_PAYLOAD_HASH_CORRUPT on the
    already-written row, and re-minting the hash instead raises CertificateSemanticDriftError
    / dangles parent-edge FKs) and must go through a certificate v2 envelope that excludes
    rejected-candidate diagnostics from the identity preimage instead."""
    baseline = _cert_c()
    mutated = _cert_c(rejection_reason="DIFFERENT_REASON")

    assert mutated.header.payload_hash != baseline.header.payload_hash
    assert mutated.header.certificate_hash != baseline.header.certificate_hash
    assert mutated.header.certificate_id != baseline.header.certificate_id

    dropped = _cert_c(book=False)
    assert dropped.header.payload_hash != baseline.header.payload_hash


# ---------------------------------------------------------------------------
# Second, independent E4 identity: the SAME opportunity_book content is also
# bound into edli_no_submit_receipts.receipt_hash (no_submit_receipts.py:48,
# 281-282, 355 -- popped from the hashed payload only when None). Freezing
# only certificate_hash would miss this: an in-place opportunity_book
# summarization corrupts BOTH identities independently.
# ---------------------------------------------------------------------------

GOLDEN_RECEIPT_ID = "edli_no_submit:c464527692dceffeb77f9ace758a7bb154bd345a9808951d84946b8b58bbaf56"
GOLDEN_RECEIPT_JSON = (
    '{"belief_payload":null,"bin_label":null,"c_cost_95pct":null,"c_fee_adjusted":null,'
    '"candidate_bin_id":null,"candidate_id":null,"causal_snapshot_id":null,"city":null,'
    '"condition_id":null,"direction":null,"event_id":"event-1",'
    '"executable_snapshot_id":"exec-snap-1","execution_mode_intent":null,'
    '"family_complete":null,"family_id":null,"fdr_family_id":null,"fdr_hypothesis_count":0,'
    '"fdr_pass":false,"fill_up_lease_payload":null,"final_intent_id":"intent-1",'
    '"global_actuation":null,"kelly_cost_basis_id":null,"kelly_decision_id":null,'
    '"kelly_execution_price_type":null,"kelly_pass":false,"kelly_price_fee_deducted":false,'
    '"kelly_size_usd":0.0,"maker_limit_price":null,"metric":null,'
    '"native_quote_available":null,"neg_risk":false,'
    '"opportunity_book":{"book_id":"book:event-1:family-1",'
    '"candidate_ids":["family-1:yes-1","family-1:no-1","family-1:yes-2"],'
    '"candidates":[{"candidate_id":"family-1:yes-1","condition_id":"condition-1",'
    '"cost":0.4,"direction":"buy_yes","edge_lcb":0.2,"q_live":0.7,'
    '"rejection_reason":null,"selected":true,"token_id":"yes-1"},'
    '{"candidate_id":"family-1:no-1","condition_id":"condition-1","cost":0.35,'
    '"direction":"buy_no","edge_lcb":-0.05,"q_live":0.3,'
    '"rejection_reason":"EDGE_BELOW_FLOOR","selected":false,"token_id":"no-1"},'
    '{"candidate_id":"family-1:yes-2","condition_id":"condition-2","cost":0.5,'
    '"direction":"buy_yes","edge_lcb":0.05,"q_live":0.55,'
    '"rejection_reason":"BELOW_QUALITY_FLOOR","selected":false,"token_id":"yes-2"}],'
    '"event_id":"event-1","family_id":"family-1",'
    '"selected_candidate_id":"family-1:yes-1"},'
    '"outcome_label":null,"p_fill_lcb":null,"prepared_global_family":null,'
    '"proof_accepted":true,"q_lcb_5pct":null,"q_lcb_calibration_source":null,'
    '"q_live":null,"reason":"","rest_escalation_deadline_minutes":null,'
    '"rest_then_cross_policy":null,"risk_decision_id":null,'
    '"selection_authority_applied":null,"shift_bin_lease_payload":null,'
    '"side_effect_status":"NO_SUBMIT","source_status":null,"strategy_key":null,'
    '"submitted":false,"target_date":null,"token_id":null,"trade_score":null,'
    '"trade_score_positive":false,"unit":null,"venue_ack_received":false,'
    '"venue_call_started":false,"venue_command_id":null,"venue_command_state":null,'
    '"venue_order_type":null}'
)
GOLDEN_RECEIPT_HASH = "1725a65b3e3e4d870c194d478197c0c5865780cb7ef9e4098d32a3d2614b3fea"
GOLDEN_RECEIPT_HASH_MUTATED = "9fd564036c41ed1019623c59ade47a192a9c2cb8b310fa4d58fde6f8b4114010"


def _no_submit_receipt(rejection_reason: str = "EDGE_BELOW_FLOOR") -> EventSubmissionReceipt:
    return EventSubmissionReceipt(
        submitted=False,
        event_id="event-1",
        side_effect_status="NO_SUBMIT",
        proof_accepted=True,
        final_intent_id="intent-1",
        executable_snapshot_id="exec-snap-1",
        opportunity_book=_opportunity_book(rejection_reason),
    )


def test_no_submit_receipt_hash_binds_full_opportunity_book_and_is_pinned():
    """Exercises the real EdliNoSubmitReceiptLedger storage path (same shape of proof as the
    certificate ledger test above): _receipt_json's omit-when-None field pruning plus
    hashlib.sha256 must reproduce the frozen receipt_json bytes and receipt_hash exactly."""
    conn = _conn()
    ensure_no_submit_receipts_table(conn)
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _no_submit_receipt()

    receipt_id = ledger.insert_idempotent(receipt, decision_time=GOLDEN_TS)
    assert receipt_id == GOLDEN_RECEIPT_ID

    row = conn.execute(
        "SELECT receipt_json, receipt_hash FROM edli_no_submit_receipts WHERE receipt_id = ?",
        (receipt_id,),
    ).fetchone()
    assert row["receipt_json"] == GOLDEN_RECEIPT_JSON
    assert row["receipt_hash"] == GOLDEN_RECEIPT_HASH
    # byte-exact + round-trip, mirroring what a from-storage recompute must reproduce
    assert hashlib.sha256(row["receipt_json"].encode("utf-8")).hexdigest() == row["receipt_hash"]

    # real EdliReceiptHashDriftError guard path -- idempotent retry must not raise
    assert ledger.insert_idempotent(receipt, decision_time=GOLDEN_TS) == receipt_id


def test_no_submit_receipt_hash_mutation_proves_second_independent_e4_identity():
    """The same opportunity_book candidate-field mutation used against certificate_hash above
    also changes receipt_hash -- a SEPARATE hash system with a separate preimage
    (no_submit_receipts.py's _receipt_json, not canonicalization.stable_hash). Freezing only
    the certificate side would miss this: E4 must be fixed for both identities, which is
    exactly why the safety trace requires a v2 envelope rather than an in-place patch."""
    baseline_json = _receipt_json(_no_submit_receipt())
    mutated_json = _receipt_json(_no_submit_receipt(rejection_reason="DIFFERENT_REASON"))
    baseline_hash = hashlib.sha256(baseline_json.encode("utf-8")).hexdigest()
    mutated_hash = hashlib.sha256(mutated_json.encode("utf-8")).hexdigest()

    assert baseline_hash == GOLDEN_RECEIPT_HASH
    assert mutated_hash == GOLDEN_RECEIPT_HASH_MUTATED
    assert mutated_hash != baseline_hash
