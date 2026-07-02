# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: live-money deployment freshness false-positive after test-only HEAD drift.
"""Runtime-code-plane diff helper for live deployment freshness gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


RUNTIME_CODE_PREFIXES = (
    "src/",
    "scripts/",
    "config/",
    "architecture/",
    ".github/workflows/",
    "launchd/",
)
RUNTIME_CODE_FILES = frozenset(
    {
        "pyproject.toml",
        "poetry.lock",
        "requirements.txt",
        "requirements-dev.txt",
        "uv.lock",
        "setup.py",
        "setup.cfg",
        "Makefile",
        "Dockerfile",
    }
)


@dataclass(frozen=True)
class RuntimeCodePlaneDiff:
    boot_sha: str
    current_sha: str
    changed_paths: tuple[str, ...]
    runtime_code_changed: bool
    status: str
    error: str | None = None

    @property
    def sha_changed(self) -> bool:
        return bool(self.boot_sha and self.current_sha and self.boot_sha != self.current_sha)


def current_git_head(repo_root: Path, *, timeout: float = 2.0) -> str | None:
    """Return current git HEAD for repo_root, or None when unavailable."""

    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            timeout=timeout,
            stderr=subprocess.DEVNULL,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ):
        return None
    head = out.strip().decode()
    return head or None


def runtime_code_plane_diff(
    repo_root: Path,
    *,
    boot_sha: str,
    current_sha: str | None = None,
    timeout: float = 2.0,
) -> RuntimeCodePlaneDiff:
    """Classify whether a boot/current SHA diff touches executable runtime code.

    A live daemon must restart for changed executable code, configuration, or
    runtime manifests. Test/docs-only commits do not change the running code
    plane and must not auto-pause live money.
    """

    repo = Path(repo_root)
    boot = str(boot_sha or "").strip()
    current = str(current_sha or "").strip() or (current_git_head(repo, timeout=timeout) or "")
    if not current:
        return RuntimeCodePlaneDiff(
            boot_sha=boot,
            current_sha="",
            changed_paths=(),
            runtime_code_changed=True,
            status="current_git_head_unreadable",
            error="current_git_head_unreadable",
        )
    if not boot:
        return RuntimeCodePlaneDiff(
            boot_sha="",
            current_sha=current,
            changed_paths=(),
            runtime_code_changed=True,
            status="boot_sha_missing",
            error="boot_sha_missing",
        )
    if current == boot:
        return RuntimeCodePlaneDiff(
            boot_sha=boot,
            current_sha=current,
            changed_paths=(),
            runtime_code_changed=False,
            status="same_sha",
        )
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", boot, current, "--"],
            cwd=str(repo),
            timeout=timeout,
            stderr=subprocess.DEVNULL,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ) as exc:
        return RuntimeCodePlaneDiff(
            boot_sha=boot,
            current_sha=current,
            changed_paths=(),
            runtime_code_changed=True,
            status="diff_unreadable",
            error=type(exc).__name__,
        )
    changed_paths = tuple(
        path.strip().replace("\\", "/")
        for path in raw.decode().splitlines()
        if path.strip()
    )
    runtime_changed = any(_is_runtime_code_path(path) for path in changed_paths)
    return RuntimeCodePlaneDiff(
        boot_sha=boot,
        current_sha=current,
        changed_paths=changed_paths,
        runtime_code_changed=runtime_changed,
        status="runtime_diff" if runtime_changed else "non_runtime_diff",
    )


def _is_runtime_code_path(path: str) -> bool:
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return False
    if text in RUNTIME_CODE_FILES:
        return True
    return any(text.startswith(prefix) for prefix in RUNTIME_CODE_PREFIXES)
