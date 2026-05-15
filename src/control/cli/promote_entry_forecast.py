# Created: 2026-05-12
# Last reused/audited: 2026-05-15
# Authority basis: Operator CLI for write_promotion_evidence flow — replaces
# hand-crafted Python invocations referenced in
# src/control/entry_forecast_promotion_evidence_io.py:129 (write_promotion_evidence).
# All mutations are gated by --commit; default behavior is dry-run.
"""Operator CLI for the entry-forecast promotion-evidence flow.

Subcommands:

* ``status``       — pretty-print current evidence + rollout decision.
* ``propose``      — build an ``EntryForecastPromotionEvidence`` from CLI flags
                     and a fresh ``status_snapshot`` taken from the DB,
                     print the proposed JSON, and (with ``--commit``) atomically
                     write it via :func:`write_promotion_evidence`.
* ``flip-mode``    — validate a rollout-mode transition and print the env-var
                     change + ``launchctl kickstart`` command. Never execs.
* ``unarm``        — print or apply the rollback steps (rewrite
                     ``state/cutover_guard.json`` to ``NORMAL`` and restore the
                     ``state/auto_pause_failclosed.tombstone``).

Invocable both as a module and as a script::

    python -m src.control.cli.promote_entry_forecast SUBCOMMAND [args]
    python src/control/cli/promote_entry_forecast.py SUBCOMMAND [args]

Constraint: opens ``state/zeus-forecasts.db`` in read-only URI mode for status
snapshot building by default. Never opens it writable. Never execs ``launchctl`` or
``arm_live_mode.sh``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

# Make ``python src/control/cli/promote_entry_forecast.py`` work as well as
# ``python -m ...`` by ensuring the project root is importable.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import (  # noqa: E402  (deferred import after sys.path fix)
    EntryForecastConfig,
    EntryForecastRolloutMode,
    entry_forecast_config,
    state_path,
)
from src.control.entry_forecast_promotion_evidence_io import (  # noqa: E402
    DEFAULT_PROMOTION_EVIDENCE_PATH,
    PromotionEvidenceCorruption,
    evidence_to_dict,
    read_promotion_evidence,
    write_promotion_evidence,
)
from src.control.entry_forecast_rollout import (  # noqa: E402
    EntryForecastPromotionEvidence,
    evaluate_entry_forecast_rollout_gate,
)
from src.data.live_entry_status import build_live_entry_forecast_status  # noqa: E402
from src.state.db import ZEUS_FORECASTS_DB_PATH  # noqa: E402

ROLLOUT_MODE_ENV = "ZEUS_ENTRY_FORECAST_ROLLOUT_MODE"
LAUNCHD_LABEL = "com.zeus.live-trading"
ARM_SCRIPT = "scripts/arm_live_mode.sh"

OPERATOR_APPROVAL_PATTERN = re.compile(r"^OPS-\d{4}-\d{2}-\d{2}-")

ALLOWED_TRANSITIONS = {
    "shadow": {"shadow", "canary"},
    "canary": {"shadow", "canary", "live"},
    "live": {"shadow", "canary", "live"},
    "blocked": {"shadow", "canary", "live", "blocked"},
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@contextmanager
def _open_db_readonly(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open ``db_path`` in URI ``mode=ro``. Sets ``Row`` factory."""

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _default_db_path() -> Path:
    return ZEUS_FORECASTS_DB_PATH


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------


def _summarize_status(snapshot_dict: dict) -> str:
    return (
        f"status={snapshot_dict['status']} "
        f"executable_rows={snapshot_dict['executable_row_count']} "
        f"producer_rows={snapshot_dict['producer_readiness_count']} "
        f"producer_LIVE_ELIGIBLE={snapshot_dict['producer_live_eligible_count']} "
        f"blockers={snapshot_dict['blockers']}"
    )


def _evidence_to_json(evidence: EntryForecastPromotionEvidence) -> str:
    return json.dumps(evidence_to_dict(evidence), indent=2, sort_keys=True)


