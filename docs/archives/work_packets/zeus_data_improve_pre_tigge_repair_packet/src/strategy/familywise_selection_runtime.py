from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable


@dataclass(slots=True)
class FamilyHypothesis:
    family_id: str
    city: str
    target_date: str
    range_label: str
    direction: str
    p_value: float
    edge: float | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None
    tested: bool = True
    passed_prefilter: bool = False
    rejection_stage: str | None = None
    meta: dict[str, object] = field(default_factory=dict)
    decision_id: str | None = None
    candidate_id: str | None = None
    q_value: float | None = None
    selected_post_fdr: bool = False
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    hypothesis_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(slots=True)
class SelectionFamily:
    cycle_mode: str
    decision_snapshot_id: str | None
    city: str | None
    target_date: str | None
    strategy_key: str | None
    discovery_mode: str | None
    meta: dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    family_id: str = field(default_factory=lambda: str(uuid.uuid4()))



def benjamini_hochberg(hypotheses: Iterable[FamilyHypothesis], alpha: float = 0.10) -> list[FamilyHypothesis]:
    rows = sorted(hypotheses, key=lambda h: (math.inf if h.p_value is None else h.p_value, h.range_label, h.direction))
    tested = [h for h in rows if h.tested and h.p_value is not None]
    m = len(tested)
    if m == 0:
        return rows

    running_q = 1.0
    for rank_rev, h in enumerate(reversed(tested), start=1):
        rank = m - rank_rev + 1
        q = min(running_q, (h.p_value * m) / rank)
        h.q_value = float(min(1.0, q))
        running_q = h.q_value

    threshold_rank = 0
    for rank, h in enumerate(tested, start=1):
        if h.p_value <= alpha * rank / m:
            threshold_rank = rank

    selected_ids = {id(h) for h in tested[:threshold_rank]}
    for h in rows:
        h.selected_post_fdr = id(h) in selected_ids
        if h.q_value is None and h.p_value is not None:
            h.q_value = 1.0
    return rows



def selected_prefiltered(hypotheses: Iterable[FamilyHypothesis]) -> list[FamilyHypothesis]:
    return [h for h in hypotheses if h.selected_post_fdr and h.passed_prefilter]



def write_selection_family(
    conn: sqlite3.Connection,
    family: SelectionFamily,
    hypotheses: Iterable[FamilyHypothesis],
) -> dict[str, int]:
    conn.execute(
        """
        INSERT OR REPLACE INTO selection_family_fact (
            family_id,
            cycle_mode,
            decision_snapshot_id,
            city,
            target_date,
            strategy_key,
            discovery_mode,
            created_at,
            meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            family.family_id,
            family.cycle_mode,
            family.decision_snapshot_id,
            family.city,
            family.target_date,
            family.strategy_key,
            family.discovery_mode,
            family.created_at,
            json.dumps(family.meta, ensure_ascii=False, sort_keys=True),
        ),
    )
    count = 0
    selected = 0
    for h in hypotheses:
        conn.execute(
            """
            INSERT OR REPLACE INTO selection_hypothesis_fact (
                hypothesis_id,
                family_id,
                decision_id,
                candidate_id,
                city,
                target_date,
                range_label,
                direction,
                p_value,
                q_value,
                ci_lower,
                ci_upper,
                edge,
                tested,
                passed_prefilter,
                selected_post_fdr,
                rejection_stage,
                recorded_at,
                meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                h.hypothesis_id,
                family.family_id,
                h.decision_id,
                h.candidate_id,
                h.city,
                h.target_date,
                h.range_label,
                h.direction,
                h.p_value,
                h.q_value,
                h.ci_lower,
                h.ci_upper,
                h.edge,
                1 if h.tested else 0,
                1 if h.passed_prefilter else 0,
                1 if h.selected_post_fdr else 0,
                h.rejection_stage,
                h.recorded_at,
                json.dumps(h.meta, ensure_ascii=False, sort_keys=True),
            ),
        )
        count += 1
        selected += int(h.selected_post_fdr)
    return {"family_rows": 1, "hypothesis_rows": count, "selected_rows": selected}
