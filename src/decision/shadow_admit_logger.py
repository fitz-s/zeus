# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: would-admit shadow logger
#   (team-lead approved (a) step 3, 2026-06-22; live_order_pathology 2026-06-22). STEP-1 of the
#   selection-calibrator build found that NO current-regime (openmeteo_ecmwf_ifs9_bayes_fusion)
#   would-admit population exists — all 62,874 edli_no_submit_receipts are the stale soft-anchor
#   regime (end 06-12). Without that counterfactual population neither the selection-calibrator's
#   full would-admit EB nor a forward-positive city gate can be validated. This logger accrues it:
#   it records every evaluated side-candidate's would-admit decision + features, joined later to
#   settlement, so a real forward-positive policy can be validated once a few hundred labelled rows
#   accrue. THE PATH TO REVENUE is accrual then licensing — not a look-ahead claim today.
"""Would-admit shadow logger — append-only, OFF the live decision path (observability only).

It NEVER reads back into any gate/calibrator and NEVER alters a decision. Flag-gated
(``ZEUS_SHADOW_ADMIT_LOG``) default OFF so wiring it into the admission seam is inert until the
orchestrator turns it on. Writes JSONL (one record per evaluated side-candidate) to
``state/shadow_admit_log.jsonl`` by default. A write failure is SWALLOWED (observability must never
break trading).

admit0 (the historical admission predicate, frozen as the pre-calibrator gate):
    admit0 = native_quote_available AND quote_fresh AND (q_lcb_side_old > own_side_cost)
This is the counterfactual "would the OLD gate have admitted this candidate" label the EB selected
likelihood and the forward-validation harness need.
"""
from __future__ import annotations

import json
import os
from typing import Optional

_DEFAULT_PATH: str = "state/shadow_admit_log.jsonl"
_SHADOW_LOG_ENV: str = "ZEUS_SHADOW_ADMIT_LOG"


def _resolve_path(path: Optional[str]) -> str:
    if path:
        return path
    try:
        from src.config import state_path

        return str(state_path("shadow_admit_log.jsonl"))
    except Exception:  # noqa: BLE001
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(repo, _DEFAULT_PATH)


def shadow_log_enabled() -> bool:
    return os.environ.get(_SHADOW_LOG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def build_shadow_record(
    *,
    decision_time: str,
    city: str,
    target_date: str,
    condition_id: str,
    bin_id: str,
    side: str,
    raw_side_prob: float,
    q_lcb_side: float,
    own_side_cost: float,
    native_quote_available: bool,
    quote_fresh: bool,
    posterior_version: str,
    city_skill_admit: Optional[bool] = None,
    selection_calibrator_q_safe: Optional[float] = None,
) -> dict:
    """Build the would-admit shadow record (the counterfactual the regime currently lacks)."""
    admit0 = bool(native_quote_available) and bool(quote_fresh) and (float(q_lcb_side) > float(own_side_cost))
    return {
        "decision_time": str(decision_time),
        "city": str(city),
        "target_date": str(target_date)[:10],
        "condition_id": str(condition_id),
        "bin_id": str(bin_id),
        "side": "NO" if str(side).upper() == "NO" else "YES",
        "raw_side_prob": float(raw_side_prob),
        "q_lcb_side": float(q_lcb_side),
        "own_side_cost": float(own_side_cost),
        "admission_margin": float(q_lcb_side) - float(own_side_cost),
        "native_quote_available": bool(native_quote_available),
        "quote_fresh": bool(quote_fresh),
        "admit0": admit0,
        "city_skill_admit": city_skill_admit,
        "selection_calibrator_q_safe": selection_calibrator_q_safe,
        "posterior_version": str(posterior_version),
    }


def append_shadow_record(record: dict, *, path: Optional[str] = None) -> None:
    """Append one record as a JSONL line. Raises on a genuine I/O error (callers use maybe_log)."""
    p = _resolve_path(path)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_shadow_log(*, path: Optional[str] = None) -> list[dict]:
    """Read all shadow records. [] when absent/unreadable."""
    p = _resolve_path(path)
    out: list[dict] = []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        return []
    return out


def maybe_log_candidate(*, path: Optional[str] = None, **fields) -> bool:
    """Flag-gated, fail-soft entry point for the live admission seam. DEFAULT OFF -> no-op (returns
    False). When ON, builds + appends the record; ANY error is swallowed (observability must never
    break the decision path). Returns True iff a record was written."""
    if not shadow_log_enabled():
        return False
    try:
        rec = build_shadow_record(**fields)
        append_shadow_record(rec, path=path)
        return True
    except Exception:  # noqa: BLE001 — never break trading for a log write.
        return False
