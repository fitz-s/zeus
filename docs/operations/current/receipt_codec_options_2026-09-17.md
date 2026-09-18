# Receipt codec — the win is available with NO new dependency (2026-09-17)

The pre-submit decomposition put the production `zlib-9 + base64` encode at 123.6 ms of
a 516 ms median. I had recommended `zstd-3`, but **`zstandard` is not installed in
`.venv` and is not declared in `requirements.txt`** — my benchmarks ran on system
python3. `requirements.txt` pins exact versions and its documented bump procedure is
scratch venv → full suite → `pip-audit` → pinned line with a dated reason, so adding a
native dependency to the money path is an operator decision, not a side effect of a
latency fix.

So: is the win reachable with stdlib only? **Yes, most of it.**

## Measured across 40 real rows (150 payloads, 241.1 MB raw)

| encoding | size | time | vs production |
|---|---|---|---|
| **`zlib-9` + base64 (production)** | 96.27 MB | 5109.4 ms | — |
| **`zlib-1` raw BLOB (stdlib)** | **78.70 MB** | **1138.6 ms** | **18.3% smaller, 4.5x faster** |
| `zlib-3` raw BLOB (stdlib) | — | — | 20.1% smaller, 3.2x faster |
| `zlib-6` raw BLOB (stdlib) | — | — | 24.1% smaller, 1.4x faster |
| `zlib-9` raw BLOB (stdlib) | — | — | 25.0% smaller, 1.0x faster |
| `lzma` preset=1 raw BLOB | — | — | smallest (1.43 vs 2.27 MB on one row) but **1.5x slower than production** |
| `zstd-3` raw BLOB (NEW DEP) | — | — | 46% smaller, 11x faster |

`zlib-1` saves **99.3 ms per receipt** and losslessness was verified byte-identical on
every one of the 150 payloads (`zlib.decompress(new) == original`).

Two independent effects, worth separating:
- **Dropping base64** is pure win: it inflates binary by exactly 33% and costs CPU. This
  is where the 18.3% comes from — note `zlib-9` BLOB is 25% smaller than `zlib-9`+base64
  at identical compression.
- **Lowering the level** 9 → 1 is where the 4.5x speed comes from, and it costs ~7
  points of ratio (25.0% → 18.3%). On this payload shape level 9 buys very little over
  level 1 while costing 4.4x the CPU.

## Recommendation

**`zlib-1` raw BLOB is the right target.** It is stdlib, captures 88% of zstd-3's time
saving (99.3 of 112.5 ms), shrinks the biggest table's hottest column by 18.3%, and adds
zero supply-chain or ABI risk to a live money path. But it is **not** committed here: it
needs the schema move described below, which deserves its own change rather than riding
along with an instrumentation commit.

**Then decide on `zstandard` separately**, on its own merits: it would roughly double
both wins (46% smaller, 11x faster) and is also the right tool for the offline
`provenance_json` rewrite (zstd-19+dict measured 5.02-5.75x, ~46.8 GB → ~9 GB). That is
one dependency serving two large wins — a good trade, but it belongs to the documented
bump procedure, not to this change.

## Implementation contract (either codec)

- The encoding is already self-describing: every component carries a sibling
  `*_encoding` field, today `"zlib+base64+canonical-json-v1"` (and `-object-v1`,
  `-object-delta-v1`, `-v2` variants). Version it there — the decoder must dispatch on
  that tag, never guess from the bytes.
- **Keep a decoder for the old format.** There are nine `b64decode` sites across four
  files (`global_batch_runtime.py`, `control/live_health.py`,
  `calibration/market_anchored_live_fit.py`, `engine/event_reactor_adapter.py`) and
  106,846 existing `decision_log` rows that must stay readable. `event_reactor_adapter.py:2257`
  already rejects an unrecognized encoding, which is the correct shape to extend.
- Column type: these live inside `artifact_json` TEXT as JSON string values, so a raw
  BLOB cannot be embedded as-is. Either move the component to its own BLOB column, or
  keep it in JSON and accept base64 there — **which means the 18.3% requires the
  schema-side move, while the 4.5x speed does not.** That asymmetry is the real design
  decision, and it is why this is written up rather than committed: the speed win is
  available today by changing only the level, the size win needs a column.

## The one-token change, measured — and it is NOT free

Changing `level=9` → `level=1` at the ten `zlib.compress` sites while keeping base64 and
the existing tag (no schema or encoding change whatsoever), across the same 40 receipts:

```
level 9 + base64 (today)   :  96.27 MB   5102.0 ms
level 1 + base64 (1 token) : 104.93 MB   1238.3 ms
time : 4.1x faster, 96.6 ms saved per receipt
size : +9.0% LARGER
```

So the speed and the size do **not** come together for free: base64 re-inflates what
level 1 gives up, and the result is 9% more stored bytes on the biggest column in the
199.5 GB DB. Given the disk is at 95% and the operator's requirement is *both* faster
and smaller, **this is rejected as a standalone change** — it buys 96.6 ms by making the
storage problem worse.

The honest options are therefore:

| option | time saved / receipt | size | new dep | schema change |
|---|---|---|---|---|
| `level=1`, keep base64 | 96.6 ms | **+9.0% worse** | no | none |
| **`zlib-1` raw BLOB** | **99.3 ms** | **-18.3%** | no | **yes, needs a column** |
| `zstd-3` raw BLOB | 112.5 ms | -46% | **yes** | yes, needs a column |

Both real wins require moving the component out of the `artifact_json` TEXT blob into its
own BLOB column. That is the actual blocker — not the codec choice. The migration is
mechanical but touches the nine decode sites and 106,846 existing rows, so it wants its
own change with the dual-read decoder landed first.
