# Trade receipt keyed delta — Plan

Date: 2026-07-28
Branch: `fix/trade-receipt-keyed-delta-20260728`
Status: closed — landed and verified against live data (read-only replay of decision rows
255920→255940 reconstructed the canonical SHA-256 exactly, at 87.21% size reduction).
Retained for the delta-encoding rationale, not as in-flight work.

## Background

The live `state/zeus_trades.db` is about 146 GiB. Recent bounded samples show
`decision_log` is still the active growth leader after earlier snapshot and
receipt compaction. In a 500-row sample,
`global_single_order_auction_delta` carried about 63.5 MB of JSON; candidate
receipts were inline in 143/240 recent auction rows and accounted for about
57.7 MB.

The remaining fallback is structural, not random compression noise. Candidate
payload v12 stores two large indexed collections:

- `buy_candidate_index`: about 353 KB raw / 1,071 rows in a current live cut;
- `buy_condition_side_masks`: about 49 KB raw / 673 rows.

The v2 semantic delta treats both collections as atomic top-level values.
Adding or changing one row therefore writes the entire collection and often
crosses the `delta < 50% of full` admission threshold, causing a 300–900 KB
inline keyframe. A live 255920→255940 sample changed one row in each indexed
collection. The implemented keyed one-hop codec reproduced that live cut's
canonical hash exactly at 39,016 bytes versus the 305,096-byte full candidate
blob (87.21% reduction).

## Scope

See sibling `scope.yaml`.

Money-path location:

`decision -> immutable receipt -> monitoring/replay audit`

This changes only the physical encoding of one already content-addressed,
hash-verified decision certificate. It does not change candidate generation,
probability, edge, selection, order submission, lifecycle, or DB schema.

SCOPE: new candidate delta rows written after deployment and the health reader
that reconstructs them.

DRAIN: every new delta points directly to one self-contained full anchor;
restart, missing anchor, invalid shape, or hash mismatch falls back to a new
full keyframe.

RESET: rollback stops emitting v3; the reader retains v1/v2 compatibility and
historical v3 rows remain self-describing and hash-verifiable.

## Deliverables

- Candidate delta v3 with keyed patches for:
  - `buy_candidate_index`, keyed by its stable five-field market/action
    identity while retaining the per-epoch `candidate_id` value;
  - `buy_condition_side_masks`, keyed by `condition_id`.
- Preserve the existing detailed-candidate semantic delta, one-hop anchor
  bound, canonical full-payload SHA-256, and fail-closed reconstruction.
- Backward-compatible live-health decoding for legacy object delta v1,
  semantic delta v2, and keyed delta v3.
- Behavioral antibodies proving:
  - one-row indexed changes reconstruct byte-identically;
  - malformed/duplicate keyed collections fail closed;
  - v3 remains below half of the equivalent full payload on a realistic
    high-cardinality fixture;
  - the health reader reconstructs the engine's v3 payload.

## Verification

- Focused engine receipt and live-health decoder tests.
- Python compile and `git diff --check`.
- Source-rationale/test-topology changed-surface checks.
- Planning-lock check against this plan.
- After landing: exact loaded SHA, live receipt encoding, reconstruction/hash
  proof, and a bounded before/after `decision_log` byte-rate sample.

## Non-goals and safety

- No deletion, `VACUUM`, retention, table/index/schema change, or live DB copy.
- No weakening of audit evidence or replacement of full-certificate hashes
  with summaries.
- No chained deltas and no stale-as-fresh behavior.
- No control command or gate semantic change; `src/control/live_health.py`
  only gains a compatible receipt decoder.

## Implementation evidence

- Focused engine/control compatibility suite: 12 passed.
- Python compile, `git diff --check`, planning lock: passed.
- Source-rationale changed-surface check: 0 findings.
- Live read-only replay of decision rows 255920→255940:
  - full candidate blob: 305,096 base64 bytes;
  - v3 keyed delta: 39,016 base64 bytes;
  - reduction: 87.21%;
  - changed BUY-index rows: 1;
  - changed condition-mask rows: 1;
  - reconstructed canonical SHA-256: exact.
- `black` and `ruff format` were not available in the current local venv;
  compile and whitespace checks ran instead.
