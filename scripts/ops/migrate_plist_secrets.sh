#!/usr/bin/env bash
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R0-f, §C constraint 8
#   (secrets NEVER enter source/reports/git). Operator runbook:
#   docs/rebuild/r0f_plist_migration.md
#
# WHAT: macOS launchd has no EnvironmentFile directive, so today the 9-10
#   com.zeus.*.plist LaunchAgents carry POLYMARKET_API_KEY/SECRET/PASSPHRASE
#   and WU_API_KEY as literal <string> values inside EnvironmentVariables.
#   This script migrates each plist that has secret keys to a wrapper-exec
#   form:
#     ProgramArguments = [
#       "/bin/bash", "-lc",
#       "set -a; source $HOME/.zeus/secrets.env; set +a; exec <original argv>"
#     ]
#   and deletes the literal secret keys from EnvironmentVariables. The values
#   are extracted once into $HOME/.zeus/secrets.env (chmod 600, created only
#   on --apply), which is OUTSIDE the repo and never read/printed by this
#   script except to redirect straight into that file.
#
# SAFETY:
#   - Dry-run by default. Nothing is written unless --apply is passed.
#   - Dry-run NEVER captures a secret value into a shell variable — existence
#     checks redirect the printed value to /dev/null. Only key NAMES are
#     ever printed, never values, in either mode.
#   - --apply backs up every plist it touches to
#     $LAUNCH_AGENTS_DIR/backup_<YYYY-MM-DD>/ before rewriting.
#   - Idempotent: already-migrated plists (ProgramArguments already
#     "/bin/bash -lc") are detected and only re-checked for stray secret
#     keys; the ProgramArguments array is not rebuilt a second time.
#   - This script never runs launchctl or scripts/deploy_live.py. It only
#     PRINTS the next-step commands for the operator to run by hand.
#
# USAGE:
#   scripts/ops/migrate_plist_secrets.sh            # dry-run (default)
#   scripts/ops/migrate_plist_secrets.sh --apply     # extract + rewrite
#
# ENV OVERRIDES (for testing against a fixture dir instead of the live one):
#   ZEUS_LAUNCH_AGENTS_DIR  default $HOME/Library/LaunchAgents
#   ZEUS_SECRETS_ENV        default $HOME/.zeus/secrets.env

set -euo pipefail

LAUNCH_AGENTS_DIR="${ZEUS_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
SECRETS_ENV="${ZEUS_SECRETS_ENV:-$HOME/.zeus/secrets.env}"
DATE_TAG="$(date +%Y-%m-%d)"
BACKUP_DIR="$LAUNCH_AGENTS_DIR/backup_${DATE_TAG}"
SECRET_KEYS="POLYMARKET_API_KEY POLYMARKET_API_SECRET POLYMARKET_API_PASSPHRASE WU_API_KEY"
PB="/usr/libexec/PlistBuddy"

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help)
      sed -n '1,40p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (use --apply or --help)" >&2
      exit 2
      ;;
  esac
done

echo "== migrate_plist_secrets.sh $( [ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN ) =="
echo "launch agents dir : $LAUNCH_AGENTS_DIR"
echo "secrets env target: $SECRETS_ENV (created only on --apply)"
echo "backup dir        : $BACKUP_DIR (created only on --apply, only if a plist changes)"
echo ""

shopt -s nullglob
plists=("$LAUNCH_AGENTS_DIR"/com.zeus.*.plist)
shopt -u nullglob

if [ "${#plists[@]}" -eq 0 ]; then
  echo "no com.zeus.*.plist found under $LAUNCH_AGENTS_DIR — nothing to do"
  exit 0
fi

extract_tmp=""
if [ "$APPLY" = 1 ]; then
  mkdir -p "$(dirname "$SECRETS_ENV")"
  extract_tmp="$(mktemp)"
  trap 'rm -f "$extract_tmp"' EXIT
fi

changed_count=0
skipped_count=0

