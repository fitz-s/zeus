# Certificate v2 envelope — concrete, implementable spec

Lifecycle: created=2026-07-21; last_reviewed=2026-07-21. Design-only; zero runtime code.
Pairs with — and **concretizes / supersedes the sketch in** — `certificate_v1_freeze.md §4`.
Authority basis: `tests/test_certificate_v1_golden_freeze.py` (the frozen v1 identity), `certificate_e1e4_safety_trace.md` (E1-SAFE / E4-needs-v2 verdicts), `authority_matrix.md` §CRITIC-MATRIX B1 (world `decision_certificates` = live money authority; trade copy = dead ghost), `REDESIGN_v2.md` §0.3/§3/§6 (wave placement).

Grounded in the real code: `certificate.py`, `canonicalization.py`, `ledger.py`, `verifier.py`, `no_submit_receipts.py`, `opportunity_book.py`, `state/schema/decision_certificates_schema.py`.

---

## 0. What is frozen, and which copy is authority

Two independent, cryptographically frozen identity chains. v2 must add a version WITHOUT moving one byte of a v1 preimage.

**Chain 1 — certificate identity** (`src/decision_kernel/`):
```
payload_hash      = stable_hash(payload)                       # certificate.py:105  — ENTIRE payload dict
certificate_hash  = certificate_hash_for(header)              # certificate.py:133,145 — header fields incl.
                                                              #   schema_version, canonicalization_version,
                                                              #   ORDERED parent_edges, and payload_hash
certificate_id    = f"{certificate_type}:{certificate_hash[:24]}"   # certificate.py:134
```
`certificate_hash_for` (certificate.py:145-166) commits to `payload_hash` (the digest), **not** the raw payload, and it **already commits to `schema_version` and `canonicalization_version`** (lines 148-149). Stored form: ledger writes `payload_json = canonical_json(cert.payload)` (full payload) + `payload_hash` + `certificate_hash` + the `schema_version`/`canonicalization_version` columns (`decision_certificates_schema.py:12-13`). On every idempotent retry the ledger re-derives `stable_hash(json.loads(payload_json)) == payload_hash` (`ledger.py:227`) and fires `DECISION_CERTIFICATE_PAYLOAD_HASH_CORRUPT` on mismatch.

**Chain 2 — NO_SUBMIT receipt identity** (`src/events/no_submit_receipts.py`):
```
receipt_json = _receipt_json(receipt)                         # :237-355 — omit-when-None field pruning
receipt_hash = sha256(receipt_json.encode())                  # :48
receipt_id   = "edli_no_submit:" + sha256({event_id, final_intent_id, side_effect_status})  # :214-223
```
`receipt_id` is **already opportunity_book-independent** (only the three-field natural key). `receipt_hash` binds the **entire** receipt payload incl. `opportunity_book` (popped only when `None`, `:281-282`). `schema_version` is stored as a column (`:161`) but is **not** in the `receipt_hash` preimage today.

**Authority (do not re-invert — `authority_matrix.md` §CRITIC-MATRIX B1):** the **world** `decision_certificates` (1.35M rows) is the **live money authority** read by money-path gates (`check_edli_live_canary_gate.py:342/365`, `check_live_restart_preflight.py:1144-1166`, `live_profit_audit.py:594`) and revoked by world `fact_revocations`. The **trade** copy (58K rows) is a pre-PR-S4b **dead ghost** with no live writer. Certificates are cryptographic commitments — not reconstructible. Consequence for this spec: the v2 grammar is **DB-agnostic** (it lives in `certificate.py`/`canonicalization.py`, independent of which connection the ledger writes), but the **migration** rules (§5) and **ancestor preservation** (§4) apply to the **world** authority copy; the trade ghost is out of scope (drop, never migrate).

**The v1 golden vectors are function-level, not row-level.** `test_certificate_v1_golden_freeze.py` builds synthetic certs A/B/C in-memory and pins their real production output. It therefore freezes the identity *function*, independent of any DB rows — so it is correct regardless of the world/trade authority question. There is no `certificate_v1_golden.jsonl` live-row fixture (grep-confirmed absent); if one is ever added it must be sourced from **world** (authority), never trade.

