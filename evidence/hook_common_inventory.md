# hook_common.py Function Inventory
# Created: 2026-05-06
# Authority basis: critic-opus §0.5 ATTACK 5 sub-finding
#   "Phase 2 brief MUST inventory hook_common.py (326 LOC) function-by-function
#    with reuse-or-rewrite verdict before Phase 3 deletion"
# Source file: .claude/hooks/hook_common.py (326 LOC, Created 2026-05-02)

## Purpose

`hook_common.py` is a shared helper module providing git-command parsing,
staging inspection, and field extraction for the 7 legacy shell hook scripts.
Phase 3 will retire those shell scripts. This inventory determines what
each function is for and whether Phase 3 dispatch.py should reuse or rewrite it.

---

## Module-level constants and patterns

| Constant | Purpose | Verdict |
|---|---|---|
| `GIT_SUBCOMMANDS_WITH_VALUE` | Set of git global options that take a value (e.g. `-C`, `--git-dir`) | REUSE — dispatch.py shell-command parsing will need this exact set |
| `GIT_VALUELESS_OPTIONS` | Set of git global value-less flags (e.g. `--bare`, `--no-pager`) | REUSE — same use case |
| `SEPARATORS` | Shell operator set `{&&, \|\|, ;, \|}` | REUSE — needed by `git_subcommands` + `git_add_is_broad` |
| `ENV_ASSIGNMENT` | Regex `^[A-Za-z_][A-Za-z0-9_]*=.*$` | REUSE — detects shell env assignments before git token |
| `REVIEW_SAFE_TAG` | Regex matching the inline review-safe bracket tag format (see hook_common.py line 45) | REUSE — used by `validate_staged_review_safe_tags` + `_registered_review_safe_tags` |

---

## Functions

### `_is_git_token(token: str) -> bool`
Checks whether a token is the git binary (exact `"git"` or ends with `"/git"`).

**Used by:** `git_subcommands`, `git_add_is_broad`
**Verdict: REUSE** — single-line predicate; correct and minimal. dispatch.py needs it for `pre_checkout_uncommitted_overlap` and any future Bash-command-parsing hooks.

---

### `_raw_mentions_git(command: str) -> bool`
Regex fast-path to check if a command string contains `git` at all before heavier parsing.

**Used by:** `git_subcommands` (fast-exit), `git_add_is_broad` (fast-exit)
**Verdict: REUSE** — effective early-exit guard. dispatch.py should adopt it to avoid shlex on non-git commands.

---

### `_shell_tokens(command: str) -> list[str]`
POSIX shlex tokenizer with `whitespace_split=True` and `punctuation_chars=True`.
Used to produce a token stream including shell operators as individual tokens.

**Used by:** `git_subcommands`
**Note:** `git_add_is_broad` uses its own inline shlex instance (without `whitespace_split`) to preserve quote handling — intentional divergence documented in the function's docstring.
**Verdict: REUSE** — correct tokenizer for subcommand parsing. dispatch.py `_run_blocking_check_pre_checkout_uncommitted_overlap` imports `re` and `shlex` inline; could consolidate via this helper.

---

### `git_subcommands(command: str) -> list[str]`
Full parser: extracts all git subcommands from a multi-git shell command string.
Handles env assignments, git global options (value/valueless), absolute git paths,
multiple invocations separated by `&&`/`;`/`||`/`|`.

**Used by:** `has-git-subcommand` CLI subcommand (shell hooks use `HOOK_COMMAND=... python3 hook_common.py has-git-subcommand commit`)
**Verdict: REUSE** — this is the most sophisticated piece of logic in the file and has test coverage (`test_hook_worktree_parser_edge_cases` references it per commit `6181be72`). dispatch.py Phase 2 reimplements a simpler regex variant for `pre_checkout_uncommitted_overlap`; Phase 3 should consolidate on this parser.

---

### `_git(repo_root: str, args: list[str], *, check: bool = False) -> CompletedProcess`
Thin wrapper around `subprocess.run(["git", "-C", repo_root, *args], capture_output=True)`.

**Used by:** `_staged_blob`, `_registered_review_safe_tags`, `validate_staged_review_safe_tags`
**Verdict: REUSE** — dispatch.py currently uses inline `subprocess.run` with `cwd=REPO_ROOT` which is functionally equivalent. Consolidating on this helper would reduce duplication, but it uses positional `repo_root` string vs. dispatch.py's `REPO_ROOT` Path constant. Minor mismatch; reconcile in Phase 3.

---

### `_staged_blob(repo_root: str, path: str) -> bytes | None`
Returns the staged (index) content of a file as bytes, or None if not staged.
Uses `git show :<path>`.

**Used by:** `_registered_review_safe_tags`, `validate_staged_review_safe_tags`
**Verdict: REUSE** — needed by the secrets_scan hook's REVIEW_SAFE_TAG validation. dispatch.py Phase 3 secrets_scan implementation will need this.

