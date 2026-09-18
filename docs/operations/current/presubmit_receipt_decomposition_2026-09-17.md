# Pre-submit receipt store — the 516 ms decomposed (2026-09-17)

The consult flagged the compression-vs-DB-I/O split as UNVERIFIED and said to profile
`_store_global_auction_receipt` before assuming a codec swap recovers the median. Done.
Measured with the **real** payloads recovered from the largest recent
`decision_log` global-auction row (3.81 MB stored, 5 `*_zlib_b64` fields, 5.62 MB raw),
best-of-N on this host. The write leg ran against a throwaway WAL DB at production row
size, not the live file.

| stage | ms | share of p50 |
|---|---|---|
| **zlib-9 + base64 (production encode)** | **123.6** | 24% |
| canonical `json.dumps(whole artifact)` | 10.1 | 2% |
| `INSERT` + `commit()` (fsync) | 4.4 | 1% |
| **unaccounted — DB reads + delta-compute** | **377.9** | **73%** |
| accounted subtotal | 138.1 | |
| observed production p50 (`global_batch_runtime.py:9430`, n=3,269) | 516.0 | |

Proposed replacement encode, same payloads: **zstd-3 raw bytes = 11.1 ms.**

## What this settles

1. **The codec swap is real but not the majority.** zlib-9+base64 → zstd-3 BLOB saves
   **~112.5 ms, 21.8% of the p50**, losslessly, and simultaneously cuts ~46% of the
   stored bytes. Worth doing — it is the single cheapest change on the list — but it does
   not recover the 516 ms on its own. My earlier framing implied a larger share.
2. **The fsync is negligible: 4.4 ms of 516.** This independently confirms the earlier
   decision to reject `PRAGMA synchronous=NORMAL`: the durability guarantee costs
   ~1% of this path, so trading it away would buy almost nothing. Group-commit
   batching of this write is likewise not worth designing.
3. **73% is the delta-compute and the reads that feed it.** Five component builders run
   per receipt — `_book_native_side_receipt` (`:4051`), `_json_object_delta_receipt`
   (`:4428`), `_candidate_evaluations_delta_receipt` (`:4494`),
   `_keyed_object_list_delta_receipt` (`:4583`), `_book_native_side_delta_receipt`
   (`:4648`) — each diffing the current state against the prior receipt. That is where
   the time is, and it is *computation on the decision thread*, not I/O.

## Recommended order for this path

1. **Codec swap (do first).** ~112 ms off every submit and ~46% fewer stored bytes, one
   mechanical change, blast radius confined to `decision_log.artifact_json` and four
   files. Version it through the existing `"zlib+base64+canonical-json-v1"` tag and keep
   a decoder that accepts both, so historical rows stay readable.
2. **Then attack the 377.9 ms delta-compute**, which needs a design decision rather than
   a swap. The consult's framing is the right one: the invariant at submission is that
   the decision's *proof is decided and will become durable*, not that a full diffed
   audit envelope has already been materialized. Options, in increasing order of change:
   make the delta incremental (maintain it as candidates are scored, rather than diffing
   the whole prior receipt at the end); write a small durable **decision certificate**
   that references already-durable evidence and materialize the bulk envelope after
   submission; or move the component builders off the decision thread entirely with a
   durable hand-off. Each needs its crash story stated: what an operator can prove about
   a submitted order if the process dies between submit and envelope materialization.
3. **Instrument per-stage first.** There is no per-builder timing today. Add a timer
   around each of the five builders so the 377.9 ms is attributed before anyone
   restructures it — the same discipline that just refuted three other hypotheses in this
   audit.

## Method note

Measured, not inferred: the earlier 65 ms figure came from timing one 1.99 MB payload in
isolation, which understated the production encode (123.6 ms across all five fields) and
said nothing about the rest of the function. Timing the whole operation from the log line
production already emits, then decomposing it against real payloads, is what produced an
honest split. `516 - 138.1` is a residual, so it is labelled as one; it is not itself a
measurement of the delta-compute, and step 3 above exists to replace it with one.
