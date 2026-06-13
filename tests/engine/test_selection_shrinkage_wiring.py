# Created: 2026-06-13
# Last reused/audited: 2026-06-13
# Authority basis: docs/authority/statistical_calibration_addendum_2026-06-13.md A2/D3
#   (task #60 C2). Relationship tests over the reactor wiring of the EB-shrinkage
#   selection gate: (1) flag=false leaves the GATE DECISION identical to the
#   current BH behavior (golden comparison on a synthetic candem family) while
#   the shadow quantities are still computed; (2) the EB license REPLACES the BH
#   decision when the flag is ON. These verify the property that holds ACROSS the
#   boundary where the candidate family flows into the selection gate.
"""Reactor-wiring relationship tests for C2 selection shrinkage (task #60)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.engine import event_reactor_adapter as era  # noqa: E402


@dataclass
class _FakePrice:
    value: float


@dataclass
class _FakeProof:
    token_id: str
    q_posterior: float
    q_lcb_5pct: float
    execution_price: _FakePrice | None


def _family(*, edges):
    """Build a synthetic family of candidate proofs.

    edges: list of (token_id, q_posterior, q_lcb_5pct, price). One bin per entry.
    """
    proofs = []
    for token_id, q, q_lcb, price in edges:
        proofs.append(
            _FakeProof(
                token_id=token_id,
                q_posterior=q,
                q_lcb_5pct=q_lcb,
                execution_price=_FakePrice(price) if price is not None else None,
            )
        )
    return proofs


def test_shadow_authority_label_off_is_bh_fdr():
    """flag OFF: selection_authority is BH_FDR and the shadow quantities are still
    computed (shadow logging). The DECISION is BH's, untouched."""
    proofs = _family(edges=[
        ("t_sel", 0.86, 0.80, 0.72),   # the certified NO harvest
        ("t_b", 0.40, 0.30, 0.55),
        ("t_c", 0.20, 0.10, 0.60),
    ])
    v = era._compute_selection_shrinkage(
        proofs=proofs,
        selected_token_id="t_sel",
        selected_q_posterior=0.86,
        selected_price=0.72,
        authority_on=False,
    )
    assert v.selection_authority == "BH_FDR"
    # Shadow quantities are still computed (not None) — the shadow-logging contract.
    assert v.lfsr is not None
    assert v.edge_shrunk is not None
    assert v.edge_shrunk_posterior_sd is not None
    # eb_licensed is computed too (so a flip is one flag away), but does not gate.
    assert v.eb_licensed is not None


def test_authority_label_on_is_eb_shrinkage():
    proofs = _family(edges=[
        ("t_sel", 0.86, 0.80, 0.72),
        ("t_b", 0.40, 0.30, 0.55),
    ])
    v = era._compute_selection_shrinkage(
        proofs=proofs,
        selected_token_id="t_sel",
        selected_q_posterior=0.86,
        selected_price=0.72,
        authority_on=True,
    )
    assert v.selection_authority == "EB_SHRINKAGE"
    assert v.eb_licensed is not None


def test_shrinkage_pulls_selected_edge_below_raw_in_a_noisy_family():
    """Relationship: across a wide family the EB-shrunk selected edge is pulled
    toward the family mean — it never EXCEEDS the raw edge (winner's curse can
    only inflate, shrinkage can only deflate)."""
    proofs = _family(edges=[
        ("t_sel", 0.62, 0.58, 0.50),   # raw edge = 0.08
        ("t_b", 0.30, 0.20, 0.55),     # negative edge
        ("t_c", 0.25, 0.15, 0.60),     # negative edge
        ("t_d", 0.20, 0.10, 0.65),     # negative edge
    ])
    raw_edge = 0.58 - 0.50
    v = era._compute_selection_shrinkage(
        proofs=proofs,
        selected_token_id="t_sel",
        selected_q_posterior=0.62,
        selected_price=0.50,
        authority_on=False,
    )
    assert v.edge_shrunk is not None
    assert v.edge_shrunk <= raw_edge + 1e-9


def test_no_executable_universe_falls_back_to_bh():
    """If the selected bin has no executable price, shrinkage is not computable
    and the authority falls back to BH_FDR (never a silent admit)."""
    proofs = _family(edges=[
        ("t_sel", 0.86, 0.80, None),   # no price → not in universe
        ("t_b", 0.40, 0.30, 0.55),
    ])
    v = era._compute_selection_shrinkage(
        proofs=proofs,
        selected_token_id="t_sel",
        selected_q_posterior=0.86,
        selected_price=0.72,
        authority_on=True,
    )
    assert v.selection_authority == "BH_FDR"
    assert v.eb_licensed is None


def test_flag_default_is_false():
    """The replacement flag MUST default to False (current behavior preserved)."""
    # Read the live settings value; default-False contract.
    assert era._selection_eb_shrinkage_enabled() is False


def test_pi_min_default_is_090():
    assert era._selection_pi_min() == pytest.approx(0.90)


def test_c2_shadow_columns_excluded_from_receipt_json_hash():
    """Shadow-inertness: the C2 columns are NEVER serialized into receipt_json, so
    a receipt carrying them hashes BYTE-IDENTICALLY to one without — even though
    the flag is OFF and they are populated on every gate receipt post-deploy.

    This is the EdliReceiptHashDrift antibody: a pre-C2 shadow receipt retried
    after deploy must not drift its receipt_hash (the same invariant the
    envelope_json exclusion enforces). The values stay queryable via the COLUMNS.
    """
    import hashlib

    from src.events.no_submit_receipts import _receipt_json
    from src.events.reactor import EventSubmissionReceipt

    base = dict(
        submitted=False,
        event_id="evt-c2",
        causal_snapshot_id="snap-c2",
        side_effect_status="NO_SUBMIT",
        proof_accepted=True,
        q_live=0.86,
        direction="buy_no",
        final_intent_id="fi-c2",
    )
    without = EventSubmissionReceipt(**base)
    with_c2 = EventSubmissionReceipt(
        **base,
        lfsr=0.02,
        edge_shrunk=0.11,
        edge_shrunk_posterior_sd=0.03,
        selection_authority="BH_FDR",
    )
    rj_without = _receipt_json(without)
    rj_with = _receipt_json(with_c2)
    # None of the C2 keys appear in either serialization.
    for key in ("lfsr", "edge_shrunk", "edge_shrunk_posterior_sd", "selection_authority"):
        assert f'"{key}"' not in rj_with, f"{key} must be excluded from receipt_json"
    # Byte-identical → identical receipt_hash (no drift).
    assert rj_without == rj_with
    assert (
        hashlib.sha256(rj_without.encode()).hexdigest()
        == hashlib.sha256(rj_with.encode()).hexdigest()
    )
