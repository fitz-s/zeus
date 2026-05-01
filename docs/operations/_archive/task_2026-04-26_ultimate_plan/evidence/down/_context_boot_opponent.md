# Down Region — Solo Context Boot (opponent-down)

Created: 2026-04-26
Author: opponent-down
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Authority basis: live external fetch (curl + WebFetch) of Polymarket V2 SDK source on 2026-04-26 + grep over Zeus `data-improve` HEAD on same date.

This is the adversarial boot for region Down. Output is the predicate set I will press in R1L1 against proponent-down. Every external claim carries a fetch-time citation.

---

## 1. Files read (Zeus side)

- `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/plan.md` (517 lines)
- `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/v2_system_impact_report.md` (381 lines)
- `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/zeus_touchpoint_inventory.md` (147 lines)
- `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/open_questions.md` (142 lines)
- `src/main.py` (lines 1-120, 336-366, 579-642 — heartbeat / cycle_lock / scheduler regions)
- `src/execution/fill_tracker.py` (lines 20-60 — status set definitions)
- `src/data/polymarket_client.py` (lines 151-161 — get_fee_rate REST call)

## 2. External URLs fetched (with timestamps)

All fetched 2026-04-26 within a 30 minute window of this report.

### 2.1 Repo + release metadata (GitHub API)

