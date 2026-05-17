# T-1_COMPAT_SUBMIT_SCAN.md

**Artifact:** T-1.4 (MASTER_PLAN_v2 para 7)
**Produced:** 2026-05-04T16:49:38Z
**Branch/HEAD:** source-grep-header-only-migration-2026-05-04 / 1116d827

---

## Command 1

Command: git grep -n -E submit_limit_order OR assert_live_submit_bound OR legacy-compat OR legacy: src

Raw stdout:

    src/contracts/venue_submission_envelope.py:95: if self.condition_id.startswith("legacy:"):
    src/contracts/venue_submission_envelope.py:97: if self.question_id == "legacy-compat":
    src/contracts/venue_submission_envelope.py:107:    def assert_live_submit_bound(self) -> None:
    src/data/polymarket_client.py:691:    validator = getattr(envelope, "assert_live_submit_bound", None)
    src/venue/polymarket_v2_adapter.py:140:    def submit_limit_order(
    src/venue/polymarket_v2_adapter.py:528:    def submit_limit_order(
    src/venue/polymarket_v2_adapter.py:643:        condition_id = f"legacy:{token_id}"
    src/venue/polymarket_v2_adapter.py:661:            question_id="legacy-compat",

## Command 2

Command: git grep -n compatibility src/execution

Raw stdout:

    src/execution/collateral.py:36:    # Legacy compatibility for tests/callers without token identity.
    src/execution/executor.py:1102:    docstring: Legacy compatibility wrapper for executor-level exit-order path.
    src/execution/exit_lifecycle.py:141:    docstring: Thin compatibility adapter over the executor-level exit-order path.
    src/execution/exit_triggers.py:254:    Kept for API compatibility.
    src/execution/fill_tracker.py:938:    schema. If neither the direct numeric compatibility fields ...
    src/execution/harvester.py:1296:    docstring: Get stored P_raw vector from canonical v2 snapshot, then legacy compatibility.

## Command 3

Command: git grep -n placeholder.*submit src

Raw stdout: (no matches - exit code 1, empty output)

---

## Compatibility surface analysis

| File:Line | Identifier | Classification | Live-bound asserted? | T1F flag |
|---|---|---|---|---|
| src/venue/polymarket_v2_adapter.py:140 | submit_limit_order (definition 1) | live-bound-asserted PARTIAL | Downstream in polymarket_client.py:691 via optional getattr - not unconditional | YES - getattr fallback makes assertion optional |
| src/venue/polymarket_v2_adapter.py:528 | submit_limit_order (definition 2) | placeholder-only or live-bound-asserted | UNKNOWN - requires code read to confirm | YES - second definition may be compat wrapper that creates legacy: identity at line 643 |
| src/venue/polymarket_v2_adapter.py:643 | condition_id construction in compat helper | placeholder-only | NO - constructs condition_id = legacy:{token_id} without assert_live_submit_bound | YES - primary T1F target; placeholder envelope enters submit path |
| src/venue/polymarket_v2_adapter.py:661 | question_id construction in compat helper | placeholder-only | NO | YES - sets question_id=legacy-compat; companion to line 643 |
| src/contracts/venue_submission_envelope.py:95-97 | legacy: and legacy-compat guard logic | live-bound-asserted | YES - assert_live_submit_bound method defined at line 107 checks both markers | NO - guard exists; gap is that adapter must call it unconditionally |
| src/data/polymarket_client.py:691 | assert_live_submit_bound call in client | live-bound-asserted PARTIAL | PARTIAL - getattr with None fallback; non-envelope object silently skips assertion | YES - must become unconditional call |
| src/execution/executor.py:1102 | Legacy compatibility wrapper exit path | deprecated-but-reachable | NO live-bound assertion visible | advisory - not a venue submit path |
| src/execution/exit_lifecycle.py:141 | Thin compatibility adapter exit path | deprecated-but-reachable | NO | advisory - wraps executor exit-order path |
| src/execution/exit_triggers.py:254 | API compatibility stub | deprecated-but-reachable | NO | advisory - kept for API compat |
| src/execution/collateral.py:36 | Legacy compat comment tests/callers | deprecated-but-reachable | NO | advisory - not a submit path |
| src/execution/harvester.py:1296 | Legacy compat in p_raw lookup | deprecated-but-reachable | NO | advisory - not a venue submit path |
| src/execution/fill_tracker.py:938 | Schema compat comment | doc-reference | NO | advisory - comment only |

**T1F targets lacking live-bound assertion on live submit path: 3**

1. src/venue/polymarket_v2_adapter.py:643+661 - compat helper constructs placeholder identity
   (condition_id=legacy:{token_id}, question_id=legacy-compat) and can forward into submit
   without unconditional assert_live_submit_bound.
2. src/data/polymarket_client.py:691 - getattr fallback makes assert_live_submit_bound optional;
   must become unconditional before any SDK call.
3. src/venue/polymarket_v2_adapter.py:528 - second submit_limit_order definition requires
   code read to confirm whether it calls assert_live_submit_bound before SDK contact.

No placeholder+submit adjacent code found (command 3 returned empty). Placeholder identity is
constructed at the envelope level (condition_id = f"legacy:{token_id}"), not at submit call sites.
