# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/probability/outcome_space.py" section);
#   docs/rebuild/q_engine_violation_ledger.md Layer 0 (Omega, complete MECE
#   partition, fail-closed). Reuses the live MECE validator
#   src/types/market.py::validate_bin_topology and the Bin type.
"""OutcomeSpace — one complete MECE outcome partition (Omega) before q.

Stage 1 of the q-kernel rebuild. ``OutcomeSpace`` is the single Omega per
(city, target_date, metric). It carries the full settlement partition — including
non-tradeable tail/shoulder bins (``executable=False``) — so q, the band, and FDR
run over the COMPLETE partition, not the fresh-executable subset.

``validate()`` enforces the spec's invariants and fails CLOSED:
  * at least 2 bins (a single universal bin is not a family);
  * EVERY bin carries the SAME ``rounding_rule`` as ``resolution.rounding_rule``
    (the per-city settlement rule threaded from EventResolution — no bin may
    declare a different rounding rule than the family resolves under);
  * the bins form a complete, non-overlapping integer partition (MECE):
    leftmost open-low, rightmost open-high, interior edges contiguous with no
    gap and no overlap.

If the venue family is incomplete (a gap, an overlap, a missing tail), live
eligibility fails closed HERE at the OutcomeSpace — no mass leak, no executable-
subset renormalization, no synthetic "Other" invented in the decision layer.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from src.probability.event_resolution import EventResolution
from src.types.market import Bin, BinTopologyError, validate_bin_topology


class OutcomeSpaceError(ValueError):
    """Raised when an outcome family is not a valid complete MECE partition.

    Fail-closed signal: the family cannot be served for q integration.
    """


@dataclass(frozen=True)
class OutcomeBin:
    """A single outcome bin in the complete partition.

    ``lower_native`` / ``upper_native`` are the bin bounds in the settlement
    unit; ``None`` denotes an open (shoulder) edge ("X or below" / "X or
    higher"). ``rounding_rule`` MUST equal the family resolution's rounding rule
    (validated by ``OutcomeSpace.validate``). ``executable`` is False for
    non-tradeable tail bins that are KEPT in the family so the partition stays
    complete.
    """

    bin_id: str
    condition_id: str
    label: str
    lower_native: float | None
    upper_native: float | None
    yes_token_id: str | None
    no_token_id: str | None
    executable: bool
    rounding_rule: str


@dataclass(frozen=True)
class OutcomeSpace:
    """The complete MECE outcome partition for one event family (Omega)."""

    family_id: str
    resolution: EventResolution
    bins: tuple[OutcomeBin, ...]
    topology_hash: str

    def validate(self) -> None:
        """Enforce the spec invariants; raise ``OutcomeSpaceError`` (fail-closed).

        1. >= 2 bins.
        2. Every bin's ``rounding_rule`` == ``resolution.rounding_rule``.
        3. The bins form a complete, non-overlapping integer partition (MECE),
           validated via the live ``validate_bin_topology``.
        """
        if len(self.bins) < 2:
            raise OutcomeSpaceError(
                f"INCOMPLETE_FAMILY: {len(self.bins)} bin(s); a family needs >= 2"
            )

        rule = self.resolution.rounding_rule
        for b in self.bins:
            if b.rounding_rule != rule:
                raise OutcomeSpaceError(
                    f"ROUNDING_RULE_MISMATCH: bin {b.bin_id!r} declares "
                    f"{b.rounding_rule!r} but resolution is {rule!r}"
                )

        unit = self.resolution.measurement_unit
        try:
            topo_bins = [
                Bin(
                    low=b.lower_native,
                    high=b.upper_native,
                    unit=unit,
                    label=b.label,
                )
                for b in self.bins
            ]
            validate_bin_topology(topo_bins)
        except (BinTopologyError, ValueError) as exc:
            raise OutcomeSpaceError(f"INCOMPLETE_FAMILY: {exc}") from exc


def compute_topology_hash(
    family_id: str,
    resolution: EventResolution,
    bins: Sequence[OutcomeBin],
) -> str:
    """Deterministic identity hash over family id, rounding rule, and bin edges.

    Stable across process runs so a receipt can prove which exact partition q
    was integrated over.
    """
    h = hashlib.sha256()
    h.update(family_id.encode("utf-8"))
    h.update(resolution.rounding_rule.encode("utf-8"))
    h.update(resolution.semantics_version.encode("utf-8"))
    for b in sorted(bins, key=lambda x: (str(x.lower_native), str(x.upper_native), x.bin_id)):
        h.update(
            f"{b.bin_id}|{b.lower_native}|{b.upper_native}|{b.rounding_rule}|{b.executable}".encode(
                "utf-8"
            )
        )
    return h.hexdigest()