def _print_decision(
    cfg: EntryForecastConfig, evidence: EntryForecastPromotionEvidence | None
) -> None:
    decision = evaluate_entry_forecast_rollout_gate(config=cfg, evidence=evidence)
    print(f"  rollout_decision     : {decision.status}")
    print(f"  reason_codes         : {list(decision.reason_codes)}")
    print(f"  may_run_canary       : {decision.may_run_canary}")
    print(f"  may_submit_live      : {decision.may_submit_live_orders}")


# ---------------------------------------------------------------------------
# `status` subcommand
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    evidence_path = Path(args.evidence_path) if args.evidence_path else DEFAULT_PROMOTION_EVIDENCE_PATH
    cfg = entry_forecast_config()
    print("=== entry_forecast_config (from config/settings.json) ===")
    print(f"  rollout_mode (config): {cfg.rollout_mode.value}")
    env_mode = os.environ.get(ROLLOUT_MODE_ENV)
    print(f"  {ROLLOUT_MODE_ENV}: {env_mode if env_mode is not None else '<unset>'}")
    print()
    print(f"=== promotion_evidence ({evidence_path}) ===")
    if not evidence_path.exists():
        print("  <no evidence file present>")
        _print_decision(cfg, None)
        return 0
    try:
        evidence = read_promotion_evidence(path=evidence_path)
    except PromotionEvidenceCorruption as exc:
        print(f"  CORRUPTION: {exc}")
        print("  treated as EVIDENCE_MISSING")
        _print_decision(cfg, None)
        return 1
    if evidence is None:
        print("  <no evidence (file vanished mid-read)>")
        _print_decision(cfg, None)
        return 0
    print(f"  operator_approval_id           : {evidence.operator_approval_id!r}")
    print(f"  g1_evidence_id                 : {evidence.g1_evidence_id!r}")
    print(f"  calibration_promotion_approved : {evidence.calibration_promotion_approved!r}")
    print(f"  canary_success_evidence_id     : {evidence.canary_success_evidence_id!r}")
    print(f"  status_snapshot                : {_summarize_status(evidence.status_snapshot.to_dict())}")
    print()
    print("=== rollout decision ===")
    _print_decision(cfg, evidence)
    return 0


# ---------------------------------------------------------------------------
# `propose` subcommand
# ---------------------------------------------------------------------------


def _validate_propose_inputs(
    operator_approval_id: str, g1_evidence_id: str
) -> list[str]:
    errors: list[str] = []
    if not OPERATOR_APPROVAL_PATTERN.match(operator_approval_id):
        errors.append(
            f"--operator-approval-id must match {OPERATOR_APPROVAL_PATTERN.pattern!r}; "
            f"got {operator_approval_id!r}"
        )
    g1_path = Path(g1_evidence_id)
    if not g1_path.exists():
        errors.append(f"--g1-evidence-id must be an existing file path; got {g1_evidence_id!r}")
    return errors


def cmd_propose(args: argparse.Namespace) -> int:
    errors = _validate_propose_inputs(args.operator_approval_id, args.g1_evidence_id)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2

    cfg = entry_forecast_config()
    db_path = Path(args.db) if args.db else _default_db_path()
    with _open_db_readonly(db_path) as conn:
        snapshot = build_live_entry_forecast_status(conn, config=cfg)

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id=args.operator_approval_id,
        g1_evidence_id=args.g1_evidence_id,
        status_snapshot=snapshot,
        calibration_promotion_approved=not args.no_calibration_approved,
        canary_success_evidence_id=args.canary_success_evidence_id,
    )

    target = Path(args.evidence_path) if args.evidence_path else DEFAULT_PROMOTION_EVIDENCE_PATH

    print(f"=== proposed evidence (would write to {target}) ===")
    print(_evidence_to_json(evidence))
    print()
    print("=== rollout decision under proposed evidence ===")
    _print_decision(cfg, evidence)

    if not args.commit:
        print()
        print("DRY-RUN: not writing. Re-run with --commit to apply.")
        return 0

    write_promotion_evidence(evidence, path=target)
    print()
    print(f"WROTE {target}")
    return 0


# ---------------------------------------------------------------------------
# `flip-mode` subcommand
# ---------------------------------------------------------------------------


