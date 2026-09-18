# `.total_loss` is a path-migration leak, not a compression problem (2026-09-18)

Supersedes the "Recommended action" in
`family_books_compacted_and_total_loss_audit_2026-09-18.md` §2, which recommended
compressing `source_clocks.raw_json` in place. **That recommendation was wrong and would
have reverted itself.** The real defect is structural: two path migrations left the old
paths behind, one of them silently breaking the repair stage.

## 1. The compression recommendation was self-reverting

`evidence.db` is a *derived* snapshot, and `_evidence_pair_valid` gates it on
`size_bytes`, `evidence_mtime_ns` **and** `sha256` recorded in `manifest.json`. Any
in-place rewrite fails that gate, and a failed gate is not an error — `_capture_hard_evidence`
simply rebuilds the snapshot at full size. Compressing 663 databases would therefore have
bought nothing and cost a rebuild of each.

The measurement behind it was also wrong twice over:

- The "3.81x with zstd-19 + dict" figure came from one file. Across 12 stratified files
  the real ratio is **8.01x** (1.88 GB -> 0.235 GB), because the rows are small and
  near-identical — a trained dictionary is what wins, not a higher level.
- `source_clocks` is not the lever it appeared to be. Payload across the 14 largest
  files: **`price_ticks` 49.8%**, `source_clocks` 43.2%, `monitor_events` 6.4%. The
  earlier `dbstat` read generalized a single unrepresentative file.

And the biggest single cost is already fixed upstream: `price_ticks.raw_json` carried
`depth_before_json` (1,610 of 2,606 B/row) *alongside* the `depth_json` column. The
current builder excludes it by name; only the pre-fix files still pay for it.

## 2. The live defect: the repair agent was handed a dead path

`_build_evidence_snapshot` has written `generations/<id>/evidence.db` behind a `CURRENT`
pointer since `6721fa637` (2026-08-24). `_start_repair` still named the pre-migration
`incidents/<id>/evidence.db`.

| | |
|---|---|
| incidents queued at `repair_waiting` | 83 |
| with **no** legacy file (agent got a nonexistent path) | **66** |
| with a valid CURRENT generation sitting beside it | 77 |

Fixed in `47da51eee` by resolving through `_evidence_pair_paths` — the same resolver the
diagnosis phase and the pair-validity gate already use. Mutation-verified antibody.

This is the same class as the ECMWF `coordsha` drift: a writer moved, its reader did not,
and nothing failed loudly. Here the symptom was a repair prompt pointing at nothing.

## 3. The actual leak: superseded generations were never reaped

`_reap_incomplete_generations` returned early for any directory holding both
`evidence.db` and `manifest.json` — so it only removed *interrupted* builds. But `CURRENT`
is the only entry point into `generations/`, so a complete generation the pointer has
moved off can never be read again. Every rebuild leaked a whole snapshot.

| | files | bytes |
|---|---|---|
| CURRENT generations (live) | 402 | 6.23 GB |
| superseded generations (unreachable) | 160 dirs | 2.18 GB |
| legacy-layout snapshots | 101 | 10.62 GB |

Fixed in `d27f4bd62`, with one bound: when `CURRENT` is unreadable the live generation
cannot be told from a superseded one, so `complete_is_reapable` falls back to the previous
incomplete-only rule rather than deleting the snapshot the next rebuild would reuse. Both
antibodies fail only on their own mutation.

## 4. Reclaiming what leaked before the fixes

`scripts/ops/evict_superseded_total_loss_evidence.py` — **10.06 GB**, proof-gated, dry-run
by default:

```
superseded generations :   234 dirs      2.18 GB
redundant legacy files :    69 files     7.88 GB
retained, live CURRENT generation      :  402
retained, legacy file is only evidence :    0
retained, no usable CURRENT pointer    : 1391
```

Every one of the 69 replacement generations was verified to open and query. Retention is
proof-gated rather than age-gated:

- no usable `CURRENT` -> retain everything under that incident (1,391 of them);
- legacy file is an incident's only evidence -> retain, because `_start_repair`'s fallback
  still names it;
- no incident directory, manifest, diagnosis, or `memory.db` row is ever touched.

**All 2,181 live incidents keep their evidence.** `observing` (1,933 rows) is resumable per
`total_loss_loop.py:4191`, so it is not a terminal state and is not treated as one here.
The earlier verdict "do not mass-delete" stands — this deletes nothing on the basis of
state, only on the basis of unreachability.

### Order of operations

The daemon (pid 46677) has run since **2026-09-04**, so it carries pre-fix code and still
reads the legacy path. Restart it *before* applying the eviction, or the 11 legacy files
belonging to `repair_waiting`/`queued` incidents would be pulled out from under it.
`com.zeus.total-loss-loop` is launchd-supervised with `KeepAlive.SuccessfulExit=0`, and
there are no in-flight model runs and no `workspace_writer_leases`, so a TERM restarts it
cleanly.

## 5. What is left, honestly

Compression is still available on what remains, but it is now a **second-order** win:
after eviction the store is ~10 GB, of which the live CURRENT generations are 6.23 GB, and
those are written by the current budgeted builder that already excludes the duplicated
order book. Compressing them would have to survive the manifest hash gate — meaning the
codec belongs *inside* `_build_evidence_snapshot`, at write time, where the manifest is
computed over the compressed file. That is a real option and a clean one; it is not the
in-place rewrite the previous doc proposed.

Standing corrections to `family_books_compacted_and_total_loss_audit_2026-09-18.md`:

- §2 "Recommended action: compress `source_clocks.raw_json` in place" — **withdrawn**, it
  would fail the manifest gate and trigger a full-size rebuild.
- §2 "the lever is `source_clocks`, not `monitor_events`" — the lever is actually
  `price_ticks` (49.8%), and its dominant cost is already fixed in the current builder.
- §2 "~15 GB reclaimable by lossless compression" — 10.06 GB is reclaimable by deletion
  of unreachable files, which needs no codec and no schema change.
- §2 "DO NOT mass-delete" — stands, and is the reason this eviction is keyed on
  unreachability rather than on incident state.
