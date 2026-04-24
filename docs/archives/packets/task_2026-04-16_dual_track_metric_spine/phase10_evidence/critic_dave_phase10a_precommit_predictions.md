# critic-dave Phase 10A Pre-Commitment Predictions

**Cycle 2, written 2026-04-19 @ 630a1e6 BEFORE any implementation lands.**
**Mandate source**: team-lead onboarding brief + phase10a_contract.md read.
**Mode**: Gen-Verifier opening. L0.0 peer-not-suspect. L6 3-streak vigilance active.

## Pre-commitment: 6 items I predict will surface at wide review

### P1 — S5/S6/S7 are LIKELY ALREADY DONE AT HEAD (contract drift from reality)

**Confidence: HIGH.** Grep against HEAD before predictions:

- `src/contracts/provenance_registry.py:108-136` already has per-entry try/except with explicit B009 comment. Contract says "currently single `yaml.safe_load(f)` with broad try/except — one bad entry kills whole registry". **This is false at HEAD.** B009 landed at `68cbacc` (+ critic amend `389247b`).
- `src/state/truth_files.py:123-144` already has narrow `except (ValueError, TypeError, AttributeError, OverflowError)` with explicit B079 critic-amendment comment. Contract says "malformed `generated_at` silently falls back to `datetime.min` / NOW". **This is false at HEAD.** B079 landed at `cf9c148` + amend `b4d140f`. (Note: current fix logs + returns None; does NOT raise `TruthFileCorruptedError` as contract proposes.)
- `src/control/gate_decision.py:32-49` already raises `ValueError` on non-bool `enabled` with explicit B015 comment. Contract says "current: `enabled: bool | int | None` accepts truthy coercion". **This is false at HEAD.** B015 landed at `dd59c88`.

**Prediction**: executor will implement S5/S6/S7 and produce either (a) no-op diffs, (b) duplicate antibodies for already-fixed behavior, or (c) conflicting re-work (e.g. changing existing `ValueError` to `TypeError` breaks any caller catching `ValueError`).

**Escalation**: **This is a CONTRACT-QUALITY finding, not an implementation review item.** The contract was written against stale subagent memory. S5/S6/S7 should be re-scoped as **doc-flip only (S8 expansion)**, not code changes. Team-lead should either (a) verify at HEAD and re-scope, or (b) produce a concrete "gap vs current" delta per item. I flag this NOW before executor wastes cycles.

### P2 — S6 TypeError vs existing ValueError: API breakage

**Confidence: HIGH.** Contract R-CM.1 says "GateDecision raises TypeError on non-bool enabled". HEAD raises `ValueError`. If executor obeys contract literally, they change exception type. Any caller catching `ValueError` — and there WILL be some in a DT gate path — now silently passes the raise through. This is a drop-in regression risk.

**Escalation**: executor must grep `except.*ValueError` / `except Exception` in `control/` + `engine/` before changing the exception type. Or contract should align to existing `ValueError`.

### P3 — S4 evaluator line-reference is wrong + parallel status-vocab with P9C replay

**Confidence: HIGH.** Two distinct problems:

(a) **Line-reference drift**: Contract says `src/engine/evaluator.py:1676-1750` is "materialized-decision emission". Actual content at 1676-1750: `_snapshot_issue_time_value` / `_snapshot_valid_time_value` / `_store_ens_snapshot` (ensemble-snapshot DB storage, NOT materialized-decision emission). The actual decision-time fabrication sites are at L1280 (`DECISION_TIME_FABRICATED_AT_SELECTION_FAMILY`) and L1366 (`DECISION_TIME_FABRICATED_AT_STRATEGY_POLICY`). Commit `177ae8b`'s body EXPLICITLY says "handoff-doc pointer L1494-1515 turned out to be stale". Contract inherits that staleness.

(b) **Parallel status vocabularies (SD-G breach risk)**: P9C landed `decision_time_status` in `replay.py:345,418` with values `"SYNTHETIC_MIDDAY"`, `"OK"`. Contract S4 introduces `time_field_status` with values `"OK"`, `"UNAVAILABLE_UPSTREAM"`, `"UNSPECIFIED"`. Two enums, two names, overlapping semantics, living in sibling modules. **This is exactly SD-G's "don't fabricate absence as presence" distilled back into a naming-convention violation.** Executor will not know which vocabulary is canonical. Fitz-constraint #2 (translation loss) fires: future session sees two names, cannot recover which is authoritative.

**Escalation**: before executor touches S4, contract must pick ONE vocabulary (recommendation: keep `decision_time_status`; generalize the enum values). Otherwise S4 ships another SD-G debt instead of closing one.

### P4 — S1 except narrowing may eat legitimate ValueErrors

**Confidence: MEDIUM.** S1 downgrades `except Exception` → `except (RuntimeError, ValueError)`. I verified at monitor_refresh.py:614: the except wraps the WHOLE Day0 fetch+refresh block, including a DB query, network calls to Polymarket (`conn.execute`, `PolymarketClient`-style path), TIGGE fetch indirection, and numpy ops. Likely exceptions that CURRENTLY surface but would now escape: `KeyError` (bin lookup), `IndexError` (member array), `AttributeError` (null position field), `sqlite3.OperationalError` (DB contention), `requests.ConnectionError` (CLOB fetch). All of those would previously be swallowed to `logger.debug`; post-fix they become uncaught and crash the monitor tick. Production-behavior shift beyond the stated NameError target.

