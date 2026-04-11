from __future__ import annotations

from pathlib import Path

from _yaml_bootstrap import import_yaml

yaml = import_yaml()

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "architecture_advisory_gates.yml"

REQUIRED_BLOCKING_JOBS = {
    "advisory-gate-policy",
    "architecture-manifests",
    "module-boundaries",
    "packet-grammar",
    "kernel-invariants",
}

REQUIRED_ADVISORY_JOBS = {
    "semgrep-zeus",
    "replay-parity",
}

REQUIRED_TRIGGER_PATHS = {
    "AGENTS.md",
    ".github/workflows/**",
    "scripts/_yaml_bootstrap.py",
    "scripts/check_*",
    "scripts/replay_parity.py",
    "tests/test_architecture_contracts.py",
    "tests/test_cross_module_invariants.py",
    "docs/work_packets/**",
}

REQUIRED_ENV_KEYS = {
    "GATE_OWNER",
    "GATE_RATIONALE",
    "GATE_REVIEW_CONDITION",
}

FORBIDDEN_EXTERNAL_REFERENCES = (
    "scripts/audit_architecture_alignment.py",
    "docs/authority/zeus_openclaw_venus_delivery_boundary.md",
)


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def workflow_triggers(data: dict) -> dict:
    return data.get("on") or data.get(True) or {}


def ensure_paths(data: dict, errors: list[str]) -> None:
    triggers = workflow_triggers(data)
    for trigger in ("pull_request", "push"):
        paths = set(triggers.get(trigger, {}).get("paths", []))
        missing = sorted(REQUIRED_TRIGGER_PATHS - paths)
        if missing:
            errors.append(f"{trigger}: missing trigger paths {missing}")


def ensure_jobs(data: dict, errors: list[str]) -> None:
    jobs = data.get("jobs", {})

    for job_name in sorted(REQUIRED_BLOCKING_JOBS):
        if job_name not in jobs:
            errors.append(f"missing blocking job: {job_name}")
            continue
        if jobs[job_name].get("continue-on-error") is True:
            errors.append(f"blocking job must not be advisory: {job_name}")

    for job_name in sorted(REQUIRED_ADVISORY_JOBS):
        if job_name not in jobs:
            errors.append(f"missing advisory job: {job_name}")
            continue
        if jobs[job_name].get("continue-on-error") is not True:
            errors.append(f"advisory job must continue-on-error: {job_name}")

    for job_name in sorted(REQUIRED_BLOCKING_JOBS | REQUIRED_ADVISORY_JOBS):
        if job_name not in jobs:
            continue
        env = jobs[job_name].get("env", {})
        missing = sorted(key for key in REQUIRED_ENV_KEYS if not env.get(key))
        if missing:
            errors.append(f"{job_name}: missing env keys {missing}")


def ensure_semgrep_and_replay_are_advisory(data: dict, errors: list[str]) -> None:
    jobs = data.get("jobs", {})

    semgrep_steps = "\n".join(
        step.get("run", "")
        for step in jobs.get("semgrep-zeus", {}).get("steps", [])
        if isinstance(step, dict)
    )
    if "semgrep --config architecture/ast_rules/semgrep_zeus.yml" not in semgrep_steps:
        errors.append("semgrep-zeus: expected advisory scan command missing")
    if "--severity ERROR" not in semgrep_steps:
        errors.append("semgrep-zeus: expected severity pin missing")
    if " src" not in semgrep_steps and "\nsrc" not in semgrep_steps:
        errors.append("semgrep-zeus: expected src target missing")

    replay_steps = "\n".join(
        step.get("run", "")
        for step in jobs.get("replay-parity", {}).get("steps", [])
        if isinstance(step, dict)
    )
    if (
        "python scripts/replay_parity.py" not in replay_steps
        or "--ci" not in replay_steps
    ):
        errors.append("replay-parity: expected advisory replay command missing")

    semgrep_review = (
        jobs.get("semgrep-zeus", {}).get("env", {}).get("GATE_REVIEW_CONDITION", "")
    )
    replay_review = (
        jobs.get("replay-parity", {}).get("env", {}).get("GATE_REVIEW_CONDITION", "")
    )
    if "Promote only after" not in semgrep_review:
        errors.append("semgrep-zeus: promotion condition must stay explicit")
    if "Promote only after" not in replay_review:
        errors.append("replay-parity: promotion condition must stay explicit")


def ensure_no_external_blocking_references(data: dict, errors: list[str]) -> None:
    rendered = WORKFLOW_PATH.read_text()
    for forbidden in FORBIDDEN_EXTERNAL_REFERENCES:
        if forbidden in rendered:
            errors.append(
                f"workflow must not hard-wire external-boundary advisory surface: {forbidden}"
            )


def main() -> int:
    if not WORKFLOW_PATH.exists():
        print(f"missing workflow: {WORKFLOW_PATH}")
        return 1

    data = load_workflow()
    errors: list[str] = []

    ensure_paths(data, errors)
    ensure_jobs(data, errors)
    ensure_semgrep_and_replay_are_advisory(data, errors)
    ensure_no_external_blocking_references(data, errors)

    if errors:
        print("\n".join(errors))
        return 1

    print("advisory gate policy ok")
    print("policy verdict only; advisory jobs still require separate evidence review")
    print(f"blocking jobs={sorted(REQUIRED_BLOCKING_JOBS)}")
    print(f"advisory jobs={sorted(REQUIRED_ADVISORY_JOBS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
