# certificate_v1_freeze — consolidated deliverable (design-C)

Every certificate/receipt/settlement hash, the E1/E4-safety verdict, the golden-vector freeze harness, and the v2 envelope. Verified by direct reads + bounded read-only queries against live `zeus_trades.db` (26 certificate types; recompute samples) + a repo-wide hash-site census. Absence-of-path claims carry the confirming grep.

Canonical rule (`src/decision_kernel/canonicalization.py`): `stable_hash(v) = sha256(json.dumps(normalize(v), sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False))`; `normalize`: datetime→UTC ISO `"+00:00"`→`"Z"`, Decimal→`format(d.normalize(),"f")`, Enum→`.value`, dict→str-keyed+sorted, tuple/set→list.

Census: **71 `stable_hash(` + 174 `hashlib.sha256(` = ~245 raw compute sites.** These collapse to the ~18 distinct identity-bearing preimage shapes below; the remaining ~150 are repeated *generic idempotency / local content digests* (row group G). Coverage map at the end.

---

## 1. Hash inventory

`kind` legend: **CERT**=certificate identity · **EDGE**=parent-edge binding · **SUB**=sub-identity field embedded in a cert payload · **RCPT**=receipt identity · **BOOK**=book/epoch identity · **IDEM**=generic idempotency/local content · **N/A**=not a hash.