def cmd_flip_mode(args: argparse.Namespace) -> int:
    target_mode = args.target_mode
    cfg = entry_forecast_config()
    current_mode = cfg.rollout_mode.value
    env_mode = os.environ.get(ROLLOUT_MODE_ENV)

    print(f"=== flip-mode {current_mode!r} -> {target_mode!r} ===")
    print(f"  config rollout_mode  : {current_mode}")
    print(f"  {ROLLOUT_MODE_ENV}: {env_mode if env_mode is not None else '<unset>'}")

    allowed = ALLOWED_TRANSITIONS.get(current_mode, set())
    if target_mode not in allowed and not args.force:
        print(
            f"ERROR: transition {current_mode} -> {target_mode} not allowed "
            f"(allowed: {sorted(allowed)}). Use --force to override.",
            file=sys.stderr,
        )
        return 2

    evidence_path = Path(args.evidence_path) if args.evidence_path else DEFAULT_PROMOTION_EVIDENCE_PATH
    evidence: EntryForecastPromotionEvidence | None
    try:
        evidence = read_promotion_evidence(path=evidence_path)
    except PromotionEvidenceCorruption as exc:
        print(f"WARNING: evidence file corrupt ({exc}); treated as missing", file=sys.stderr)
        evidence = None

    if target_mode == "live":
        if evidence is None or not evidence.canary_success_evidence_id:
            if not args.force:
                print(
                    "ERROR: target=live requires canary_success_evidence_id in "
                    f"{evidence_path}. Run a canary, then `propose --canary-success-evidence-id ID --commit`. "
                    "Override with --force (NOT RECOMMENDED).",
                    file=sys.stderr,
                )
                return 2
            print("WARNING: --force overriding missing canary_success_evidence_id", file=sys.stderr)

    # Synthesise a hypothetical config for the proposed mode and re-evaluate.
    try:
        proposed_mode = EntryForecastRolloutMode(target_mode)
    except ValueError:
        print(f"ERROR: unknown rollout mode {target_mode!r}", file=sys.stderr)
        return 2
    hypothetical_cfg = replace(cfg, rollout_mode=proposed_mode)
    decision = evaluate_entry_forecast_rollout_gate(config=hypothetical_cfg, evidence=evidence)
    print()
    print("=== predicted rollout decision under target mode ===")
    print(f"  rollout_decision : {decision.status}")
    print(f"  reason_codes     : {list(decision.reason_codes)}")
    if decision.status == "BLOCKED" and not args.force:
        print(
            "ERROR: predicted decision is BLOCKED — refusing to print flip commands. "
            "Fix blockers, or override with --force.",
            file=sys.stderr,
        )
        return 2

    print()
    print("=== commands to flip (NOT EXECUTED) ===")
    print(f"  export {ROLLOUT_MODE_ENV}={target_mode}")
    print(f"  # also update config/settings.json -> entry_forecast.rollout_mode = {target_mode!r}")
    print(f"  launchctl kickstart -k gui/$(id -u)/{LAUNCHD_LABEL}")
    print()
    print(f"NOTE: this CLI does NOT exec these commands or {ARM_SCRIPT}. Run them manually.")
    return 0


# ---------------------------------------------------------------------------
# `unarm` subcommand
# ---------------------------------------------------------------------------


