# WorktreeCreate hook cwd-leakage — 2026-05-18

## Symptom (2026-05-17)

Two parallel subagents (`a90cfda19f7ff32d3`, `a66c192ac98db4c54`) spawned
with `isolation:"worktree"` both died within ~60s of start with every
Bash/Read failing on `<cwd>/.claude/hooks/dispatch.py: No such file or
directory`. The `<cwd>` env was set to the literal string
`{"continue":true,"hookSpecificOutput":{"hookEventName":"WorktreeCreate","worktreePath":"…"}}`
— a JSON envelope, not a path.

## Root cause

`~/.claude/hooks/worktree-create.mjs` (user-global, created 2026-05-17)
emitted a JSON envelope on stdout. Claude Code's `executeWorktreeCreateHook`
(`evH` in the 2.1.143 binary) consumes command-hook stdout literally:

```js
let K = q.find(T => T.succeeded && T.output.trim().length > 0);
return { worktreePath: K.output.trim() };
```

`output.trim()` of the JSON envelope IS the JSON envelope. The harness
then chdirs the spawned agent into that string.

The empirical contract is also burned into the binary's error string:

> `WorktreeCreate hook failed: hook succeeded but returned no worktree path
>  (command: echo the path to stdout; http/callback: return
>  hookSpecificOutput.worktreePath)`

and the field description:

> `Hook-specific output for the WorktreeCreate event. Provides the absolute
>  path to the created worktree directory. Command hooks print the path on
>  stdout instead.`

The `hookSpecificOutput` shape is for HTTP/callback hooks. Command hooks
must emit the path as a plain string on stdout.

## Fix

`~/.claude/hooks/worktree-create.mjs` (OUT-OF-REPO; lives in user-global
config). Three stdout sites changed:

1. Outside-git-repo branch (was `JSON.stringify({continue:true})`) → empty
   stdout (harness falls back to default handler).
2. Supplied-path branch (was JSON envelope) → `abs + "\n"`.
3. Main success branch (was JSON envelope) → `abs + "\n"`.

A fixed copy is snapshotted alongside this file at `worktree-create.mjs.fixed`
for recovery if `~/.claude/` is lost.

## Antibody

`tests/test_worktree_create_mjs_contract.py`:
- **Dynamic probe**: runs the hook, asserts stdout is a single absolute
  path (no JSON tokens), worktree directory exists, cleans up after itself.
- **Static probe**: greps the hook source for
  `process.stdout.write(JSON.…)` — the exact mechanism that caused the
  2026-05-17 incident.

Sed-break/restore meta-verification: both assertions correctly fail when
the bug is reintroduced and pass after restore. The companion in-repo
contract test (`tests/test_worktree_create_contract.py`) is unchanged and
still green — dispatch.py's Round-2 fix (stderr-only) was correct and is
preserved.

## Three-probe verification

1. **Producer probe** — hook stdout is a plain path.
2. **Antibody probe** — pytest passes; sed-break reintroduces JSON → tests
   fail; restore → tests pass again.
3. **Harness simulation** — runs both registered WorktreeCreate hooks
   in order, applies the binary's exact `find(T => T.succeeded &&
   T.output.trim().length>0)` consumer logic, asserts the resulting
   `worktreePath` is a real directory and that
   `<worktreePath>/.claude/hooks/dispatch.py` resolves — i.e. the exact
   failure mode from 2026-05-17 is impossible.

A true end-to-end probe (spawning a fresh agent with `isolation:"worktree"`
from a top-level session) requires `Task` tool access, which subagents
don't have. The harness-simulation probe exercises the same consumer
function evH from the binary; that code path is the one that decides cwd.

## Authority

- Claude Code 2.1.143 binary symbol strings (extracted via `strings`):
  `executeWorktreeCreateHook` (`evH`), error message contract,
  `Hook-specific output …` field description.
- `.claude/hooks/dispatch.py` lines 103-114 — empirical Round-2 finding
  from 2026-05-17 incident (Zeus advisor route is correct, stderr-only).
- Memory: `feedback_hook_design_failure_cascades_to_discipline_violation`.
