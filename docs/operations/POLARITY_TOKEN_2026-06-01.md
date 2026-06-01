# POLARITY / TOKEN / OUTCOME-LABEL / VENUE-SIDE Audit — 2026-06-01

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: OPERATOR LAW (YES/NO independently grounded, never mirror); HEAD 6fcd05a69f
- Scope: read-only. Trace decided direction → token_id → venue POST end-to-end; flag any swap or
  any place where `outcome_label` is assumed from `direction` without grounding in the token map.

## VERDICT: CONFIRM — no inversion found

The decided direction (`buy_yes`/`buy_no`) is bound to the correct venue token (`yes_token_id`/
`no_token_id`) at EVERY stage. The token is **carried verbatim** through the cert chain (never
re-derived from `direction`), and the executor independently re-derives the expected token from
`direction` against the elected snapshot's own `yes/no` columns and **fail-closes** on any
mismatch. Live data: 61/61 `buy_no` receipts → `token_id == no_token_id`, `outcome_label == NO`,
ZERO inversions.

## DIRECTION → TOKEN → VENUE TABLE

| Stage | File:line | buy_no resolves to | buy_yes resolves to | Grounded in token map? |
|---|---|---|---|---|
| Proof gen (direction↔token bind) | `event_reactor_adapter.py:2876-2878` | `no_token_id` | `yes_token_id` | YES — tuple binds token+direction together |
| Quote side (direction↔book) | `event_reactor_adapter.py:4248-4253` | `book.no_asks` | `book.yes_asks` | YES — direct map, no inference |
| Cost/exec-price guard | `event_reactor_adapter.py:4085-4088` | rejects if token∉{yes,no}; rejects if selected≠label | same | YES — fail-closed |
| outcome_label emit | `event_reactor_adapter.py:928` | `"NO" if token==no_token_id` | else `"YES"` | YES — derived from token, not direction |
| Receipt token | `event_reactor_adapter.py:899,1444` | `selected_token_id` (=no) | (=yes) | YES — carried verbatim |
| FINAL_INTENT cert | `certificates/execution.py:114,116` | `token_id=action[token_id]`; `side="BUY"` | same token verbatim; `side="BUY"` | YES — no re-map from direction |
| Cert→intent translate | `event_bound_final_intent.py:269-270` | `selected_token_id=final_payload[token_id]` | same | YES — verbatim |
| Executor triple-check | `executor.py:1730-1745` | re-derives `expected=no_token_id` from direction vs snapshot; raises on mismatch | `expected=yes_token_id` | YES — independent fail-closed re-derivation |
| Legacy entry envelope | `executor.py:1830` | `token_id=intent.selected_token_id` | same | YES — verbatim, no re-map |
| Venue POST | `executor.py:3046/3567` | `token_id=intent.token_id, side="BUY"` | same | YES — exact token POSTed |

Polymarket binary semantics confirmed CORRECT: buying NO of "be 34°C" = a **BUY** of the
`no_token_id` (line 2016 / `_side_for_direction` `executor.py:547-552`: both `buy_yes`/`buy_no`
map to `side="BUY"`; the OUTCOME is encoded in the token, never in BUY/SELL). There is no place
that converts `buy_no` into a SELL of the YES token.

## SWAP-RISK SURFACES (each examined, each safe)

1. `executor.py:1595-1598` — `create_execution_intent`: `buy_no → order_token = no_token_id`.
   Here the param `token_id` is the YES token and `no_token_id` is separate, so the re-map is
   correct. NOTE: this is the LEGACY cycle-runner path; the EDLI live path does NOT use it (EDLI
   goes `submit_event_bound_final_intent_via_existing_executor` → `execute_final_intent` →
   `_legacy_entry_intent_from_final`, which uses `intent.selected_token_id` verbatim,
   `executor.py:1830`). No inversion either way.
2. `contracts/execution_intent.py:184-198` `_selected_token_for_direction` — derives token FROM
   direction, BUT fail-closes (lines 192-197) if the snapshot's stored
   `selected_outcome_token_id` / `outcome_label` disagree. Grounded, not assumed.
3. `events/edli_position_bridge.py:249` — `outcome_label = pre_submit.outcome_label or ("NO" if
   direction=="buy_no" else "YES")`. The fallback assumes label from direction, BUT the primary
   source is the cert-carried `pre_submit.outcome_label` (token-grounded upstream). LOW risk: the
   fallback only fires if the upstream label is missing, and by then token↔direction was already
   bound at `:2876` and triple-checked at `executor.py:1740`. Recommend (non-blocking): make this
   fallback fail-closed rather than direction-assumed, to keep the "never assume label from
   direction" law uniform. Not an inversion today.

## INVARIANT GROUNDING (category-killers already in place)

- `contracts/executable_market_snapshot.py:236-239` — snapshot rejects construction if
  `outcome_label=="YES"` but `selected≠yes_token_id` (and NO symmetric). Token↔label cannot drift
  at the data layer.
- `contracts/venue_submission_envelope.py:116` — envelope re-derives `expected_token` from its own
  `outcome_label` and validates against the signed order token.
- `executor.py:1730-1745` — the FinalExecutionIntent↔snapshot triple-check: (a) selected==snapshot
  selected, (b) expected-from-direction, (c) selected==expected. This is the strongest guard:
  even a corrupted upstream token cannot reach the venue without matching the snapshot's NO side.

## LIVE DATA — Singapore / June rows (zeus_trades.db snapshots + zeus-world.db receipts)

- Snapshot grounding (sample): cond `0x56d8cff96ed9` (SF) appears with BOTH a NO row
  (`selected=949069429050592600=no_token_id, outcome_label=NO`) and a YES row
  (`selected=281357327553801178=yes_token_id, outcome_label=YES`). `token_map_json`:
  `outcomes=['Yes','No']`, `labels_swapped=False`, `clobtokenids[0]==yes_token_id`,
  `clobtokenids[1]==no_token_id`. Yes/No assignment matches canonical Polymarket order.
- buy_no receipts joined to snapshot by condition_id: **61/61 matched, 0 unmatched, 0 INVERSIONS.**
  Every `buy_no.token_id == snapshot.no_token_id`; every populated `outcome_label == "NO"`.
  Sources: `execution_feasibility_evidence`, `no_trade_regret_events`, `edli_no_submit_receipts`.
- `venue_submission_envelopes` and `position_current(buy_no)` are empty: no live `buy_no` order has
  yet crossed the venue boundary (EDLI canary still gated), so the audit confirms the DECISION→
  COMMAND chain; the venue POST itself is proven by code path, not yet by a live fill row.

## CONSISTENCY ON THE DECISIVE buy_no (Singapore high "be 34°C")

direction `buy_no` → `token_id == no_token_id` (proof bind `:2878`, receipt `:899`) →
`outcome_label == "NO"` (token-derived `:928`) → cert `token_id` verbatim (`execution.py:114`) →
`side == "BUY"` (`:116`) → executor expects `no_token_id` from direction and asserts equality
(`executor.py:1736,1740`) → venue POST `token_id=no_token_id, side=BUY`. All say NO. ✅