CUTOVER_GUARD_FILENAME = "cutover_guard.json"
TOMBSTONE_FILENAME = "auto_pause_failclosed.tombstone"


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_unarm(args: argparse.Namespace) -> int:
    cutover_path = state_path(CUTOVER_GUARD_FILENAME) if not args.cutover_path else Path(args.cutover_path)
    tombstone_path = state_path(TOMBSTONE_FILENAME) if not args.tombstone_path else Path(args.tombstone_path)

    print("=== unarm (rollback to NORMAL/shadow) ===")
    print(f"  cutover_guard      : {cutover_path}")
    print(f"  tombstone          : {tombstone_path}")
    print()

    print("=== planned actions ===")
    print(f"  1. rewrite {cutover_path} state -> 'NORMAL' (atomic, preserve history)")
    print(f"  2. touch   {tombstone_path}  (restore fail-closed sentinel)")
    print(f"  3. export {ROLLOUT_MODE_ENV}=shadow")
    print(f"  4. # also update config/settings.json -> entry_forecast.rollout_mode = 'shadow'")
    print(f"  5. launchctl kickstart -k gui/$(id -u)/{LAUNCHD_LABEL}")
    print()

    if not args.commit:
        print("DRY-RUN: not modifying any files. Re-run with --commit to apply steps 1+2.")
        print("Steps 3-5 are NEVER auto-applied; copy/paste manually.")
        return 0

    # Step 1: rewrite cutover_guard.json with NORMAL state, preserving transitions.
    if cutover_path.exists():
        try:
            current = json.loads(cutover_path.read_text())
        except json.JSONDecodeError as exc:
            print(f"ERROR: cutover_guard JSON invalid ({exc}); refusing to overwrite", file=sys.stderr)
            return 1
        if not isinstance(current, dict):
            print("ERROR: cutover_guard payload is not an object; refusing", file=sys.stderr)
            return 1
        prior_state = current.get("state")
        transitions = current.get("transitions") or []
        if not isinstance(transitions, list):
            print("ERROR: cutover_guard.transitions is not a list; refusing", file=sys.stderr)
            return 1
        from datetime import datetime, timezone

        transitions = list(transitions) + [
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "by": "promote_entry_forecast.unarm",
                "from": prior_state,
                "to": "NORMAL",
                "reason": "operator unarm via promote_entry_forecast CLI",
            }
        ]
        new_payload = {**current, "state": "NORMAL", "transitions": transitions}
    else:
        from datetime import datetime, timezone

        new_payload = {
            "state": "NORMAL",
            "transitions": [
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "by": "promote_entry_forecast.unarm",
                    "from": None,
                    "to": "NORMAL",
                    "reason": "operator unarm via promote_entry_forecast CLI (no prior file)",
                }
            ],
        }
    _atomic_write_text(cutover_path, json.dumps(new_payload, indent=2, sort_keys=True) + "\n")
    print(f"WROTE {cutover_path} (state=NORMAL)")

    # Step 2: restore tombstone (touch).
    tombstone_path.parent.mkdir(parents=True, exist_ok=True)
    tombstone_path.touch()
    print(f"TOUCHED {tombstone_path}")

    print()
    print("Steps 3-5 (env + launchctl) NOT executed. Run manually.")
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promote_entry_forecast",
        description="Operator CLI for entry-forecast promotion-evidence flow.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # status
    p_status = sub.add_parser("status", help="Show current evidence + rollout decision.")
    p_status.add_argument("--evidence-path", default=None, help="override evidence JSON path")
    p_status.set_defaults(func=cmd_status)

    # propose
    p_propose = sub.add_parser(
        "propose",
        help="Build promotion evidence from CLI flags + DB-derived status_snapshot.",
    )
    p_propose.add_argument("--operator-approval-id", required=True)
    p_propose.add_argument("--g1-evidence-id", required=True, help="path to G1 evidence file")
    p_propose.add_argument("--canary-success-evidence-id", default=None)
    p_propose.add_argument(
        "--no-calibration-approved",
        action="store_true",
        help="set calibration_promotion_approved=False (default True)",
    )
    p_propose.add_argument("--db", default=None, help="override DB path (read-only)")
    p_propose.add_argument("--evidence-path", default=None, help="override evidence JSON write path")
    p_propose.add_argument("--commit", action="store_true", help="actually write (default dry-run)")
    p_propose.set_defaults(func=cmd_propose)

    # flip-mode
    p_flip = sub.add_parser(
        "flip-mode",
        help="Validate a rollout-mode transition; print env + launchctl command (no exec).",
    )
    p_flip.add_argument("target_mode", choices=["shadow", "canary", "live"])
    p_flip.add_argument("--force", action="store_true", help="bypass transition / decision checks")
    p_flip.add_argument("--evidence-path", default=None, help="override evidence JSON path")
    p_flip.set_defaults(func=cmd_flip_mode)

    # unarm
    p_unarm = sub.add_parser(
        "unarm", help="Print or apply rollback (cutover_guard NORMAL + restore tombstone)."
    )
    p_unarm.add_argument("--commit", action="store_true", help="actually rewrite state files")
    p_unarm.add_argument("--cutover-path", default=None)
    p_unarm.add_argument("--tombstone-path", default=None)
    p_unarm.set_defaults(func=cmd_unarm)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
