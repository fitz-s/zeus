#!/usr/bin/env bash
# Created: 2026-06-12
# Authority basis: docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md (Component 1)
# Purpose: install an idempotent post-commit hook that runs `codegraph sync`
#          only for explicitly registered roots with an existing index.
set -euo pipefail

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  echo "Usage: bash scripts/install_codegraph_hooks.sh [repo-or-worktree-root]"
  exit 0
fi

TARGET_ROOT="${1:-$(pwd)}"
REPO_ROOT="$(git -C "$TARGET_ROOT" rev-parse --show-toplevel)"
HOOK_DIR="$(git -C "$REPO_ROOT" rev-parse --git-path hooks)"
case "$HOOK_DIR" in
  /*) ;;
  *) HOOK_DIR="$REPO_ROOT/$HOOK_DIR" ;;
esac
HOOK="$HOOK_DIR/post-commit"
ROOTS_FILE="$HOOK_DIR/codegraph-sync-roots"
CG_BIN="$(command -v codegraph || true)"

if [ -z "$CG_BIN" ]; then
  echo "codegraph not on PATH; install it first (npm i -g @graphify/codegraph or equivalent)." >&2
  exit 1
fi

MARKER="# >>> codegraph sync hook >>>"
END_MARKER="# <<< codegraph sync hook <<<"

mkdir -p "$HOOK_DIR"

# Create the hook file with a shebang if absent.
if [ ! -f "$HOOK" ]; then
  printf '#!/usr/bin/env bash\n' > "$HOOK"
  chmod +x "$HOOK"
fi

touch "$ROOTS_FILE"
if ! grep -Fxq "$REPO_ROOT" "$ROOTS_FILE"; then
  printf '%s\n' "$REPO_ROOT" >> "$ROOTS_FILE"
fi

# Idempotent: remove any prior managed block, then append a fresh one.
if grep -qF "$MARKER" "$HOOK"; then
  perl -0pi.bak -e 's/\n?# >>> codegraph sync hook >>>\n.*?# <<< codegraph sync hook <<<\n?/\n/s' "$HOOK"
  rm -f "$HOOK.bak"
fi

cat >> "$HOOK" <<EOF
$MARKER
# Incremental index refresh for registered roots only; backgrounded and quiet.
_codegraph_repo_root=\$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
_codegraph_roots_file="$ROOTS_FILE"
if [ -r "\$_codegraph_roots_file" ] \\
  && grep -Fxq "\$_codegraph_repo_root" "\$_codegraph_roots_file" \\
  && [ -d "\$_codegraph_repo_root/.codegraph" ]; then
  ( "$CG_BIN" sync -q "\$_codegraph_repo_root" >/dev/null 2>&1 & )
fi
unset _codegraph_repo_root _codegraph_roots_file
$END_MARKER
EOF

chmod +x "$HOOK"
echo "Installed codegraph post-commit sync hook at $HOOK"
echo "Registered codegraph root: $REPO_ROOT"
