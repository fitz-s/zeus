# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/activation/UNLOCK_CRITERIA.md — operator-runnable evidence factory for ZEUS_ENTRY_FORECAST_{ROLLOUT_GATE,READINESS_WRITER,HEALTHCHECK_BLOCKERS} flag flips.
"""Produce activation-evidence artifacts for the operator's flag flip.

Each function below dry-runs ONE Phase-C flag's daemon path against a
synthetic candidate / in-memory DB and emits an artifact under
``<out_dir>/`` that the unlock-criteria audit trail consumes.

The functions are deliberately import-friendly so the unit test in
``tests/test_produce_activation_evidence.py`` can drive them directly
without a subprocess. The CLI at the bottom is a thin wrapper.

Why three separate producers and not one daemon-cycle replay:
- The daemon's evaluator path needs producer-readiness rows, ensemble
  snapshots, source-run coverage, etc. that the operator does not
  always have populated on a dev box. This script bypasses those by
  running each gate / writer in isolation against a clean DB.
- Each artifact answers ONE question: "if I flip flag X today, what
  state will the daemon land in?" Aggregating into a single replay
  would obscure per-flag readiness.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import (
    City,
    EntryForecastRolloutMode,
    entry_forecast_config,
)
from src.control import entry_forecast_promotion_evidence_io as evidence_io
from src.control.entry_forecast_promotion_evidence_io import (
    PromotionEvidenceCorruption,
    clear_evidence_read_cache,
    read_promotion_evidence,
)
from src.control.entry_forecast_rollout import evaluate_entry_forecast_rollout_gate
from src.data.entry_readiness_writer import ENTRY_FORECAST_STRATEGY_KEY
from src.engine import evaluator as evaluator_module
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX

UTC = timezone.utc

C1_FLAG = "ZEUS_ENTRY_FORECAST_ROLLOUT_GATE"
C3_FLAG = "ZEUS_ENTRY_FORECAST_READINESS_WRITER"
C4_FLAG = "ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS"


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _sample_city() -> City:
    return City(
        name="London",
        lat=51.4775,
        lon=-0.4614,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="London",
        wu_station="EGLL",
    )


def _live_cfg():
    return replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)


def _new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


# -------------------------------------------------------------------- #
# C3: writer dry-run
# -------------------------------------------------------------------- #


def produce_c3_writer_evidence(
    *,
    out_dir: Path,
    promotion_evidence_path: Path,
    as_of: datetime,
) -> dict[str, Any]:
    """Dry-run the entry-readiness writer. Returns a verdict dict and
    writes ``<out_dir>/<date>_c3_writer.sql`` containing the
    ``readiness_state`` row the writer produced.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / f"{_stamp(as_of)}_c3_writer.sql"

    # Redirect the evidence reader to the operator-supplied path so we
    # never accidentally read the prod state file from a dev shell.
    original_path = evidence_io.DEFAULT_PROMOTION_EVIDENCE_PATH
    evidence_io.DEFAULT_PROMOTION_EVIDENCE_PATH = promotion_evidence_path
    clear_evidence_read_cache()
    try:
        conn = _new_conn()
        evaluator_module._write_entry_readiness_for_candidate(
            conn,
            cfg=_live_cfg(),
            city=_sample_city(),
            target_local_date=as_of.date() + (date(2026, 5, 8) - date(2026, 5, 4)),
            temperature_metric=HIGH_LOCALDAY_MAX,
            market_family="POLY_TEMP_LONDON",
            condition_id="condition-evidence-probe",
            decision_time=as_of,
        )
        rows = conn.execute(
            "SELECT status, reason_codes_json, market_family, condition_id "
            "FROM readiness_state WHERE strategy_key = ?",
            (ENTRY_FORECAST_STRATEGY_KEY,),
        ).fetchall()
    finally:
        evidence_io.DEFAULT_PROMOTION_EVIDENCE_PATH = original_path
        clear_evidence_read_cache()

    if not rows:
        verdict = {
            "flag": C3_FLAG,
            "rows_written": 0,
            "row_status": None,
            "row_reason_codes": [],
            "ready_to_flip": False,
            "rationale": "writer wrote no rows; producer scope/condition mismatch.",
            "artifact_path": str(artifact_path),
            "as_of": as_of.isoformat(),
        }
        artifact_path.write_text(_render_c3_artifact(verdict, rows=[]))
        return verdict

    row = rows[0]
    reasons = json.loads(row["reason_codes_json"]) if row["reason_codes_json"] else []
    status = row["status"]

    if status == "BLOCKED" and "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in reasons:
        rationale = (
            "writer fail-closed as expected: no evidence file ⇒ BLOCKED row "
            "with EVIDENCE_MISSING. Reader will surface the typed blocker "
            "rather than silently miss the row."
        )
        ready = True
    elif status == "LIVE_ELIGIBLE":
        rationale = (
            "all gates aligned: complete evidence + LIVE rollout + approved "
            "calibration ⇒ writer lands LIVE_ELIGIBLE row. Live entry-forecast "
            "submission becomes possible after this flip."
        )
        ready = True
    else:
        rationale = f"writer landed status={status} with reasons={reasons}; review required."
        ready = False

    verdict = {
        "flag": C3_FLAG,
        "rows_written": len(rows),
        "row_status": status,
        "row_reason_codes": reasons,
        "ready_to_flip": ready,
        "rationale": rationale,
        "artifact_path": str(artifact_path),
        "as_of": as_of.isoformat(),
    }
    artifact_path.write_text(_render_c3_artifact(verdict, rows=rows))
    return verdict


