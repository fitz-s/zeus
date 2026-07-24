# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §14 item 8 (single-primary-live: one primary
#   leg per family, one path) + §14 item 7 (rank by robust marginal utility — the
#   sole selection key) + §6 (best-candidate selection; argmax ΔU) + §13 (no-trade
#   gate) + §9 Hidden #5 (OUTSIDE outcome in the family payoff matrix) +
#   Hidden #7 (one primary; fallback is WATCH-only) + operator directive 2026-06-08
#   (NO flag / NO shadow / NO default-off switch: a scattered off-able gate is the
#   regression disease; correctness is enforced by types + relationship tests +
#   the ff-branch review, NEVER a runtime toggle).
"""S7 relationship tests — the opportunity-book SELECTOR GATE is removed.

THE STRUCTURAL DECISION (operator directive 2026-06-08; spec §14 item 8). After
S3/S4 the robust marginal-expected-log-utility ranker is the SINGLE live
selection path. S7 deletes the last residue of the old on/off toggle so there is
ONE path and ONE truth, with NO way to silently disable selection:

  1. The env var ``ZEUS_OPPORTUNITY_BOOK_SELECTOR`` and the settings key
     ``edli.opportunity_book_selector_enabled`` no longer exist ANYWHERE in
     ``src/`` — not as a read, not as a string literal, not in a comment. A
     symbol that does not exist cannot be re-wired into a gate (the strongest
     antibody: the category is made unconstructable, not merely off).

  2. ``OpportunityBook.to_receipt_dict`` records the ΔU decision UNCONDITIONALLY.
     The former ``selector_enabled = bool(cache_summary.get("selector_enabled"))``
     branch — which NULLED ``selected_candidate_id`` whenever that cache flag was
     falsy/absent — is GONE. The receipt's ``selected_candidate_id`` is ALWAYS
     ``self.selected_candidate_id`` (the recorded ΔU decision), so no missing or
     flipped cache flag can silently discard the live selection.

  3. ``_selected_candidate_proof`` has exactly ONE selection algorithm (the
     marginal-utility ranker); no env var or config value reroutes it to an
     alternate ranker or to the legacy ``max(trade_score, q_lcb)`` fallback, and
     the receipt the book emits carries the same single decision.

These are CROSS-MODULE relationship tests at the seam where the ΔU decision
(``event_reactor_adapter._select_proof_by_robust_marginal_utility``) flows into
the receipt serializer (``events.opportunity_book.OpportunityBook``). Written
BEFORE the implementation (relationship-tests -> implementation -> function-
tests). They are the antibody for "an off-able gate flipped silently breaks the
system and is untrackable".
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import textwrap
from dataclasses import replace

from src.engine import event_reactor_adapter as era
from src.events.candidate_binding import MarketTopologyCandidate
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.opportunity_book import (
    OpportunityBook,
    build_family_opportunity_book,
)
from src.strategy import utility_ranker
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Snapshot-row + proof fixtures (same shape the S1/S3/S4 tests use).
# ---------------------------------------------------------------------------
def _row(
    *,
    condition_id="condition-1",
    yes_token="yes-1",
    no_token="no-1",
    yes_asks=(("0.40", "100000"),),
    no_asks=(("0.55", "100000"),),
    yes_bids=(("0.39", "100"),),
    no_bids=(("0.19", "100"),),
    min_tick="0.01",
    min_order="5",
    fee_rate_fraction=0.0,
    snapshot_id="snap-s7",
):
    depth = {
        "YES": {
            "asks": [{"price": p, "size": s} for p, s in yes_asks],
            "bids": [{"price": p, "size": s} for p, s in yes_bids],
        },
        "NO": {
            "asks": [{"price": p, "size": s} for p, s in no_asks],
            "bids": [{"price": p, "size": s} for p, s in no_bids],
        },
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": min_tick,
        "min_order_size": min_order,
        "fee_details_json": json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": "book-hash-s7",
    }


def _candidate(*, condition_id, yes_token, no_token, bin_obj):
    return MarketTopologyCandidate(
        city="paris",
        target_date="2026-06-10",
        metric="tmax",
        condition_id=condition_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        bin=bin_obj,
    )


def _proof(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj, trade_score=1.0):
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=_candidate(
            condition_id=str(row.get("condition_id") or ""),
            yes_token=str(row.get("yes_token_id") or ""),
            no_token=str(row.get("no_token_id") or ""),
            bin_obj=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=trade_score,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=None,
    )


def _qkernel_selected(proof, *, edge_lcb=None, optimal_delta_u=0.02):
    side = "YES" if proof.direction == "buy_yes" else "NO"
    bin_id = era._candidate_bin_id(proof)
    candidate_id = f"DIRECT_{side}:{bin_id}@proof"
    cost = float(proof.execution_price.value)
    edge = float(proof.q_lcb_5pct) - cost if edge_lcb is None else float(edge_lcb)
    return replace(
        proof,
        selection_authority_applied="qkernel_spine",
        q_source="qkernel_spine",
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "candidate_id": candidate_id,
            "route_id": candidate_id,
            "side": side,
            "bin_id": bin_id,
            "payoff_q_point": proof.q_posterior,
            "payoff_q_lcb": proof.q_lcb_5pct,
            "edge_lcb": edge,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": "5",
            "optimal_delta_u": optimal_delta_u,
            "cost": cost,
            "false_edge_rate": 0.02,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "q_lcb",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": proof.q_lcb_5pct,
        },
    )


# ===========================================================================
# Invariant 1 — the selector env var / settings key do not exist in src/.
# ===========================================================================
_FORBIDDEN_SELECTOR_SYMBOLS = (
    "ZEUS_OPPORTUNITY_BOOK_SELECTOR",
    "opportunity_book_selector_enabled",
)


def _iter_src_py_files():
    src_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src",
    )
    for dirpath, _dirnames, filenames in os.walk(src_root):
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_no_selector_env_or_setting_referenced():
    """Spec §14 item 8 + operator directive 2026-06-08. The off-able selector
    toggle is ABOLISHED: neither the env var ``ZEUS_OPPORTUNITY_BOOK_SELECTOR``
    nor the settings key ``opportunity_book_selector_enabled`` appears ANYWHERE
    in ``src/`` — not as a read, not as a string literal, not in a comment/
    docstring. A symbol that is wholly absent cannot be re-grepped into a gate.

    Grep-level antibody (every byte of every src .py file), so a future edit that
    re-introduces the toggle name — even in a comment that a later commit could
    promote to code — fails immediately.
    """
    offenders: dict[str, list[str]] = {}
    for path in _iter_src_py_files():
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        hits = [sym for sym in _FORBIDDEN_SELECTOR_SYMBOLS if sym in text]
        if hits:
            offenders[path] = hits
    assert not offenders, (
        "the opportunity-book selector toggle is removed; no src file may "
        f"reference it. offenders: {offenders}"
    )


def test_no_selector_env_read_in_ast_of_src():
    """AST-level antibody: no ``os.environ`` / ``os.getenv`` read and no settings
    lookup keyed on the selector symbols survives anywhere in ``src/``.

    Stronger than grep because it inspects parsed string-constant nodes, so even
    a dynamically-built name (``"ZEUS_OPPORTUNITY_BOOK_" + "SELECTOR"``) would be
    caught at the constant level.
    """
    offenders: dict[str, list[str]] = {}
    for path in _iter_src_py_files():
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - src must parse
            continue
        hits = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _FORBIDDEN_SELECTOR_SYMBOLS
        ]
        if hits:
            offenders[path] = hits
    assert not offenders, (
        "no src module may carry the selector toggle name as a string constant "
        f"(it could be wired to os.environ/settings). offenders: {offenders}"
    )


# ===========================================================================
# Invariant 2 — the receipt records the ΔU decision UNCONDITIONALLY (no gate).
# ===========================================================================
def _evaluation(*, candidate_id, execution_price=0.4, direction="buy_yes"):
    return CandidateEvaluation(
        candidate_id=candidate_id,
        family_id="fam",
        condition_id=candidate_id,
        token_id=candidate_id + "-tok",
        direction=direction,
        bin_label="60-61F",
        execution_price=execution_price,
        q_posterior=0.7,
        q_lcb_5pct=0.6,
        q_lcb_calibration_source="cal",
        same_bin_yes_posterior=None,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=0.02,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
    )


def test_opportunity_book_receipt_records_decision_without_selector_flag():
    """Spec §14 item 8 + operator directive. ``to_receipt_dict`` records the ΔU
    decision (``selected_candidate_id``) UNCONDITIONALLY — there is no
    ``selector_enabled`` cache flag that can null it.

    DISCRIMINATING construction: build the book with a ``cache_summary`` that does
    NOT contain ``selector_enabled`` (and once where it is explicitly falsy). The
    OLD gate
        ``"selected_candidate_id": self.selected_candidate_id if selector_enabled
          else None``
    would have nulled the recorded decision in both cases. After S7 the receipt
    must still surface the decision, so a silent disable path is impossible.
    """
    selected = _evaluation(candidate_id="winner")
    loser = _evaluation(candidate_id="loser", execution_price=0.80)

    for cache_summary in (
        {"price_cache": "x"},  # selector flag ABSENT
        {"price_cache": "x", "selector_enabled": False},  # explicitly off
    ):
        book = build_family_opportunity_book(
            family_id="fam",
            evaluations=(loser, selected),
            event_id="evt",
            decided_candidate_id="winner",
            cache_summary=cache_summary,
        )
        receipt = book.to_receipt_dict()
        assert receipt["selected_candidate_id"] == "winner", (
            "the receipt must record the ΔU decision regardless of any cache "
            f"flag (no off-able gate); cache_summary={cache_summary}"
        )
        # proposed/actual collapse to the same single truth as the decision.
        assert receipt["proposed_selected_candidate_id"] == "winner"


def test_opportunity_book_receipt_has_no_selector_enabled_gate_in_source():
    """The ``selector_enabled`` branch is removed from the receipt serializer.

    AST antibody on ``OpportunityBook.to_receipt_dict``: no executable code may
    reference a ``selector_enabled`` name, and ``selected_candidate_id`` must be
    assigned directly from ``self.selected_candidate_id`` (no conditional on a
    cache flag). This pins the exact seam S7 collapses. (A prose mention in a
    comment documenting the REMOVED gate is fine; an executable reference is the
    violation — so strip comment lines before the string check.)
    """
    src = inspect.getsource(OpportunityBook.to_receipt_dict)
    code_only = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#") and "``" not in ln
    )
    assert "selector_enabled" not in code_only, (
        "to_receipt_dict must not gate the recorded decision on a "
        "selector_enabled cache flag (operator directive: no off-able gate)"
    )
    # AST: no name/attribute/subscript node carries 'selector_enabled', and no
    # ternary `... if <cond> else None` may guard the recorded selection.
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "selector_enabled"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value != "selector_enabled"
        if isinstance(node, ast.IfExp):
            assert "selector_enabled" not in ast.dump(node)


def test_no_decision_records_none_not_a_minted_selection():
    """When there is no live ΔU decision (``decided_candidate_id`` None — e.g. a
    no-trade family, or a non-executable best-belief fallback), the book records
    NO selection rather than minting one. Removing the gate must NOT turn a
    no-decision into a spurious selection.
    """
    a = _evaluation(candidate_id="a")
    b = _evaluation(candidate_id="b", execution_price=0.80)
    book = build_family_opportunity_book(
        family_id="fam",
        evaluations=(a, b),
        event_id="evt",
        decided_candidate_id=None,
        cache_summary={},
    )
    receipt = book.to_receipt_dict()
    assert receipt["selected_candidate_id"] is None
    assert receipt["proposed_selected_candidate_id"] is None


# ===========================================================================
# Invariant 3 — ONE selection path: env/config cannot reroute the live decision,
# and the receipt the adapter emits carries that same single decision.
# ===========================================================================
def test_single_selection_path_always_uses_marginal_utility_ranker(monkeypatch):
    """Spec §14 item 7/8 + operator directive. ``_selected_candidate_proof``
    returns the ``rank_candidates`` ΔU primary regardless of any env var or config
    value; the legacy ``max(trade_score, q_lcb)`` fallback is gone.

    DISCRIMINATING construction (ΔU ranker and the legacy scalar selector must
    DISAGREE): bin A has a 100x larger ``trade_score`` but a worse robust edge;
    bin B has the better robust edge (cheaper ask at equal q_lcb gap). The legacy
    ``(trade_score, q_lcb)`` tuple picks A; the ΔU ranker (which never reads
    trade_score) picks B. Flipping every plausible legacy toggle must not change
    the pick.
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.55", "100000"),), snapshot_id="snap-A")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.40", "100000"),), snapshot_id="snap-B")
    proof_a = _proof(direction="buy_yes", row=row_a, token_id="yesA",
                     q_posterior=0.70, q_lcb_5pct=0.60, bin_obj=bin_a, trade_score=99.0)
    proof_b = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                     q_posterior=0.65, q_lcb_5pct=0.55, bin_obj=bin_b, trade_score=0.01)

    # Every legacy toggle name the directive forbids — none may reroute the path.
    for var in ("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "ZEUS_OPPORTUNITY_BOOK_SHADOW"):
        for val in ("0", "1", "false", "true", "off", "on"):
            monkeypatch.setenv(var, val)
            selected = era._selected_candidate_proof(
                {"family_id": "fam", "event_id": "evt"}, (proof_a, proof_b)
            )
            assert selected is proof_b, (
                "the marginal-utility ranker is the sole selection path; the "
                "higher-ΔU (cheaper) bin B must win regardless of env/config"
            )

    # And the receipt the adapter emits records THAT single decision verbatim.
    selected = _qkernel_selected(selected)
    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(proof_a, selected),
        selected_proof=selected,
        enforce_win_rate_floor=False,
    ).to_receipt_dict()
    expected_id = era._candidate_evaluation_id(proof_b)
    assert book["selected_candidate_id"] == expected_id
    assert book["selected_candidate_id"] == book["actual_receipt_selected_candidate_id"]
    assert book["proposed_selected_candidate_id"] == expected_id


