#!/usr/bin/env bash
# Created: 2026-06-12
# Authority basis: docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md (Component 1)
# Purpose: install an idempotent post-commit hook that runs `codegraph sync`
#          so the local index never silently goes stale after commits.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/.git/hooks/post-commit"
CG_BIN="$(command -v codegraph || true)"

if [ -z "$CG_BIN" ]; then
  echo "codegraph not on PATH — install it first (npm i -g @graphify/codegraph or equivalent)." >&2
  exit 1
fi

MARKER="# >>> codegraph sync hook >>>"
END_MARKER="# <<< codegraph sync hook <<<"

# Create the hook file with a shebang if absent.
if [ ! -f "$HOOK" ]; then
  printf '#!/usr/bin/env bash\n' > "$HOOK"
  chmod +x "$HOOK"
fi

# Idempotent: remove any prior managed block, then append a fresh one.
if grep -qF "$MARKER" "$HOOK"; then
  sed -i.bak "/$MARKER/,/$END_MARKER/d" "$HOOK" && rm -f "$HOOK.bak"
fi

cat >> "$HOOK" <<EOF
$MARKER
# Incremental index refresh; backgrounded + silenced so commits stay fast.
( "$CG_BIN" sync "$REPO_ROOT" >/dev/null 2>&1 & )
$END_MARKER
EOF

chmod +x "$HOOK"
echo "Installed codegraph post-commit sync hook at $HOOK (CLI: $CG_BIN)"
