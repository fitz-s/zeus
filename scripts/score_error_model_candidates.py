#!/usr/bin/env python3
# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: operator redesign 2026-05-28 — evidence-ledger-backed candidate selection.
#   Principle 1: raw baseline DOMINATES unless a correction proves OOS improvement.
#   Principle 2: a bias correction is a candidate, not an entitlement.
#   Accept rule (operator, verbatim): "Accept candidate only if: candidate beats raw on at
#   least 2 of 3 proper scores AND bootstrap LCB(improvement) > 0 AND no catastrophic cohort
#   regression. If none pass: use raw identity."
"""Candidate model selection — the accept-gate that makes "promote a correction that did not
beat raw OOS" structurally unwritable.

This module is built bottom-up. The SELECTION RULE (`choose_candidate`) is independent of how
the proper scores were computed, so it is pinned first by relationship tests
(`tests/test_t4_selection_rule_invariants.py`). The scoring path — per-bucket candidate
construction {raw, scale-only, prior-bias, live-bias, transported, hierarchical-fallback},
blocked-by-target_date OOS folds, re-MC of each candidate's p_raw distribution via the
PRODUCTION sampler `src.signal.ensemble_signal.p_raw_vector_from_maxes`, proper scoring vs
SETTLEMENT, and the bootstrap LCB of improvement — plugs into this rule and is wired in a
follow-up commit (it depends on the bin-grid source confirmed by the baseline proper-score run).

NOTHING here decides live trading. It emits a `candidate_selection_manifest` only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# All three are "lower is better".
PROPER_SCORES: tuple[str, ...] = ("logloss", "rps", "brier")

# A correction must beat raw on at least this many of the PROPER_SCORES to be eligible.
MIN_PROPER_SCORE_WINS = 2


@dataclass(frozen=True)
class CandidateDecision:
    """Outcome of the accept-gate for one (city, metric, season) bucket."""
    chosen: str
    reason: str
    raw_is_default: bool
    beats_raw_count: dict[str, int] = field(default_factory=dict)
    passing: list[str] = field(default_factory=list)


def _is_real(x) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _beats_raw_count(cand: dict[str, float], raw: dict[str, float]) -> int:
    """Count PROPER_SCORES where candidate strictly improves on raw (lower=better).

    A missing or NaN candidate/raw score for a metric does NOT count as a win (conservative:
    we never credit a correction for a score we could not compute).
    """
    n = 0
    for m in PROPER_SCORES:
        c, r = cand.get(m), raw.get(m)
        if _is_real(c) and _is_real(r) and c < r:
            n += 1
    return n


def choose_candidate(
    candidate_metrics: dict[str, dict[str, float]],
    raw_metrics: dict[str, float],
    improvement_lcb: dict[str, float],
    catastrophic: dict[str, bool],
    *,
    raw_name: str = "raw",
) -> CandidateDecision:
    """Select the model for one bucket. Returns raw_name unless a correction clears the gate.

    Parameters
    ----------
    candidate_metrics : name -> {"logloss":, "rps":, "brier":} on held-out folds. The raw
        identity may be present here too; it is never selected over itself.
    raw_metrics : raw identity's held-out {"logloss":, "rps":, "brier":} (the baseline to beat).
    improvement_lcb : name -> bootstrap lower-confidence-bound of (raw_aggregate -
        candidate_aggregate) across OOS folds. > 0 means even the pessimistic bound shows the
        candidate beating raw out-of-sample.
    catastrophic : name -> True if the candidate catastrophically regresses ANY cohort
        (a hard veto regardless of aggregate wins).

    Gate (ALL required): beats_raw_count >= MIN_PROPER_SCORE_WINS (2/3) AND improvement_lcb > 0
    AND not catastrophic. Among passing candidates, pick the one with the largest
    improvement_lcb (most robust worst-case OOS gain). If none pass: raw identity.
    """
    beats: dict[str, int] = {}
    passing: list[str] = []
    for name, cand in candidate_metrics.items():
        if name == raw_name:
            continue
        b = _beats_raw_count(cand, raw_metrics)
        beats[name] = b
        lcb = improvement_lcb.get(name)
        if (
            b >= MIN_PROPER_SCORE_WINS
            and _is_real(lcb)
            and lcb > 0
            and not catastrophic.get(name, False)
        ):
            passing.append(name)

    if not passing:
        return CandidateDecision(
            chosen=raw_name,
            reason="no candidate cleared the OOS gate (>=2/3 proper-score wins + LCB>0 + no catastrophe); raw identity dominates",
            raw_is_default=True,
            beats_raw_count=beats,
            passing=[],
        )

    chosen = max(passing, key=lambda n: improvement_lcb[n])
    return CandidateDecision(
        chosen=chosen,
        reason=f"beats raw on {beats[chosen]}/3 proper scores, bootstrap LCB(improvement)={improvement_lcb[chosen]:+.4f}>0, no catastrophic cohort regression; selected over {len(passing)} passing candidate(s) by max LCB",
        raw_is_default=False,
        beats_raw_count=beats,
        passing=sorted(passing),
    )
