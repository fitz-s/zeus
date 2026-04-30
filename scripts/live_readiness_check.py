#!/usr/bin/env python3
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Enforce R3 G1 live-readiness gate aggregation without live side effects.
# Reuse: Run before live-readiness, staged-smoke, cutover, or operator-deploy gate decisions.
# Created: 2026-04-27
# Last reused/audited: 2026-04-30
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/G1.yaml
"""R3 G1 live-readiness gate orchestrator.

This script is an enforcement/readiness surface only.  It never submits,
cancels, redeems, deploys, transitions CutoverGuard, reads credentials, or
mutates canonical DB/state artifacts.  Exit code 0 means all 17 readiness gates
passed *and* staged-live-smoke evidence is present; it is still not live-deploy
authority without the operator's live-money-deploy-go decision.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOTS = (
    ROOT / "docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence",
    ROOT / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence",
)
EVIDENCE_HMAC_SECRET_ENV = "ZEUS_LIVE_READINESS_EVIDENCE_HMAC_SECRET"
PASS = "PASS"
FAIL = "FAIL"


@dataclass(frozen=True)
class GateSpec:
    gate_id: str
    name: str
    phase: str
    antibody: str
    kind: str
    command: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    name: str
    phase: str
    antibody: str
    status: str
    evidence: str
    command: str | None = None


@dataclass(frozen=True)
class ReadinessReport:
    status: str
    gate_count: int
    passed_gates: int
    gates: tuple[GateResult, ...]
    staged_smoke_status: str
    staged_smoke_evidence: str
    operator_gate: str = "live-money-deploy-go"
    live_deploy_authorized: bool = False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["gates"] = [asdict(gate) for gate in self.gates]
        return payload


GATES: tuple[GateSpec, ...] = (
    GateSpec(
        "G1-01",
        "V2 SDK gate",
        "Z2",
        "NC-NEW-G / no legacy V1 SDK imports in live source",
        "legacy_sdk_scan",
        description="Live source must not import py_clob_client v1 directly.",
    ),
    GateSpec(
        "G1-02",
        "Host / Zeus egress gate",
        "Z2",
        "Q1-zeus-egress evidence",
        "q1_evidence",
        description="Q1 evidence must show HTTP 200 from Zeus daemon egress with funder context.",
    ),
    GateSpec(
        "G1-03",
        "Heartbeat gate",
        "Z3/M5",
        "HEARTBEAT_CANCEL_SUSPECTED reconciliation antibody",
        "pytest",
        ("tests/test_exchange_reconcile.py::test_heartbeat_suspected_cancel_finding_emitted_after_heartbeat_loss",),
    ),
    GateSpec(
        "G1-04",
        "pUSD gate",
        "Z4",
        "pUSD balance/allowance preflight antibodies",
        "pytest",
        (
            "tests/test_collateral_ledger.py::test_buy_preflight_blocks_when_pusd_insufficient",
            "tests/test_collateral_ledger.py::test_buy_preflight_blocks_when_pusd_allowance_insufficient",
        ),
    ),
    GateSpec(
        "G1-05",
        "Sell-token gate",
        "Z4/M4",
        "CTF token balance and exit reservation antibodies",
        "pytest",
        (
            "tests/test_collateral_ledger.py::test_sell_preflight_blocks_when_token_balance_insufficient",
            "tests/test_exit_safety.py::test_exit_preflight_uses_token_balance_not_pusd",
        ),
    ),
    GateSpec(
        "G1-06",
        "Snapshot gate",
        "U1",
        "Stale executable snapshot blocks submit",
        "pytest",
        ("tests/test_executable_market_snapshot_v2.py::test_stale_snapshot_blocks_submit",),
    ),
    GateSpec(
        "G1-07",
        "Provenance gate",
        "U2",
        "Full side-effect provenance chain reconstructable",
        "pytest",
        ("tests/test_provenance_5_projections.py::test_full_provenance_chain_reconstructable",),
    ),
    GateSpec(
        "G1-08",
        "Order-type gate",
        "Z2/A2",
        "GTC/GTD/FOK/FAK are explicit and behavior-changing",
        "order_type_scan",
    ),
    GateSpec(
        "G1-09",
        "Unknown side-effect gate",
        "M2",
        "Duplicate submit blocked during unknown outcome",
        "pytest",
        ("tests/test_unknown_side_effect.py::test_duplicate_retry_blocked_during_unknown",),
    ),
    GateSpec(
        "G1-10",
        "Optimistic-not-final gate",
        "M3/U2",
        "MATCHED/MINED cannot final-close; CONFIRMED is success finality",
        "pytest",
        (
            "tests/test_user_channel_ingest.py::test_matched_event_does_not_final_close_lot",
            "tests/test_user_channel_ingest.py::test_mined_event_is_optimistic_exposure_not_finality",
            "tests/test_user_channel_ingest.py::test_confirmed_event_finalizes_trade_and_permits_canonical_pnl",
            "tests/test_exchange_reconcile.py::test_nonconfirmed_full_size_trade_is_optimistic_exposure_not_fill_finality",
            "tests/test_exchange_reconcile.py::test_confirmed_full_size_trade_is_required_for_fill_finality",
        ),
    ),
    GateSpec(
        "G1-11",
        "User-channel gate",
        "M3",
        "User trade statuses persist into canonical facts",
        "pytest",
        ("tests/test_user_channel_ingest.py::test_ws_message_parsed_to_trade_fact",),
    ),
    GateSpec(
        "G1-12",
        "Cancel/replace gate",
        "M4",
        "CANCEL_UNKNOWN blocks replacement sells",
        "pytest",
        ("tests/test_exit_safety.py::test_CANCEL_UNKNOWN_blocks_replacement",),
    ),
    GateSpec(
        "G1-13",
        "Cutover/wipe gate",
        "Z1/M5",
        "Open-order wipe simulation creates findings, not silent resting state",
        "pytest",
        ("tests/test_exchange_reconcile.py::test_cutover_wipe_findings_emitted_in_POST_CUTOVER_RECONCILE_state",),
    ),
    GateSpec(
        "G1-14",
        "Crash gate",
        "T1/M2",
        "Post-side-effect timeout becomes unknown side effect",
        "pytest",
        ("tests/test_fake_polymarket_venue.py::test_failure_injection_timeout_after_post_creates_unknown_side_effect_shape",),
    ),
    GateSpec(
        "G1-15",
        "Paper/live parity gate",
        "T1",
        "Fake venue emits live-adapter-compatible envelope/result schema",
        "pytest",
        ("tests/test_fake_polymarket_venue.py::test_fake_submit_uses_same_submit_result_and_envelope_shape",),
    ),
    GateSpec(
        "G1-16",
        "Strategy benchmark gate",
        "A1",
        "Replay/paper/shadow promotion gate blocks unsafe strategies and optimistic success samples",
        "pytest",
        (
            "tests/test_strategy_benchmark.py::test_promotion_blocked_unless_replay_paper_shadow_all_pass",
            "tests/test_strategy_benchmark.py::test_paper_benchmark_matched_mined_are_not_success_finality",
            "tests/test_strategy_benchmark.py::test_paper_benchmark_confirmed_trade_counts_as_success_finality",
        ),
    ),
    GateSpec(
        "G1-17",
        "Agent-docs gate",
        "Z0..A2",
        "Agent-facing docs do not expose legacy direct SDK live paths",
        "agent_docs_scan",
    ),
)


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)


def run_readiness(
    *,
    root: Path = ROOT,
    evidence_roots: Sequence[Path] = DEFAULT_EVIDENCE_ROOTS,
    run_commands: bool = True,
    runner: CommandRunner = default_runner,
) -> ReadinessReport:
    gates = tuple(_run_gate(gate, root=root, evidence_roots=evidence_roots, run_commands=run_commands, runner=runner) for gate in GATES)
    smoke_status, smoke_evidence = _staged_smoke_status(evidence_roots)
    all_pass = all(g.status == PASS for g in gates) and smoke_status == PASS
    return ReadinessReport(
        status=PASS if all_pass else FAIL,
        gate_count=len(gates),
        passed_gates=sum(1 for g in gates if g.status == PASS),
        gates=gates,
        staged_smoke_status=smoke_status,
        staged_smoke_evidence=smoke_evidence,
    )


def _run_gate(
    gate: GateSpec,
    *,
    root: Path,
    evidence_roots: Sequence[Path],
    run_commands: bool,
    runner: CommandRunner,
) -> GateResult:
    if gate.kind == "legacy_sdk_scan":
        return _legacy_sdk_gate(gate, root)
    if gate.kind == "q1_evidence":
        return _q1_evidence_gate(gate, evidence_roots)
    if gate.kind == "order_type_scan":
        return _order_type_gate(gate, root)
    if gate.kind == "agent_docs_scan":
        return _agent_docs_gate(gate, root)
    if gate.kind == "pytest":
        cmd = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *gate.command)
        if not run_commands:
            return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "pytest execution disabled", " ".join(cmd))
        proc = runner(cmd)
        evidence = (proc.stdout or proc.stderr or "").strip().splitlines()
        tail = " | ".join(evidence[-3:]) if evidence else f"exit={proc.returncode}"
        return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, PASS if proc.returncode == 0 else FAIL, tail, " ".join(cmd))
    return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, f"unknown gate kind {gate.kind!r}")


def _legacy_sdk_gate(gate: GateSpec, root: Path) -> GateResult:
    offenders = []
    for path in (root / "src").rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if _imports_legacy_py_clob_client(path):
            offenders.append(rel)
    if offenders:
        return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "legacy SDK import in " + ", ".join(offenders))
    return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, PASS, "no legacy py_clob_client imports in src/")


def _imports_legacy_py_clob_client(path: Path) -> bool:
    text = path.read_text(errors="ignore")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return (
            "import py_clob_client" in text
            or "from py_clob_client " in text
            or "from py_clob_client." in text
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "py_clob_client":
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] == "py_clob_client":
                return True
    return False


def _q1_evidence_gate(gate: GateSpec, evidence_roots: Sequence[Path]) -> GateResult:
    files = _glob_evidence(evidence_roots, ("q1_zeus_egress*.json", "q1_v2_host_probe*.json"))
    if not files:
        return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "missing Q1 Zeus-egress evidence")
    for path in files:
        payload, error = _load_signed_evidence(path, evidence_type="q1_zeus_egress")
        if error:
            continue
        status_code = int(payload.get("status_code", 0) or 0)
        protocol = str(payload.get("protocol") or "").upper()
        daemon = str(payload.get("daemon") or "").lower()
        funder = bool(payload.get("funder_address_present") or payload.get("gnosis_safe_present"))
        if status_code == 200 and protocol.startswith("HTTP") and "zeus" in daemon and funder:
            return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, PASS, path.as_posix())
    return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "Q1 evidence exists but lacks signed HTTP 200 + Zeus daemon + funder/Gnosis proof")


def _order_type_gate(gate: GateSpec, root: Path) -> GateResult:
    heartbeat = (root / "src/control/heartbeat_supervisor.py").read_text(errors="ignore")
    adapter = (root / "src/venue/polymarket_v2_adapter.py").read_text(errors="ignore")
    executor = (root / "src/execution/executor.py").read_text(errors="ignore")
    required = ("GTC", "GTD", "FOK", "FAK")
    if not all(token in heartbeat for token in required):
        return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "heartbeat OrderType set missing one of GTC/GTD/FOK/FAK")
    if "order_type: str = \"GTC\"" not in adapter or "order_type=envelope.order_type" not in adapter:
        return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "adapter does not preserve explicit order_type")
    if "_select_risk_allocator_order_type" not in executor or "order_type=order_type" not in executor:
        return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "executor does not submit selected order_type")
    return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, PASS, "GTC/GTD/FOK/FAK explicit and selected order_type reaches adapter")


def _agent_docs_gate(gate: GateSpec, root: Path) -> GateResult:
    candidates: list[Path] = []
    for base in (root, root / "docs", root / "src"):
        candidates.extend(base.rglob("AGENTS.md"))
    candidates.extend((root / "docs/reference/modules").glob("*.md"))
    offenders: list[str] = []
    for path in candidates:
        text = path.read_text(errors="ignore")
        rel = path.relative_to(root).as_posix()
        if "from py_clob_client " in text or "ClobClient.create_order(" in text:
            offenders.append(rel)
    if offenders:
        return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, FAIL, "legacy/direct SDK snippets in " + ", ".join(sorted(offenders)))
    return GateResult(gate.gate_id, gate.name, gate.phase, gate.antibody, PASS, "agent-facing docs contain no legacy direct SDK snippets")


def _staged_smoke_status(evidence_roots: Sequence[Path]) -> tuple[str, str]:
    files = _glob_evidence(evidence_roots, ("staged_live_smoke_*.json", "live_readiness_smoke_*.json"))
    if not files:
        return FAIL, "missing staged-live-smoke evidence"
    for path in files:
        payload, error = _load_signed_evidence(path, evidence_type="staged_live_smoke")
        if error:
            continue
        status = str(payload.get("status") or payload.get("overall") or "").upper()
        gates = int(payload.get("gates_passed", payload.get("passed_gates", 0)) or 0)
        smoke = str(payload.get("environment") or payload.get("env") or "").lower()
        if status == PASS and gates >= 17 and "staged" in smoke:
            return PASS, path.as_posix()
    return FAIL, "staged-live-smoke evidence exists but does not prove signed PASS + 17/17 + staged environment"


def _load_signed_evidence(path: Path, *, evidence_type: str) -> tuple[dict, str | None]:
    secret = os.environ.get(EVIDENCE_HMAC_SECRET_ENV, "")
    if not secret:
        return {}, f"{EVIDENCE_HMAC_SECRET_ENV} is required for signed readiness evidence"
    try:
        doc = json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON evidence: {exc}"
    if not isinstance(doc, dict):
        return {}, "evidence document must be a JSON object"
    if int(doc.get("schema_version", 0) or 0) != 1:
        return {}, "evidence schema_version must be 1"
    if str(doc.get("evidence_type") or "") != evidence_type:
        return {}, f"evidence_type must be {evidence_type}"
    payload = doc.get("payload")
    if not isinstance(payload, dict):
        return {}, "evidence payload must be an object"
    payload_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(str(doc.get("payload_sha256") or ""), payload_hash):
        return {}, "payload_sha256 mismatch"
    message = f"{evidence_type}\n{payload_hash}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(doc.get("hmac_sha256") or ""), expected):
        return {}, "hmac_sha256 mismatch"
    return payload, None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _test_runtime_enabled() -> bool:
    return (
        os.environ.get("ZEUS_TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or "pytest" in sys.modules
    )


def _glob_evidence(evidence_roots: Sequence[Path], patterns: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for root in evidence_roots:
        if not root.exists():
            continue
        for pattern in patterns:
            files.extend(sorted(root.glob(pattern)))
    return files


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run R3 G1 live-readiness gates.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--evidence-root", action="append", default=[], help="Test-only evidence root override; may be passed multiple times.")
    parser.add_argument("--no-run-commands", action="store_true", help="Do not run pytest gates; they fail closed. Intended only for manifest debugging.")
    args = parser.parse_args(argv)

    if args.evidence_root and not _test_runtime_enabled():
        parser.error("--evidence-root is test-only; production readiness uses canonical evidence roots")
    evidence_roots = tuple(Path(p).resolve() for p in args.evidence_root) if args.evidence_root else DEFAULT_EVIDENCE_ROOTS
    report = run_readiness(evidence_roots=evidence_roots, run_commands=not args.no_run_commands)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"G1 live readiness: {report.status} ({report.passed_gates}/{report.gate_count} gates); staged_smoke={report.staged_smoke_status}")
        for gate in report.gates:
            print(f"{gate.gate_id} {gate.status:4s} {gate.name} — {gate.evidence}")
        print(f"staged_smoke {report.staged_smoke_status}: {report.staged_smoke_evidence}")
        print("live_deploy_authorized: false (operator live-money-deploy-go still required)")
    return 0 if report.status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