---

## 1. Versioning discriminator

**`schema_version` is the discriminator. Exact values:**

| field | v1 (frozen) | v2 (new) | role |
|---|---|---|---|
| `schema_version` (INTEGER col) | `1` | `2` | **envelope / identity-preimage structure** version |
| `canonicalization_version` (TEXT col) | `"decision-kernel-json-v1"` | `"decision-kernel-json-v1"` (**unchanged**) | byte-level JSON rules (normalize / separators / sort_keys / ensure_ascii) |

The two columns carry **orthogonal** meanings, and v2 changes exactly one of them:
- `canonicalization_version` = *how bytes are produced* (`normalize` + `canonical_json`, canonicalization.py:18-46). **v2 does not touch canonicalization** — same separators, same datetime→"Z", same sorting. So it stays `"decision-kernel-json-v1"`.
- `schema_version` = *which fields enter the preimage*. v2 changes **only** the `payload_hash` preimage (§2). So it goes `1 → 2`.

**Why `schema_version` is a safe discriminator, verified:**
1. It is **already hash-bound** on the cert side — `certificate_hash_for` includes `header.schema_version` (certificate.py:148). A row cannot claim to be v1 while carrying a v2 hash, or vice versa, without breaking `certificate_hash`. The discriminator is itself tamper-evident.
2. **No code branches on `schema_version == 1`** (grep of `src/decision_kernel/` + schema: zero `== / != / assert` sites). Introducing `2` hits no assertion.
3. `build_certificate` is **always** called with the default `schema_version=1` today (no non-default caller in `src/`). W3 flips specific writers to `2`; the change is localized, not global.

**Reader rule (both chains):** read `schema_version` from the row, then hash under that version's rules. v1 rows are hashed **exactly as today, forever**; v2 rows under §2.

**Receipt-side discriminator (make it hash-bound too):** because `receipt_hash` does **not** currently commit to `schema_version`, a v2 receipt MUST add `"schema_version": 2` as a first-class key inside `_receipt_json`'s payload, so `receipt_hash` commits to the version (matching the cert side's tamper-evidence). v1 receipts add no such key → their `receipt_json` bytes stay byte-identical and frozen.

---

## 2. v2 identity preimage — the `_diagnostics` annex

### 2.1 The one new rule

For `schema_version == 2`, redefine only `payload_hash`:

```
v1:  payload_hash = stable_hash(payload)
v2:  payload_hash = stable_hash(identity_view(payload))

identity_view(p) = { k: v for k, v in p.items() if k != "_diagnostics" }   # strip exactly one reserved key
```

**Everything else is unchanged.** `certificate_hash_for`, `certificate_id`, `parent_edges`, the ledger INSERT, the schema DDL, `payload_json` storage (still the full payload) — all byte-identical between v1 and v2. The single v2 delta is the `identity_view` projection applied to the `payload_hash` input when `schema_version == 2`.

**`_diagnostics` is a new reserved top-level payload key.** The payload today already uses reserved `_`-prefixed keys that are load-bearing and verifier-read (`_parent_times` certificate.py:183; `_edli_q_source` verifier.py:55; `_edli_day0_*` throughout day0_authority.py). Therefore `identity_view` strips **exactly** `_diagnostics` — **not** all underscore keys — or it would silently drop real identity fields the verifier reads. `_diagnostics` is the single, named home for everything excluded from identity.

Rejected alternative (named decision A): a *field-specific* projection that strips `payload["opportunity_book"]["candidates"]` in place, leaving the payload shape flat. Rejected because the identity/diagnostics boundary would live in projection code, invisible in the data, and would need editing for every future diagnostic field — a missing edit silently pulls a diagnostic into identity. The `_diagnostics` annex **names the boundary in the shape** (Wittgenstein: an unnamed distinction gets violated), makes the projection a trivial one-key strip, and generalizes to E1 evidence, E7 provenance, and forecast_posteriors diagnostic arrays without new code.

