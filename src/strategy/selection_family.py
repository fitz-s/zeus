"""Family-wise hypothesis selection helpers.

The active evaluator uses this module after the full-family scan. Family scope
is determined by `family_id`: currently one candidate/market/snapshot family
across strategy keys, not one whole-cycle family across all markets.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HypothesisRecord:
    family_id: str
    hypothesis_id: str
    p_value: float
    tested: bool = True
    passed_prefilter: bool = False


def make_family_id(
    *,
    cycle_mode: str,
    city: str,
    target_date: str,
    strategy_key: str,
    discovery_mode: str,
    decision_snapshot_id: str = "",
) -> str:
    parts = [cycle_mode, city, target_date, strategy_key, discovery_mode]
    if decision_snapshot_id:
        parts.append(decision_snapshot_id)
    return "|".join(parts)


def benjamini_hochberg_mask(p_values: list[float], q: float) -> list[bool]:
    """Return discovery mask under Benjamini-Hochberg."""
    n = len(p_values)
    if n == 0:
        return []
    ordered = sorted(enumerate(p_values), key=lambda item: item[1])
    threshold_index = -1
    for rank, (_idx, p_value) in enumerate(ordered, start=1):
        if float(p_value) <= q * rank / n:
            threshold_index = rank - 1
    if threshold_index < 0:
        return [False] * n
    cutoff = float(ordered[threshold_index][1])
    return [float(p_value) <= cutoff for p_value in p_values]


def _bh_q_values(p_values: list[float]) -> list[float]:
    n = len(p_values)
    if n == 0:
        return []
    ordered = sorted(enumerate(p_values), key=lambda item: item[1])
    ranked_q = [0.0] * n
    running = 1.0
    for reverse_rank, (idx, p_value) in enumerate(reversed(ordered), start=1):
        rank = n - reverse_rank + 1
        running = min(running, float(p_value) * n / rank)
        ranked_q[idx] = running
    return ranked_q


def apply_familywise_fdr(rows: list[dict], q: float = 0.10) -> list[dict]:
    """Apply BH independently per `family_id` over all tested hypotheses."""
    out = [dict(row) for row in rows]
    by_family: dict[str, list[int]] = {}
    for idx, row in enumerate(out):
        if not bool(row.get("tested", True)):
            row["q_value"] = None
            row["selected_post_fdr"] = 0
            continue
        by_family.setdefault(str(row["family_id"]), []).append(idx)

    for indices in by_family.values():
        p_values = [float(out[idx]["p_value"]) for idx in indices]
        selected = benjamini_hochberg_mask(p_values, q)
        q_values = _bh_q_values(p_values)
        for local_idx, row_idx in enumerate(indices):
            out[row_idx]["q_value"] = q_values[local_idx]
            out[row_idx]["selected_post_fdr"] = int(selected[local_idx])

    return out