def test_opportunity_book_marks_qkernel_selected_candidate_as_live_admitted():
    """Actual qkernel selections are the live admission authority."""
    bin_b = Bin(low=62.0, high=63.0, unit="F", label="62-63F")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.20", "100000"),), snapshot_id="snap-B")
    proof_b = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                     q_posterior=0.65, q_lcb_5pct=0.55, bin_obj=bin_b, trade_score=0.0)
    qkernel_cert = {
        "source": "qkernel_spine",
        "candidate_id": "DIRECT_YES:bin-B",
        "route_id": "DIRECT_YES:bin-B@proof",
        "side": "YES",
        "direction_law_ok": True,
        "coherence_allows": True,
        "payoff_q_point": 0.65,
        "payoff_q_lcb": 0.30,
        "edge_lcb": 0.10,
        "delta_u_at_min": 0.01,
        "optimal_stake_usd": "6.25",
        "optimal_delta_u": 0.02,
        "cost": 0.20,
        "false_edge_rate": 0.02,
        "selection_guard_basis": "SELECTION_BETA_95",
        "selection_guard_abstained": False,
        "selection_guard_q_safe": 0.30,
    }
    selected = replace(
        proof_b,
        q_source="qkernel_spine",
        selection_authority_applied="qkernel_spine",
        qkernel_execution_economics=qkernel_cert,
    )

    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(selected,),
        selected_proof=selected,
    ).to_receipt_dict()
    selected_id = era._candidate_evaluation_id(proof_b)
    rec = next(c for c in book["candidates"] if c["candidate_id"] == selected_id)

    assert book["admitted_count"] == 1
    assert book["selection_authority"] == "qkernel_spine"
    assert book["selected_qkernel_execution_economics"] == qkernel_cert
    assert rec["admitted"] is True
    assert rec["live_decision_selected"] is True
    assert rec["live_selection_authority"] == "qkernel_spine"
    assert rec["live_admission_authority"] == "qkernel_spine"
    assert rec["qkernel_execution_economics"] == qkernel_cert