Rejected alternative (named decision B): `certificate_v1_freeze.md §4`'s `certificate_hash_v2 = stable_hash({schema_version, decision_identity, economics_identity, selected_candidate_identity, model_run_identity, payload_entries})`. Rejected because it **re-invents the header**: `decision_identity` (event_id/decision_time/semantic_key/claim_type/mode) and `model_run_identity` (authority/algorithm/config/model_version) are **already** hashed by `certificate_hash_for`, and its `payload_entries[role].content` restructuring would break every `verifier.py` read of `payload["q_live"]`, `payload["candidate_id"]`, … by name. Keeping `certificate_hash_for` and changing only `payload_hash` is the minimal shape that keeps the verifier working unchanged and avoids two sources of truth for the same identity fields.

### 2.2 Exact IN / OUT partition for `ActionableTradeCertificate` (the E4 carrier)

The `IN` set is grounded in what `verify_actionable_trade` → `_verify_actionable_payload` (verifier.py:474-522) actually reads.

**IN the v2 identity preimage** (everything except `_diagnostics`):
- Verifier-bound top-level scalars: `event_id, event_type, causal_snapshot_id, family_id, candidate_id, condition_id, token_id, direction, executable_snapshot_id, fdr_family_id, kelly_decision_id, risk_decision_id, live_cap_usage_id, final_intent_id, strategy_key` and `submitted, execution_command_id, side_effect_status, action_score, trade_score, p_fill_lcb, q_live, q_lcb_5pct, c_fee_adjusted, c_cost_95pct, native_quote_available` (verifier.py:476-516).
- Probability-authority block: `day0_probability_authority` and the `_edli_*` reserved keys the day0/replacement path reads (verifier.py:249-283; day0_authority.py).
- **Opportunity-book identity core**: `opportunity_book.book_id` and `opportunity_book.selected_candidate_id`.
  - `book_id = "opportunity_book:" + stable_hash({event_id, family_id, candidate_ids, selected_candidate_id})` (opportunity_book.py:379-386). It commits to **which candidates existed** (the full `candidate_ids` list) and **who won** — and is **invariant** under any change to a rejected candidate's *economics*. Keeping it IN identity means: you cannot secretly add/drop a candidate or change the winner without breaking `certificate_hash`.

