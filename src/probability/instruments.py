# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/probability/instruments.py" block lines 590-617: the Instrument
#   dataclass 594-599, payoff_vector 601-609 where YES_i = e_i and NO_i = 1 - e_i,
#   and the NO probability/lcb derivation 611-617 — fair_yes_i = q[i],
#   fair_no_i = 1 - q[i], no_lcb_i = np.quantile(1 - band.samples[:, i], alpha))
#   reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; the DRIFT-LEDGER MAJOR row: the NO lower
#   bound is NOT 1 - q_ucb_yes — the live replacement defect at
#   event_reactor_adapter.py:9955 — and it is NOT
#   probability_uncertainty.no_side_samples. NO_i is the payoff vector 1 - e_i: it
#   WINS on every bin EXCEPT i, so NO is a real basket of all the OTHER YES by
#   construction; fair_no_i = 1 - q[i]; no_lcb_i = np.quantile(1 - band.samples[:, i],
#   alpha) computed from the SAME row-normalized JointQBand.samples the point q /
#   band ran over).
#   Live dependencies (ALL already built; imported, never re-implemented):
#     - src/probability/joint_q.py::JointQ            (JointQ.q — the (n_bins,)
#                       normalized mass aligned 1:1 with omega.bins; fair_yes/fair_no
#                       point values read straight off it)
#     - src/probability/joint_q_band.py::JointQBand   (JointQBand.samples — the
#                       (n_draws, n_bins) simplex matrix; the NO lcb is the lower
#                       quantile of 1 - samples[:, i], the joint complement)
#     - src/probability/outcome_space.py::OutcomeSpace / OutcomeBin
#                       (the complete Omega; the bin index is the position of
#                       bin_id within omega.bins — the alignment q / samples use)
"""Instrument — YES / NO as payoff vectors over the complete Omega (Stage 7a).

This is Stage 7a of the q-kernel rebuild (consult_build_spec.md lines 590-617). An
``Instrument`` is one tradeable claim (a YES or a NO on one bin of one event
family). Its economic content is a PAYOFF VECTOR over the COMPLETE Omega — not a UI
label, not a scalar complement of a YES belief.

THE ONE CONTRACT (operator law — make the bad output mathematically impossible, NOT
a downstream gate/cap that catches a bad NO value and leaves the broken transform in
place):

  NO_i is the payoff vector ``1 - e_i`` — the all-ones vector with a single zero at
  bin ``i``. It pays 1 on EVERY bin except ``i``. So a NO on bin ``i`` is, BY
  CONSTRUCTION, a basket of all the OTHER bins' YES: it wins whenever ANY other bin
  settles. Its fair value and its lower credible bound are therefore DIRECT
  consequences of the joint distribution over Omega and Σq = 1, computed from the
  SAME normalized joint samples the YES side uses:

      fair_yes_i = q[i]                                   # the bin's own mass
      fair_no_i  = 1 - q[i]                               # = Σ_{j != i} q[j]
      no_lcb_i   = np.quantile(1 - band.samples[:, i], alpha)

  Because ``samples`` is the (n_draws, n_bins) matrix in which EVERY row already sums
  to 1 (the JointQBand simplex invariant), ``1 - samples[:, i]`` is, per draw,
  EXACTLY the summed mass of all the other bins for that coherent joint draw. Its
  alpha-quantile is the genuine downside of the NO basket — the lower credible bound
  of "some other bin wins". There is ONE source of the NO bound (the joint
  complement of the row-normalized draw matrix), so the two defects this replaces are
  UNCONSTRUCTABLE here:

    * NOT ``1 - q_ucb_yes`` (the live defect at event_reactor_adapter.py:9955). That
      took the YES UPPER bound and flipped it, which double-counts the YES estimation
      error onto the NO side and is NOT the lower quantile of the NO payoff across
      coherent joint draws. Here the NO lcb is read from the SAME draw matrix as a
      per-draw complement, so a NO that is really a basket of nine other winning bins
      can never be priced off a single bin's flipped upper bound.
    * NOT ``probability_uncertainty.no_side_samples`` (a separately-sampled NO
      belief). There is no independent NO sample set — the NO bound is the joint
      complement of the YES draw matrix, so the YES and NO bounds are coherent over
      the SAME Σq = 1 rows by construction (they cannot disagree about how much mass
      is "elsewhere").

  The bad output is impossible not because a validator rejects it, but because the
  ONLY way to obtain the NO bound is the joint complement of the row-normalized
  samples — there is no flipped-ucb path and no second sample set to choose from.

PAYOFF VECTOR (spec lines 601-609): for an instrument on bin ``i = index(bin_id)``,

    YES -> e_i              (1 at i, 0 elsewhere — pays iff bin i settles)
    NO  -> 1 - e_i          (1 everywhere, 0 at i — pays iff any OTHER bin settles)

The vector is length ``len(omega.bins)`` and is aligned 1:1 with ``omega.bins`` —
the SAME alignment ``JointQ.q`` and ``JointQBand.samples`` use, so the dot product
``payoff_vector(omega) @ q`` is exactly the instrument's fair value
(``q[i]`` for YES, ``1 - q[i]`` for NO).

DRIFT RESOLVED (recorded per operator law; see the implementation report):

  The spec writes ``i = omega.index(self.bin_id)`` (instruments.py line 603), but the
  LIVE ``OutcomeSpace`` (src/probability/outcome_space.py) carries NO ``index``
  method — it exposes ``bins`` (a tuple of ``OutcomeBin``, each with ``bin_id``).
  Resolution (toward the live type, per the drift ledger "prefer Actual-live"
  directive): the bin index is the POSITION of ``bin_id`` within ``omega.bins`` —
  the canonical alignment ``JointQ.q`` and ``JointQBand.samples`` are already keyed
  on (``q[i]`` is the mass of ``omega.bins[i]``). A ``bin_id`` not present in the
  partition fails closed with ``InstrumentError`` rather than silently selecting the
  wrong bin.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from src.probability.joint_q import JointQ
from src.probability.joint_q_band import JointQBand
from src.probability.outcome_space import OutcomeSpace


class InstrumentError(ValueError):
    """Raised when an instrument cannot be resolved against an Omega / joint q.

    Fail-closed signal: the instrument's ``bin_id`` is not a member of the complete
    partition the payoff vector / fair value would be taken over, so there is no
    coherent payoff to serve. Refused rather than served against the wrong bin.
    """


def _bin_index(omega: OutcomeSpace, bin_id: str) -> int:
    """The position of ``bin_id`` within ``omega.bins`` — the live ``index``.

    DRIFT RESOLUTION: the spec writes ``omega.index(self.bin_id)`` but the live
    ``OutcomeSpace`` has no ``index`` method. This derives the index from the bin's
    POSITION in ``omega.bins`` — the exact alignment ``JointQ.q`` and
    ``JointQBand.samples`` use (``q[i]`` / ``samples[:, i]`` is the mass of
    ``omega.bins[i]``). Fails closed if the ``bin_id`` is not in the partition.
    """
    for i, b in enumerate(omega.bins):
        if b.bin_id == bin_id:
            return i
    raise InstrumentError(
        f"BIN_NOT_IN_OMEGA: instrument bin_id={bin_id!r} is not a member of the "
        f"complete partition (bins={[b.bin_id for b in omega.bins]!r})"
    )


# ---------------------------------------------------------------------------
# Instrument (spec lines 594-599) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Instrument:
    """One tradeable YES / NO claim on a single bin (spec lines 594-599).

    Field names are verbatim from consult_build_spec.md.

    * ``instrument_id`` — a stable id for the claim.
    * ``bin_id`` — the bin of the complete Omega this claim is about. The instrument's
      payoff is taken over the position of this ``bin_id`` within ``omega.bins``.
    * ``side`` — ``"YES"`` (pays iff bin ``i`` settles) or ``"NO"`` (pays iff ANY
      OTHER bin settles — a basket of all the other YES, by the payoff vector).
    * ``direct_token_id`` — the venue token id of the direct claim, if one exists
      (the bin's ``yes_token_id`` for YES, ``no_token_id`` for NO). ``None`` when the
      claim has no direct venue token (e.g. a NO that must be synthesised from the
      other-bin YES basket). This field carries provenance only; it does NOT change
      the payoff vector, which is always the structural ``e_i`` / ``1 - e_i``.
    """

    instrument_id: str
    bin_id: str
    side: Literal["YES", "NO"]
    direct_token_id: str | None

    def payoff_vector(self, omega: OutcomeSpace) -> np.ndarray:
        """The instrument's payoff over the COMPLETE Omega (spec lines 601-609).

        YES -> ``e_i`` (1 at bin ``i``, 0 elsewhere).
        NO  -> ``1 - e_i`` (1 everywhere, 0 at bin ``i``) — pays on EVERY bin except
        ``i``, so a NO is a basket of all the OTHER bins' YES by construction.

        The vector is length ``len(omega.bins)`` aligned 1:1 with ``omega.bins`` (the
        same alignment ``JointQ.q`` / ``JointQBand.samples`` use), so
        ``payoff_vector(omega) @ q`` is the instrument's fair value.
        """
        e = np.zeros(len(omega.bins))
        i = _bin_index(omega, self.bin_id)
        if self.side == "YES":
            e[i] = 1.0
        else:
            e[:] = 1.0
            e[i] = 0.0
        return e


# ---------------------------------------------------------------------------
# NO probability / lower bound — a DIRECT consequence of the payoff vector and
# Σq = 1 (spec lines 611-617). NOT a special formula, NOT 1 - q_ucb_yes, NOT a
# separately-sampled no_side_samples. The joint complement of the SAME normalized
# joint samples.
# ---------------------------------------------------------------------------

def fair_yes(joint_q: JointQ, bin_id: str) -> float:
    """The fair YES value of bin ``bin_id`` — ``q[i]`` (spec line 615).

    The bin's own normalized joint mass. Reads straight off ``JointQ.q`` (which sums
    to 1 by construction), so it is a genuine probability, never a renormalized
    executable-subset share.
    """
    i = _bin_index(joint_q.omega, bin_id)
    return float(joint_q.q[i])


def fair_no(joint_q: JointQ, bin_id: str) -> float:
    """The fair NO value of bin ``bin_id`` — ``1 - q[i]`` (spec line 616).

    ``1 - q[i] == Σ_{j != i} q[j]`` exactly, because Σq = 1. This is the fair value of
    the NO payoff vector ``1 - e_i``: the total mass on all the OTHER bins, i.e. the
    probability that some bin OTHER than ``i`` settles. It is the basket value of all
    the other YES, computed from the single normalized joint q — never a UI complement
    invented in the decision layer.
    """
    i = _bin_index(joint_q.omega, bin_id)
    return float(1.0 - joint_q.q[i])


def no_lcb(band: JointQBand, bin_id: str, *, alpha: float | None = None) -> float:
    """The NO lower credible bound of bin ``bin_id`` (spec line 617).

        no_lcb_i = np.quantile(1 - band.samples[:, i], alpha)

    The CORRECTED transformation (operator law — make the bad output mathematically
    impossible; no flip-of-a-flipped-bound, no second sample set):

    ``band.samples`` is the (n_draws, n_bins) matrix in which EVERY row sums to 1 (the
    JointQBand simplex invariant). For draw ``k``, ``1 - samples[k, i]`` is EXACTLY the
    summed mass of all the OTHER bins on that coherent joint draw — the per-draw payoff
    of the NO basket ``1 - e_i``. Its ``alpha``-quantile across draws is the genuine
    lower credible bound of "some other bin wins".

    This is NOT ``1 - q_ucb_yes`` (the live defect at event_reactor_adapter.py:9955):
    that flips the YES upper bound, double-counting the YES estimation error onto the
    NO side and is not a lower quantile of the NO payoff. And it is NOT a
    separately-sampled ``no_side_samples``: the NO bound is read from the SAME draw
    matrix as the joint complement, so the YES and NO bounds agree over the identical
    Σq = 1 rows by construction. There is one source of the NO bound, so the broken
    forms are unconstructable.

    ``alpha`` defaults to ``band.alpha`` (the lower-tail probability the band was built
    at), so the NO lcb is taken at the SAME tail as the YES q_lcb the band already
    carries — coherent by default. An explicit ``alpha`` must lie in ``(0, 1)``.
    """
    i = _bin_index(band.joint_q.omega, bin_id)
    a = band.alpha if alpha is None else float(alpha)
    if not (0.0 < a < 1.0):
        raise InstrumentError(
            f"DEGENERATE_ALPHA: alpha={a!r} (need 0 < alpha < 1)"
        )
    # The joint complement of the row-normalized samples: per draw, 1 - samples[:, i]
    # is the total mass on every OTHER bin (Σ row == 1). Its alpha-quantile is the NO
    # basket's lower credible bound — the ONLY source of the NO bound.
    no_samples = 1.0 - band.samples[:, i]
    return float(np.quantile(no_samples, a))