| Hash | kind | Compute (file:line) | Verify (file:line) | Preimage fields | Serialization |
|---|---|---|---|---|---|
| `payload_hash` | CERT | `certificate.py:105` `stable_hash(payload)` | `ledger.py:227` re-hash stored `payload_json` on idempotent hit | **ENTIRE cert payload dict** (incl. `opportunity_book`, `qkernel_execution_economics`) | `stable_hash` |
| `certificate_hash` | CERT | `certificate.py:133,145` `certificate_hash_for(header)` | `verifier.py:181` `!= header.certificate_hash` | type, schema_version, canonicalization_version, semantic_key, claim_type, mode, decision_time, source/agent/persisted_at, **parent_edges** (ordered {role,cert_hash,type,required}), authority_id/version, algorithm_id/version, config_hash, model_version_hash, **`payload_hash`** | `stable_hash`; ts→`"Z"`; edges order-sensitive |
| `certificate_id` | CERT | `certificate.py:134` | — | `f"{type}:{certificate_hash[:24]}"` | prefix |
| `CompileFailure.failure_id` | CERT | `ledger.py:39` | — | `{event_id,decision_time,mode,claim_type,stage,reason_code,parent_hashes}` | `stable_hash[:32]` |
| parent edge | EDGE | `ledger.py:190-208` (store) | `verifier.py:409-420` | FK to parent's `certificate_hash` | (no new hash) |
| `qkernel_current_state_identity_hash` | SUB | `canonicalization.py:149-157` | `canonicalization.py:208`, `verifier.py:210` | ~120 named economics identity fields (`_QKERNEL_CURRENT_STATE_IDENTITY_FIELDS` [+buy_fak]) | `stable_hash` |
| `bin_labels_hash` | SUB | `event_reactor_adapter.py:20390` | `verifier.py:1654,1656` | `tuple(str(bin.label) …)` | `stable_hash` |
| `members_json_hash` | SUB | `event_reactor_adapter.py:21085` | `verifier.py:1655` | `tuple(sorted(float member set))` | `stable_hash` |
| `posterior_identity_hash` | SUB | `event_reactor_adapter.py:30054,30296` | `verifier.py:271` | posterior identity fields | `stable_hash` |
| `aggregate_event_hash` / `aggregate_pre_submit_event_hash` / `aggregate_execution_command_event_hash` / `aggregate_cap_transition_event_hash` / `executor_native_intent_hash` / `cost_basis_hash` / `executable_snapshot_hash` / `sample_hash` / `p_cal_*` / `p_live_*` | SUB | `event_reactor_adapter.py` (44-site `stable_hash` cluster) + `qkernel_spine_bridge.py:*` | bound field-equality in `verifier.py` (`:1104-1123`,`:1390`,`:1644-1655`, etc.) | each = a **semantic subset** (ids / probability vectors / economics) — **never** the zlib blobs or opportunity_book bulk | `stable_hash` |
| no-submit `projection_hash` | SUB | `verifier.py:1676` | `verifier.py:1677` | `{event_id,final_intent_id,side_effect_status,proof_accepted,submitted,executable_snapshot_id}` | `stable_hash` |
| **`receipt_hash` (global auction)** | RCPT | `global_batch_runtime.py:1783` (+`:2265` dup-path) `sha256(encoded)` | **string-compare only** (`live_health.py:5130,5162`; `global_batch_runtime.py:1220`) — **never recomputed** | **ENTIRE receipt incl. the 4 inline `*_zlib_b64` strings** + all `*_sha256` + identities | `sha256(json.dumps(receipt, default=str, sort_keys=True, separators=(",",":")))` |
| `book_native_side_states_sha256` | RCPT | `global_batch_runtime.py:814,892` | `live_health.py:5101`; `global_batch_runtime.py:1224` | **UNCOMPRESSED** `{fields,rows}` bytes | `sha256`; paired w/ `book_native_side_states_zlib_b64` |
| `candidate_evaluations_sha256` | RCPT | `global_batch_runtime.py:1756` | `live_health.py:5101-5104,5202-5205` | UNCOMPRESSED `evaluation_json` | paired w/ `candidate_evaluations_zlib_b64` |
| `holding_auction_coverage_sha256` | RCPT | `global_batch_runtime.py:1743` | same | UNCOMPRESSED `holding_coverage_json` | paired w/ `holding_auction_coverage_zlib_b64` |
| `buy_minimum_marketable_repairs_sha256` | RCPT | `global_batch_runtime.py:1770` | same | UNCOMPRESSED `minimum_repair_json` | paired w/ `buy_minimum_marketable_repairs_zlib_b64` |
| `book_native_side_delta_sha256` | RCPT | `global_batch_runtime.py:926` | `global_batch_runtime.py:1224` | UNCOMPRESSED delta bytes | paired w/ `*_delta_zlib_b64` |
| `_global_auction_payload_identity` / `_global_auction_decision_payload_identity` | RCPT | `global_batch_runtime.py:1090-1113` / `:1116-1139` | — | tuples of `(encoding, *_sha256)` — **not the blobs** | `sha256` |
| `current_global_book_epoch_identity` | BOOK | `global_auction_universe.py:158-170` | — | sorted **raw** asset-state repr + captured_at ISO | `sha256` |
| `_canonical_raw_book_hash` | BOOK | `global_auction_universe.py:406-414` | — | `dict(raw_book)` | `sha256(json.dumps)` |
| **`receipt_hash` (NO_SUBMIT)** | RCPT | `no_submit_receipts.py:48` `sha256(_receipt_json(receipt))` | `:62-70` string-compare → **`EdliReceiptHashDriftError`** | **ENTIRE receipt payload incl. `opportunity_book`** (popped only when `None`, `:281-282`) | `sha256(json.dumps(payload, sort_keys=True, separators=(",",":")))` `:355` |
| no-submit `projection_hash` | SUB | `no_submit_receipts.py:358` | — | `{event_id,final_intent_id,side_effect_status,proof_accepted,…}` | `stable_hash` |
| `increment_position_generation` | IDEM | `executor.py:2300-2310` | — | `"\x1f".join(position_id,phase,order_id,shares,cost)` | `sha256` |
| `_canonical_payload_hash` | IDEM | `executor.py:2601-2603` | — | venue order payload | `sha256(json.dumps default=str)` |
| **G. generic local digests (~150 sites)** | IDEM | `venue/polymarket_v2_adapter.py`(6), `state/venue_command_repo.py`(2), `execution/command_recovery.py`(7), `execution/exit_lifecycle.py`(4), `execution/edli_*_resolver.py`, `events/live_order_aggregate.py`(3), `solve/solver.py`(5), `reduce/generation.py`(2), `probability/joint_q_band.py`(2), `data/*`(forecast sample/content) | local | each hashes its **own domain object** for idempotency/content-addressing — **none touch a cert payload, the zlib blobs, or opportunity_book** | `sha256`/`stable_hash` |
| settlement | N/A | `settlement_semantics.py:57` `settlement_preimage_offsets` | — | numeric probability-integration bounds — **NOT a hash** | — |

