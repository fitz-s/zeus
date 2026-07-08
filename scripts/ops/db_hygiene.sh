#!/usr/bin/env bash
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R0-g (basic hygiene).
#   Canonical DB names/split: architecture/db_table_ownership.yaml (K1 3-DB
#   split — all live truth lives under state/*.db; anything at repo root is
#   a decoy).
#
# WHAT: report-only inventory of two DB filesystem hygiene problems:
#   1. Root-directory *.db* files. K1 canon puts every live DB under
#      state/ (state/zeus-world.db, state/zeus-forecasts.db,
#      state/zeus_trades.db, state/risk_state.db, ...). Any *.db* file
#      sitting at the repo root is a leftover/decoy, not truth.
#   2. state/ underscore-vs-hyphen naming duplicates: two files whose
#      names are identical except '-' vs '_' (e.g. zeus_world.db /
#      zeus-world.db). When one side of such a pair is 0 bytes and the
#      other is non-zero, the 0-byte one is almost certainly a decoy stub
#      created by an old code path that used the wrong separator; it is
#      flagged, never assumed which pair member is canonical beyond that.
#
# SAFETY:
#   - Report-only by default (no --apply): lists candidates, deletes nothing.
#   - --apply deletes ONLY files that are (a) reported as a 0-byte decoy
#     candidate under rule 1 or 2 above, AND (b) exactly 0 bytes at delete
#     time, AND (c) not held open by any process (checked via lsof).
#     Any candidate failing (b) or (c) is a HARD REFUSAL — reported, not
#     deleted, and the script exits non-zero if any refusal occurred.
#   - Never touches non-.db* files, never touches state/*.db files that are
#     not part of a detected naming-duplicate pair, never inspects DB
#     content (sqlite3), never writes into any DB.
#
# USAGE:
#   scripts/ops/db_hygiene.sh              # report-only (default)
#   scripts/ops/db_hygiene.sh --apply       # delete refused-clean 0-byte decoys
#
# ENV OVERRIDES:
#   ZEUS_REPO_ROOT   default this script's repo root
#   ZEUS_STATE_DIR   default <repo>/state

set -euo pipefail

REPO_ROOT="${ZEUS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
STATE_DIR="${ZEUS_STATE_DIR:-$REPO_ROOT/state}"

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help)
      sed -n '1,34p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (use --apply or --help)" >&2
      exit 2
      ;;
  esac
done

echo "== db_hygiene.sh $( [ "$APPLY" = 1 ] && echo APPLY || echo REPORT-ONLY ) =="
echo "repo root : $REPO_ROOT"
echo "state dir : $STATE_DIR"
echo ""

file_size() { stat -f '%z' "$1" 2>/dev/null || stat -c '%s' "$1"; }

refusals=0
delete_candidates_tmp="$(mktemp)"
trap 'rm -f "$delete_candidates_tmp"' EXIT

echo "--- rule 1: root-directory *.db* files (K1 canon = state/ only) ---"
shopt -s nullglob
root_db_files=("$REPO_ROOT"/*.db "$REPO_ROOT"/*.db-wal "$REPO_ROOT"/*.db-shm "$REPO_ROOT"/*.db-journal)
shopt -u nullglob
if [ "${#root_db_files[@]}" -eq 0 ]; then
  echo "(none found)"
else
  for f in "${root_db_files[@]}"; do
    [ -f "$f" ] || continue
    size="$(file_size "$f")"
    if [ "$size" -eq 0 ]; then
      echo "DECOY  $f  size=0B  (root-level DB file; K1 split forbids live DBs outside state/)"
      echo "$f" >> "$delete_candidates_tmp"
    else
      echo "REVIEW $f  size=${size}B  (root-level DB file, NON-ZERO — not auto-deletable, needs operator review; K1 split says this should not exist here)"
    fi
  done
fi

echo ""
echo "--- rule 2: state/ underscore-vs-hyphen naming duplicates ---"
if [ ! -d "$STATE_DIR" ]; then
  echo "state dir does not exist: $STATE_DIR"
else
  pairs_tmp="$(mktemp)"
  shopt -s nullglob
  for f in "$STATE_DIR"/*.db; do
    base="$(basename "$f")"
    norm="${base//-/_}"
    size="$(file_size "$f")"
    printf '%s\t%s\t%s\t%s\n' "$norm" "$base" "$size" "$f" >> "$pairs_tmp"
  done
  shopt -u nullglob

  # groups with 2+ distinct actual basenames sharing the same normalized key
  dup_keys="$(cut -f1 "$pairs_tmp" | sort | uniq -d)"
  if [ -z "$dup_keys" ]; then
    echo "(no underscore/hyphen naming duplicates found)"
  else
    while IFS= read -r key; do
      [ -n "$key" ] || continue
      group="$(awk -F'\t' -v k="$key" '$1==k' "$pairs_tmp")"
      # is there at least one zero-byte member and one non-zero member?
      has_zero="$(echo "$group" | awk -F'\t' '$3==0' | wc -l | tr -d ' ')"
      has_nonzero="$(echo "$group" | awk -F'\t' '$3!=0' | wc -l | tr -d ' ')"
      echo "GROUP  normalized=$key"
      echo "$group" | while IFS=$'\t' read -r _ base size path; do
        if [ "$size" = 0 ] && [ "$has_nonzero" -gt 0 ]; then
          echo "  DECOY  $path  size=0B  (canonical counterpart in this group is non-zero)"
        elif [ "$size" = 0 ]; then
          echo "  ZERO   $path  size=0B  (no non-zero counterpart in this group — report only, not auto-flagged)"
        else
          echo "  KEEP   $path  size=${size}B"
        fi
      done
      if [ "$has_zero" -gt 0 ] && [ "$has_nonzero" -gt 0 ]; then
        echo "$group" | awk -F'\t' '$3==0 {print $4}' >> "$delete_candidates_tmp"
      fi
    done <<< "$dup_keys"
  fi
  rm -f "$pairs_tmp"
fi

echo ""
echo "--- delete-candidate resolution ---"
if [ ! -s "$delete_candidates_tmp" ]; then
  echo "no delete candidates."
else
  while IFS= read -r cand; do
    [ -n "$cand" ] || continue
    if [ ! -e "$cand" ]; then
      continue
    fi
    size_now="$(file_size "$cand")"
    open_by="$(lsof "$cand" 2>/dev/null | tail -n +2 | awk '{print $1":"$2}' | tr '\n' ',' || true)"
    if [ "$size_now" != 0 ]; then
      echo "REFUSE $cand  now ${size_now}B (no longer 0 bytes) — will NOT delete"
      refusals=$((refusals + 1))
      continue
    fi
    if [ -n "$open_by" ]; then
      echo "REFUSE $cand  currently open (lsof: $open_by) — will NOT delete"
      refusals=$((refusals + 1))
      continue
    fi
    if [ "$APPLY" = 1 ]; then
      rm -f "$cand"
      echo "DELETED $cand"
    else
      echo "WOULD DELETE $cand (0 bytes, not open)"
    fi
  done < "$delete_candidates_tmp"
fi

echo ""
if [ "$APPLY" = 0 ]; then
  echo "REPORT-ONLY complete. Re-run with --apply to delete confirmed-clean 0-byte decoys."
else
  echo "APPLY complete. $refusals refusal(s)."
fi

if [ "$refusals" -gt 0 ]; then
  exit 1
fi
