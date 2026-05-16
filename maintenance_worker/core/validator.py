# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/validator.py + §4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Validator Semantics" + §"Forbidden Targets" + §"Pre-Action Validator"
"""
validator — ActionValidator.validate_action(path, operation, context, manifest) -> ValidatorResult

Implements all 5 SAFETY_CONTRACT.md Validator Semantics guarantees:
  (a) READ is not exempt — credential reads, state/*.db*, authority surfaces
      return FORBIDDEN_PATH.
  (b) Canonicalize via realpath before match — os.path.realpath() / Path.resolve()
      before any pattern match.
  (c) Symlink and hardlink resolution — resolve() before pattern match; symlinks
      whose target escapes the allowed-write set are FORBIDDEN_PATH.
  (d) Per-leaf decomposition — directory operations decomposed to per-file checks;
      single leaf failure aborts entire directory operation.
  (e) Git remote URL allowlist — before any git push, remote URL checked against
      install_metadata.allowed_remote_urls; mismatch → FORBIDDEN_OPERATION.

SEV-2 #4 structural fix (carry-forward from P5.1):
  validate_action(path, Operation.WRITE, context, manifest=None):
    If path.exists() AND (manifest is None OR path not in manifest.proposed_modifies):
      return FORBIDDEN_PATH with reason "in-place write outside manifest"
  This structurally forbids in-place WRITE mutations that post_mutation_detector
  cannot catch (it only sees move/delete/create, not in-place edits).

Path A invariant (SCAFFOLD §3 lines 190-195):
  validate_action() returning FORBIDDEN_* → caller calls refuse_fatal().
  validate_action() NEVER calls write_self_quarantine() (Path B only).
  Tested explicitly in test_validator.py.

Dry-run floor:
  enforce_dry_run_floor() is called for every non-exempt task via
  validate_action_with_floor() convenience wrapper. Base validate_action()
  does not call the floor check — callers integrate it separately for
  operation-level checks vs task-level floor checks.

Forbidden Targets (SAFETY_CONTRACT.md §"Forbidden Targets"):
  6 named groups implemented as ordered pattern list:
    Group 1: Source code and tests (src/**, tests/**, scripts/**, bin/**, *.py, *.ts …)
    Group 2: Authority surfaces (architecture/**, docs/reference/**, AGENTS.md, CLAUDE.md …)
    Group 3: Runtime / state (state/*.db*, state/calibration/**, LaunchAgents/*.plist …)
    Group 4: Secrets and credentials (*.env, *secret*, *token*, *.pem, ~/.ssh/**, …)
    Group 5: Git plumbing (.git/**, .gitmodules, .gitattributes, .gitignore)
    Group 6: External system surfaces (/etc/**, ~/Library/LaunchDaemons/**, crontab)

Operation enum coverage:
  READ, WRITE, MKDIR, MOVE, DELETE — handled by path-pattern checks.
  GIT_EXEC, GH_EXEC, SUBPROCESS_EXEC — guard delegation; validator returns
  MISSING_PRECHECK for these to signal the caller must use the specialized guard.

Stdlib only. Imports only from maintenance_worker.types.* and
maintenance_worker.core.install_metadata.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from maintenance_worker.core.install_metadata import (
    FLOOR_EXEMPT_TASK_IDS,
    DryRunFloor,
    InstallMetadata,
    enforce_dry_run_floor,
)
from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ValidatorResult
from maintenance_worker.types.specs import ProposalManifest, TickContext


# ---------------------------------------------------------------------------
# Forbidden pattern definitions (SAFETY_CONTRACT.md §"Forbidden Targets")
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForbiddenRule:
    """
    A single forbidden-target rule.

    pattern: fnmatch-compatible glob OR prefix string (if prefix=True).
    group: named group from SAFETY_CONTRACT.md (1–6).
    description: human-readable rule description.
    prefix: if True, match as prefix check instead of fnmatch glob.
    exact_name: if True, match the basename only (any depth).
    """
    pattern: str
    group: str
    description: str
    prefix: bool = False
    exact_name: bool = False


# ---------------------------------------------------------------------------
# Forbidden rules source selection
# ---------------------------------------------------------------------------
# MW_FORBIDDEN_RULES_FROM_CODE=1 → use hardcoded list below (transition safety).
# Default (unset or 0) → load from bindings/ via forbidden_rules_loader.
# BINDINGS_DIR env var overrides the default bindings path resolution.
# Loader is called lazily inside _get_active_rules() to avoid import-time
# circular-dependency (loader → validator → loader).
# ---------------------------------------------------------------------------

import logging as _logging
_validator_logger = _logging.getLogger(__name__)


def _get_active_rules(bindings_dir: "Optional[Path]" = None) -> "list[ForbiddenRule]":
    """
    Return the active list of ForbiddenRules.

    If MW_FORBIDDEN_RULES_FROM_CODE=1: return hardcoded _FORBIDDEN_RULES.
    Otherwise: delegate to forbidden_rules_loader.load_forbidden_rules().

    bindings_dir: explicit override; if None, resolved from BINDINGS_DIR env
                  var or from the file's own location (../../bindings relative
                  to this module).
    """
    if os.environ.get("MW_FORBIDDEN_RULES_FROM_CODE", "").strip() == "1":
        return _FORBIDDEN_RULES

    # Resolve bindings directory
    if bindings_dir is None:
        env_dir = os.environ.get("BINDINGS_DIR", "").strip()
        if env_dir:
            bindings_dir = Path(env_dir)
        else:
            # Default: bindings/ is two levels up from this file
            # (maintenance_worker/core/validator.py → maintenance_worker/ → repo_root/bindings/)
            _here = Path(__file__).resolve().parent  # .../maintenance_worker/core/
            bindings_dir = _here.parent.parent / "bindings"

    try:
        from maintenance_worker.core.forbidden_rules_loader import (  # noqa: PLC0415
            load_forbidden_rules,
            ConfigurationError,
        )
        return load_forbidden_rules(str(bindings_dir))
    except Exception as exc:
        _validator_logger.warning(
            "validator: forbidden_rules_loader failed (%s); falling back to "
            "hardcoded rules. Set MW_FORBIDDEN_RULES_FROM_CODE=1 to suppress.",
            exc,
        )
        return _FORBIDDEN_RULES


# Source code and tests (Group 1)
_GROUP_SOURCE = "source_code_and_tests"

# Authority surfaces (Group 2)
_GROUP_AUTHORITY = "authority_surfaces"

# Runtime / state (Group 3)
_GROUP_RUNTIME = "runtime_and_state"

# Secrets and credentials (Group 4)
_GROUP_SECRETS = "secrets_and_credentials"

# Git plumbing (Group 5)
_GROUP_GIT = "git_plumbing"

# External system surfaces (Group 6)
_GROUP_EXTERNAL = "external_system_surfaces"


# Ordered list: checked in order; first match wins.
# All paths are matched against their CANONICAL absolute form.
_FORBIDDEN_RULES: list[ForbiddenRule] = [
    # ── Group 1: Source code and tests ────────────────────────────────────
    ForbiddenRule("*/src/*", _GROUP_SOURCE, "source tree src/**", prefix=False),
    ForbiddenRule("*/src", _GROUP_SOURCE, "source tree src/ root", prefix=False),
    ForbiddenRule("*/tests/*", _GROUP_SOURCE, "test tree tests/**", prefix=False),
    ForbiddenRule("*/tests", _GROUP_SOURCE, "test tree tests/ root", prefix=False),
    ForbiddenRule("*/scripts/*", _GROUP_SOURCE, "scripts tree scripts/**", prefix=False),
    ForbiddenRule("*/scripts", _GROUP_SOURCE, "scripts tree scripts/ root", prefix=False),
    ForbiddenRule("*/bin/*", _GROUP_SOURCE, "bin tree bin/**", prefix=False),
    ForbiddenRule("*/bin", _GROUP_SOURCE, "bin tree bin/ root", prefix=False),
    # Source file extensions outside docs/operations/archive/** and STATE_DIR/**
    # (Extension check is handled via _is_source_extension() logic below)

    # ── Group 2: Authority surfaces ───────────────────────────────────────
    ForbiddenRule("*/architecture/*", _GROUP_AUTHORITY, "architecture/**", prefix=False),
    ForbiddenRule("*/architecture", _GROUP_AUTHORITY, "architecture/ root", prefix=False),
    ForbiddenRule("*/docs/reference/*", _GROUP_AUTHORITY, "docs/reference/**", prefix=False),
    ForbiddenRule("*/docs/reference", _GROUP_AUTHORITY, "docs/reference/ root", prefix=False),
    ForbiddenRule("AGENTS.md", _GROUP_AUTHORITY, "AGENTS.md at any depth", exact_name=True),
    ForbiddenRule("CLAUDE.md", _GROUP_AUTHORITY, "CLAUDE.md at any depth", exact_name=True),
    ForbiddenRule("*/.claude/CLAUDE.md", _GROUP_AUTHORITY, ".claude/CLAUDE.md"),
    ForbiddenRule("*/.claude/settings.json", _GROUP_AUTHORITY, ".claude/settings.json"),
    ForbiddenRule("*/.claude/agents/*", _GROUP_AUTHORITY, ".claude/agents/**"),
    ForbiddenRule("*/.claude/skills/*", _GROUP_AUTHORITY, ".claude/skills/**"),
    ForbiddenRule("*/.claude/hooks/*", _GROUP_AUTHORITY, ".claude/hooks/**"),
    ForbiddenRule("*/.codex/hooks.json", _GROUP_AUTHORITY, ".codex/hooks.json"),
    ForbiddenRule("*/.codex/hooks/*", _GROUP_AUTHORITY, ".codex/hooks/**"),
    ForbiddenRule("*/.openclaw/*", _GROUP_AUTHORITY, ".openclaw/** (except own cron job entry)"),

    # ── Group 3: Runtime / state ──────────────────────────────────────────
    ForbiddenRule("*/state/*.db", _GROUP_RUNTIME, "state/*.db"),
    ForbiddenRule("*/state/*.db-wal", _GROUP_RUNTIME, "state/*.db-wal"),
    ForbiddenRule("*/state/*.db-shm", _GROUP_RUNTIME, "state/*.db-shm"),
    ForbiddenRule("*/state/*.sqlite*", _GROUP_RUNTIME, "state/*.sqlite*"),
    ForbiddenRule("*/state/calibration/*", _GROUP_RUNTIME, "state/calibration/**"),
    ForbiddenRule("*/state/forecasts/*", _GROUP_RUNTIME, "state/forecasts/**"),
    ForbiddenRule("*/state/world/*", _GROUP_RUNTIME, "state/world/**"),
    # LaunchAgents active plists (backups allowed but active plists are forbidden)
    # Pattern: ~/Library/LaunchAgents/*.plist but NOT ~/Library/LaunchAgents/.archive/**
    # Handled via _is_active_launch_agent_plist() function below

    # ── Group 4: Secrets and credentials ─────────────────────────────────
    ForbiddenRule("*.env", _GROUP_SECRETS, "*.env files"),
    ForbiddenRule("*.env.*", _GROUP_SECRETS, ".env.* files"),
    ForbiddenRule(".env*", _GROUP_SECRETS, ".env* dotfiles"),
    ForbiddenRule("*credential*", _GROUP_SECRETS, "*credential* files"),
    ForbiddenRule("*secret*", _GROUP_SECRETS, "*secret* files"),
    ForbiddenRule("*token*", _GROUP_SECRETS, "*token* files"),
    ForbiddenRule("*key*", _GROUP_SECRETS, "*key* files"),
    ForbiddenRule("*authn*", _GROUP_SECRETS, "*authn* files"),
    ForbiddenRule("*oauth*", _GROUP_SECRETS, "*oauth* files"),
    ForbiddenRule("*.pem", _GROUP_SECRETS, "*.pem certificate files"),
    ForbiddenRule("*.p12", _GROUP_SECRETS, "*.p12 certificate files"),
    ForbiddenRule("*.pfx", _GROUP_SECRETS, "*.pfx certificate files"),
    # Well-known credential directories (prefix checks via _is_credential_dir)
    ForbiddenRule("*auth-profiles.json", _GROUP_SECRETS, "auth-profiles.json files"),

    # ── Group 5: Git plumbing ─────────────────────────────────────────────
    ForbiddenRule("*/.git/*", _GROUP_GIT, ".git/**"),
    ForbiddenRule("*/.git", _GROUP_GIT, ".git directory"),
    ForbiddenRule("*/.gitmodules", _GROUP_GIT, ".gitmodules"),
    ForbiddenRule("*/.gitattributes", _GROUP_GIT, ".gitattributes"),
    ForbiddenRule("*/.gitignore", _GROUP_GIT, ".gitignore"),

    # ── Group 6: External system surfaces ────────────────────────────────
    # /etc — also matches /private/etc on macOS (where /etc → /private/etc symlink)
    ForbiddenRule("/etc/*", _GROUP_EXTERNAL, "/etc/**"),
    ForbiddenRule("/etc", _GROUP_EXTERNAL, "/etc root", prefix=True),
    ForbiddenRule("/private/etc", _GROUP_EXTERNAL, "/private/etc root (macOS)", prefix=True),
    ForbiddenRule("/usr/local/etc/*", _GROUP_EXTERNAL, "/usr/local/etc/**"),
    ForbiddenRule("/usr/local/etc", _GROUP_EXTERNAL, "/usr/local/etc root", prefix=True),
    ForbiddenRule("/private/usr/local/etc", _GROUP_EXTERNAL, "/private/usr/local/etc (macOS)", prefix=True),
    ForbiddenRule("*/Library/LaunchDaemons/*", _GROUP_EXTERNAL, "~/Library/LaunchDaemons/**"),
]

# Source code file extensions forbidden outside docs/operations/archive/** and STATE_DIR/**
_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".ts", ".rs", ".go", ".c", ".cpp", ".swift"}
)

# Known credential home directories (any path starting with these is forbidden).
_CREDENTIAL_HOME_DIRS: tuple[str, ...] = (
    os.path.expanduser("~/.aws"),
    os.path.expanduser("~/.gcloud"),
    os.path.expanduser("~/.ssh"),
    os.path.expanduser("~/.config/gcloud"),
    os.path.expanduser("~/.config/op"),
)

# LaunchAgents base dir
_LAUNCH_AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
_LAUNCH_AGENTS_ARCHIVE = os.path.join(_LAUNCH_AGENTS_DIR, ".archive")


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------


def _canonicalize(path: Path) -> Path:
    """
    Guarantee (b): resolve path to canonical absolute form.

    Uses Path.resolve(strict=False) so non-existent paths still have ..
    collapsed and symlinks (in existing segments) expanded.
    """
    return path.resolve()


def _is_source_extension(path: Path, state_dir: Optional[Path] = None) -> bool:
    """
    Return True if path has a source-code extension AND is not in an exempt location.

    Exempt locations (SAFETY_CONTRACT §"Forbidden Targets" Group 1):
      - docs/operations/archive/**
      - ${STATE_DIR}/**
    """
    if path.suffix.lower() not in _SOURCE_EXTENSIONS:
        return False
    path_str = str(path)
    # Exempt: docs/operations/archive/
    if "/docs/operations/archive/" in path_str:
        return False
    # Exempt: STATE_DIR
    if state_dir is not None:
        state_str = str(state_dir)
        if path_str.startswith(state_str):
            return False
    return True


def _is_credential_dir_path(path: Path) -> bool:
    """Return True if path is under a known credential home directory."""
    path_str = str(path)
    for cred_dir in _CREDENTIAL_HOME_DIRS:
        if path_str == cred_dir or path_str.startswith(cred_dir + os.sep):
            return True
    return False


def _is_active_launch_agent_plist(path: Path) -> bool:
    """
    Return True if path is an active LaunchAgents plist (not in .archive/).

    SAFETY_CONTRACT Group 3: ~/Library/LaunchAgents/*.plist is forbidden.
    Exception: ~/Library/LaunchAgents/.archive/** is allowed (backup target).
    """
    path_str = str(path)
    if not path_str.startswith(_LAUNCH_AGENTS_DIR):
        return False
    # If it's under .archive/, it's allowed
    if path_str.startswith(_LAUNCH_AGENTS_ARCHIVE):
        return False
    # Any .plist directly under LaunchAgents (not in .archive/) is forbidden
    if path_str.endswith(".plist"):
        return True
    return False


def _has_private_key_bytes(path: Path) -> bool:
    """
    Return True if the first 200 bytes of path contain 'BEGIN .* PRIVATE KEY'.

    SAFETY_CONTRACT Group 4: "Any file containing BEGIN .* PRIVATE KEY in its
    first 200 bytes". Only checked if path exists and is readable.
    """
    if not path.exists() or not path.is_file():
        return False
    try:
        data = path.read_bytes()[:200]
        text = data.decode("utf-8", errors="replace")
        return "BEGIN" in text and "PRIVATE KEY" in text
    except OSError:
        return False


def _match_forbidden_rules(path: Path, state_dir: Optional[Path] = None) -> Optional[ForbiddenRule]:
    """
    Check canonical path against all forbidden rules.

    Returns the first matching ForbiddenRule, or None if no match.
    Uses _get_active_rules() which honours MW_FORBIDDEN_RULES_FROM_CODE env var.
    """
    path_str = str(path)
    basename = path.name

    for rule in _get_active_rules():
        if rule.exact_name:
            if basename == rule.pattern:
                return rule
        elif rule.prefix:
            # Prefix check: path == pattern OR path starts with pattern + sep
            if path_str == rule.pattern or path_str.startswith(rule.pattern + os.sep):
                return rule
        elif fnmatch.fnmatch(path_str, rule.pattern):
            return rule

    # Extension check (source files outside exempt dirs)
    if _is_source_extension(path, state_dir):
        return ForbiddenRule(
            path.suffix,
            _GROUP_SOURCE,
            f"source file extension {path.suffix} outside archive/state",
        )

    # Credential home directory check
    if _is_credential_dir_path(path):
        return ForbiddenRule(
            str(path),
            _GROUP_SECRETS,
            "path under credential home directory",
            prefix=True,
        )

    # Active LaunchAgents plist check
    if _is_active_launch_agent_plist(path):
        return ForbiddenRule(
            str(path),
            _GROUP_RUNTIME,
            "active LaunchAgents plist",
        )

    # Private key bytes check (expensive — only for existing files)
    if _has_private_key_bytes(path):
        return ForbiddenRule(
            str(path),
            _GROUP_SECRETS,
            "file contains BEGIN PRIVATE KEY in first 200 bytes",
        )

    return None


# ---------------------------------------------------------------------------
# LeafCheck — result of per-leaf decomposition (guarantee d)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LeafCheck:
    """
    Result of a single leaf-level path validation within a directory operation.

    path: the leaf file path (canonical).
    result: ValidatorResult for this leaf.
    rule: the ForbiddenRule that matched, or None if ALLOWED.
    """
    path: Path
    result: ValidatorResult
    rule: Optional[ForbiddenRule]


# ---------------------------------------------------------------------------
# ActionValidator
# ---------------------------------------------------------------------------


class ActionValidator:
    """
    Pre-action validator implementing SAFETY_CONTRACT.md §"Pre-Action Validator"
    and all 5 Validator Semantics guarantees.

    Usage:
        validator = ActionValidator(state_dir=config.state_dir)
        result = validator.validate_action(path, Operation.WRITE, context, manifest)

    Operation.GIT_EXEC, GH_EXEC, SUBPROCESS_EXEC: validator returns
    MISSING_PRECHECK to signal caller must use the specialized guard module.
    This is the design choice (a) per SCAFFOLD §3 advisory — validator handles
    filesystem ops only; *_EXEC operations are delegated to their own guards.
    """

    def __init__(self, state_dir: Optional[Path] = None) -> None:
        """
        state_dir: used for source-extension exemption (STATE_DIR/** is exempt).
        May be None for tests that do not exercise the extension exemption.
        """
        self._state_dir = state_dir

    # -------------------------------------------------------------------------
    # Main gate
    # -------------------------------------------------------------------------

    def validate_action(
        self,
        path: Path,
        operation: Operation,
        context: Optional[TickContext] = None,
        manifest: Optional[ProposalManifest] = None,
    ) -> ValidatorResult:
        """
        Primary validation gate. Call before EVERY filesystem operation.

        Returns:
          ALLOWED                — proceed
          FORBIDDEN_PATH         — fatal error; caller calls refuse_fatal
          FORBIDDEN_OPERATION    — fatal error; caller calls refuse_fatal
          MISSING_PRECHECK       — caller must use specialized guard (*_EXEC ops)
          ALLOWED_BUT_DRY_RUN_ONLY — emit proposal, do NOT mutate

        Path A invariant: this method NEVER calls write_self_quarantine().
        FORBIDDEN_* → caller calls refuse_fatal(). Next tick starts clean.

        SEV-2 #4 structural fix:
          WRITE to an existing path requires manifest registration in
          proposed_modifies. Without this, in-place edits bypass the
          post_mutation_detector (which only tracks moves/deletes/creates).
        """
        # Handle *_EXEC operations: delegate to specialized guards.
        if operation in (Operation.GIT_EXEC, Operation.GH_EXEC, Operation.SUBPROCESS_EXEC):
            return ValidatorResult.MISSING_PRECHECK

        # Guarantee (b): canonicalize before any match.
        canonical = self.canonicalize_path(path)

        # Guarantee (c): resolve symlink/hardlink target.
        canonical = self.resolve_symlink_target(canonical)

        # Check forbidden path patterns (covers guarantee a: READ not exempt).
        matched_rule = _match_forbidden_rules(canonical, self._state_dir)
        if matched_rule is not None:
            return ValidatorResult.FORBIDDEN_PATH

        # SEV-2 #4 structural fix: WRITE to existing file requires manifest.
        if operation == Operation.WRITE and canonical.exists():
            if not self._is_write_in_manifest(canonical, manifest):
                return ValidatorResult.FORBIDDEN_PATH

        return ValidatorResult.ALLOWED

    # -------------------------------------------------------------------------
    # Guarantee (b): canonicalize
    # -------------------------------------------------------------------------

    def canonicalize_path(self, path: Path) -> Path:
        """
        Guarantee (b): resolve path to canonical absolute form.

        Uses Path.resolve(strict=False) — collapses '..', expands existing
        symlinks, makes path absolute. Non-existent paths still get '..'-collapsed.
        """
        return _canonicalize(path)

    # -------------------------------------------------------------------------
    # Guarantee (c): symlink / hardlink resolution
    # -------------------------------------------------------------------------

    def resolve_symlink_target(self, path: Path) -> Path:
        """
        Guarantee (c): resolve symlink targets before pattern matching.

        If path is a symlink, follow all links to the final target using
        os.path.realpath() (which resolves the entire chain, not just one hop).
        Returns the canonical target path. For non-symlinks, returns path as-is.

        Hardlinks: cannot be resolved without OS-level stat comparison. We use
        realpath() which handles symlink chains; hardlinks share the same inode
        but have different directory entries — validation via forbidden-path
        pattern matching still applies to the presented path (the canonical
        form already collapsed '..').
        """
        try:
            resolved = Path(os.path.realpath(str(path)))
            return resolved
        except OSError:
            return path

    # -------------------------------------------------------------------------
    # Guarantee (d): per-leaf decomposition
    # -------------------------------------------------------------------------

    def decompose_directory_op(
        self, dir_path: Path, op: Operation,
        manifest: Optional[ProposalManifest] = None,
    ) -> list[LeafCheck]:
        """
        Guarantee (d): decompose a directory operation into per-leaf checks.

        Enumerates all files under dir_path recursively and validates each.
        Returns a list of LeafCheck results. Caller aborts the directory
        operation if ANY leaf returns FORBIDDEN_PATH or FORBIDDEN_OPERATION.

        If dir_path does not exist, returns an empty list (no files to check).

        For WRITE operations, any existing leaf that is not registered in
        manifest.proposed_modifies is FORBIDDEN_PATH (SEV-2 #4 carry-forward).
        """
        if not dir_path.exists() or not dir_path.is_dir():
            return []

        results: list[LeafCheck] = []
        for leaf in sorted(dir_path.rglob("*")):
            if not leaf.is_file():
                continue
            canonical = self.canonicalize_path(leaf)
            canonical = self.resolve_symlink_target(canonical)
            rule = _match_forbidden_rules(canonical, self._state_dir)
            if rule is not None:
                results.append(LeafCheck(
                    path=canonical,
                    result=ValidatorResult.FORBIDDEN_PATH,
                    rule=rule,
                ))
            elif op == Operation.WRITE and canonical.exists():
                # In-place WRITE to existing file requires manifest registration.
                if not self._is_write_in_manifest(canonical, manifest):
                    results.append(LeafCheck(
                        path=canonical,
                        result=ValidatorResult.FORBIDDEN_PATH,
                        rule=None,
                    ))
                else:
                    results.append(LeafCheck(
                        path=canonical,
                        result=ValidatorResult.ALLOWED,
                        rule=None,
                    ))
            else:
                results.append(LeafCheck(
                    path=canonical,
                    result=ValidatorResult.ALLOWED,
                    rule=None,
                ))
        return results

    # -------------------------------------------------------------------------
    # Guarantee (e): git remote URL allowlist
    # -------------------------------------------------------------------------

    def check_remote_url_allowlist(
        self,
        remote_url: str,
        install_meta: InstallMetadata,
    ) -> ValidatorResult:
        """
        Guarantee (e): verify remote URL is in the install-time allowlist.

        Before any git push, the remote URL must be in
        install_metadata.allowed_remote_urls. Any redirect, rewrite, or
        git remote set-url that changes the URL → FORBIDDEN_OPERATION.
        """
        if remote_url in install_meta.allowed_remote_urls:
            return ValidatorResult.ALLOWED
        return ValidatorResult.FORBIDDEN_OPERATION

    # -------------------------------------------------------------------------
    # SEV-2 #4: in-place WRITE manifest check
    # -------------------------------------------------------------------------

    def _is_write_in_manifest(
        self, canonical: Path, manifest: Optional[ProposalManifest]
    ) -> bool:
        """
        Return True if the canonical path is registered in manifest.proposed_modifies.

        If manifest is None, returns False (WRITE to existing path is FORBIDDEN).
        If path is in proposed_modifies, returns True (ALLOWED).
        """
        if manifest is None:
            return False
        import os as _os
        for entry in manifest.proposed_modifies:
            if Path(_os.path.realpath(str(entry))) == canonical:
                return True
        return False

    # -------------------------------------------------------------------------
    # Dry-run floor convenience wrapper
    # -------------------------------------------------------------------------

    def validate_action_with_floor(
        self,
        path: Path,
        operation: Operation,
        context: Optional[TickContext],
        manifest: Optional[ProposalManifest],
        task_id: str,
        install_meta: InstallMetadata,
        floor_cfg: DryRunFloor,
    ) -> ValidatorResult:
        """
        Run validate_action then apply the dry-run floor check.

        If validate_action returns FORBIDDEN_*, returns that immediately.
        Otherwise applies enforce_dry_run_floor() on top.

        The floor is a task-level gate; validate_action is a path-level gate.
        Both must pass before an operation proceeds as ALLOWED (not dry-run-only).
        """
        base_result = self.validate_action(path, operation, context, manifest)
        if base_result in (
            ValidatorResult.FORBIDDEN_PATH,
            ValidatorResult.FORBIDDEN_OPERATION,
            ValidatorResult.MISSING_PRECHECK,
        ):
            return base_result

        # Floor check
        floor_result_str = enforce_dry_run_floor(task_id, install_meta, floor_cfg)
        floor_result = ValidatorResult(floor_result_str)
        if floor_result == ValidatorResult.ALLOWED_BUT_DRY_RUN_ONLY:
            return ValidatorResult.ALLOWED_BUT_DRY_RUN_ONLY

        return base_result