Settlement note: there is **no cryptographic settlement hash.** `SettlementCertificate` (`claims.py:51`) has no special hasher and no construction site found; if built it would use the generic `certificate_hash`/`payload_hash`.

---

## 2. E1/E4 safety verdict

### E4 — summarize rejected candidates in `ActionableTradeCertificate`: **UNSAFE in-place; requires v2.**
`opportunity_book` (candidates[] of every rejected candidate ≈ 92 KB of a measured 91,856 B row) is inside `ActionableTradeCertificate.payload`, hashed in full: `payload_hash = stable_hash(payload)` (`certificate.py:105`) → `certificate_hash` (`:145-166`) → `certificate_id` (`:134`).
- Ledger **re-hashes stored `payload_json`** vs `payload_hash` on every idempotent re-insert (`ledger.py:210-228`) → summarization raises `DECISION_CERTIFICATE_PAYLOAD_HASH_CORRUPT`.
- Recomputing `payload_hash` instead changes `certificate_hash` → dedup key fires `CertificateSemanticDriftError` (`ledger.py:106`); parent-edge FKs dangle (`verifier.py:980,1251`).
- **Second frozen identity:** same `opportunity_book` → `edli_no_submit_receipts.receipt_hash` (`no_submit_receipts.py:48,281-282,355`) → `EdliReceiptHashDriftError`.
- Fixable because **no verifier reads `opportunity_book`** (0 hits in `src/decision_kernel/`) — diagnostic-only yet hash-bound. `book_id` already commits only to `{event_id,family_id,candidate_ids,selected_candidate_id}` (`opportunity_book.py:379`), not rejected economics. **v1 rows never rewritten.**

### E1 — move the four `*_zlib_b64` to BLOB columns: **SAFE, under one invariant.**
The base64 blobs are committed by no recomputable identity:
- Each paired with a `*_sha256` over **uncompressed** bytes (`:814,892,1743,1756,1770`); every read verifies `sha256(decompress(blob))==*_sha256` (`live_health.py:5101-5104,5185-5188,5202-5205`; `global_batch_runtime.py:1224`) — storage-independent.
- `payload_identity`/`decision_payload_identity` commit to `(encoding,*_sha256)` tuples, not blobs (`:1090-1139`).
- `receipt_hash` commits to the inline strings **but is minted once and never recomputed from storage**: `rg "sha256\([^)]*artifact"` → **0 hits**; the only 3 hash-assign sites are mint/write (`global_batch_runtime.py:1783,2265`; `no_submit_receipts.py:48`); all 4 compare sites are string compares.
- **Precedent:** the compaction path (`global_batch_runtime.py:1806+`) already stores an `artifact_json` diverging from the minted receipt (heavy fields stripped/referenced) — the receipt_hash was computed at `:1783` *before* that compaction. Stored-form ≠ hashed-form is already supported.

**Required invariant:** BLOB holds the exact compressed bytes (base64-**decode**; never decompress+recompress), and every `summary["<field>_zlib_b64"]` reader is repointed to the BLOB — sites: `global_batch_runtime.py:1199-1204`; `live_health.py:5093,5133,5144,5148,5179`; `event_reactor_adapter.py:1310-1312` (SQL `json_extract`). A missed reader fails **loudly** (KeyError/None), never as silent identity corruption.

---

## 3. Golden-vector freeze harness

**Validated live now:** `sha256(payload_json_bytes)==payload_hash` byte-exact **26/26** types (incl. 91,856 B ActionableTrade); round-trip `stable_hash(json.loads(payload_json))==payload_hash` **26/26** (the exact `ledger.py:227` check); `certificate_hash_for(rebuilt_header)==certificate_hash` **5/5** incl. 19-edge ActionableTrade.