for plist in "${plists[@]}"; do
  name="$(basename "$plist")"

  present=""
  for k in $SECRET_KEYS; do
    if "$PB" -c "Print :EnvironmentVariables:$k" "$plist" >/dev/null 2>&1; then
      present="$present $k"
    fi
  done

  if [ -z "$present" ]; then
    echo "[$name] no secret env keys present — nothing to migrate"
    skipped_count=$((skipped_count + 1))
    continue
  fi

  arg0="$("$PB" -c 'Print :ProgramArguments:0' "$plist" 2>/dev/null || true)"
  arg1="$("$PB" -c 'Print :ProgramArguments:1' "$plist" 2>/dev/null || true)"
  already_wrapped=0
  if [ "$arg0" = "/bin/bash" ] && [ "$arg1" = "-lc" ]; then
    already_wrapped=1
    echo "[$name] ProgramArguments already wrapper-exec form; re-checking stray secret keys only"
  fi

  orig_argv=()
  if [ "$already_wrapped" = 0 ]; then
    i=0
    while true; do
      v="$("$PB" -c "Print :ProgramArguments:$i" "$plist" 2>/dev/null)" || break
      orig_argv+=("$v")
      i=$((i + 1))
    done
  fi

  echo "[$name] secret env keys to remove:$present"
  wrapper_cmd=""
  if [ "${#orig_argv[@]}" -gt 0 ]; then
    quoted=""
    for a in "${orig_argv[@]}"; do
      quoted="$quoted $(printf '%q' "$a")"
    done
    wrapper_cmd="set -a; source $SECRETS_ENV; set +a; exec$quoted"
    echo "[$name] would rewrite ProgramArguments -> [\"/bin/bash\", \"-lc\", \"$wrapper_cmd\"]"
  fi

  if [ "$APPLY" = 1 ]; then
    mkdir -p "$BACKUP_DIR"
    cp -p "$plist" "$BACKUP_DIR/$name"
    echo "[$name] backed up -> $BACKUP_DIR/$name"

    for k in $present; do
      val="$("$PB" -c "Print :EnvironmentVariables:$k" "$plist")"
      qval="$(printf '%q' "$val")"
      if grep -q "^export $k=" "$extract_tmp" 2>/dev/null; then
        existing_line="$(grep "^export $k=" "$extract_tmp" | head -1)"
        existing_val="${existing_line#export $k=}"
        if [ "$existing_val" != "$qval" ]; then
          echo "[$name] WARNING: $k differs from an earlier plist's value; keeping first-seen (no values shown)" >&2
        fi
      else
        printf 'export %s=%s\n' "$k" "$qval" >> "$extract_tmp"
      fi
      "$PB" -c "Delete :EnvironmentVariables:$k" "$plist"
      unset val qval
    done

    if [ "${#orig_argv[@]}" -gt 0 ]; then
      "$PB" -c "Delete :ProgramArguments" "$plist"
      "$PB" -c "Add :ProgramArguments array" "$plist"
      "$PB" -c "Add :ProgramArguments:0 string /bin/bash" "$plist"
      "$PB" -c "Add :ProgramArguments:1 string -lc" "$plist"
      "$PB" -c "Add :ProgramArguments:2 string $wrapper_cmd" "$plist"
    fi

    remaining=0
    for k in $SECRET_KEYS; do
      if "$PB" -c "Print :EnvironmentVariables:$k" "$plist" >/dev/null 2>&1; then
        remaining=1
      fi
    done
    if [ "$remaining" = 1 ]; then
      echo "[$name] ERROR: secret key still present after rewrite — aborting" >&2
      exit 1
    fi
    echo "[$name] rewritten (secrets removed, wrapper-exec installed)"
    changed_count=$((changed_count + 1))
  fi
  echo ""
done

if [ "$APPLY" = 1 ] && [ -s "$extract_tmp" ]; then
  umask 177
  cat "$extract_tmp" > "$SECRETS_ENV"
  chmod 600 "$SECRETS_ENV"
  key_count="$(wc -l < "$SECRETS_ENV" | tr -d ' ')"
  echo "wrote $SECRETS_ENV (chmod $(stat -f '%Lp' "$SECRETS_ENV" 2>/dev/null || echo 600), $key_count keys)"
fi

echo ""
if [ "$APPLY" = 0 ]; then
  echo "DRY-RUN complete: $((${#plists[@]} - skipped_count)) plist(s) with secrets, $skipped_count already clean. No files written. Re-run with --apply to migrate."
else
  echo "APPLY complete: $changed_count plist(s) rewritten, $skipped_count already clean."
fi

cat <<EOF

Next steps (NOT run by this script — operator executes by hand):
  1. Verify no plist under $LAUNCH_AGENTS_DIR/com.zeus.*.plist contains a literal secret:
       for f in $LAUNCH_AGENTS_DIR/com.zeus.*.plist; do plutil -p "\$f" | grep -iE "KEY|SECRET|PASSPHRASE|TOKEN"; done
     Expect: either no output, or only the wrapper-exec bash -lc string that
     references \$HOME/.zeus/secrets.env (never a raw credential).
  2. Restart the mesh coherently — NEVER a bare launchctl kickstart (split-brain risk):
       python3 scripts/deploy_live.py restart all
  3. After preflight is GREEN, resume entries per the live-daemon-deploy runbook.
  (Full sequence with exact commands: docs/rebuild/r0f_plist_migration.md)
EOF