**Escalation**: narrow to `except (NameError, RuntimeError, ValueError)` — or leave `except Exception` but emit `logger.error` + re-raise on NameError specifically. The CRITICAL is "NameError got eaten"; the antibody should be "NameError cannot be eaten", not "everything else must now propagate". L6 3-streak calibration: "downgrade except" as a surgical move is the exact kind of move that passes code review and wrecks ops.

### P5 — R-CH.2 AST-walk antibody is brittle

**Confidence: MEDIUM.** Contract specifies R-CH.2 as "AST-walk". Carol's L9 "runtime-probe > grep-only" applies: an AST-walk that asserts "no bare `except Exception`" in a function is trivially circumvented by (a) wrapping in a helper, (b) re-catching at caller, (c) renaming, or (d) importing Exception as Alias. Static AST check passes; runtime swallowing persists. Also, AST-walks on large files are fragile to import changes.

**Escalation**: R-CH.2 should be a **runtime-probe antibody**: inject a NameError-raising mock into `_day0_monitor_probability_refresh`, call the wrapper, assert NameError propagates (is not in logger.debug buffer). Surgical-revert test: revert S1 L614 narrowing → antibody must FAIL.

### P6 — S2 ingest probe timing is backwards

**Confidence: MEDIUM.** Contract §Sequencing step 5: "executor implements S1+S2 FIRST; if S2 surfaces mis-stamp, REPLAN". This means scope might flip AFTER executor already spent cycles. Worse: if S2 discovers mis-stamp, commit #1 becomes "P9C correction" with different reviewer rotation — but Phase 10B is already queued behind it.

However, I verified at HEAD: `scripts/extract_tigge_mn2t6_localday_min.py:101` has `TEMPERATURE_METRIC = LOW_LOCALDAY_MIN.temperature_metric` (= `"low"`), L356 stamps it into payload, L411-412 validate mismatch. Ingest probe will PASS. So replan risk is low. But: contract doesn't specify what "mock-conn write capture" antibody looks like when the actual code path is `scripts/*.py` (typically run as `__main__`), not importable cleanly. Testeng may produce a skeleton that doesn't actually cover the INSERT path.

**Escalation**: testeng skeleton for R-CI.1 must import `scripts.extract_tigge_mn2t6_localday_min` (or refactor to exportable module) and exercise the actual DB write, not a mirrored constant. If script is not importable-as-module, S2 antibody is decorative.

## Scope push-back I'd raise

1. **S5/S6/S7 should be DROPPED from S-code list and folded into S8 doc-flip.** They're already-done. If team-lead disagrees, produce file:line diff showing what current code lacks vs contract wants.

2. **S4 should STALL until status-vocabulary is reconciled** with P9C's `decision_time_status`. One vocabulary or this phase ships SD-G violation #2.

3. **S8 doc-flip verification burden**: contract asserts 11 bugs flipped via "Phase 5A/5B/5C commits" — I verified B015/B009/B079 are real closures. But the contract doesn't enumerate the other 8 (B063, B069, B070, B073, B077, B078, B093, B100, B050 = 9 per the list). Each flip must cite an actual commit SHA + code location. Without that, S8 is "trust the subagent" — exactly the translation-loss vector Fitz-constraint #2 warns against.

## Items I DO NOT push back on

- **S1 NameError fix (2-line)**: verified CRITICAL, production bug, exactly as contract says. Two call-sites only in src/ (tests have one doc-comment mention unrelated). Proceed.
- **S2 probe as gate**: valid instinct. Probe result is the discriminating fact. Keep it gating S3+.
- **S3 token_suppression B070-pattern copy**: sibling pattern, B070 landed. I'll probe at review time whether every write/read site flipped to view. Not flagging now.
- **R-CH.1 relationship antibody**: correct shape (runtime probe assert `last_monitor_prob_is_fresh=True`).

## Commitment for wide review

When implementation lands, I will specifically check:

1. S5/S6/S7 diffs — confirm they are no-ops OR justified additions, NOT duplicates
2. S6 exception type — `ValueError` vs `TypeError` consistency, caller grep
3. S4 vocabulary — unified with `decision_time_status` or net-new + SD-G harm
4. S1 except narrowing — which exceptions now escape that previously logged+continued
5. R-CH.2 — AST-walk vs runtime probe; surgical-revert tested
6. R-CI.1 — imports actual script module, not mirrored constant
7. S8 — every flip cites actual commit SHA
8. Sibling probe for S1 — any OTHER file calling undefined symbols by similar rename drift (P6→P7B alias-removal sweep completeness)

## Staying persistent

I'm live for the whole phase. Ping me via SendMessage when commit is staged for wide review. I'll open adversarial + do surgical-revert probes on every antibody.

— critic-dave
