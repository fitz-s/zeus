# T1F Security Review (10-probe adversarial)

## Threat model recap

T1F defends a K0 LIVE BOUNDARY in a real-money Polymarket trading daemon. The
target attack: a compatibility-helper code path constructs a placeholder
`VenueSubmissionEnvelope` with `condition_id='legacy:{token_id}'` and
`question_id='legacy-compat'` (no real market identity), and that envelope
reaches `client.create_and_post_order` / `client.post_order`, causing real-money
order submission against an unverified market. The pre-T1F soft signal
`is_compatibility_placeholder=True` was advisory only and not enforced as a
hard gate before SDK contact. T1F lifts it to a hard gate at TWO levels: outer
gate in `submit_limit_order` (evidence-aware compat refusal) and inner gate in
`submit()` (envelope-level live-bound assertion before any SDK call).

## Probe table

| # | Probe | Verdict | Evidence (file:line) | Severity |
|---|-------|---------|----------------------|----------|
| 1 | Direct SDK bypass — any `create_and_post_order` / `create_order` / `post_order` call outside `submit()`? | PASS — all 5 SDK call sites are inside `submit()` lines 361-385; the only adapter caller, `polymarket_client.py:457`, calls `adapter.submit(pending_envelope)` (the asserted entry). `submit_limit_order` re-routes through `submit()` at line 632. | `src/venue/polymarket_v2_adapter.py:361,362,369,371,380` (all in submit); `src/data/polymarket_client.py:457` (only external caller, uses submit) | N/A |
| 2 | Assertion try/except swallow — does `assert_live_submit_bound()` failure silently continue? | PASS — `try/except ValueError` at lines 318-328 logs the rejection counter then `return _rejected_submit_result(...)`. No `pass`, no `continue`, no fallthrough to SDK. Control flow guaranteed. | `src/venue/polymarket_v2_adapter.py:318-328` | N/A |
| 3 | `_allow_compat_for_test` exposure surface — class attribute, env var, config? | PASS — flag is a function-default kwarg only (`_allow_compat_for_test: bool = False` at line 552). No class attribute, no module variable, no env-var or config-file plumbing (`grep -rn _allow_compat_for_test src/ scripts/` → 3 occurrences in adapter only; `grep -rn '*.json *.yaml ...'` → 0 production config; `grep -rn 'os.environ.*[Cc]ompat' src/ scripts/` → 0). Underscore-prefixed name signals private/test API. | `src/venue/polymarket_v2_adapter.py:552,565,571` (only definition + 2 internal references) | N/A |
| 4 | Placeholder identity bypass via case/whitespace — does `LEGACY:` or ` legacy:` evade detection? | LOW — `compatibility_placeholder_reason` at `venue_submission_envelope.py:95` uses case-sensitive `condition_id.startswith("legacy:")` and exact-match `question_id == "legacy-compat"`. An attacker that controls envelope construction COULD craft `LEGACY:` or `Legacy:` and evade the placeholder detector. **However**, the placeholder string is constructed only at lines 686/703 from `f"legacy:{token_id}"` literal, never from external input. The risk is hypothetical: it requires either (a) production code learning to construct `LEGACY:` envelopes (no such caller exists), or (b) external deserialization of a hostile envelope (no untrusted-source deserialization path identified). The second arm of the assertion (`selected_outcome_token_id != expected_token`, line 117) catches the operational footprint of the helper because it always sets `yes_token_id == no_token_id` (line 706). Recommend hardening to case-insensitive prefix check defensively. | `src/contracts/venue_submission_envelope.py:95-100,107-120`; `src/venue/polymarket_v2_adapter.py:686,703-706` | LOW |
| 5 | Partial placeholder — only one of `condition_id` / `question_id` set | PASS — `compatibility_placeholder_reason` evaluates BOTH fields independently (`if startswith("legacy:")`, `if question_id == "legacy-compat"`, `if yes==no`) and returns the union. Any one trigger surfaces a non-empty reason → `assert_live_submit_bound` raises. The collapsed-token check (line 99-100) is a third independent guardrail. Three-way OR boolean. | `src/contracts/venue_submission_envelope.py:93-101,107-120` | N/A |
| 6 | Race / mutation between assertion and SDK call | PASS — `VenueSubmissionEnvelope` is `@dataclass(frozen=True)` (line 34); reassignment after creation raises `FrozenInstanceError`. In `submit()` (lines 315-398), parameter `envelope` is never rebound; only field READS occur (lines 339, 352, 365-366, 382-383). `with_updates()` at line 338 creates a NEW envelope only on the rejected branch (preflight failure), which short-circuits to return at lines 342-347 — never reaches SDK. No mutation window. | `src/contracts/venue_submission_envelope.py:34`; `src/venue/polymarket_v2_adapter.py:315-398` | N/A |
| 7 | Counter emit reliability — does rejection depend on log success? | PASS — `logger.warning("telemetry_counter event=...")` at lines 321 and 572 occurs BEFORE `return _rejected_submit_result(...)`. Python `logging.Logger.warning` swallows handler errors internally (calls `self.handleError(record)`); even if the handler raises, the `return` always executes. Rejection control flow is independent of telemetry. **Caveat (LOW)**: counter is `logger.warning` text, not a typed counter sink. Telemetry observability could degrade silently if log filtering is enabled, but the rejection itself is unaffected. T1F invariant T1F-COUNTER-EMITTED accepts this trade-off. | `src/venue/polymarket_v2_adapter.py:321-328,572-587` | LOW (telemetry-only, not security) |
| 8 | Error code leakage — does `SubmitResult` include secrets in `error_code`/`error_message`? | PASS — `_rejected_submit_result` (line 918) and `_compat_rejected_submit_result` (line 634) populate only `error_code` (literal constants like `BOUND_ENVELOPE_NOT_LIVE_AUTHORITY`) and `error_message` (either a constant string or `str(exc)` from a stdlib `ValueError` raised by `assert_live_submit_bound`, which contains only the placeholder reason — no key material). `signer_key` is held as `self.signer_key` but never traversed into envelope/result fields. `funder_address` is included in envelope (public on-chain identity, not a secret). 7 `str(exc)` sites (lines 248, 327, 335, 357, 378, 599, 622) wrap exceptions from `assert_live_submit_bound` (deterministic message, no secret), `preflight()`, `_sdk_client()`, `_compat_snapshot_for_token()`, `client.create_order()`. The SDK-exception sites COULD theoretically leak SDK-internal data (e.g. signed payloads, response bodies), but these execute AFTER the live-bound assertion has passed (real bound envelope only), so a placeholder envelope never reaches them. | `src/venue/polymarket_v2_adapter.py:918-937,634-671` | LOW (residual: SDK-side exception messages on real-bound path could leak network/SDK detail; out of T1F scope) |
| 9 | Test-flag leakage into `src/` | PASS — `git grep -n _allow_compat_for_test src/ scripts/ tests/` returns: `src/venue/polymarket_v2_adapter.py:552,565,571` (definition + comments + check, all default-False), and `tests/test_v2_adapter.py:241,276,293` (3 sites passing True, exactly matching the AMD-T1F-1 named-tests boundary). Zero `=True` calls in `src/` or `scripts/`. AMD-T1F-1 boundary held. AMD-T1F-2 used envelope-construction rewrite (no kwarg) for the 4th test. | git grep result; `tests/test_v2_adapter.py:241,276,293` | N/A |
| 10 | T1A regression — duplicate inline `CREATE TABLE settlement_commands` DDL | PASS — `git grep "CREATE TABLE IF NOT EXISTS settlement_commands" src/ scripts/ tests/` returns exactly 1 hit in `src/execution/settlement_commands.py:28` (the canonical schema source). The test-side hit at `tests/test_settlement_commands_schema.py:19` is a string pattern in the regression-asserter, not a duplicate DDL. T1A-DDL-SINGLE-SOURCE invariant intact; T1F did not re-introduce inline DDL. | `src/execution/settlement_commands.py:28`; T1A invariant log entry 2026-05-05T04:55:00Z | N/A |