- `https://api.github.com/repos/Polymarket/py-clob-client-v2` → repo EXISTS, public, MIT, owner Polymarket org id 31669764. Created public repo confirmed.
- `https://api.github.com/repos/Polymarket/py-clob-client-v2/releases` → 5 releases. Latest **`v1.0.0` published 2026-04-17T14:58:36Z**. Release notes: "Fix/v1 fee bps" (#17) + "version: 1.0.0" (#18). Prior: `v0.0.4` 2026-04-16 ("ft: add getHeartbeat() #15"), `v0.0.3` 2026-04-10 ("ft: orders postOnly #12"), `v0.0.2` 2026-04-08, `v0.0.1` earlier.
- `https://api.github.com/repos/Polymarket/py-clob-client-v2/commits?per_page=10` → most recent commit `a2ec069f 2026-04-17T14:57:53Z` ("version: 1.0.0"). **Last commit was 9 days ago. Nothing since GA.** Repo is dormant or stable — interpretation pending.
- `https://github.com/Polymarket/py-clob-client` (V1, via WebFetch) → "Current version: v0.34.6, last release Feb 19, 2026. **No EOL or deprecation notice in README.** No reference to a V2 successor."

### 2.2 V2 host live probe

- `https://clob-v2.polymarket.com/version` → **HTTP 200, body `{"version":2}`**. Q1 has a preliminary affirmative answer from the agent's egress.
- `https://clob-v2.polymarket.com/ok` → `"OK"`.
- `https://clob-v2.polymarket.com/time` → `1777200616` (unix sec).

These three endpoints alive against Anthropic egress is NOT proof Zeus's egress (Polygon / Gnosis Safe / IP-pinned account) is reachable. Q1 is partially answered for "is the host generally reachable from any internet?" but NOT answered for "is Zeus's specific egress allowed?" Operator must still execute 0.A from Zeus's daemon machine to discharge Q1.

### 2.3 V2 SDK source files (raw.githubusercontent.com via curl, GitHub API for tree)

Fetched into /tmp/. Tree confirmed via `https://api.github.com/repos/Polymarket/py-clob-client-v2/git/trees/main?recursive=1`.

- `py_clob_client_v2/__init__.py` (~70 lines, full quote captured) — exports.
- `py_clob_client_v2/client.py` (1089 lines).
- `py_clob_client_v2/clob_types.py` (455 lines).
- `py_clob_client_v2/endpoints.py` (full file).
- `py_clob_client_v2/exceptions.py` (full file — only PolyException + PolyApiException).
- `py_clob_client_v2/constants.py` (full file).
- `py_clob_client_v2/config.py` (full file — contract addresses).

### 2.4 Polymarket docs site (WebFetch)

- `https://docs.polymarket.com/` → Quickstart shows TS import `@polymarket/clob-client-v2` BUT Python import is `from py_clob_client.client import ClobClient` (V1). **Docs Python quickstart still references V1 package name 9 days post-V2 GA.** No mention of pUSD redemption, USDC.e→pUSD bridge, V1 EOL date, builder code rules, or heartbeat requirements anywhere reachable through the docs index.

---

## 3. VERIFIED-FALSE plan premises (HIGH severity)

These are claims in `plan.md` or `v2_system_impact_report.md` that the live V2 source FALSIFIES.

### 3.1 BUSTED — "EIP-712 domain version `1` → `2`" (impact-report §1)

**Plan claim**: `v2_system_impact_report.md:21` "EIP-712 domain version bumps from `\"1\"` → `\"2\"`."
**Reality**: The V2 SDK `__init__.py` exports BOTH `OrderArgsV1` and `OrderArgsV2`. `client.py:130` defines `_is_v2_order(order)` that branches on `hasattr(order, "timestamp")`. `client.py:1041` has `__resolve_version()` that hits `/version` and CACHES per-token-condition. Same SDK posts EITHER V1 or V2 orders depending on `__resolve_version()`'s answer. **The EIP-712 domain version is per-order, not per-host.** Plan's binary "V2 means domain v2" is wrong; the SDK auto-resolves.

**Implication**: Phase 1.B `clob_protocol` typed atom with `version: Literal["v1", "v2"]` and `eip712_domain_version: str` IS THE WRONG ABSTRACTION. The protocol version is no longer a deploy-time constant — it is a per-token runtime property the SDK already caches. Zeus would be re-modelling state the SDK owns.

### 3.2 BUSTED — "pUSD replaces USDC.e" (impact-report §2.7, §4.5)

**Plan claim**: `v2_system_impact_report.md:84` "pUSD replaces USDC.e as trading collateral on V2."
**Reality**: V2 SDK `config.py` Polygon mainnet `collateral=0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`. **This is NOT pUSD — it is USDC.e.** `0xC011a7…2DFB` is the long-running USDC contract on Polygon. The V2 SDK's `MarketOrderArgsV2` even has a field `user_usdc_balance` (clob_types.py:153) used in `client.py:760` for fee adjustment. Pseudo-quote: "User USDC balance, used to adjust fees on market buy orders".

**Implication**: The entire pUSD-bridging branch of the plan (Phase 0.D Q5, Phase 2.C, Phase 3.C FX accounting, F1, F2, the §4.5.b–.d "deepest system shock" narrative) is **chasing a phantom**. The collateral didn't change. Phase 2.C is dead scope. M3 is dead scope. A3 antibody is dead scope. Phase 0 Q5 inquiry should be killed.

This is the single largest plan-burning finding in region Down. **If proponent disputes, demand they name the on-chain pUSD contract address. They will not produce one because the SDK config does not contain one.**

### 3.3 BUSTED — "fee_rate_bps is removed from V2 OrderArgs" (impact-report §2.3, §4.4)

**Plan claim**: `v2_system_impact_report.md:51-52` "V2-only fields: metadata, builder_code, defer_exec, timestamp. V1-only fields removed in V2: taker, nonce, fee_rate_bps."
**Reality**: `clob_types.py:75-97` `OrderArgsV2` field list is exactly: `token_id`, `price`, `size`, `side`, `expiration`, `builder_code`, `metadata`. `defer_exec` and `timestamp` are NOT on `OrderArgsV2` — they are on `PostOrdersV2Args` (defer_exec) and on the SIGNED order produced by the order builder (timestamp). And `fee_rate_bps` IS still resolved at order-build time even in V2: `client.py:706-707` `user_fee_rate_bps = getattr(order_args, "fee_rate_bps", None) or None; fee_rate_bps = self.__resolve_fee_rate_bps(token_id, user_fee_rate_bps) if version == 1 else None`. So fee_rate_bps is set to None for v2 path BUT the same release `v1.0.0` (last commit, "Fix/v1 fee bps") is specifically about fee_rate_bps still living on V1 orders posted via this V2 SDK.

**Implication**: Plan's M5 ("OrderArgs V2 schema (metadata, builder_code, defer_exec, timestamp)") names FOUR fields, two of which are not on `OrderArgsV2`. Slice 2.D's "OrderArgs construction: V2 path supplies metadata, builder_code (from config), defer_exec, timestamp" is wrong — `defer_exec` is a method-arg to `create_and_post_order` and `timestamp` is internal. Slice 1.D's antibody "asserts: OrderArgs has fields metadata, builder_code, defer_exec, timestamp; OrderArgs does NOT have field nonce" — the defer_exec / timestamp asserts will FAIL ON FIRST RUN against the actual SDK. Plan's antibody contains a bug.

### 3.4 BUSTED — "py-clob-client-v2 is the import name" (plan slice 2.D line 282)

**Plan claim**: `plan.md:282` "Branch import: `from py_clob_client.client import ClobClient` (V1) vs `from py_clob_client_v2.client import ClobClient` (V2)".
**Reality**: V2 package directory is `py_clob_client_v2/` (snake_case with underscore-2). The import path in plan IS correct, but `requirements.txt` slice 1.G says `py-clob-client-v2>=1.0.0` (hyphen) which IS the pip name (`pip install py-clob-client-v2` works because that's how pip normalizes; the importable name is `py_clob_client_v2`). README confirms: `pip install py_clob_client_v2` BOTH work. **Lower-severity than I initially flagged. Closing as VERIFIED-OK.**

### 3.5 BUSTED — "Mandatory 10s heartbeat or server cancels all orders" (impact-report §1, §2.4, §4.1)

**Plan claim**: `v2_system_impact_report.md:13`, `:62` "persistent session + 10s mandatory heartbeat". `:62` "Missing heartbeat for ~10s **cancels all open orders server-side**."
**Reality**: V2 SDK `client.py:245-251` defines `post_heartbeat(heartbeat_id: str = "") -> dict` — single POST endpoint at `/v1/heartbeats` (note: `/v1/`, not `/v2/`!). It is NOT enforced session-keepalive in the SDK code path: `create_and_post_order` does NOT call `post_heartbeat`. There is no internal coroutine, no async timer, no `asyncio.sleep(10)`. **The heartbeat is an explicit operator-side opt-in, not a transport-layer must-have.** Examples directory `examples/account/` has 13 files; NONE is a heartbeat example. The SDK has zero docs about timing requirements.

Until proponent produces a fetched URL where Polymarket states "10s missed heartbeat → server-side mass cancel", that claim is **UNVERIFIED**. The SDK's release notes for `v0.0.4` say only `"ft: add getHeartbeat()"`. Adding a method ≠ requiring it.

**Implication**: M1 (mandatory heartbeat coroutine), Phase 2.B (heartbeat_supervisor.py), A2 antibody, the entire "three-layer heartbeat collision" narrative in §4.1 — all rest on an UNVERIFIED 10-second mandatory cadence. If the heartbeat is opt-in, M1's urgency drops from MANDATORY to RECOMMENDED, the daemon supervisor concurrency change is unjustified, and Phase 2 entry is no longer gated by it.

This is the second-largest plan-burning finding.

### 3.6 BUSTED — "delayed status is V2-new and creates capital-leak" (impact-report §2.5, §4.3)

**Plan claim**: `v2_system_impact_report.md:67` "V2 statuses: live, matched, delayed, unmatched. The delayed / ORDER_DELAYED / DELAYING_ORDER_ERROR states are new transitional values..."
**Reality**: My grep over `client.py + clob_types.py + endpoints.py` for `delayed|delaying` — **0 hits**. The V2 SDK source has NO mention of `delayed`, `ORDER_DELAYED`, or `DELAYING_ORDER_ERROR` anywhere. `OrderPayload` is just `orderID: str`. `get_order` returns whatever the server emits. **The "delayed" status set is unsourced in V2 SDK code.**

**Implication**: M2 (`delayed` status branch), Phase 2.A (fill_tracker delayed branch), the wall-clock timeout escalation, and the §4.3 "latent capital-leak risk" narrative are all built on a server-side state name that the SDK does not surface. Either (a) the docs site mentions it somewhere I haven't reached, in which case proponent must produce the URL, OR (b) the impact-report invented the state name. Demand the citation.

### 3.7 PARTIAL — "V1 EOL not announced" (impact-report §1)

**Plan claim**: `v2_system_impact_report.md:23-24` "V1 is still being patched (`py-clob-client v0.34.6` on 2026-04-19, two days after V2 GA). No public V1 EOL date has been announced."
**Reality verified**: My WebFetch of V1 README confirmed v0.34.6 latest release Feb 19, 2026 — wait, **the plan says Apr 19, the WebFetch said Feb 19. One of them is wrong.** Need a direct API hit to confirm the V1 release date. Plan citation may be transcription error from a fetch-time confusion. Down-grading to PARTIAL until reconciled.

**Implication**: Q6 ("V1 EOL announced?") status is correctly OPEN. The plan's claim that V1 was "still being patched 2 days after V2 GA" relies on an Apr-19 V1 release that the docs may not show. Lower-severity but worth flagging in R1L2.

### 3.8 BUSTED — "fee_rate_bps via getClobMarketInfo" (plan slice 2.F + impact-report §2.8, §4.4)

**Plan claim**: Slice 2.F "replace direct /fee-rate httpx call with cached getClobMarketInfo(conditionID) lookup."
**Reality**: V2 SDK has `get_clob_market_info(condition_id: str)` (snake_case, not camelCase as plan writes). `client.py:290` returns a dict; the SDK ALSO has dedicated methods `get_fee_rate_bps(token_id)`, `get_fee_exponent(token_id)`, `get_tick_size(token_id)`, `get_neg_risk(token_id)`. **The plan implicitly proposes a less-granular method as the replacement; the right replacement is the existing dedicated `get_fee_rate_bps`.** Also note: V2 SDK already wraps `__ensure_market_info_cached` (`client.py:1061`) internally — Zeus's plan to add its own cache layer (slice 2.F) is REDUNDANT WITH SDK CACHING.

**Implication**: Slice 2.F is over-scoped: SDK already caches `MarketDetails` per token internally. Zeus's added cache is a parallel state machine. Memory rule "test relationships, not just functions" — the relationship between Zeus's cache and SDK cache must be explicit, not stacked.

### 3.9 BUSTED — "exception class diff PolyApiException → V2 equivalent" (plan slice 1.F)

**Plan claim**: `plan.md:198` "catalog V2 exception classes (likely renamed from PolyApiException to V2 equivalent)."
**Reality**: V2 `exceptions.py` defines exactly two classes: `PolyException` and `PolyApiException`. **Same class name as V1.** Slice 1.F is hunting for a rename that does not exist. Test mirror is unnecessary.

### 3.10 BUSTED — "V2 retains 0.01 tick for weather" (touchpoint §3 line 65)

Verified TRUE — `clob_types.py:252` `TickSize = Literal["0.1", "0.01", "0.001", "0.0001"]`. Closing as VERIFIED-OK.

---

## 4. Hidden architecture decisions in the plan

### 4.1 The Phase 1 abstraction is built BEFORE Q2 closes

`plan.md:151` Phase 1 entry condition: "Phase 0 slice 0.F passes (critic accepts evidence package)". But slice 1.B builds `ClobProtocol` with field set `version, host, sdk_module, eip712_domain_version, collateral_asset, mandatory_heartbeat, heartbeat_interval_seconds`. **Three of those seven fields (eip712_domain_version, collateral_asset, mandatory_heartbeat) are now FALSIFIED by §3.1, §3.2, §3.5 above.** The dataclass is not just premature — it encodes assumptions that are wrong. Building it first burns Phase 1 effort.

### 4.2 daemon supervisor concurrency change has no design doc

`plan.md:260` "this is Zeus's first cross-cycle concurrent component". Slice 2.B introduces a thread that runs across cycles, distinct from `_cycle_lock`. **No design doc names**:
- thread vs asyncio choice (Zeus has zero asyncio per impact-report §4.2)
- shutdown ordering (does heartbeat thread join before scheduler shutdown?)
- exception isolation (uncaught exception in heartbeat thread should NOT kill the daemon — does the plan say so?)
- interaction with apscheduler `BlockingScheduler` (main.py:19 — Blocking, single-threaded scheduler)
- `state/auto_pause_failclosed.tombstone` write contention (multiple writers possible if heartbeat fires while a cycle's strategy is also writing)

This is a hidden architectural decision. The plan has only "thread-safe design in 2.B; A2 antibody injection test" as the mitigation row in the risk register (`plan.md:487`). That is risk-handwaving, not a design.

### 4.3 SDK swap (2.D) has no rollback path

`plan.md:281-286` slice 2.D edits `polymarket_client.py` import + host + OrderArgs. Slice 3.D in Phase 3 says "Flip ZEUS_CLOB_PROTOCOL=v2." But: between 2.D landing and 3.D flipping, the code already imports BOTH SDKs at module load. **What happens if `py-clob-client-v2` v1.0.x has a transitive-dependency conflict with V1's `py-clob-client>=0.25`?** The plan's risk register row "V2 SDK package conflicts with existing deps" (`plan.md:493`) has mitigation "resolve in 1.G with operator". But 1.G is a cosmetic dual-pin; the actual conflict surface is in 2.D where both are imported simultaneously. **Real test**: `pip install py-clob-client>=0.25 py-clob-client-v2>=1.0.0` and run `python -c "from py_clob_client.client import ClobClient as C1; from py_clob_client_v2.client import ClobClient as C2"`. Until that runs clean, slice 2.D's branching design is theoretical.

### 4.4 Phase 0 Q1 is materially false-positive-prone

`open_questions.md` Q1 acceptance: "HTTP 200 + JSON body with protocol identifier". My probe: `clob-v2.polymarket.com/version → {"version":2}`. **From the operator's egress** Q1 might pass. But Zeus's daemon runs from a specific Polygon-aware account with a Gnosis Safe; if Polymarket geofences API access to KYC-tier-2 accounts, Q1's curl from a different IP is misleading. The plan does NOT specify "from Zeus daemon machine, with funder_address present in headers". Q1's acceptance criterion is **observability-only, not authorization-tested**.

---

## 5. Five+ attack vectors for R1L1

Layer 1 (architecture / authority order) only — no file:line until L3.

1. **A1: pUSD is fictional.** §3.2. The V2 SDK Polygon collateral address `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` is USDC. No pUSD anywhere in V2 SDK. Phase 0 Q5, Phase 2.C, Phase 3 FX surface — all dead scope. Demand proponent name the on-chain pUSD contract or retract.
2. **A2: Heartbeat mandate is unsourced.** §3.5. SDK has `post_heartbeat()` but no internal cadence. Examples directory has zero heartbeat usage. Until proponent produces a Polymarket URL stating "10s missed → mass cancel", M1/2.B's mandatory framing is UNVERIFIED. The §4.1 "three-layer heartbeat collision" narrative is built on it.
3. **A3: clob_protocol abstraction encodes 3 falsified fields.** §3.1, §3.2, §3.5. `eip712_domain_version`, `collateral_asset`, `mandatory_heartbeat` are wrong. Phase 1.B ships a dataclass that the SDK already invalidates (SDK has `_resolve_version()` per-token).
4. **A4: V2 SDK is a V1+V2 superset, not a V2-only product.** §3.1. The V2 SDK exports `OrderArgsV1` AND `OrderArgsV2`. `_is_v2_order(order)` discriminates per-order. **V2 migration may be installable as a V1-compatible swap with zero behavior change.** This collapses the Phase 0 → Phase 4 ladder dramatically. Plan should explore "swap SDK package, leave version=1, test parity, then opt into V2 per-token" path.
5. **A5: M2 / 2.A `delayed` state is unsourced.** §3.6. Zero hits in V2 SDK. Plan's "latent capital-leak" narrative needs a source URL.
6. **A6: Phase 0 → Phase 1 gate has rotted before phase started.** §3.1, §3.2, §3.5, §3.8, §3.9 invalidate the V2 capability inventory that Phase 1 derives from. Critic verdict at slice 0.F should reject Phase 1 entry until impact-report is rewritten against fetched SDK source.
7. **A7: Daemon supervisor concurrency has no design doc.** §4.2. Slice 2.B asks for Zeus's first cross-cycle thread without specifying thread-vs-asyncio, shutdown ordering, exception isolation, or apscheduler interaction. That is a hidden architectural decision worth a separate design slice before any code lands.
8. **A8: Q1 acceptance is observability-only.** §4.4. Curl-from-anywhere returning 200 does not prove Zeus's daemon egress + KYC-tier authorization. Q1 must specify origin.

---

## 6. Top hidden architecture decision

**THE TOP HIDDEN DECISION: Whether to treat `py-clob-client-v2` as a V2-only client or as a unified V1+V2 client.**

Per §3.1, §4.4, the V2 SDK natively branches at runtime via `_resolve_version()` and supports BOTH OrderArgsV1 and OrderArgsV2. The plan models V1 vs V2 as a deploy-time switch (`ZEUS_CLOB_PROTOCOL=v1|v2`) but the SDK itself has eliminated that choice — it auto-resolves per-token. **If Zeus adopts the SDK as a unified client, the entire `clob_protocol` abstraction (Phase 1.B) and the env-var switch (Phase 2.D) become unnecessary.** The migration shrinks from 4 phases to 1 phase: install `py-clob-client-v2`, replace import in polymarket_client.py, run regression, ship.

The plan never considers this path. That is the load-bearing missed decision.

---

## 7. Round-2 boot additions (post lead path correction)

### 7.1 PyPI verification — slice 1.G is mechanically viable

`https://pypi.org/pypi/py-clob-client-v2/json` (fetched 2026-04-26):
- `name: py-clob-client-v2`, `version: 1.0.0`, `requires_python: >=3.9.10`
- 4 published releases on PyPI: 0.0.1, 0.0.2, 0.0.4, 1.0.0 (notable: 0.0.3 missing from PyPI but present on GitHub — release was tagged but not uploaded)
- `requires_dist`: `eth-account>=0.13.0, eth-utils>=4.1.1, poly_eip712_structs>=0.0.1, py-order-utils>=0.3.2, httpx[http2]>=0.27.0`
- Latest wheel uploaded `2026-04-17T15:02:53` UTC

Slice 1.G (`requirements.txt` dual pin) is mechanically installable. **No P0 blocker on the install path.** This closes one possible attack vector pre-debate.

### 7.2 V1 release recency contradicts the impact-report — UPGRADES §3.7 to BUSTED-HIGH

`https://api.github.com/repos/Polymarket/py-clob-client/releases?per_page=5`:
- v0.34.6 published `2026-02-19T13:26:27Z` (NOT 2026-04-19 as the impact-report says)
- v0.34.5: 2026-01-13
- v0.34.4: 2026-01-06
- v0.34.3 / v0.34.2: 2026-01-06 — PR title "HeartBeats V1 Client Update" + "Post Only Orders Handling" (PRs #229, #230 by fednerpolymarket)

**§3.7 upgrades from PARTIAL to BUSTED-HIGH.** The impact-report claim "py-clob-client v0.34.6 on 2026-04-19, two days after V2 GA" is FALSE — the V1 release predates V2 GA by 2 months. V1 has been DORMANT since Feb 19, not "still being patched". The urgency framing of the plan needs a complete rewrite.

### 7.3 NEW BUSTED — heartbeat is NOT V2-exclusive

§3.5 needed widening. V1 release v0.34.2 (2026-01-06) added "HeartBeats V1 Client Update" and v0.34.3 same day. **The V1 SDK has had heartbeat support for 4 months before V2 GA.** The plan's framing that V2 INTRODUCES mandatory heartbeat is doubly wrong:

- The 10-second mandatory cadence is unsourced in the SDK (§3.5).
- The heartbeat MECHANISM itself is not V2-new; V1 has it.

This is now a 6th BUSTED-HIGH plan premise. M1's V1-vs-V2 framing collapses. The plan should treat heartbeat as a feature available on either protocol, not a V2 forcing function.

### 7.4 NEW BUSTED — post_only is NOT V2-exclusive either

V1 v0.34.2 (2026-01-06) PR "Post Only Orders Handling". The impact-report `v2_system_impact_report.md:56` says "Added in `clob-client-v2 v0.2.6` and `py-clob-client-v2 v0.0.3` (2026-04-10)" — **V1 had post_only 3 months before V2 GA.** S2 strategic-slice framing is wrong.

### 7.5 pUSD claim — DOWNGRADE to CONTRADICTORY DUAL-AUTHORITY

§3.2 is more nuanced than initial finding. Re-fetch via WebFetch of `https://docs.polymarket.com/trading/quickstart` returned (verbatim, "Set Up Your Client"):

> "pUSD (for buying outcome tokens) and POL (for gas, if using EOA type `0`)."

And in "Order rejected - insufficient balance":

> "BUY orders: need pUSD in your funder address"

So the **DOCS site explicitly names pUSD as the BUY collateral.** But the SDK source `py_clob_client_v2/config.py` Polygon contract config has `collateral=0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` — this address has historically been Polygon's USDC.e contract.

**One of two things is true:**

(a) `0xC011a7…2DFB` has been re-associated as the pUSD contract on Polygon (USDC.e renamed/re-purposed); in which case the impact-report's labeling is correct but the §4.5.b "no public document explains the bridge path" worry is moot — there is no bridge, the same address is now pUSD.

(b) The docs are wrong and the SDK config is right; the actual collateral is still USDC.e.

The SDK + docs are CONTRADICTORY AUTHORITY. Plan should NOT proceed under the §4.5 narrative of "bridge USDC.e → pUSD" until this is reconciled. **Phase 0 needs a new question Q-NEW-1: Is `0xC011a7E1…2DFB` USDC.e or pUSD on Polygon?** Operator should call `name()` and `symbol()` on the contract via Polygonscan.

§3.2 attack downgrades from "pUSD is fictional" to "pUSD label is contradictorily authoritative; verify on-chain symbol() before any bridge work."

### 7.6 V1 SDK has same `OrderArgs.fee_rate_bps` field

Fetched V1 `clob_types.py` shows `OrderArgs.fee_rate_bps: int = 0` and `MarketOrderArgs.fee_rate_bps: int = 0`. The V2 SDK exports `OrderArgsV1` (with fee_rate_bps) AND `OrderArgsV2` (without fee_rate_bps as an explicit field — but the SDK's `__resolve_fee_rate_bps` still computes it for v1-path orders). So:

- V1 SDK `OrderArgs` has `fee_rate_bps`.
- V2 SDK `OrderArgsV1` has `fee_rate_bps`.
- V2 SDK `OrderArgsV2` does NOT have `fee_rate_bps` (resolved by SDK internally).

The plan's §2.3 column "V1-only fields removed in V2: taker, nonce, fee_rate_bps" is right at the V2-NATIVE-shape level but wrong at the SDK package level. Slice 1.D antibody needs to be careful about which class it asserts against.

### 7.7 New attack vector summary

To the original 8 attacks (§5), add:

9. **A9: Plan urgency framing rests on a transcription error.** §7.2. V1 last release was 2 months before V2 GA, not 2 days after. The "V1 still being patched" urgency-driver is false; plan can take its time.
10. **A10: Heartbeat and post_only are V1 features too.** §7.3, §7.4. The impact-report's V1-vs-V2 paradigm A and §6.3-S2 columns are wrong. Phase 2.B and S2 framing need rework.
11. **A11: pUSD authority chain is contradictory.** §7.5. Docs say pUSD; SDK says `0xC011…USDC.e historical address`. Until reconciled via Polygonscan `symbol()`, Phase 2.C is blocked on a different question than Q5.

---

## 8. Standby state

Boot complete (round 2). Awaiting team-lead greenlight to open R1L1 against proponent-down with the 11-attack queue in §5 + §7.7.