**Two subtleties the harness MUST encode (else false failures):**
1. Timestamp columns stored via `ledger._dt` as `"+00:00"` but hashed as `normalize`'s `"Z"` — re-normalize each (`fromisoformat`→`astimezone(utc)`→`isoformat().replace("+00:00","Z")`), not the raw column string.
2. `parent_edges` order-sensitive, in a separate table — reconstruct `... FROM decision_certificate_edges WHERE child_certificate_id=? ORDER BY rowid` (rowid = insertion order = tuple order, since `_persist_edges` iterates `header.parent_edges`).

**Shape** — `tests/decision_kernel/test_certificate_v1_freeze.py` + committed fixture `certificate_v1_golden.jsonl` (generated once via the bounded read-only pattern; **test needs no DB**):
- **Capture:** ≥100 `decision_certificates` rows spanning **every** `certificate_type` (≥26 present; ≥4 each; ≥3 ActionableTrade + all multi-edge types) — pin raw `payload_json` bytes, header columns, rowid-ordered edges, stored `payload_hash`+`certificate_hash`. Plus ≥N `decision_log` rows across all 3 receipt modes (`global_single_order_auction[_delta|_duplicate]`) — `artifact_json`, `receipt_hash`, 4×(`*_sha256`+`*_zlib_b64`). Plus ≥N `edli_no_submit_receipts` — `receipt_json`+`receipt_hash`.
- **Recompute & assert per row from pinned bytes:** (1) byte-exact **and** round-trip `payload_hash`; (2) `certificate_hash` from columns+edges with both subtleties; (3) auction: `sha256(canonical_receipt)==receipt_hash` (zlib_b64 inline) and per-component `sha256(decompress(b64decode))==*_sha256`; (4) NO_SUBMIT: `sha256(receipt_json)==receipt_hash`; (5) every recomputed value `== pinned golden` (catches lockstep changes to canonicalization).
- **Fails** on any change to `normalize`/`canonical_json` separators/`sort_keys`/`ensure_ascii`, header field set, `_dt`, edge ordering, or receipt shape. Pure-Python + fixture → CI-landable, no daemon.

---

## 4. v2 envelope sketch

```
CertificateEnvelopeV2:
  schema_version: 2
  decision_identity:   event_id, decision_time, semantic_key, claim_type, mode
  economics_identity:  current_state_identity_hash, selected_qkernel_economics_digest
  selected_candidate_identity: candidate_id, condition_id, token_id, direction, book_id
  model_run_identity:  authority_id/version, algorithm_id/version, config_hash, model_version_hash
  payload_entries: [
    { role, semantic_content_hash: sha256(canonical(full_content)),   # identity — FROZEN
      storage_hash: sha256(stored_bytes),
      codec_id: "raw-json" | "zlib+base64+canonical-json-v1" | "blob-zlib-v1" | "summary+ref" } ]

certificate_hash_v2 = stable_hash({ schema_version, decision_identity, economics_identity,
    selected_candidate_identity, model_run_identity,
    payload_entries: [{role, semantic_content_hash, codec_id}] })    # NOT storage_hash, NOT bytes
```

- Identity commits to each role's `semantic_content_hash`+`codec_id`, never `storage_hash`/bytes → **E1** = a `codec_id` change (`…canonical-json-v1`→`blob-zlib-v1`), identity untouched.
- **E4** = redefine the `opportunity_book` role's semantic content to the identity core `book_id` already commits to (`candidate_ids`+`selected`+winner economics); move rejected-candidate economics to a separate `role:"rejected_candidate_diagnostics"` entry **excluded from `certificate_hash_v2`** → summarizable/relocatable freely.
- **Discipline:** v1 rows stay `schema_version=1`, frozen (§3 harness enforces). E1/E4 land only for v2 writers — or, for E1, as the byte-preserving v1 relocation in §2 (alters no preimage). No dual-write ever rewrites a v1 preimage.

---

### Confidence & residual
All four sections backed by direct reads + cited greps + live-DB recompute (26/26 payload, 5/5 certificate_hash, zero `sha256(...artifact...)`). Not personally swept: `src/engine/replay.py` + tests for an out-of-band `receipt_hash` full-recompute — non-blocking because (a) zero `sha256(...artifact...)` repo-wide and (b) `receipt_hash` is mint-only + string-compared; if such a tool surfaces, E1 still holds provided the §2 invariant hydrates identical base64 bytes before any recompute.
