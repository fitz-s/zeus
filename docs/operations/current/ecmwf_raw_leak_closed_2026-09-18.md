# ECMWF raw leak — closed, with the root cause it took to find it (2026-09-18)

Supersedes the "KEEP_AS_IS / retention is working" verdict in
`encoding_audit_all_sources_2026-09-17.md` §1. Raw retention was **not** working: it had
been dead for 9 days and the test suite could not see it.

## What actually reclaimed the space

**19.14 GB, 482 files, zero errors.** The GRIB store went 19 GB → 1.0 GB and the disk
from 35 → 41 GB free. Forecast data verified intact afterwards: 1,342,477
`ensemble_snapshots`, 597,659 `forecast_posteriors`, newest posterior one minute old.

The config change alone would have freed nothing.

## Root cause: identity drift across a writer/reader pair

`collect_open_ens_cycle` stamps the source-run id as

```
ecmwf_open_data:<track>:<cycle>Z:coordsha:<manifest_sha>
```

while `_plan_decoded_open_data_raw_retention` probed for equality against the **bare**
cycle prefix. Every group therefore resolved as `source_run: MISSING` → retained.

| when | what | effect |
|---|---|---|
| 2026-08-21 `0afd62b16` | retention written, probing the bare id | worked |
| 2026-09-09 `160b5ff40` | `:coordsha:<sha>` added to the **writer only** | retention silently dead |
| 2026-09-18 `2d10408c6` | prefix match on both probes | 0 eligible → 10 eligible |

Live evidence before the fix: 10 recognized groups, **all** `source_run: MISSING`,
0 eligible, 19.14 GB held while the disk sat at 97%.

A gate whose failure mode is "retain everything" is silent by construction — no error,
no alarm, just a number that never moves. That is why nine days passed.

### Why 13 green tests missed it

The fixture wrote the **bare** id, so the tests and production were wrong in the same
direction and corroborated each other. The fixture now defaults to the real
coordsha-suffixed shape (`coordsha=None` keeps the legacy form), which makes two
**pre-existing** tests catch this class too.

### Strictness was tightened, not relaxed

Matching a prefix could have widened a delete gate, so the predicate compensates:

- **every** `source_run` sharing the cycle prefix must be proven — one unproven
  `coordsha` pin retains the whole group, so a re-pin under a new manifest can never
  authorize deleting raw an earlier pin still needs;
- pins disagreeing on `observed_count` retain (the snapshot proof would otherwise have no
  single number to check against);
- `_sql_like_escape` stops a future track label containing `_` or `%` from widening the
  probe — the one direction this gate must never fail in;
- the snapshot proof got the same prefix treatment, since it reads the same suffixed id.

## Two further leaks closed in the same pass

**Transport sidecars** (`438950b6a`). The success path unlinked `pf_partial`/`cf_partial`
but never their `.ranges.json` manifests — only a checksum/ETag mismatch or the weak-ETag
discard path ever did — so one accumulated per completed step forever. And `.partial` /
`.ranges.json` are invisible to the group proof by construction (`_raw_file_identity`
matches only `.grib2`), so the calendar cutoff cannot reach them at any age. I swept
1,104 by hand earlier in the day; **350 regrew within a single cycle**, which is what
proved a manual sweep was not a fix.

The sweep is conservative: a sidecar is planned only when its canonical `.grib2` already
exists (the byte-range prefix is spent) or is being evicted in the same plan. A sidecar
with **no** canonical is an in-flight or resumable download and is left alone — deleting
its manifest would discard recoverable transport progress.

**Coordinate-manifest cycle views** (`77cef49c9`). The decoded-JSON views under
`coordinate_manifests/<sha>/<subdir>/<city>/<cycle>` inherited no retention when the
extractor's `--output-root` moved there on 2026-09-09. They are write-only once a newer
cycle lands — `_build_cycle_scoped_json_root` builds a temp view of the selected cycle
only, and its own comment says "stale raw directories cannot satisfy a new source_run".
Measured: 33 cycle dirs per city × 54 cities spanning 09-09..09-17, 234 MB at ~26 MB/day,
unbounded at ~9.5 GB/year. Dry run: **3,456 removable cycle dirs = 169.1 MB**, 108 kept,
0 unparsed.

Prunes only cycles strictly older than the ingested one (a retry of the same cycle still
finds its view), only names that round-trip through `_parse_cycle_extract_dir_name`, only
on `status == "ok"`, after the canonical commit, outside the BULK writer lock, fail-soft.

## Standing corrections to the 2026-09-17 audit

- §1 "retention is automatic, 2 calendar days, proof-gated … Not a leak. **KEEP_AS_IS**"
  — the design was right; the implementation was broken. Read this file first.
- The `.partial`/`.ranges.json` finding stands and is now fixed in code rather than by
  hand.
- `coordinate_manifests` "inherited NO retention" stands and is now fixed.

## Still open

- `scripts/ops/evict_proven_ecmwf_raw.py` exists for the case where retention's fix has
  landed but the daemon has not reloaded, or a mesh restart is refused. It reuses the
  module's own plan/apply, adds no delete authority, and needs no writer lock.
- The in-daemon prune and sidecar sweep only take effect after `forecast-live` reloads.
  As of writing the daemon predates both, so the one-shot script is the interim path.
- `family_books.db` (26.8 GB) is unrelated to this path — see
  `plans/family_books_compaction_runbook_2026-09-18.md`.