## Cross-reference vs `src/data/polymarket_client.py:407-424` mirror

T1F's `submit()` rejection block (lines 318-328) STRENGTHENS the mirror:
- Mirror: only checks `_pending_submission_envelope` (executor path).
- T1F: checks every envelope reaching adapter.submit(), including the
  internally-constructed placeholder from `submit_limit_order` itself (defense
  in depth — even if outer gate at line 571 is bypassed by `_allow_compat_for_test=True`,
  the inner gate at line 319 still rejects placeholder identities).
- Mirror returns a `dict` `{"success": False, "errorCode": ...}`; T1F returns
  a typed `SubmitResult` envelope with the same error_code constant
  (`BOUND_ENVELOPE_NOT_LIVE_AUTHORITY`). Naming convergence is intentional.
- Mirror uses helper `_submission_envelope_live_bound_error(envelope)`
  (`polymarket_client.py:690-698`) that wraps `validator()` in try/except
  Exception. T1F catches `ValueError` specifically. Narrower catch is
  STRONGER (won't mask unexpected non-ValueError errors as benign rejection).

Divergence is acceptable per the planner directive. Both gates are sound.

## Notes on residual surfaces (informational, out of T1F scope)

- `polymarket_client.py:690-698` catches bare `Exception` and returns the
  message as a rejection reason. If a future contributor changes
  `assert_live_submit_bound` to raise a non-ValueError on a non-placeholder
  fault (e.g. hardware failure during validation), the mirror site would
  classify it as a placeholder rejection. Not a T1F regression; consider in
  a future contract-hardening packet.
- `compatibility_placeholder_reason` is case-sensitive. A defensive
  `.casefold().startswith("legacy:")` and stripped equality check would
  remove a hypothetical case-skew bypass at zero performance cost. Out of
  T1F scope; tracked in this review as LOW.

## T1F Security Verdict

SECURITY_DONE_T1F
verdict: APPROVE_WITH_MITIGATIONS
critical_findings: []
high_findings: []
medium_findings: []
low_findings:
  - "Probe 4 — placeholder detector uses case-sensitive prefix; defensive casefold recommended (no current bypass path; hypothetical only)"
  - "Probe 7 — counter is logger.warning text not typed counter sink; rejection itself is independent of log success"
  - "Probe 8 — SDK-exception path on real-bound envelopes could leak network/SDK detail in error_message; out of T1F scope (only fires post-assertion on live-bound envelopes)"
mirror_pattern_consistent: yes
test_flag_in_src: no
no_t1a_regression: yes
ready_for_close: yes