**OUT of the v2 identity preimage** (the `_diagnostics` annex):
- `_diagnostics.opportunity_book.content` — the volatile bulk: `candidates[]` (each loser's `q_live, cost, edge_lcb, rejection_reason`), `loser_reasons`, `family_rank`/`global_rank`, `cache_summary`. This is the ~92 KB that E4 summarizes.
- `_diagnostics.opportunity_book.diagnostics_hash` — the **single commitment field**: `sha256(canonical_json(content))`. Content-addresses the diagnostics for tamper-evidence (retrieve → recompute → compare). Because it lives in the annex, it is **out of identity** — summarizing the book produces a new `content` and a new `diagnostics_hash` **without touching `payload_hash`**.
- `_diagnostics.opportunity_book.codec_id` — `"raw-json" | "summary-v1" | "blob-zlib-v1"` (E1/E4 storage form).
- `_diagnostics.<evidence>` — any compressed-evidence entry (§3).

**Security decomposition (the core insight):** the two properties the task requires are split across two commitments, one IN and one OUT of identity:
- *Membership + winner* are anchored by `book_id` (IN identity) → strongly tamper-evident, cannot change without breaking `certificate_hash`.
- *Rejected-candidate economics* are anchored by `diagnostics_hash` (OUT of identity) → tamper-evident against the stored bytes, but **free to summarize/relocate** because no verifier and no money path reads them (`certificate_e1e4_safety_trace.md` TRACE 2: zero `opportunity_book` reads in `verifier.py`). This is the correct strength for diagnostic-only data.

### 2.3 Walking the v1 golden vectors — proof v1 stays valid and v2 has the E4 property

**CERT A** (`ClockModeCertificate`, payload `{"mode":"NO_SUBMIT","note":"golden-freeze-a"}`, no `_diagnostics`): `identity_view` strips nothing, so a v2 cert over the same payload has `payload_hash == GOLDEN_A_PAYLOAD_HASH` (byte-identical). Its `certificate_hash`/`certificate_id` differ only because `schema_version=2` enters `certificate_hash_for`. → For any diagnostics-free payload, **v2 ≡ v1 at the payload-identity level**; `identity_view` is a conservative extension. The v1 row is untouched (still `schema_version=1`).

**CERT B** (`ExecutionCommandCertificate`, 3 parent edges, no `_diagnostics`): same — v2 `payload_hash == GOLDEN_B_PAYLOAD_HASH`. Edge order stays load-bearing because `parent_edges` is hashed positionally by the **unchanged** `certificate_hash_for`; the reversed-edge antibody (`GOLDEN_B_CERTIFICATE_HASH_EDGES_REVERSED`) holds identically for v2.

**CERT C** (`ActionableTradeCertificate` carrying `opportunity_book`): this is where v2 bites.
- The v1 row keeps `GOLDEN_C_PAYLOAD_HASH = stable_hash(full payload incl. candidates[])` **forever** (`schema_version=1`, never rewritten).
- The v2 form of the **same decision** moves `candidates[]` into `_diagnostics.opportunity_book.content`, keeping `opportunity_book:{book_id, selected_candidate_id}` in identity. Then `payload_hash_v2 = stable_hash(payload − _diagnostics)`. This is a **different** value from `GOLDEN_C` — and that is correct: v2 is a **new write with its own identity**, never a rewrite of the v1 row (§5 dedup guardrail keeps them from colliding).
- **Selected-candidate economics identity is preserved.** The winner's economics (`candidate_id, q_live, q_lcb_5pct, c_fee_adjusted, trade_score, action_score, …`) are **top-level** payload fields that `identity_view` keeps, and `book_id` still commits to `selected_candidate_id`. The redundant copy of the winner inside `candidates[0]` moves to `_diagnostics`, but nothing about the winner's identity is lost.
- **Rejected-candidate churn no longer perturbs identity.** `test_actionable_trade_opportunity_book_mutation_breaks_certificate_identity` proves the v1 property: mutating a loser's `rejection_reason` changes `payload_hash`/`certificate_hash`/`certificate_id`. The **v2 antibody is its exact inverse**: the same mutation, now inside `_diagnostics`, is stripped by `identity_view` → `payload_hash_v2` (and thus `certificate_hash`, `certificate_id`) is **invariant**. v1 says "mutation breaks identity → in-place summarization is unsafe"; v2 says "annex mutation cannot break identity → summarization is safe."

**Round-trip audit (v2 form of `ledger.py:227`):** `stable_hash(identity_view(json.loads(payload_json))) == payload_hash` holds for v2 by construction, because `payload_json` stores the full payload (annex included) and `identity_view` strips the annex before hashing — the same projection the writer used.

---

## 3. E1 under v2 — compressed evidence base64→BLOB without moving the preimage

E1 relocates zlib-compressed evidence from base64-in-JSON to a BLOB column. Under v2 this is identity-neutral by construction, and the invariant is stated for both cases.

**Case (a) — evidence carried by a certificate payload (the v2 default):** the compressed evidence lives under `_diagnostics.<evidence> = {codec_id, content_hash, storage_ref}` where `content_hash = sha256(<decoded compressed bytes>)`. Because the whole `_diagnostics` annex is excluded from `payload_hash` (§2), moving those bytes between codecs (`"…base64+canonical-json-v1"` → `"blob-zlib-v1"`) touches **no** preimage. The `content_hash` provides tamper-evidence and is computed over the **decoded compressed bytes**, so it is codec-independent: base64-in-JSON and BLOB hydrate the identical byte string.

**Case (b) — the general invariant, for any evidence that IS identity-load-bearing:** the preimage commits to `content_hash = sha256(<canonical byte definition>)` + `codec_id`, and **never** to the base64 string or the physical storage bytes. The canonical byte definition is the **decoded, still-compressed** bytes.

> **E1 INVARIANT (precise).** A codec change is identity-preserving **iff** it reproduces the exact decoded compressed byte string. `base64-decode → the exact same compressed bytes` is **ALLOWED**. `decompress → recompress` is **FORBIDDEN**: zlib output is not canonical (level, memlevel, dictionary, and library version can all change the compressed bytes for identical decompressed content), so recompression can move `content_hash` even when the plaintext is unchanged. A missed reader must fail **loudly** (KeyError/None), never as silent identity corruption.

This is the versioned generalization of the v1 relocation already proven safe for the global-auction receipt (`certificate_e1e4_safety_trace.md` TRACE 1 / `certificate_v1_freeze.md §2`): there, `receipt_hash` is minted once and only ever string-compared (never recomputed from storage), so a byte-preserving base64→BLOB relocation is safe even for v1. v2 makes the same guarantee **structural** rather than incidental — it holds regardless of whether any identity path recomputes from storage.

---

## 4. Parent-chain / cross-version edge contract

`certificate_hash_for` commits to `parent_edges` = ordered tuple of `{role, certificate_hash, certificate_type, required}` (certificate.py:157). An edge stores the parent's `certificate_hash` **string**; it never re-derives the parent's hash. This makes the version boundary transparent:

1. **A v2 child may reference a v1 parent (or the reverse) with no special handling** — the child's `certificate_hash` commits to the parent's `certificate_hash` *string*, whatever version rules produced it.
2. **Each certificate is verified under its OWN `schema_version`**, read from its row (hash-bound, un-spoofable): a v1 parent is re-hashed with `payload_hash = stable_hash(payload)`; a v2 child with `payload_hash = stable_hash(identity_view(payload))`. Mixed-version chains verify edge-by-edge, each node under its own version.
3. **Edge order stays load-bearing in both versions.** `certificate_hash_for` is unchanged, so `parent_edges` is hashed positionally; reconstruction remains `... FROM decision_certificate_edges WHERE child_certificate_id=? ORDER BY rowid` (rowid = insertion order = tuple order, per `_persist_edges` ledger.py:190-208). The reversed-edge antibody (golden CERT B) extends verbatim to v2.
4. **Ancestor-preservation (from `authority_matrix.md` §CRITIC-MATRIX B1).** When world `decision_certificates` are split/relocated (W5), any cert that is a Merkle **ancestor** — via `decision_certificate_edges` — of a `selected`/`settled`/`VERIFIED` cert MUST be preserved with its **identity bytes intact**. A child commits to its parent's `certificate_hash` string; if the parent's identity bytes were rewritten (hash moved), the child's parent-edge reference would dangle and the child's own `certificate_hash` (which commits to that parent hash) would no longer verify. The version boundary makes this **stronger**, not weaker: a v2 child of a v1 parent is verifiable **only** while the v1 parent's bytes stay frozen. W5 therefore may relocate ancestor rows to cheaper storage **as opaque bytes**, but may never re-mint or semantically rewrite them.

---

## 5. Migration path + wave placement

Per `REDESIGN_v2.md §6` and `EXECUTION_MASTER.md §2`:

- **v1 rows are frozen forever** (`schema_version=1`; `test_certificate_v1_golden_freeze.py` guards every byte). No v1 write path changes, ever.
- **W2 — the smallest change that ADDS v2 without touching v1 writes** (this is the "证书 v2 格式" half of W2; the freeze half is already landed at `3d7bf34d1`/`40760942f`):
  1. `certificate.py` — parameterize `payload_hash` on `schema_version`: for `2`, `stable_hash(identity_view(payload))`; for `1`, `stable_hash(payload)` (unchanged). Add `identity_view` (strip reserved `_diagnostics`) to `canonicalization.py` (or `certificate.py`).
  2. `ledger.py:_audit_existing_payload_hash` — make version-aware: additionally `SELECT schema_version`, and re-derive `stable_hash(identity_view(payload))` for `2`, `stable_hash(payload)` for `1`. (The dedup path, INSERT, and DDL are untouched — columns already exist, `decision_certificates_schema.py:12-13`.)
  3. `no_submit_receipts.py` — version-aware `_receipt_json`: for v2, add `"schema_version":2` and route the volatile `opportunity_book` diagnostics out of the hashed payload into a column (mirroring the existing `envelope_json` / C2-columns "store-full-in-column, exclude-from-hash" precedent at `:311-343`); v1 path unchanged.
  4. Extend the golden freeze test with v2 vectors (§6).
  - **No writer emits `schema_version=2` yet.** W2 adds the *ability* to hash a v2 cert; every writer still emits `1` through W2. No DDL, no storage change, no `_diagnostics` population yet.
- **W3 — v2 new writes begin.** Flip the `ActionableTradeCertificate` writer (`certificates/action.py:build_actionable_trade_certificate` → `build_certificate(..., schema_version=2)`) and the NO_SUBMIT receipt writer to emit v2 **for new decisions only**, populating `_diagnostics`. E1 evidence relocation (base64→BLOB) also lands here for new writes, under the §3 invariant.
- **W5 — history rewrite, with the hard line:** **never semantically rewrite a v1 preimage.** Concretely:
  - A v1 `ActionableTradeCertificate`'s `opportunity_book` may be **RELOCATED byte-preservingly** (move `payload_json`, or the book substring, to cheaper storage as opaque bytes, still recomputable to the same `payload_hash`) — but may **never be SUMMARIZED**, because v1 `payload_hash = stable_hash(full payload)` binds every diagnostic byte. Summarization is available **only** to v2 rows (whose identity excludes the annex).
  - A v1 row can **never** be re-minted as v2: bumping `schema_version 1→2` changes `certificate_hash_for` → new `certificate_hash` → new `certificate_id` → dangles every child edge and every downstream reference (settlement skill-attribution, EDLI redeem, restart-preflight). v1 identity is permanent.

**Migration guardrail — the dedup interaction.** `insert_idempotent` dedups on `{certificate_type, semantic_key, mode, decision_time}` (ledger.py:82-98) and raises `CertificateSemanticDriftError` if a matching row has a different `certificate_hash` (ledger.py:106). A v2 cert over a decision that **already has a v1 row** (same key) would trip this (v1 and v2 `certificate_hash` differ by construction). Therefore W3 flips the writer **forward in time**: v2 is emitted for **new** decisions only, never re-issued for a decision already carrying a v1 row. This falls out naturally from "v1 frozen, v2 for new writes."

---

## 6. Forward-compat antibody — extend the golden freeze to v2

`test_certificate_v1_golden_freeze.py` extends to pin v2 golden vectors alongside the v1 ones, so a future v3 can break neither.

- **CERT D (v2 `ActionableTradeCertificate`)** — build a `schema_version=2` cert whose payload has the identity core + a `_diagnostics.opportunity_book` annex. Pin `payload_hash`, `certificate_hash`, `certificate_id`. Assert:
  - `payload_hash == stable_hash(identity_view(payload))` (the v2 rule) — and `identity_view(payload) == payload` minus exactly `_diagnostics`.
  - **v2 E4 property (inverse of CERT C's v1 antibody):** mutating anything under `_diagnostics` — a loser's `rejection_reason`, the `diagnostics_hash`, the `codec_id` — leaves `payload_hash`/`certificate_hash`/`certificate_id` **invariant**.
  - **Core still binds:** mutating `opportunity_book.book_id`, `opportunity_book.selected_candidate_id`, or a selected-economics field (`q_live`, `candidate_id`) **does** change all three.
  - **Round-trip:** `stable_hash(identity_view(json.loads(payload_json))) == payload_hash` (the v2 form of `ledger.py:227`).
  - **Cross-version edge:** a v2 child with a v1 parent edge → pin `certificate_hash`; reversing edge order → a different pinned hash (edge-order antibody, v2).
- **RECEIPT v2** — pin a `schema_version=2` NO_SUBMIT receipt whose `receipt_json` carries `"schema_version":2` and excludes the volatile `opportunity_book` diagnostics. Assert mutating the excluded diagnostics leaves `receipt_hash` invariant, and that `receipt_id` (already three-field) is unchanged from v1.
- **Cross-version independence (the "v3 can't break v2 either" guarantee):** the suite pins one golden vector per (version × shape) and asserts each is hashed under its own version's rules. Any future v3 that perturbs `normalize`/`canonical_json`, the header field set, `_dt`, edge ordering, `identity_view`, or the receipt shape moves a pinned v1 **or** v2 constant and fails loudly — exactly as the v1 freeze does today. v2 is frozen the moment its vectors land.

---

## 7. Where `REDESIGN_v2.md §3` / `freeze §4` were underspecified or wrong — resolved

1. **§3 conflated *identity* with *retention*.** Its E4 row ("shared opportunity_set payload once + selected full + all-candidate compact score/reject + top-K/in-boundary full + Merkle commitment") describes a **storage/retention** policy for the diagnostics, and reads as though it also defines identity. **Resolved:** the two are orthogonal. *Identity* = `book_id` (membership+winner) + top-level selected economics — fixed and minimal (§2.2). *Retention* = how much of the losers' economics to keep retrievable (compact-all + full-top-K) — lives entirely inside `_diagnostics.opportunity_book.content`, **out of identity**. §3's concern that "(id,score,reason) is insufficient to re-evaluate the historical selection" is a **retention** requirement (keep enough diagnostics to replay), satisfied by the annex content policy — it is **not** a reason to pull loser economics into identity (doing so would re-introduce the E4 churn). The selection is *recorded* provenance, not a re-verified proof (`opportunity_book.py:359-372`: the ΔU ranker decides, the book serializes; no verifier re-derives it), so loser economics are correctly identity-excluded.

2. **`freeze §4` over-structured the envelope.** Its `certificate_hash_v2` re-invents `decision_identity`/`model_run_identity` that `certificate_hash_for` already hashes, and its `payload_entries[role].content` restructuring would break by-name verifier reads. **Resolved (named decision B, §2.1):** keep `certificate_hash_for` byte-identical; change **only** `payload_hash` via `identity_view`. The whole v2 delta becomes a one-key strip plus a version-aware audit — the smallest shape that preserves the verifier and avoids duplicate identity fields.

3. **Neither doc stated the `_audit_existing_payload_hash` breakage.** The moment `payload_hash` excludes the annex while `payload_json` still stores the full payload, the v1 audit check `stable_hash(json.loads(payload_json)) == payload_hash` (`ledger.py:227`) **fails** for v2 rows. **Resolved (§5 step 2):** the audit must become version-aware — this is the one non-obvious required edit, and it is the reason W2 (not W3) must land the format even though no writer emits v2 yet.

4. **Neither doc addressed the receipt-side discriminator gap.** `receipt_hash` doesn't commit to `schema_version`. **Resolved (§1):** v2 receipts add `"schema_version":2` into the hashed payload so the discriminator is tamper-evident, matching the cert side.

**No operator/architecture fork here.** The v2 grammar is DB-agnostic and additive; it needs no K1 authority ruling (that gate is the separate `money-hot.db` merge, `REDESIGN_v2 §7`, W4). The one thing to confirm at W3, not decide now: which cert types adopt v2. Recommendation — **v2 is opt-in per writer, adopted only where it buys something**: `ActionableTradeCertificate` (E4) and any cert carrying compressed evidence (E1). Diagnostics-free types (ClockMode, Belief, …) stay v1 forever; flipping them to v2 would change their `certificate_id` for zero benefit.
