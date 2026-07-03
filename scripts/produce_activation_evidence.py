# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/activation/UNLOCK_CRITERIA.md — operator-runnable evidence factory for active healthcheck blocker flag flips.
"""Produce activation-evidence artifacts for active operator flag flips.

Each function below dry-runs ONE active control flag path against live
or injected healthcheck evidence and emits an artifact under
``<out_dir>/`` that the unlock-criteria audit trail consumes.

The functions are deliberately import-friendly so the unit test in
``tests/test_produce_activation_evidence.py`` can drive them directly
without a subprocess. The CLI at the bottom is a thin wrapper.

The retired evaluator-side rollout gate is intentionally absent here:
this script must not keep producing artifacts for controls that no
longer affect live evaluator behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UTC = timezone.utc

C4_FLAG = "ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS"


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


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
                "after rollout evidence has been observed for a cycle) before flipping."
        )
    else:
        ready = True
        rationale = (
            "flag toggle would correctly surface entry_forecast_blockers "
                "in the healthy predicate; ready to flip after rollout evidence is stable."
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
    check_fn: Callable[[], dict[str, Any]] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Run all active producers and write a markdown summary."""

    if as_of is None:
        as_of = datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)

    c4 = produce_c4_healthcheck_evidence(
        out_dir=out_dir,
        check_fn=check_fn,
        as_of=as_of,
    )

    summary_path = out_dir / f"{_stamp(as_of)}_summary.md"
    summary_path.write_text(
        _render_summary(c4=c4, as_of=as_of)
    )

    return {
        "c4": c4,
        "summary_path": str(summary_path),
    }


def _render_summary(*, c4, as_of: datetime) -> str:
    rows = [
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
        "1. ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS"
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
        default=Path("docs/historical_evidence/activation"),
        help="Directory to write artifact files into (default: docs/historical_evidence/activation/).",
    )
    parser.add_argument(
        "--c4",
        action="store_true",
        help="Produce ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS evidence.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Produce all active flags' evidence + summary.",
    )
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    as_of = datetime.now(UTC)

    if args.all or not args.c4:
        result = produce_all(
            out_dir=out_dir,
            as_of=as_of,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    payload: dict[str, Any] = {}
    if args.c4:
        payload["c4"] = produce_c4_healthcheck_evidence(
            out_dir=out_dir,
            as_of=as_of,
        )
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
