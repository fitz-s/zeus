# The severed order-emission wire: pre-submit JIT /book cold-TLS-handshake timeout (2026-06-22)

Forward, real-chain. Not backtest. The engine IS finding edge — the orders die at the final
submit gate on a cold-TLS-handshake timeout regression.

## Symptom (live daemon, zeus-live.log/.err, window 2026-06-17 → 2026-06-22)
- **118 of 120** submit-time JIT order-book fetches failed: `_ssl.c:1064: The handshake
  operation timed out`.
- **112** `EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_{STALE,MISSING}`
  requeues vs **22** `PreSubmitRevalidated` → ~84% of fully-decided, edge-positive orders
  never reached the venue. The gate fires only when the JIT `/book` fetch returns None
  (then the ≤1000ms DB fallback — which the ~11s-gap shared feed can't satisfy — fails).

## Root cause
`src/main.py:_edli_pre_submit_jit_book_quote_provider` did `with PolymarketClient(...) as clob:`
**per call** → `__exit__`→`close()` destroyed the httpx keepalive pool every submit → a **cold
TLS handshake on every order**. `PolymarketClient._public_http` coupled `connect=t, read=2t`
from the scalar `public_http_timeout`, and the **2026-06-19 "bound pre-submit venue reads"**
daemon-protection commit clamped that inner budget to **2.0s**. A daemon-protection fix
inadvertently severed order emission.

## Forward measurement (this machine → clob.polymarket.com, 2026-06-22, read-only /ok)
| connection | latency |
|---|---|
| COLD (fresh client each call) ×4 | 2180, 2264, 2278, 2221 ms |
| WARM (reused client) | first 2660 ms (cold), then 663, 701, 661 ms |

Cold handshake = **2.18–2.66s**, i.e. just over the 2.0s connect budget → times out every
time (the 118/120). Warm reuse drops the fetch to **~0.66s** (well within budget) — but the
first cold handshake still needs ~2.2-2.7s, so reuse alone is insufficient; the connect budget
must also clear the floor.

## Fix (TDD, deployed)
1. `src/data/polymarket_client.py`: `public_http_timeout` now accepts an explicit
   `httpx.Timeout` (additive, backward-compatible) so connect and read can be **decoupled**
   (the scalar path can't give "generous connect, tight read").
2. `src/main.py`:
   - `_edli_pre_submit_jit_book_timeout()` → `httpx.Timeout(connect=max(read, min(3.5,
     outer-read-0.5)), read=inner_io, ...)`. Defaults: outer=6.0, read=2.0 → **connect=3.5,
     read=2.0**; connect clears the 2.7s floor and connect+read=5.5 < outer 6.0 so the inner
     venue IO still times out FIRST (the daemon-protection invariant is preserved).
   - `_edli_pre_submit_jit_clob_client()` — a **warm, lock-guarded, reused** client; the TLS
     connection stays warm across submit candidates (≈0.66s fetches), no per-call cold
     handshake. httpx.Client is thread-safe to share across the guard worker threads. A
     transiently-dead socket costs at most one requeue (httpx reopens on the next fetch).
   - `_edli_reset_pre_submit_jit_clob_client()` — clean-shutdown / test-isolation hook.

## Tests
- `tests/test_presubmit_jit_book_warm_connection.py` (NEW): connect>2.7 ∧ connect+read<outer;
  provider reuses one client across 3 calls (construct-at-most-once). RED→GREEN.
- `tests/money_path/test_edli_live_canary.py`: updated the stale
  `..._uses_short_http_timeout` (asserted the regression-causing coupled scalar <1.25s under an
  artificially tight outer=2.5) → `..._uses_decoupled_bounded_timeout`. 119 pre-submit tests pass.

## Hardening (frontier consult REQ-20260622-044035, Pro Extended — independently endorsed)
The consult agreed (warm reuse + decoupled timeout, not a scalar bump) and found a real
daemon-protection bug in the first cut: **httpcore applies the connect timeout to
`connect_tcp` AND `start_tls` separately**, so a `connect=3.5` JIT budget can consume 7.0s
worst-case > the 6.0s outer guard → reintroduces the leaked-worker pathology the 2026-06-19
commit fixed. Corrected design (deployed):
- **STRICT submit profile**: connect=2.25, read=0.85, write=0.25, pool=0.10 → worst case
  `2*connect + read + write + pool = 5.70s < 6.0s` (inner fails closed FIRST).
- **Boot pre-warm + 25s keepalive pinger** (`_edli_pre_submit_jit_keepalive_tick`, gated to
  live mode): GET `/time` (NOT `/book`) on the dedicated warm client with a GENEROUS warmup
  timeout (connect=4.5), OUTSIDE the submit worker, so cold handshakes are absorbed there and
  the submit-time fetch always reuses a warm connection. Read-only, fail-soft, touches no
  trading state, max_instances=1.
- **Dedicated limits** `PRESUBMIT_JIT_CLOB_HTTP_LIMITS` keepalive_expiry=90s (> 60s cycle).
- Lock only on singleton init/teardown, never around `/book` (no serialization of clustered
  candidates). Witness semantics unchanged: every candidate still fetches `/book` for its
  token; the pinger never supplies the book.

## Forward proof on the deployed code path (live venue, 2026-06-22)
| step | result |
|---|---|
| strict profile | connect=2.25, worst-case 5.70s < 6.0 ✓ |
| pre-warm (generous, outside worker) | 2271ms, ok=True (absorbed the cold handshake) |
| strict `/book` fetch ×3 (warm) | 659, 665, 655 ms — all OK; succeed under the 2.25s connect *because* warm |

So the strict profile that would time out a cold handshake never pays one — the pinger keeps
it warm; every submit fetch is ~0.66s.

## Forward validation (post-deploy, in-vivo)
Watch zeus-live.err for the disappearance of "JIT book fetch failed ... handshake operation
timed out" and zeus-live.log for PreSubmitRevalidated rising vs PRE_SUBMIT_BOOK_AUTHORITY
requeues falling — and an actual order reaching the venue. That live stream is the proof.
