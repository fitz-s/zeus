# hook_phase3r_legacy_test_disposition.md
# Created: 2026-05-06
# Authority basis: Phase 3.R task brief §4 + PLAN §6.5 cutover

## Context

Phase 3.R (2026-05-06) ported all 7 legacy shell hook logics into
`dispatch.py`. The legacy shell scripts moved to `.claude/hooks/legacy/`.
Tests that directly invoked those shell paths now fail with exit 127
(file not found).

## Disposition: 13 tests → xfail (strict=False, retire by 2026-06-06)

### tests/test_pre_commit_hook.py — 11 tests

All 11 tests invoke `bash .claude/hooks/pre-commit-invariant-test.sh`
which no longer exists at that path (moved to `legacy/`). Canonical
gate logic is now `dispatch.py::_run_blocking_check_invariant_test`,
covered by `test_hook_dispatch_smoke.py` realistic payload tests.

| Test | Disposition |
|------|-------------|
| TestChannelAMarker::test_marker_in_m_flag_skips | xfail |
| TestChannelAMarker::test_marker_in_heredoc_skips | xfail |
| TestChannelAMarker::test_non_commit_command_passes_through | xfail |
| TestChannelBMarker::test_commit_editmsg_fallback_skips | xfail |
| TestChannelBMarker::test_commit_editmsg_without_marker_does_not_skip | xfail |
| TestChannelBMarker::test_ps_walk_skips_when_marker_in_parent_argv | xfail |
| TestSentinelBypass::test_sentinel_file_skips_channel_b | xfail |
| TestSentinelBypass::test_env_var_skips_channel_a | xfail |
| TestSentinelBypass::test_env_var_skips_channel_b | xfail |
| TestBlockedErrorMessage::test_blocked_message_mentions_marker_and_sentinel | xfail |
| TestWorktreeVenvDiscovery::test_falls_through_to_main_worktree_venv_when_local_venv_missing | xfail |
| TestWorktreeVenvDiscovery::test_operator_override_wins_even_when_local_venv_missing | xfail |

Note: `test_no_marker_does_not_skip` and `test_parser_preserves_paths_with_spaces`
remain PASSING (they do not invoke the moved shell directly).

### tests/test_post_merge_cleanup_hook.py — 2 tests

Both tests invoke `bash .claude/hooks/post-merge-cleanup-reminder.sh`
which moved to `legacy/`. Canonical logic is
`dispatch.py::_run_advisory_check_post_merge_cleanup`, covered by
`test_post_merge_cleanup_gh_pr_merge_emits_advisory`.

| Test | Disposition |
|------|-------------|
| TestWorktreeListParser::test_worktree_path_with_embedded_space_is_preserved | xfail |
| TestWorktreeListParser::test_sibling_prefix_worktree_is_not_excluded | xfail |

## Cutover deadline

Retire (delete or rewrite to target dispatch.py) by **2026-06-06** per PLAN §6.5.

## Verification

```
pytest tests/test_pre_commit_hook.py tests/test_post_merge_cleanup_hook.py --tb=no -q
# Expected: 2 passed, 13 xfailed, 1 xpassed
```
