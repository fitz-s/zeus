#!/usr/bin/env python3
# Created: 2026-08-22
# Last reused or audited: 2026-08-22
# Authority basis: operator request for an event-triggered, isolated-memory full-loss investigation loop.
"""Detect canonical full losses and trigger an isolated Codex investigation.

This sidecar never writes Zeus canonical databases.  It repeatedly scans the
trade DB read-only, creates deterministic incident identities, and starts one
ephemeral Codex run for the pending incident batch.  All durable investigation
state lives in a dedicated workspace, never in another agent's memory.

The investigator may edit only a configured clean, non-live worktree.  If that
gate is not exact it still investigates from the live tree under a read-only
sandbox and emits a repair specification instead of changing source.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.probe_lib import ro as open_read_only
except ModuleNotFoundError:  # direct `python scripts/...` entrypoint
    from probe_lib import ro as open_read_only


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = Path.home() / ".openclaw" / "workspace-zeus-loss-investigator"
DEFAULT_CODEX = shutil.which("codex") or str(Path.home() / ".npm-global" / "bin" / "codex")
SCHEMA_VERSION = 1
FULL_LOSS_RATIO = 0.95
MAX_BATCH = 20
ROOT_CAUSE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")


INVESTIGATOR_RULES = """# Zeus Full-Loss Investigator

This workspace and its `memory/` tree are exclusively for full-loss incidents.
Never read or write ~/.codex/memories, another OpenClaw workspace, another
agent's sessions, or unrelated conversation memory.  Runtime and canonical DB
facts outrank this memory.  Prior root causes are hypotheses to falsify, never
authority.

Do not mutate /Users/leofitz/zeus, operate tmux, stop/start other agents, or
deploy.  A configured non-live worktree may be edited only when the invocation
prompt explicitly says `repair_mode=workspace_write`.  Obey that repository's
AGENTS.md before inspecting or changing it.
"""


PROMPT_TEMPLATE = """You are the dedicated Zeus full-loss investigator.

Read and obey AGENTS.md in the current repository before source work.  The
controller has supplied only this subsystem's prior root-cause registry; do not
load any other memory.  This is an event-time causal investigation, not a
post-hoc story.

Objective hierarchy:
1. Maximize expected capital growth.
2. Eliminate engine-preventable full loss without suppressing valid alpha.
3. Preserve fast entry, monitoring, and normal order execution.

For every incident:
- Reconstruct the continuous timeline from immutable entry evidence through
  settlement: decision certificate, forecast/source issue/fetch/write times,
  posterior revisions, executable book snapshots, MONITOR_REFRESHED cadence,
  first negative edge, last fresh bid >= 0.05, EXIT_INTENT, venue command,
  acknowledgement/fill/retry, chain state, and settlement.
- At each timestamp use only evidence available then.  Never infer that an
  earlier decision was wrong merely because settlement later lost.
- Locate the earliest causal divergence and the last realistically executable
  capital-preserving action.  Separate prediction/source error, stale or
  missing monitoring, probability-update error, decision-law error, command
  persistence/submission error, no-bid/gap risk, and settlement jump.
- Compare against every prior root cause.  Classify as same_root, variant,
  new_root, repair_regression, historical_exposure, or insufficient_evidence.
- Map uncertainty to known_known, known_unknown, unasked_known, or
  unknown_unknown.  State evidence that would falsify the classification.
- A personalized rule for one market is forbidden.  A repair must name the
  structural invariant, causal contract, affected twins, failing behavioral
  antibody, and re-decision behavior.
- If repair_mode=workspace_write and the defect is engine-preventable, make the
  smallest structural source/test change permitted by repo law and run the
  narrowest disproof test.  Stop and emit an exact repair_spec instead of
  editing when the change crosses a STOP-AND-PLAN boundary or evidence is not
  sufficient.  Never commit, cherry-pick, restart, or deploy.
- If there is no evidence delta from an existing cause, do not refactor.  Reuse
  its antibody, determine whether its fix was loaded before this position's
  last preventable decision window, and emit the next falsifiable check.

Return JSON matching the supplied schema.  Every incident_id in the batch must
appear exactly once.  Evidence refs must be concrete DB row ids/timestamps or
absolute file:line references.  Do not claim profitability, safety, repair, or
unpreventability without the corresponding proof lines.

repair_mode={repair_mode}
live_repo={live_repo}
analysis_repo={analysis_repo}
loaded_sha_at_detection={loaded_sha}

PRIOR ROOT-CAUSE REGISTRY (dedicated memory only):
{root_causes}