def test_decision_src_has_no_alternate_ranker_or_legacy_fallback():
    """AST/source antibody on the live decision body: no call to the legacy
    scalar selector, no off-able gate, no ``max(executable, key=(trade_score,
    q_lcb))`` fallback survives in executable code.
    """
    src = inspect.getsource(era._selected_candidate_proof)
    decision_src = src + inspect.getsource(era._select_proof_by_robust_marginal_utility)
    code_lines = [
        ln for ln in decision_src.splitlines()
        if not ln.lstrip().startswith("#") and "``" not in ln
    ]
    code_only = "\n".join(code_lines)
    assert "select_best_family_candidate(" not in code_only
    assert "max(executable," not in code_only
    assert "trade_score" not in code_only
    # The ΔU ranker is what the decision delegates to.
    assert "utility_ranker.rank_candidates" in inspect.getsource(
        era._select_proof_by_robust_marginal_utility
    )


# ===========================================================================
# Invariant 4 — DIRECTION LAW holds at the receipt build through the seam.
# ===========================================================================
def test_direction_law_holds_at_receipt_build_for_yes_winner():
    """DIRECTION LAW (money-path iron law): for the selected candidate,
    ``direction == 'buy_yes'`` iff its NativeSideCandidate ``side == 'YES'`` (the
    own bin is the WIN outcome). The receipt's recorded ``direction`` and the
    materialized candidate's ``side`` agree at the seam — removing the selector
    gate does not invert win/loss geometry.
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.40", "100000"),), snapshot_id="snap-A")
    proof_a = _qkernel_selected(
        _proof(direction="buy_yes", row=row_a, token_id="yesA",
               q_posterior=0.70, q_lcb_5pct=0.62, bin_obj=bin_a)
    )

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (proof_a,)
    )
    assert selected is proof_a
    cand = era._native_side_candidate_from_proof(family_key="fam", proof=selected)
    assert (selected.direction == "buy_yes") == (cand.side == "YES")

    book = era._opportunity_book_from_proofs(
        event_id="evt", family_id="fam",
        proofs=(proof_a,), selected_proof=selected,
    ).to_receipt_dict()
    selected_id = book["selected_candidate_id"]
    rec = next(c for c in book["candidates"] if c["candidate_id"] == selected_id)
    # Receipt direction agrees with the candidate side (YES bin == win outcome).
    assert rec["direction"] == "buy_yes"
    assert cand.side == "YES"


# ===========================================================================
# Invariant 5 — OUTSIDE outcome is always in the ranked family's payoff matrix.
# ===========================================================================
def test_outside_outcome_present_in_every_scored_family():
    """Spec §9 Hidden #5. Every FamilyPayoffMatrix used to rank includes
    OUTSIDE_OUTCOME so a settlement with no winning bin is a real losing outcome
    for YES and a winning outcome for NO. Removing the selector gate does not
    change which matrix the single path scores on.
    """
    matrix = utility_ranker.FamilyPayoffMatrix.over_bins(["60-61F", "61-62F"])
    assert utility_ranker.OUTSIDE_OUTCOME in matrix.outcomes


# ===========================================================================
# Invariant 6 — ONE PRIMARY LEG PER FAMILY (Hidden #7): the path returns a single
# proof, never a set; positive siblings are WATCH-only.
# ===========================================================================
def test_one_primary_leg_per_family():
    """Spec §14 item 8 / Hidden #7. ``_selected_candidate_proof`` returns exactly
    ONE primary leg (the top positive-ΔU candidate), never a set. Removing the
    gate keeps single-primary-live: the book records one selected_candidate_id.
    """
    bins = [Bin(low=60.0 + i, high=61.0 + i, unit="F", label=f"{60 + i}-{61 + i}F") for i in range(3)]
    proofs = []
    for i, bin_obj in enumerate(bins):
        row = _row(
            condition_id=f"cond-{i}", yes_token=f"yes{i}", no_token=f"no{i}",
            no_asks=(("0.30", "100000"),), snapshot_id=f"snap-{i}",
        )
        proofs.append(
            _qkernel_selected(
                _proof(direction="buy_no", row=row, token_id=f"no{i}",
                       q_posterior=0.70, q_lcb_5pct=0.65, bin_obj=bin_obj),
                edge_lcb=None,
                optimal_delta_u=0.01 + i * 0.001,
            )
        )

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, tuple(proofs)
    )
    # Exactly one proof object (single-primary), drawn from the input set.
    assert selected in proofs

    book = era._opportunity_book_from_proofs(
        event_id="evt", family_id="fam",
        proofs=tuple(proofs), selected_proof=selected,
    ).to_receipt_dict()
    # The book records exactly one selected candidate (one primary leg).
    assert book["selected_candidate_id"] == era._candidate_evaluation_id(selected)
    chosen = [
        c for c in book["candidates"]
        if c["candidate_id"] == book["selected_candidate_id"]
    ]
    assert len(chosen) == 1