def _render_c3_artifact(verdict: dict[str, Any], *, rows) -> str:
    lines = [
        f"-- Phase C-3 writer dry-run — {verdict['as_of']}",
        f"-- flag={verdict['flag']}",
        f"-- ready_to_flip={verdict['ready_to_flip']}",
        f"-- rationale={verdict['rationale']}",
        "",
        "-- columns: strategy_key | status | market_family | condition_id | reason_codes_json",
    ]
    for row in rows:
        lines.append(
            f"{ENTRY_FORECAST_STRATEGY_KEY} | "
            f"{row['status']} | {row['market_family']} | "
            f"{row['condition_id']} | {row['reason_codes_json']}"
        )
    if not rows:
        lines.append("-- (no rows produced)")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- #
# C1: rollout-gate dry-run
# -------------------------------------------------------------------- #


def produce_c1_rollout_gate_evidence(
    *,
    out_dir: Path,
    promotion_evidence_path: Path,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Dry-run the rollout gate. Returns a verdict dict and writes
    ``<out_dir>/<date>_c1_rollout_gate.txt`` with the gate's reason
    codes for the supplied evidence.
    """

    if as_of is None:
        as_of = datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / f"{_stamp(as_of)}_c1_rollout_gate.txt"

    blocker_code: str | None
    evidence_present: bool
    rationale: str

    try:
        evidence = read_promotion_evidence(path=promotion_evidence_path)
    except PromotionEvidenceCorruption as exc:
        blocker_code = f"ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:{exc}"
        evidence_present = promotion_evidence_path.exists()
        ready = False
        rationale = (
            "evidence file present but failed strict parsing; operator must "
            "fix or rewrite before flipping."
        )
        decision_payload = {"reason_codes": [blocker_code]}
    else:
        decision = evaluate_entry_forecast_rollout_gate(
            config=_live_cfg(), evidence=evidence
        )
        evidence_present = evidence is not None
        if decision.may_submit_live_orders:
            blocker_code = None
            ready = True
            rationale = (
                "gate would PASS with current evidence; flipping the flag "
                "delegates the rollout-blocker check to the typed evidence "
                "gate. Combined with flag 2 ON, live submission becomes "
                "possible (still subject to producer-readiness)."
            )
        else:
            blocker_code = decision.reason_codes[0] if decision.reason_codes else "ENTRY_FORECAST_ROLLOUT_GATE_BLOCKED"
            ready = True  # fail-closed is the EXPECTED first-flip state
            rationale = (
                f"gate fail-closes with reason {blocker_code!r}; this is the "
                "expected first-flip behavior — flag is safe to flip while "
                "the operator continues populating evidence."
            )
        decision_payload = {
            "reason_codes": list(decision.reason_codes),
            "status": decision.status,
            "may_submit_live_orders": decision.may_submit_live_orders,
        }

    verdict = {
        "flag": C1_FLAG,
        "blocker_code": blocker_code,
        "evidence_present": evidence_present,
        "ready_to_flip": ready,
        "rationale": rationale,
        "artifact_path": str(artifact_path),
        "as_of": as_of.isoformat(),
    }

    artifact_path.write_text(_render_c1_artifact(verdict, decision=decision_payload))
    return verdict


def _render_c1_artifact(verdict: dict[str, Any], *, decision: dict[str, Any]) -> str:
    return (
        f"# Phase C-1 rollout-gate dry-run — {verdict['as_of']}\n"
        f"flag={verdict['flag']}\n"
        f"evidence_present={verdict['evidence_present']}\n"
        f"blocker_code={verdict['blocker_code']!r}\n"
        f"ready_to_flip={verdict['ready_to_flip']}\n"
        f"rationale={verdict['rationale']}\n"
        f"decision={json.dumps(decision, sort_keys=True)}\n"
    )


# -------------------------------------------------------------------- #
# C4: healthcheck flag-toggle diff
# -------------------------------------------------------------------- #


def produce_c4_healthcheck_evidence(
    *,
    out_dir: Path,
    check_fn: Callable[[], dict[str, Any]] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Diff ``healthcheck.check()`` output with flag OFF vs flag ON.

    ``check_fn`` lets the unit test inject a synthetic healthcheck
    payload. Production callers leave it None and the script imports
    ``scripts.healthcheck.check`` lazily.
    """

    if as_of is None:
        as_of = datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / f"{_stamp(as_of)}_c4_healthcheck_diff.txt"

    if check_fn is None:
        from scripts.healthcheck import check as _check

        check_fn = _check

    raw = check_fn()
    blockers = list(raw.get("entry_forecast_blockers") or [])

    base_predicate = (
        bool(raw.get("daemon_alive"))
        and bool(raw.get("status_fresh"))
        and bool(raw.get("status_contract_valid"))
        and bool(raw.get("riskguard_alive"))
        and bool(raw.get("riskguard_fresh"))
        and bool(raw.get("riskguard_contract_valid"))
        and bool(raw.get("assumptions_valid"))
        and not bool(raw.get("cycle_failed"))
        and raw.get("infrastructure_level") != "RED"
    )

    healthy_when_off = base_predicate
    healthy_when_on = base_predicate and not blockers

    if healthy_when_off == healthy_when_on:
        ready = False
        rationale = (
            "no diff between flag OFF and flag ON; entry_forecast_blockers "
            "is empty, so the flip is currently a no-op. Operator should "
            "wait until at least one blocker has been observed (typically "
            "after flags 1+2 have been ON for a cycle) before flipping."
        )
    else:
        ready = True
        rationale = (
            "flag toggle would correctly surface entry_forecast_blockers "
            "in the healthy predicate; ready to flip after flags 1+2 are "
            "ON and stable per runbook order."
        )

    verdict = {
        "flag": C4_FLAG,
        "healthy_when_off": healthy_when_off,
        "healthy_when_on": healthy_when_on,
        "blockers_seen": blockers,
        "ready_to_flip": ready,
        "rationale": rationale,
        "artifact_path": str(artifact_path),
        "as_of": as_of.isoformat(),
    }

    artifact_path.write_text(
        f"# Phase C-4 healthcheck dry-run — {verdict['as_of']}\n"
        f"flag={C4_FLAG}\n"
        f"blockers_seen={blockers}\n"
        f"healthy_when_off={healthy_when_off}\n"
        f"healthy_when_on={healthy_when_on}\n"
        f"ready_to_flip={ready}\n"
        f"rationale={rationale}\n"
    )
    return verdict


# -------------------------------------------------------------------- #
# Aggregator
# -------------------------------------------------------------------- #


def produce_all(
    *,
    out_dir: Path,
    promotion_evidence_path: Path,
    check_fn: Callable[[], dict[str, Any]] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Run all three producers and write a markdown summary."""

    if as_of is None:
        as_of = datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)

    c1 = produce_c1_rollout_gate_evidence(
        out_dir=out_dir,
        promotion_evidence_path=promotion_evidence_path,
        as_of=as_of,
    )
    c3 = produce_c3_writer_evidence(
        out_dir=out_dir,
        promotion_evidence_path=promotion_evidence_path,
        as_of=as_of,
    )
    c4 = produce_c4_healthcheck_evidence(
        out_dir=out_dir,
        check_fn=check_fn,
        as_of=as_of,
    )

    summary_path = out_dir / f"{_stamp(as_of)}_summary.md"
    summary_path.write_text(_render_summary(c1=c1, c3=c3, c4=c4, as_of=as_of))

    return {"c1": c1, "c3": c3, "c4": c4, "summary_path": str(summary_path)}


def _render_summary(*, c1, c3, c4, as_of: datetime) -> str:
    rows = [
        ("ZEUS_ENTRY_FORECAST_READINESS_WRITER (C-3)", c3),
        ("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE (C-1)", c1),
        ("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS (C-4)", c4),
    ]
    body = [
        f"# Activation evidence summary — {as_of.isoformat()}",
        "",
        "Operator decision matrix (run `python scripts/produce_activation_evidence.py --all` to refresh):",
        "",
        "| flag | ready_to_flip | rationale | artifact |",
        "|---|---|---|---|",
    ]
    for label, verdict in rows:
        body.append(
            f"| `{label}` | {verdict['ready_to_flip']} | "
            f"{verdict['rationale']} | "
            f"`{Path(verdict['artifact_path']).name}` |"
        )
    body.append("")
    body.append(
        "Recommended flip order per "
        "`docs/runbooks/live-operation.md` §Phase C:\n"
        "1. ZEUS_ENTRY_FORECAST_READINESS_WRITER\n"
        "2. ZEUS_ENTRY_FORECAST_ROLLOUT_GATE\n"
        "3. ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS"
    )
    return "\n".join(body) + "\n"


# -------------------------------------------------------------------- #
# CLI
# -------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("evidence/activation"),
        help="Directory to write artifact files into (default: evidence/activation/).",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("state/entry_forecast_promotion_evidence.json"),
        help="Path to the promotion-evidence JSON file the gate / writer will consult.",
    )
    parser.add_argument(
        "--c1",
        action="store_true",
        help="Produce ZEUS_ENTRY_FORECAST_ROLLOUT_GATE evidence.",
    )
    parser.add_argument(
        "--c3",
        action="store_true",
        help="Produce ZEUS_ENTRY_FORECAST_READINESS_WRITER evidence.",
    )
    parser.add_argument(
        "--c4",
        action="store_true",
        help="Produce ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS evidence.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Produce all three flags' evidence + summary.",
    )
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    as_of = datetime.now(UTC)

    if args.all or not (args.c1 or args.c3 or args.c4):
        result = produce_all(
            out_dir=out_dir,
            promotion_evidence_path=args.evidence,
            as_of=as_of,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    payload: dict[str, Any] = {}
    if args.c3:
        payload["c3"] = produce_c3_writer_evidence(
            out_dir=out_dir,
            promotion_evidence_path=args.evidence,
            as_of=as_of,
        )
    if args.c1:
        payload["c1"] = produce_c1_rollout_gate_evidence(
            out_dir=out_dir,
            promotion_evidence_path=args.evidence,
            as_of=as_of,
        )
    if args.c4:
        payload["c4"] = produce_c4_healthcheck_evidence(
            out_dir=out_dir,
            as_of=as_of,
        )
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