INCIDENT BATCH:
{batch}
"""


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "batch_id", "batch_status", "evidence_delta_digest",
        "incidents", "root_cause_updates", "next_actions", "stop_reason",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "batch_id": {"type": "string"},
        "batch_status": {
            "type": "string",
            "enum": ["investigated", "repair_ready", "insufficient_evidence", "blocked"],
        },
        "evidence_delta_digest": {"type": "string"},
        "stop_reason": {"type": "string"},
        "incidents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "incident_id", "position_id", "classification", "root_cause_id",
                    "preventability", "confidence", "uncertainty_quadrant",
                    "earliest_causal_divergence", "last_executable_exit",
                    "causal_timeline", "evidence_refs", "falsifier", "repair_spec",
                ],
                "properties": {
                    "incident_id": {"type": "string"},
                    "position_id": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": [
                            "same_root", "variant", "new_root", "repair_regression",
                            "historical_exposure", "insufficient_evidence",
                        ],
                    },
                    "root_cause_id": {"type": ["string", "null"]},
                    "preventability": {
                        "type": "string",
                        "enum": ["engine_preventable", "market_unavoidable", "mixed", "unknown"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncertainty_quadrant": {
                        "type": "string",
                        "enum": ["known_known", "known_unknown", "unasked_known", "unknown_unknown"],
                    },
                    "earliest_causal_divergence": {"type": ["string", "null"]},
                    "last_executable_exit": {"type": ["string", "null"]},
                    "causal_timeline": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["at", "event", "available_evidence", "decision_state", "market_state"],
                            "properties": {
                                "at": {"type": "string"},
                                "event": {"type": "string"},
                                "available_evidence": {"type": "string"},
                                "decision_state": {"type": "string"},
                                "market_state": {"type": "string"},
                            },
                        },
                    },
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "falsifier": {"type": "string"},
                    "repair_spec": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "required": [
                            "invariant", "affected_surfaces", "antibody",
                            "proposed_change", "redecision_behavior", "stop_and_plan",
                        ],
                        "properties": {
                            "invariant": {"type": "string"},
                            "affected_surfaces": {"type": "array", "items": {"type": "string"}},
                            "antibody": {"type": "string"},
                            "proposed_change": {"type": "string"},
                            "redecision_behavior": {"type": "string"},
                            "stop_and_plan": {"type": "boolean"},
                        },
                    },
                },
            },
        },
        "root_cause_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "root_cause_id", "signature", "causal_invariant", "antibody",
                    "repair_status", "fix_sha",
                ],
                "properties": {
                    "root_cause_id": {"type": "string"},
                    "signature": {"type": "string"},
                    "causal_invariant": {"type": "string"},
                    "antibody": {"type": "string"},
                    "repair_status": {"type": "string"},
                    "fix_sha": {"type": ["string", "null"]},
                },
            },
        },
        "next_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "owner", "evidence_gate", "priority"],
                "properties": {
                    "action": {"type": "string"},
                    "owner": {"type": "string"},
                    "evidence_gate": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 0, "maximum": 3},
                },
            },
        },
    },
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_time(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def append_event(workspace: Path, event: str, **fields: Any) -> None:
    path = workspace / "runtime" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": iso_now(), "event": event, **fields}
    with path.open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def isolated_codex_home(workspace: Path) -> Path:
    """Create a session home that exposes auth, but no global memory/config."""
    home = workspace / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    source_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    source_auth = source_home / "auth.json"
    target_auth = home / "auth.json"
    if target_auth.is_symlink():
        if target_auth.resolve() != source_auth:
            raise RuntimeError("isolated Codex auth link points at an unexpected source")
    elif target_auth.exists():
        if not target_auth.is_file():
            raise RuntimeError("isolated Codex auth path is not a regular file")
    elif source_auth.is_file():
        target_auth.symlink_to(source_auth)
    else:
        raise RuntimeError(f"Codex auth unavailable: {source_auth}")
    return home


def incident_id(row: Mapping[str, Any]) -> str:
    material = "|".join([str(row["position_id"]), str(row["settled_at"])])
    return hashlib.sha256(material.encode()).hexdigest()[:24]


LOSS_COLUMNS = (
    "position_id, phase, market_id, city, target_date, bin_label, direction, unit, "
    "shares, cost_basis_usd, realized_pnl_usd, entry_price, p_posterior, "
    "last_monitor_prob, last_monitor_edge, last_monitor_market_price, "
    "last_monitor_best_bid, last_monitor_best_ask, strategy_key, chain_state, "
    "token_id, no_token_id, condition_id, order_id, settled_at, settlement_price, "
    "exit_price, exit_reason, decision_law_id, position_origin"
)


def scan_losses(db_path: Path, *, not_before: datetime, lookback_days: int = 7) -> list[dict[str, Any]]:
    floor = max(not_before, utc_now() - timedelta(days=lookback_days)).isoformat()
    connection = open_read_only(str(db_path.resolve()), timeout=5)
    try:
        rows = connection.execute(
            f"""SELECT {LOSS_COLUMNS}
                FROM position_current
                WHERE phase = 'settled'
                  AND cost_basis_usd > 0
                  AND realized_pnl_usd <= -? * cost_basis_usd
                  AND settled_at >= ?
                ORDER BY settled_at, position_id""",
            (FULL_LOSS_RATIO, floor),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def bootstrap_workspace(
    workspace: Path,
    *,
    repo_root: Path,
    repair_worktree: Path | None,
    lookback_hours: float,
    model: str,
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o700)
    for rel in [
        "memory/incidents", "memory/root_causes", "runtime/incidents",
        "runtime/batches", "runs", "repairs/queue",
    ]:
        (workspace / rel).mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text(INVESTIGATOR_RULES)
    (workspace / "INVESTIGATOR_PROMPT.md").write_text(PROMPT_TEMPLATE)
    atomic_json(workspace / "output_schema.json", OUTPUT_SCHEMA)
    activation_path = workspace / "runtime" / "activation.json"
    activation = read_json(activation_path, None)
    if not isinstance(activation, dict):
        activation = {
            "schema_version": SCHEMA_VERSION,
            "created_at": iso_now(),
            "not_before": (utc_now() - timedelta(hours=lookback_hours)).isoformat(),
            "lookback_days": 7,
        }
        atomic_json(activation_path, activation)
    config = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo_root.resolve()),
        "repair_worktree": str(repair_worktree.resolve()) if repair_worktree else None,
        "trades_db": str((repo_root / "state" / "zeus_trades.db").resolve()),
        "codex_bin": DEFAULT_CODEX,
        "codex_home": str(isolated_codex_home(workspace)),
        "model": model,
        "reasoning_effort": "high",
        "poll_seconds": 15,
        "agent_timeout_seconds": 2700,
        "max_batch": MAX_BATCH,
    }
    atomic_json(workspace / "runtime" / "config.json", config)
    registry_path = workspace / "memory" / "root_causes" / "registry.json"
    if not registry_path.exists():
        atomic_json(registry_path, {"schema_version": SCHEMA_VERSION, "updated_at": iso_now(), "causes": {}})
    append_event(workspace, "BOOTSTRAPPED", not_before=activation["not_before"])
    return {"workspace": str(workspace), "activation": activation, "config": config}


def detect(workspace: Path, config: dict[str, Any]) -> list[str]:
    activation = read_json(workspace / "runtime" / "activation.json", {})
    if not activation.get("not_before"):
        raise RuntimeError("workspace is not bootstrapped")
    rows = scan_losses(
        Path(config["trades_db"]),
        not_before=parse_time(activation["not_before"]),
        lookback_days=int(activation.get("lookback_days", 7)),
    )
    reset_blocked_if_contract_changed(workspace, config)
    existing_identity: dict[tuple[str, str], str] = {}
    for path in (workspace / "runtime" / "incidents").glob("*.json"):
        prior = read_json(path, {})
        loss = prior.get("loss", {})
        if loss.get("position_id") and loss.get("settled_at"):
            existing_identity[(str(loss["position_id"]), str(loss["settled_at"]))] = str(prior["incident_id"])
    created: list[str] = []
    for row in rows:
        identity = (str(row["position_id"]), str(row["settled_at"]))
        ident = existing_identity.get(identity) or incident_id(row)
        path = workspace / "runtime" / "incidents" / f"{ident}.json"
        if path.exists():
            continue
        payload = {
            "schema_version": SCHEMA_VERSION,
            "incident_id": ident,
            "status": "pending",
            "detected_at": iso_now(),
            "attempts": 0,
            "loss": row,
        }
        atomic_json(path, payload)
        atomic_json(workspace / "memory" / "incidents" / ident / "incident.json", payload)
        append_event(
            workspace,
            "FULL_LOSS_DETECTED",
            incident_id=ident,
            position_id=row["position_id"],
            settled_at=row["settled_at"],
            cost_basis_usd=row["cost_basis_usd"],
            realized_pnl_usd=row["realized_pnl_usd"],
        )
        created.append(ident)
    return created


def runner_contract_fingerprint(config: dict[str, Any]) -> str:
    body = json.dumps(
        {
            "codex_bin": config.get("codex_bin"),
            "codex_home": config.get("codex_home"),
            "model": config.get("model"),
            "reasoning_effort": config.get("reasoning_effort"),
            "prompt": PROMPT_TEMPLATE,
            "schema": OUTPUT_SCHEMA,
        },
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def reset_blocked_if_contract_changed(workspace: Path, config: dict[str, Any]) -> list[str]:
    current = runner_contract_fingerprint(config)
    reset: list[str] = []
    for path in (workspace / "runtime" / "incidents").glob("*.json"):
        incident = read_json(path, {})
        if incident.get("status") != "runner_blocked" or incident.get("failure_contract") == current:
            continue
        incident["status"] = "pending"
        incident["same_failure_count"] = 0
        incident.pop("next_attempt_at", None)
        atomic_json(path, incident)
        reset.append(str(incident["incident_id"]))
    if reset:
        append_event(workspace, "RUNNER_CONTRACT_CHANGED", incident_ids=reset)
    return reset


def _pending_incidents(workspace: Path) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for path in (workspace / "runtime" / "incidents").glob("*.json"):
        row = read_json(path, {})
        due_at = row.get("next_attempt_at")
        due = not due_at or parse_time(str(due_at)) <= utc_now()
        if row.get("status") == "pending" and due:
            pending.append(row)
    return sorted(pending, key=lambda row: (row["loss"]["settled_at"], row["incident_id"]))


def _loaded_sha(repo_root: Path) -> str | None:
    payload = read_json(repo_root / "state" / "deployment_freshness.json", {})
    return payload.get("boot_sha") or payload.get("current_sha")


def daemon_code_identity(repo_root: Path) -> dict[str, str | None]:
    head = _git(repo_root, "rev-parse", "HEAD")
    script = Path(__file__).resolve()
    return {
        "repo_sha": head.stdout.strip() if head.returncode == 0 else None,
        "script_path": str(script),
        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
    }


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=15)


def repair_context(config: dict[str, Any]) -> tuple[Path, str, str]:
    live = Path(config["repo_root"]).resolve()
    raw = config.get("repair_worktree")
    if not raw:
        return live, "read_only", "repair_worktree_not_configured"
    candidate = Path(raw).resolve()
    if candidate == live:
        return live, "read_only", "repair_worktree_is_live"
    branch = _git(candidate, "branch", "--show-current")
    status = _git(candidate, "status", "--porcelain")
    if branch.returncode or status.returncode:
        return live, "read_only", "repair_worktree_not_valid_git"
    if not branch.stdout.strip() or branch.stdout.strip() == "live":
        return live, "read_only", "repair_worktree_not_nonlive_branch"
    if status.stdout.strip():
        return live, "read_only", "repair_worktree_dirty"
    return candidate, "workspace_write", "repair_worktree_clean"


def create_batch(workspace: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    if (workspace / "runtime" / "active.json").exists():
        return None
    pending = _pending_incidents(workspace)[: int(config.get("max_batch", MAX_BATCH))]
    if not pending:
        return None
    digest = hashlib.sha256("|".join(row["incident_id"] for row in pending).encode()).hexdigest()[:12]
    batch_id = f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{digest}"
    batch = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": iso_now(),
        "status": "created",
        "attempts": 0,
        "incident_ids": [row["incident_id"] for row in pending],
        "incidents": pending,
    }
    for row in pending:
        row["status"] = "batched"
        row["batch_id"] = batch_id
        atomic_json(workspace / "runtime" / "incidents" / f"{row['incident_id']}.json", row)
    atomic_json(workspace / "runtime" / "batches" / f"{batch_id}.json", batch)
    append_event(workspace, "BATCH_CREATED", batch_id=batch_id, incident_ids=batch["incident_ids"])
    return batch


def recover_orphan_batches(workspace: Path) -> list[str]:
    """Reset batches stranded between durable creation and process spawn."""
    if (workspace / "runtime" / "active.json").exists():
        return []
    recovered: list[str] = []
    for batch_path in (workspace / "runtime" / "batches").glob("*.json"):
        batch = read_json(batch_path, {})
        if batch.get("status") not in {"created", "running"}:
            continue
        for ident in batch.get("incident_ids", []):
            incident_path = workspace / "runtime" / "incidents" / f"{ident}.json"
            incident = read_json(incident_path, {})
            if incident.get("status") == "batched":
                incident["status"] = "pending"
                incident.pop("batch_id", None)
                atomic_json(incident_path, incident)
        batch["status"] = "orphan_recovered"
        batch["recovered_at"] = iso_now()
        atomic_json(batch_path, batch)
        recovered.append(str(batch.get("batch_id")))
    if recovered:
        append_event(workspace, "ORPHAN_BATCHES_RECOVERED", batch_ids=recovered)
    return recovered


def build_prompt(workspace: Path, config: dict[str, Any], batch: dict[str, Any], mode: str, repo: Path) -> str:
    registry = read_json(workspace / "memory" / "root_causes" / "registry.json", {"causes": {}})
    return PROMPT_TEMPLATE.format(
        repair_mode=mode,
        live_repo=config["repo_root"],
        analysis_repo=str(repo),
        loaded_sha=_loaded_sha(Path(config["repo_root"])) or "unknown",
        root_causes=json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True),
        batch=json.dumps(batch, ensure_ascii=False, indent=2, sort_keys=True),
    )


def start_batch(workspace: Path, config: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    repo, mode, gate = repair_context(config)
    run_dir = workspace / "runs" / batch["batch_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "prompt.md"
    result_path = run_dir / "last_message.json"
    log_path = run_dir / "codex.jsonl"
    prompt_path.write_text(build_prompt(workspace, config, batch, mode, repo))
    command = [
        config.get("codex_bin") or DEFAULT_CODEX,
        "exec", "--ephemeral", "--ignore-user-config",
        "--sandbox", "workspace-write" if mode == "workspace_write" else "read-only",
        "-C", str(repo), "--skip-git-repo-check",
        "-m", config.get("model", "gpt-5.5"),
        "-c", f'model_reasoning_effort="{config.get("reasoning_effort", "high")}"',
        "--output-schema", str(workspace / "output_schema.json"),
        "--output-last-message", str(result_path),
        "--json", "-",
    ]
    prompt_handle = prompt_path.open("rb")
    log_handle = log_path.open("wb")
    child_env = os.environ.copy()
    child_env["CODEX_HOME"] = config["codex_home"]
    try:
        child = subprocess.Popen(
            command,
            stdin=prompt_handle,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=child_env,
        )
    except Exception:
        for ident in batch["incident_ids"]:
            incident_path = workspace / "runtime" / "incidents" / f"{ident}.json"
            incident = read_json(incident_path, {})
            incident["status"] = "pending"
            incident.pop("batch_id", None)
            atomic_json(incident_path, incident)
        batch["status"] = "spawn_failed"
        atomic_json(workspace / "runtime" / "batches" / f"{batch['batch_id']}.json", batch)
        raise
    finally:
        prompt_handle.close()
        log_handle.close()
    active = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch["batch_id"],
        "pid": child.pid,
        "controller_pid": os.getpid(),
        "started_at": iso_now(),
        "run_dir": str(run_dir),
        "result_path": str(result_path),
        "analysis_repo": str(repo),
        "repair_mode": mode,
        "repair_gate": gate,
    }
    batch["status"] = "running"
    batch["attempts"] = int(batch.get("attempts", 0)) + 1
    atomic_json(workspace / "runtime" / "batches" / f"{batch['batch_id']}.json", batch)
    atomic_json(workspace / "runtime" / "active.json", active)
    append_event(workspace, "AGENT_STARTED", **active)
    return active


def _validate_result(result: Any, batch: dict[str, Any]) -> None:
    if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid result schema_version")
    if result.get("batch_id") != batch["batch_id"]:
        raise ValueError("result batch_id mismatch")
    got = [row.get("incident_id") for row in result.get("incidents", []) if isinstance(row, dict)]
    if len(got) != len(set(got)) or set(got) != set(batch["incident_ids"]):
        raise ValueError("result must cover each batch incident exactly once")
    for row in result.get("incidents", []):
        cause_id = row.get("root_cause_id")
        if cause_id is not None and not ROOT_CAUSE_ID.fullmatch(cause_id):
            raise ValueError(f"unsafe root_cause_id: {cause_id!r}")
    for row in result.get("root_cause_updates", []):
        cause_id = row.get("root_cause_id") if isinstance(row, dict) else None
        if cause_id is not None and not ROOT_CAUSE_ID.fullmatch(cause_id):
            raise ValueError(f"unsafe root_cause update id: {cause_id!r}")


def _failure_fingerprint(active: dict[str, Any], exc: Exception) -> str:
    log_path = Path(active["run_dir"]) / "codex.jsonl"
    try:
        tail = log_path.read_bytes()[-16_384:].decode(errors="replace")
    except OSError:
        tail = ""
    material = f"{type(exc).__name__}:{exc}\n{tail}"
    return hashlib.sha256(material.encode()).hexdigest()


def _release_runner_blocked(workspace: Path) -> list[str]:
    released: list[str] = []
    for path in (workspace / "runtime" / "incidents").glob("*.json"):
        incident = read_json(path, {})
        if incident.get("status") != "runner_blocked":
            continue
        incident["status"] = "pending"
        incident["same_failure_count"] = 0
        incident.pop("next_attempt_at", None)
        atomic_json(path, incident)
        released.append(str(incident["incident_id"]))
    if released:
        append_event(workspace, "RUNNER_RECOVERY_PROVEN", incident_ids=released)
    return released


def _update_registry(workspace: Path, result: dict[str, Any]) -> None:
    path = workspace / "memory" / "root_causes" / "registry.json"
    registry = read_json(path, {"schema_version": SCHEMA_VERSION, "causes": {}})
    causes = registry.setdefault("causes", {})
    counts = Counter(
        row.get("root_cause_id")
        for row in result.get("incidents", [])
        if isinstance(row, dict) and isinstance(row.get("root_cause_id"), str)
    )
    updates = {
        row["root_cause_id"]: row
        for row in result.get("root_cause_updates", [])
        if isinstance(row, dict) and isinstance(row.get("root_cause_id"), str)
    }
    for cause_id in sorted(set(counts) | set(updates)):
        update = updates.get(cause_id, {})
        previous = causes.get(cause_id, {})
        first_seen = previous.get("first_seen_at") or iso_now()
        merged = {**previous, **update, "root_cause_id": cause_id, "first_seen_at": first_seen, "last_seen_at": iso_now()}
        merged["occurrence_count"] = int(previous.get("occurrence_count", 0)) + counts[cause_id]
        incident_ids = list(previous.get("incident_ids", []))
        incident_ids.extend(
            row["incident_id"]
            for row in result.get("incidents", [])
            if row.get("root_cause_id") == cause_id and row.get("incident_id") not in incident_ids
        )
        merged["incident_ids"] = incident_ids[-200:]
        causes[cause_id] = merged
        atomic_json(workspace / "memory" / "root_causes" / f"{cause_id}.json", merged)
    registry["updated_at"] = iso_now()
    atomic_json(path, registry)


def complete_batch(workspace: Path, returncode: int | None = None) -> bool:
    active_path = workspace / "runtime" / "active.json"
    active = read_json(active_path, None)
    if not isinstance(active, dict):
        return False
    batch_path = workspace / "runtime" / "batches" / f"{active['batch_id']}.json"
    batch = read_json(batch_path, {})
    result_path = Path(active["result_path"])
    try:
        if returncode not in (None, 0):
            raise RuntimeError(f"codex exited {returncode}")
        result = json.loads(result_path.read_text())
        _validate_result(result, batch)
        _update_registry(workspace, result)
        atomic_json(Path(active["run_dir"]) / "validated_result.json", result)
        by_id = {row["incident_id"]: row for row in result["incidents"]}
        for ident in batch["incident_ids"]:
            incident_path = workspace / "runtime" / "incidents" / f"{ident}.json"
            incident = read_json(incident_path, {})
            memory_dir = workspace / "memory" / "incidents" / ident
            version = len(list(memory_dir.glob("analysis_v*.json"))) + 1
            atomic_json(memory_dir / f"analysis_v{version}.json", by_id[ident])
            incident["status"] = "repair_ready" if result["batch_status"] == "repair_ready" else "investigated"
            incident["last_analysis_at"] = iso_now()
            incident["classification"] = by_id[ident]["classification"]
            incident["root_cause_id"] = by_id[ident].get("root_cause_id")
            atomic_json(incident_path, incident)
        batch["status"] = result["batch_status"]
        batch["completed_at"] = iso_now()
        atomic_json(batch_path, batch)
        if result.get("batch_status") == "repair_ready":
            atomic_json(workspace / "repairs" / "queue" / f"{batch['batch_id']}.json", result)
        _release_runner_blocked(workspace)
        append_event(workspace, "AGENT_COMPLETED", batch_id=batch["batch_id"], status=result["batch_status"])
        active_path.unlink(missing_ok=True)
        return True
    except Exception as exc:
        fingerprint = _failure_fingerprint(active, exc)
        contract = runner_contract_fingerprint(read_json(workspace / "runtime" / "config.json", {}))
        batch["status"] = "retryable_failure"
        batch["last_error"] = f"{type(exc).__name__}: {exc}"
        batch["failure_fingerprint"] = fingerprint
        batch["last_failed_at"] = iso_now()
        atomic_json(batch_path, batch)
        for ident in batch.get("incident_ids", []):
            incident_path = workspace / "runtime" / "incidents" / f"{ident}.json"
            incident = read_json(incident_path, {})
            incident["attempts"] = int(incident.get("attempts", 0)) + 1
            same = int(incident.get("same_failure_count", 0)) + 1 if incident.get("failure_fingerprint") == fingerprint else 1
            incident["failure_fingerprint"] = fingerprint
            incident["failure_contract"] = contract
            incident["same_failure_count"] = same
            if same >= 3:
                incident["status"] = "runner_blocked"
                incident.pop("next_attempt_at", None)
                batch["status"] = "runner_blocked"
            else:
                incident["status"] = "pending"
                delay = min(3600, 60 * (2 ** min(incident["attempts"] - 1, 6)))
                incident["next_attempt_at"] = (utc_now() + timedelta(seconds=delay)).isoformat()
            atomic_json(incident_path, incident)
        append_event(workspace, "AGENT_FAILED", batch_id=batch.get("batch_id"), error=batch["last_error"])
        active_path.unlink(missing_ok=True)
        return False


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def poll_child(pid: int) -> int | None:
    """Return an exit code for our child, otherwise None while it is alive."""
    try:
        waited, status_code = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return None if process_alive(pid) else 1
    if waited == 0:
        return None
    return os.waitstatus_to_exitcode(status_code)


def terminate_process_group(pid: int, grace_seconds: float = 5.0) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        code = poll_child(pid)
        if code is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def status(workspace: Path) -> dict[str, Any]:
    incidents = [read_json(path, {}) for path in (workspace / "runtime" / "incidents").glob("*.json")]
    counts: dict[str, int] = {}
    for incident in incidents:
        key = str(incident.get("status", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    active = read_json(workspace / "runtime" / "active.json", None)
    if isinstance(active, dict):
        active["process_alive"] = process_alive(int(active["pid"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(workspace),
        "heartbeat": read_json(workspace / "runtime" / "heartbeat.json", None),
        "active": active,
        "incidents": counts,
        "root_causes": len(read_json(workspace / "memory" / "root_causes" / "registry.json", {"causes": {}}).get("causes", {})),
    }


def record_repair(
    workspace: Path,
    *,
    root_cause_id: str,
    repair_status: str,
    fix_sha: str | None,
    evidence: list[str],
    antibodies: list[str],
) -> dict[str, Any]:
    if not ROOT_CAUSE_ID.fullmatch(root_cause_id):
        raise ValueError("unsafe root_cause_id")
    if repair_status in {"landed", "live_verified"} and not fix_sha:
        raise ValueError(f"{repair_status} requires --fix-sha")
    if repair_status == "live_verified" and not evidence:
        raise ValueError("live_verified requires at least one --evidence proof")
    if repair_status == "live_verified":
        evidence = [loaded_fix_proof(workspace, str(fix_sha)), *evidence]
    registry_path = workspace / "memory" / "root_causes" / "registry.json"
    registry = read_json(registry_path, {"schema_version": SCHEMA_VERSION, "causes": {}})
    causes = registry.setdefault("causes", {})
    if root_cause_id not in causes:
        raise ValueError(f"unknown root cause: {root_cause_id}")
    cause = causes[root_cause_id]
    transition = {
        "at": iso_now(),
        "status": repair_status,
        "fix_sha": fix_sha,
        "evidence": evidence,
        "antibodies": antibodies,
    }
    cause["repair_status"] = repair_status
    cause["fix_sha"] = fix_sha or cause.get("fix_sha")
    cause["repair_evidence"] = evidence
    cause["antibody_tests"] = antibodies
    cause.setdefault("repair_transitions", []).append(transition)
    cause["last_updated_at"] = transition["at"]
    causes[root_cause_id] = cause
    registry["updated_at"] = transition["at"]
    atomic_json(registry_path, registry)
    atomic_json(workspace / "memory" / "root_causes" / f"{root_cause_id}.json", cause)
    append_event(workspace, "REPAIR_STATUS_RECORDED", root_cause_id=root_cause_id, **transition)
    return cause


def loaded_fix_proof(workspace: Path, fix_sha: str) -> str:
    config = read_json(workspace / "runtime" / "config.json", {})
    repo = Path(config.get("repo_root", ""))
    freshness = read_json(repo / "state" / "deployment_freshness.json", {})
    loaded = freshness.get("boot_sha")
    if freshness.get("status") != "fresh" or freshness.get("code_plane_status") != "same_sha" or not loaded:
        raise ValueError("live_verified requires current fresh same-SHA deployment evidence")
    ancestry = _git(repo, "merge-base", "--is-ancestor", fix_sha, loaded)
    if ancestry.returncode != 0:
        raise ValueError(f"fix {fix_sha} is not loaded at {loaded}")
    return f"deployment_freshness.boot_sha={loaded}; includes_fix={fix_sha}"


def run_daemon(workspace: Path) -> int:
    config = read_json(workspace / "runtime" / "config.json", None)
    if not isinstance(config, dict):
        raise RuntimeError("run bootstrap before daemon")
    lock_path = workspace / "runtime" / "loop.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        append_event(workspace, "DAEMON_ALREADY_RUNNING")
        return 75
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    code_identity = daemon_code_identity(Path(config["repo_root"]))
    append_event(workspace, "DAEMON_STARTED", pid=os.getpid(), **code_identity)
    recover_orphan_batches(workspace)
    while not stopping:
        try:
            new_ids = detect(workspace, config)
            active = read_json(workspace / "runtime" / "active.json", None)
            if isinstance(active, dict):
                pid = int(active["pid"])
                started = parse_time(active["started_at"])
                exit_code = poll_child(pid)
                if exit_code is not None:
                    if (
                        int(active.get("controller_pid", -1)) != os.getpid()
                        and Path(active["result_path"]).exists()
                    ):
                        exit_code = 0
                    complete_batch(workspace, exit_code)
                elif (utc_now() - started).total_seconds() > int(config.get("agent_timeout_seconds", 2700)):
                    terminate_process_group(pid)
                    complete_batch(workspace, 124)
            else:
                batch = create_batch(workspace, config)
                if batch:
                    start_batch(workspace, config, batch)
            atomic_json(
                workspace / "runtime" / "heartbeat.json",
                {
                    "alive": True,
                    "pid": os.getpid(),
                    "at": iso_now(),
                    "new_incidents": new_ids,
                    **code_identity,
                },
            )
        except Exception as exc:
            append_event(workspace, "DAEMON_CYCLE_FAILED", error=f"{type(exc).__name__}: {exc}")
        time.sleep(max(5, int(config.get("poll_seconds", 15))))
    atomic_json(
        workspace / "runtime" / "heartbeat.json",
        {"alive": False, "pid": os.getpid(), "at": iso_now(), **code_identity},
    )
    append_event(workspace, "DAEMON_STOPPED", pid=os.getpid())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("ZEUS_FULL_LOSS_WORKSPACE", DEFAULT_WORKSPACE)))
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--repo-root", type=Path, default=PROJECT_ROOT)
    bootstrap.add_argument("--repair-worktree", type=Path)
    bootstrap.add_argument("--lookback-hours", type=float, default=48.0)
    bootstrap.add_argument("--model", default=os.environ.get("ZEUS_FULL_LOSS_MODEL", "gpt-5.5"))
    sub.add_parser("scan-once")
    sub.add_parser("daemon")
    sub.add_parser("status")
    repair = sub.add_parser("record-repair")
    repair.add_argument("--root-cause-id", required=True)
    repair.add_argument("--repair-status", required=True, choices=["proposed", "tested", "landed", "live_verified", "reopened"])
    repair.add_argument("--fix-sha")
    repair.add_argument("--evidence", action="append", default=[])
    repair.add_argument("--antibody", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.expanduser().resolve()
    if args.command == "bootstrap":
        print(json.dumps(bootstrap_workspace(
            workspace,
            repo_root=args.repo_root,
            repair_worktree=args.repair_worktree,
            lookback_hours=args.lookback_hours,
            model=args.model,
        ), ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(status(workspace), ensure_ascii=False, indent=2))
        return 0
    if args.command == "record-repair":
        print(json.dumps(record_repair(
            workspace,
            root_cause_id=args.root_cause_id,
            repair_status=args.repair_status,
            fix_sha=args.fix_sha,
            evidence=args.evidence,
            antibodies=args.antibody,
        ), ensure_ascii=False, indent=2))
        return 0
    config = read_json(workspace / "runtime" / "config.json", None)
    if not isinstance(config, dict):
        raise SystemExit("run bootstrap first")
    if args.command == "scan-once":
        created = detect(workspace, config)
        print(json.dumps({"created": created, "status": status(workspace)}, ensure_ascii=False, indent=2))
        return 0
    return run_daemon(workspace)


if __name__ == "__main__":
    raise SystemExit(main())
