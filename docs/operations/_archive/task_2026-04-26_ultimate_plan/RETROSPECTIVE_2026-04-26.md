# Ultimate Plan Debate — Mid-Run Retrospective (2026-04-26 11:08)

Triggered by operator: "定时汇总沟通和协作成果以学习和改进沟通效率". The parallel-region chaos should have been flagged earlier.

## What's gone right

- Boot phase produced extraordinarily strong evidence: opponent-down's WebFetch caught **5+8 BUSTED-HIGH plan premises** in V2 plan (pUSD vs USDC, V1 release date 2026-02-19 vs 2026-04-19 transcription error, heartbeat existed in V1 v0.34.2, post_only existed in V1 v0.34.2, unified V1+V2 client). Without WebFetch encouragement, plan would have been built on fiction.
- opponent-mid grep-verified §8.3 17 transitions over-count by 4 already-existing (REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED, RECONCILED_BY_POSITION) → real K=4-6, not 17.
- opponent-up caught proponent inventing `MarketIdentity` when Apr26 actually cites `ExecutableMarketSnapshot` (table+gate). Authority-order discipline working.
- Cross-region questions surfaced organically (X-UD-1, X-MD-1, X-UM-1) — teammates self-routing rather than asking judge.
- Slice card mints landing on disk with proper schema (id/authority/file_line/antibody_test/depends_on/h/risk/critic_gate).

## What's gone wrong

### 1. Parallel firehose chaos (operator-flagged)

Running 3 regions × 2 teammates concurrently created:
- Cross-region message crossings (proponent-up sent to opponent-up while opponent-up still booting).
- Card count drift: Up 8→10→retracted-5; Mid 6→7→6→5→K=4. Convergence kept oscillating.
- Hard-to-track judge state: 3 active rounds + 3 active layers × 2 teammates = 12 in-flight states.
- Cross-region X-cuts piled up faster than I could route (3 self-routed before R1L1 even closed).

**Should have been spotted at:** initial dispatch. The protocol said "≥2 A2A turns × 3 layers × 3 regions" implies 18+ teammate turns. With parallelism, all of those overlap. Sequential = 1/3 the in-flight state.

### 2. Boot/A2A interleave fault

I included R1L1 question in the bootstrap, expecting teammates would idle and wait. Instead, several teammates jumped straight from bootstrap into A2A. The "pause-correction" message arrived after some had already sent A2A messages.

**Fix:** spawn teammates with IDLE-ONLY bootstrap. Send R1L1 question only after explicit boot-ACK from BOTH teammates per region.

### 3. Routing yaml heuristic propagated as authoritative

Initial routing executor flagged `0 shipped_pre_HEAD` and 23 NET_NEW. I sent this to teammates as framing — even though I knew it was heuristic. opponent-mid had to grep-verify INV-30 actually closes F-001 row-state. AV-1 (signed_payload bytes residual) should have been pre-flagged in routing yaml, not discovered in debate.

**Fix:** routing executor should have verification_status as gate; "heuristic" findings should be banner-flagged in teammate prompts BEFORE being cited. Or: routing executor should be opus, not sonnet, to do real grep-verification.

### 4. File path mistakes in bootstrap prompts

Told teammates to read `src/data/hko_*.py` (doesn't exist) and `src/state/fill_tracker.py` (actually `src/execution/fill_tracker.py`). Cost: 6 follow-up correction messages, distracted teammates.

**Fix:** verify all referenced paths via `find` / `ls` BEFORE writing teammate prompts. Took 5 minutes after dispatch to spot; should have been 5 seconds before.

### 5. Token economy waste

Several teammate prompts were 8-12K chars. They could have been 2-3K. Long prompts = slow inference + dilute focus.

**Fix:** prompts should be <2K chars. Reference docs (paths, line refs) instead of inlining content. Trust teammates to read files themselves.

### 6. WebFetch blocked, no fallback dispatched

Polygonscan 403'd, raw GitHub config.py 404'd, docs.polymarket.com only confirmed "pUSD" verbatim. I let the pUSD/USDC dispute become an unresolved Q-NEW-1 instead of dispatching a sub-agent with broader toolset (curl/httpx/sub-agent's own WebFetch with different UA).

**Fix:** when judge's WebFetch is blocked, immediately dispatch sub-agent. Sub-agents often succeed where direct WebFetch fails (different proxy / retry / UA).

### 7. Notification-summary-only messages

When teammates SendMessage to judge, sometimes only the `summary` arrives in my notification stream — full body not inline. I had to disk-poll for converged-result text.

**Fix:** require teammates to ALSO write converged result to `evidence/<region>/converged_R<N>L<N>.md` before SendMessage. Disk is the durable record.

## Process adjustment (effective at R1L1 close)

Per operator directive: switch to SEQUENTIAL debate after R1L1 closes across all 3 regions.

- Phase 1: Up R2L2 + R3L3 (Mid + Down PAUSED).
- Phase 2: Mid R2L2 + R3L3 (Up + Down PAUSED).
- Phase 3: Down R2L2 + R3L3.
- Phase 4: Cross-cuts X1-X4 (judge-issued, all teammates standby).
- Phase 5: Aggregate slice cards → dependency_graph.mmd.
- Phase 6: Write ULTIMATE_PLAN.md.

## Cadence

Ad-hoc retrospectives at every region close + after cross-cuts close. Distilled lessons saved to memory for cross-session learning.

## Late-arriving positive process learning (2026-04-26 11:12)

opponent-down resolved Q-NEW-1 (pUSD vs USDC label dispute) by dispatching a direct Polygon RPC `eth_call` probe via `polygon.drpc.org` against `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`:
- `symbol()` → "pUSD"
- `name()` → "Polymarket USD"

This contradicted my earlier judge ruling ("contract is bridged USDC; pUSD is marketing label only"). The on-chain truth is dispositive — pUSD IS a distinct ERC-20. My ruling was based on chain-of-inference (Polymarket docs say "pUSD" + SDK config uses USDC.e address → label, not separate token). The inference was wrong; the contract was repurposed.

**Process learning:** when WebFetch chains fail (Polygonscan 403, raw GitHub 404, docs ambiguous), dispatch a sub-agent for direct RPC eth_call. Ground truth (on-chain bytes) > inference > docs. Sub-agents have broader toolset (Bash + curl + httpx) than judge's WebFetch, and can construct ABI-encoded eth_call payloads. This pattern should be the FIRST resort for on-chain identity disputes, not last.

This learning saved as memory `feedback_on_chain_eth_call_for_token_identity` for cross-session reuse.
