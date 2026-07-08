#!/usr/bin/env bash
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R0-g (basic hygiene).
#
# WHAT: com.zeus.* launchd daemons write directly to logs/*.log and
#   logs/*.err with no rotation (StandardOutPath/StandardErrorPath in the
#   plists point straight at these files) and hold the fds open for the
#   life of the daemon. This script rotates any log/err file at or above a
#   size threshold using COPYTRUNCATE semantics — copy the file, gzip the
#   copy, then truncate the ORIGINAL in place (never move/unlink it) —
#   because a daemon holding an open fd keeps writing to whatever inode
#   that fd points at; renaming the file out from under it would silently
#   orphan future writes into the renamed copy while the daemon believes
#   it is still writing to logs/foo.log.
#
# SAFETY:
#   - Dry-run by default. Nothing is written/truncated unless --apply.
#   - Truncation always follows the copy, so at worst a rotation loses the
#     handful of bytes written between copy and truncate (known
#     copytruncate tradeoff, not a data-integrity gate).
#   - Never deletes the live file; only gzip's a copy and truncates the
#     original to 0 bytes in place (open fd stays valid).
#   - Keeps N rotated generations (default 5), gzip'd, oldest pruned.
#
# USAGE:
#   scripts/ops/rotate_zeus_logs.sh                 # dry-run (default)
#   scripts/ops/rotate_zeus_logs.sh --apply          # rotate for real
#
# ENV OVERRIDES:
#   ZEUS_LOG_DIR            default <repo>/logs
#   ZEUS_LOG_ROTATE_MB      size threshold in MB (default 50)
#   ZEUS_LOG_ROTATE_KEEP    number of gzip generations to keep (default 5)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="${ZEUS_LOG_DIR:-$REPO_ROOT/logs}"
THRESHOLD_MB="${ZEUS_LOG_ROTATE_MB:-50}"
THRESHOLD_BYTES=$((THRESHOLD_MB * 1024 * 1024))
KEEP="${ZEUS_LOG_ROTATE_KEEP:-5}"

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help)
      sed -n '1,32p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (use --apply or --help)" >&2
      exit 2
      ;;
  esac
done

echo "== rotate_zeus_logs.sh $( [ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN ) =="
echo "log dir  : $LOG_DIR"
echo "threshold: ${THRESHOLD_MB}MB ($THRESHOLD_BYTES bytes)"
echo "keep     : $KEEP generations"
echo ""

if [ ! -d "$LOG_DIR" ]; then
  echo "log dir does not exist: $LOG_DIR"
  exit 0
fi

shopt -s nullglob
files=("$LOG_DIR"/*.log "$LOG_DIR"/*.err)
shopt -u nullglob

if [ "${#files[@]}" -eq 0 ]; then
  echo "no *.log/*.err files found under $LOG_DIR"
  exit 0
fi

total_bytes=0
rotate_count=0
skip_count=0

for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  size="$(stat -f '%z' "$f" 2>/dev/null || stat -c '%s' "$f")"
  total_bytes=$((total_bytes + size))
  human="$((size / 1048576))MB"

  if [ "$size" -lt "$THRESHOLD_BYTES" ]; then
    skip_count=$((skip_count + 1))
    continue
  fi

  rotate_count=$((rotate_count + 1))
  echo "[$f] size=${size}B (~$human) >= threshold — $( [ "$APPLY" = 1 ] && echo rotating || echo would rotate )"

  if [ "$APPLY" = 1 ]; then
    # shift existing gzip generations up: f.$((KEEP-1)).gz -> f.$KEEP.gz, ... f.1.gz -> f.2.gz
    i=$KEEP
    while [ "$i" -gt 1 ]; do
      prev=$((i - 1))
      if [ -f "$f.$prev.gz" ]; then
        mv -f "$f.$prev.gz" "$f.$i.gz"
      fi
      i=$prev
    done
    # prune anything beyond KEEP that shifting left behind (defensive)
    for stale in "$f".[0-9]*.gz; do
      [ -e "$stale" ] || continue
      gen="${stale%.gz}"
      gen="${gen##*.}"
      if [ "$gen" -gt "$KEEP" ] 2>/dev/null; then
        rm -f "$stale"
      fi
    done

    tmp_copy="$(mktemp "$f.rotate.XXXXXX")"
    cp -p "$f" "$tmp_copy"
    gzip -f "$tmp_copy"
    mv -f "$tmp_copy.gz" "$f.1.gz"
    # copytruncate: truncate the ORIGINAL in place, do not unlink/rename it —
    # the daemon's open fd must keep pointing at logs/foo.log.
    : > "$f"
    echo "[$f] rotated -> $f.1.gz ($(stat -f '%z' "$f.1.gz" 2>/dev/null || stat -c '%s' "$f.1.gz")B), original truncated in place"
  fi
done

echo ""
if [ "$APPLY" = 0 ]; then
  echo "DRY-RUN complete: $rotate_count file(s) over threshold, $skip_count under threshold. Total logs/ bytes scanned: $total_bytes. Nothing written."
else
  echo "APPLY complete: $rotate_count file(s) rotated, $skip_count left in place."
fi