---

### `_registered_review_safe_tags(repo_root: str) -> set[str]`
Reads SECURITY-FALSE-POSITIVES.md from the staged blob (or working tree fallback)
and extracts all `REVIEW-SAFE:<TAG> (bracket-tag format)` tags. Returns a set of tag strings.

**Used by:** `validate_staged_review_safe_tags`
**Verdict: REUSE** — complex logic tied to project's REVIEW-SAFE pattern. No reason to rewrite; dispatch.py Phase 3 `secrets_scan` should call this.

---

### `git_add_is_broad(command: str) -> bool`
Detects `git add -A`, `git add --all`, `git add .` in a shell command string.
Uses a separate shlex without `whitespace_split` to correctly handle quoted args.

**Used by:** `git-add-is-broad` CLI subcommand (used by `cotenant-staging-guard.sh`)
**Verdict: REUSE** — used directly by `cotenant_staging_guard` hook. The inline shlex divergence from `_shell_tokens` is intentional (quoted-string safety, documented in docstring). Keep as-is.

---

### `validate_staged_review_safe_tags(repo_root: str) -> list[tuple[str, str]]`
Scans the staged diff for newly-added `REVIEW-SAFE:<TAG> (bracket-tag format)` lines and checks
each against the registered tags in SECURITY-FALSE-POSITIVES.md (staged blob or working tree).
Returns list of `(file_path, tag)` tuples for unregistered tags.

**Used by:** `validate-review-safe-tags` CLI subcommand (used by `pre-commit-secrets.sh`)
**Verdict: REUSE** — essential for secrets_scan. This is the most security-critical logic in the file. Do NOT rewrite without full test parity.

---

### `command_from_json(payload: str, field: str) -> str`
Parses a JSON hook payload string and extracts a field from `tool_input`.
Raises `ValueError` with descriptive messages on malformed input.

**Used by:** `extract-json-field` CLI subcommand (used by multiple shell hooks via `python3 hook_common.py extract-json-field command`)
**Verdict: REUSE (partially)** — dispatch.py already inlines JSON parsing via `json.loads(sys.stdin.read())` + direct dict access. The CLI subcommand path is only used by legacy shell scripts. Once shell scripts retire, this function's *CLI path* becomes dead. The logic itself can be inlined or retired. Mark for **DEAD_DELETE** of the CLI subcommand path in Phase 3; keep the core validation pattern.

---

### `repo_relative(repo_root: str, file_path: str) -> tuple[int, str]`
Resolves a (possibly relative or absolute) file path against the repo root.
Returns `(exit_code, relative_path)`.

**Used by:** `repo-relative` CLI subcommand (used by `pre-edit-architecture.sh` + `pre-write-capability-gate.sh`)
**Verdict: REUSE** — dispatch.py `_file_path_from_payload` does a simpler version inline (relative_to(REPO_ROOT)). Phase 3 should consolidate on this to handle edge cases (symlinks, paths outside repo).

---

### `main(argv: list[str] | None = None) -> int`
CLI entry point exposing the above functions as subcommands:
- `extract-json-field <field>`
- `has-git-subcommand <targets...>`
- `git-add-is-broad`
- `repo-relative <repo_root> <file_path>`
- `validate-review-safe-tags <repo_root>`

**Used by:** 5 legacy shell hook scripts
**Verdict: DEAD_DELETE in Phase 3** — these subcommands exist solely to serve the legacy shell scripts. Once Phase 3 retires those scripts, the CLI surface is unused. The underlying functions are reused directly by dispatch.py; the `main()` argparse wrapper is not needed.

---

## Summary

| Verdict | Count | Functions |
|---|---|---|
| **REUSE** | 8 | `_is_git_token`, `_raw_mentions_git`, `_shell_tokens`, `git_subcommands`, `_git`, `_staged_blob`, `_registered_review_safe_tags`, `git_add_is_broad`, `validate_staged_review_safe_tags`, `repo_relative` |
| **DEAD_DELETE** | 2 | `command_from_json` (CLI path only; logic inline in dispatch.py), `main()` (CLI wrapper) |
| **REWRITE** | 0 | — |

**Net Phase 3 action:** Import `hook_common` into `dispatch.py` directly (it's already importable as a module). Delete the `main()` CLI entry point and `command_from_json` once shell scripts are retired. All other functions remain as-is. Do NOT delete `hook_common.py` until all 7 legacy shell scripts are removed and dispatch.py tests pass with the module imported.

**Dependency risk:** `git_subcommands` has edge-case tests referenced in commit `6181be72` (`tests/test_phase1_critic_opus_fixes_2026_05_06.py`). Those tests must remain green after Phase 3 refactor. Run `pytest tests/test_phase1_critic_opus_fixes_2026_05_06.py` as part of Phase 3 gate.
