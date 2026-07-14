# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: docs/archive/2026-Q2/operations_historical/consolidated_systemic_overhaul_2026-06-11.md K2.1
# (rejection-reason taxonomy) + /tmp/funnel_autopsy.md (operator-ratified categories).
"""Typed registry for no_trade_regret_events.rejection_reason (K2.1).

THE DISEASE THIS KILLS: rejection reasons were free-text strings — structured
prefixes at best ("BASE:detail:detail"), raw exception text at worst ("UNIQUE
constraint failed: platt_models...", "database is locked", "name '...' is not
defined" all appear in the live table). Every funnel query was substring-matching
prose, and a new emit site could invent a new spelling silently.

THE CONTRACT:
- Every rejection reason BASE (the token before the first ':') is a declared
  member of :class:`RejectionReason`, carrying a category and a docstring.
- Detail stays a colon-suffix (string value preserved for DB compat):
  ``f"{RejectionReason.X.value}:{detail}"`` or the existing literal whose base
  equals a registered value.
- CI antibody (tests/contracts/test_k2_rejection_reason_registry.py) AST-scans
  every EventSubmissionReceipt(reason=...) emit site: a string literal whose
  base is not registered FAILS CI.
- Runtime sensor: the regret-write chokepoint warns (once per base per process)
  on unregistered bases — catches dynamic/exception-leak paths that the AST
  check cannot see.
- The standing funnel report (K5.2) groups by ``classify_rejection_reason``.

Categories (operator-ratified framework, funnel autopsy 2026-06-10):
- HONEST_MARKET: the market itself offers nothing (phase closed, no edge,
  empty native book). Expected, self-resolving, not actionable.
- HONEST_DATA: our inputs honestly not ready (staleness, readiness expired,
  source runs incomplete). Self-resolving on data cadence; actionable only if
  chronically dominant.
- DESIGNED_GATE: a deliberate protection fired as designed (riskguard, Kelly,
  FDR, direction law, non-tradeable scopes, submit-time fail-closed aborts).
- ARTIFICIAL_SUSPECT: pipeline-defect signal (build failures, missing proofs
  that the pipeline should have produced, connection/boundary errors, raw
  exception text). Every sustained ARTIFICIAL_SUSPECT stream is a bug hunt.
"""

from __future__ import annotations

from enum import Enum


class RejectionCategory(str, Enum):
    HONEST_MARKET = "HONEST_MARKET"
    HONEST_DATA = "HONEST_DATA"
    DESIGNED_GATE = "DESIGNED_GATE"
    ARTIFICIAL_SUSPECT = "ARTIFICIAL_SUSPECT"


class RejectionReason(str, Enum):
    """Declared rejection-reason bases. Member value == DB string base."""

    def __new__(cls, value: str, category: RejectionCategory, doc: str) -> "RejectionReason":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.category = category
        obj.__doc__ = doc
        return obj

    # ----- HONEST_MARKET -------------------------------------------------
    EVENT_BOUND_MARKET_PHASE_CLOSED = (
        "EVENT_BOUND_MARKET_PHASE_CLOSED",
        RejectionCategory.HONEST_MARKET,
        "Market phase (post_trading/settlement_day) forbids entry. Correct by design.",
    )
    TRADE_SCORE_NON_POSITIVE = (
        "TRADE_SCORE_NON_POSITIVE",
        RejectionCategory.HONEST_MARKET,
        "Certified q_lcb minus all-in cost is non-positive: no edge exists.",
    )
    EXECUTABLE_NATIVE_ASK_MISSING = (
        "EXECUTABLE_NATIVE_ASK_MISSING",
        RejectionCategory.HONEST_MARKET,
        "Native book has no executable ask (e.g. clob_no_ask_illiquid on tail bins).",
    )
    EVENT_BOUND_SELECTED_CANDIDATE_MISSING = (
        "EVENT_BOUND_SELECTED_CANDIDATE_MISSING",
        RejectionCategory.HONEST_MARKET,
        "Full-family scan produced no positive-deltaU candidate (all bins gated or "
        "non-positive). Funnel autopsy 2026-06-10 section 3: honest. Reserved now for "
        "the genuine ZERO-PROOF family (annotated :family_candidates=..:proofs=..); the "
        "all-priced-rejected case carries EVENT_BOUND_ALL_CANDIDATES_REJECTED instead.",
    )
    EVENT_BOUND_ALL_CANDIDATES_REJECTED = (
        "EVENT_BOUND_ALL_CANDIDATES_REJECTED",
        RejectionCategory.HONEST_MARKET,
        "Every priced candidate in the family was gate-rejected (capital-efficiency "
        "EV<=0 / direction-law / buy-NO-evidence), the efficient-market normal state. "
        "Carries per-class counts + the closest-to-tradeable leg. Replaces the "
        "NATIVE_ASK_MISSING / bare SELECTED_CANDIDATE_MISSING label lie (2026-06-11) "
        "when the books were actually live two-sided.",
    )
    EVENT_BOUND_CANDIDATE_REJECTED = (
        "EVENT_BOUND_CANDIDATE_REJECTED",
        RejectionCategory.DESIGNED_GATE,
        "One priced candidate from a family-level all-candidates-rejected decision. "
        "Carries queryable bin/direction/q/cost fields so continuous redecision and "
        "portfolio rotation can reason from structured economics instead of parsing "
        "the family summary text.",
    )
    QKERNEL_SPINE_NO_TRADE = (
        "QKERNEL_SPINE_NO_TRADE",
        RejectionCategory.HONEST_MARKET,
        "The q-kernel spine evaluated the full family and returned a typed no-trade "
        "reason (for example no positive edge, no direction-law candidate, or no "
        "direct executable route). Detail after ':' carries the inner spine reason.",
    )
    EVENT_BOUND_MARKET_TOPOLOGY_INVALID = (
        "EVENT_BOUND_MARKET_TOPOLOGY_INVALID",
        RejectionCategory.HONEST_MARKET,
        "Market family topology unparseable/invalid as listed by the venue.",
    )
    GLOBAL_NOT_SELECTED = (
        "GLOBAL_NOT_SELECTED",
        RejectionCategory.HONEST_MARKET,
        "A complete same-epoch capital auction selected a strictly better action. "
        "The losing carrier is consumed; fresh belief or price evidence creates a "
        "new event and a new comparison.",
    )

    # ----- HONEST_DATA ---------------------------------------------------
    LIVE_INFERENCE_INPUTS_MISSING = (
        "LIVE_INFERENCE_INPUTS_MISSING",
        RejectionCategory.HONEST_DATA,
        "A live-inference input (posterior bundle, authority readiness, calibration "
        "row) is absent or expired. Self-resolving on the data cadence.",
    )
    EXECUTABLE_SNAPSHOT_BLOCKED = (
        "EXECUTABLE_SNAPSHOT_BLOCKED",
        RejectionCategory.HONEST_DATA,
        "Executable snapshot substrate starved (capture-retry exhausted).",
    )
    EXECUTABLE_SNAPSHOT_STALE = (
        "EXECUTABLE_SNAPSHOT_STALE",
        RejectionCategory.HONEST_DATA,
        "Selected executable snapshot expired between decision and use.",
    )
    MONEY_PATH_HORIZON_EXPIRED = (
        "MONEY_PATH_HORIZON_EXPIRED",
        RejectionCategory.HONEST_MARKET,
        "A horizon-bounded money-path transient reached a real event or execution "
        "window boundary (operator disarm, venue closed/not listed, selection "
        "deadline past, or local timeliness floor past). This is not an attempt "
        "cap; recurring volume is an operations/cadence signal.",
    )
    MONEY_PATH_TRANSIENT_EXHAUSTED = (
        "MONEY_PATH_TRANSIENT_EXHAUSTED",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Legacy durable rows only. The live reactor no longer emits attempt-count "
        "exhaustion; current terminal transients use MONEY_PATH_HORIZON_EXPIRED.",
    )
    EXECUTOR_PRE_VENUE_REJECTED = (
        "EXECUTOR_PRE_VENUE_REJECTED",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "The executor's pre-venue integrity guard refused the final intent before "
        "any venue call (no side effect). Live 2026-06-12: maker intents carried "
        "the EDLI event id where the venue-event identity was expected.",
    )
    EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING = (
        "EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING",
        RejectionCategory.HONEST_DATA,
        "No executable snapshot row bound to the event.",
    )
    EVENT_BOUND_SELECTED_SNAPSHOT_MISSING = (
        "EVENT_BOUND_SELECTED_SNAPSHOT_MISSING",
        RejectionCategory.HONEST_DATA,
        "The event's selected snapshot id resolves to no row.",
    )
    EVENT_BOUND_MARKET_TOPOLOGY_MISSING = (
        "EVENT_BOUND_MARKET_TOPOLOGY_MISSING",
        RejectionCategory.HONEST_DATA,
        "No market-topology row for the event's family.",
    )
    CALIBRATION_AUTHORITY_EVIDENCE_MISSING = (
        "CALIBRATION_AUTHORITY_EVIDENCE_MISSING",
        RejectionCategory.HONEST_DATA,
        "No calibration authority evidence (model row / Platt calibrator) for the cell.",
    )
    FSR_SOURCE_RUN_NOT_COMPLETE = (
        "FSR_SOURCE_RUN_NOT_COMPLETE",
        RejectionCategory.HONEST_DATA,
        "Forecast source run incomplete for the decision cycle.",
    )
    FSR_WINDOW_AUTHORITY_NOT_LIVE_ELIGIBLE = (
        "FSR_WINDOW_AUTHORITY_NOT_LIVE_ELIGIBLE",
        RejectionCategory.HONEST_DATA,
        "Forecast source window authority not live-eligible.",
    )
    SOURCE_TRUTH_BLOCKED = (
        "SOURCE_TRUTH_BLOCKED",
        RejectionCategory.HONEST_DATA,
        "Source-truth gate blocked the cell (observation source not authoritative).",
    )
    FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED = (
        "FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED",
        RejectionCategory.HONEST_DATA,
        "Forecast reader bundle not live-eligible (readiness expired, bounds missing).",
    )
    TOPOLOGY_CLOCK_MISSING = (
        "TOPOLOGY_CLOCK_MISSING",
        RejectionCategory.HONEST_DATA,
        "Family clock (timezone/window) unavailable for time-semantics checks.",
    )
    REPLACEMENT_FORECAST_HOOK_BLOCKED = (
        "REPLACEMENT_FORECAST_HOOK_BLOCKED",
        RejectionCategory.HONEST_DATA,
        "Replacement-forecast hook reported blocking reason codes (e.g. switch "
        "readiness missing).",
    )
    GLOBAL_FAMILY_INELIGIBLE = (
        "GLOBAL_FAMILY_INELIGIBLE",
        RejectionCategory.HONEST_DATA,
        "The family's current probability or source bundle is not yet admissible. "
        "The same event remains retryable until that current substrate advances.",
    )

    # ----- DESIGNED_GATE -------------------------------------------------
    RISK_GUARD_BLOCKED = (
        "RISK_GUARD_BLOCKED",
        RejectionCategory.DESIGNED_GATE,
        "RiskGuard refused the trade (caps, chain state, drawdown rules).",
    )
    KELLY_REJECTED = (
        "KELLY_REJECTED",
        RejectionCategory.DESIGNED_GATE,
        "Kelly sizing produced no admissible stake.",
    )
    FDR_REJECTED = (
        "FDR_REJECTED",
        RejectionCategory.DESIGNED_GATE,
        "False-discovery-rate gate rejected the candidate's p-value. NOTE: the buy_no "
        "p=1.0 hardcode (100% artificial FDR kills) was fixed 9ddad492d8; sustained "
        "one-direction-only FDR streams remain a tripwire.",
    )
    FDR_FULL_FAMILY_PROOF_MISSING = (
        "FDR_FULL_FAMILY_PROOF_MISSING",
        RejectionCategory.DESIGNED_GATE,
        "FDR requires the full-family proof; absent proof fails closed.",
    )
    UNSUPPORTED_EDLI_LIVE_SCOPE = (
        "UNSUPPORTED_EDLI_LIVE_SCOPE",
        RejectionCategory.DESIGNED_GATE,
        "Live adapter boundary rejected an unsupported EDLI live scope. The only "
        "production scope is forecast_plus_day0.",
    )
    EVENT_TYPE_OUT_OF_LIVE_SCOPE = (
        "EVENT_TYPE_OUT_OF_LIVE_SCOPE",
        RejectionCategory.DESIGNED_GATE,
        "Live adapter boundary rejected an event type outside the forecast/day0 "
        "money path.",
    )
    DAY0_OUT_OF_SCOPE_AT_BOUNDARY = (
        "DAY0_OUT_OF_SCOPE_AT_BOUNDARY",
        RejectionCategory.DESIGNED_GATE,
        "day0-lane event while scope excludes day0 entirely.",
    )
    DAY0_HARD_FACT_AUTHORITY_BLOCKED = (
        "DAY0_HARD_FACT_AUTHORITY_BLOCKED",
        RejectionCategory.DESIGNED_GATE,
        "day0 hard-fact authority (observed extreme) contradicts the candidate.",
    )
    DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE = (
        "DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE",
        RejectionCategory.HONEST_DATA,
        "Day0 remaining-day probability cannot be computed because the hourly "
        "remaining-window member substrate is unavailable or insufficient.",
    )
    ENTRY_COOLDOWN = (
        "entry_cooldown",
        RejectionCategory.DESIGNED_GATE,
        "Duplicate-entry suppression while the same token/direction is cooling down "
        "or already active; prevents repeated same-order submission.",
    )
    OPEN_POSITION_SAME_TOKEN_MONITOR_OWNED = (
        "OPEN_POSITION_SAME_TOKEN_MONITOR_OWNED",
        RejectionCategory.DESIGNED_GATE,
        "A new-entry candidate's token is already present in canonical "
        "position_current; the monitor/exit lifecycle owns further decisions for "
        "that exposure, so the entry selector excludes it before executor submit.",
    )
    LIVE_CANARY_DISABLED = (
        "LIVE_CANARY_DISABLED",
        RejectionCategory.DESIGNED_GATE,
        "Live canary off: submit path closed by operator configuration.",
    )
    OPERATOR_ARM_REQUIRED = (
        "OPERATOR_ARM_REQUIRED",
        RejectionCategory.DESIGNED_GATE,
        "Operator arm gate not armed for live submission.",
    )
    SUBMIT_DISABLED = (
        "SUBMIT_DISABLED",
        RejectionCategory.DESIGNED_GATE,
        "real_order_submit_enabled=false: decision pipeline runs, submission closed.",
    )
    EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED = (
        "EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED",
        RejectionCategory.DESIGNED_GATE,
        "Durable submit outbox unavailable; refusing non-durable submission.",
    )
    ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING = (
        "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING",
        RejectionCategory.DESIGNED_GATE,
        "buy_no admission requires the independent YES posterior for the direction "
        "law; absent fails closed.",
    )
    MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE = (
        "MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE",
        RejectionCategory.DESIGNED_GATE,
        "Stale market-channel trade events never trigger direct trading.",
    )
    GLOBAL_DUPLICATE_FAMILY_CARRIER = (
        "GLOBAL_DUPLICATE_FAMILY_CARRIER",
        RejectionCategory.DESIGNED_GATE,
        "A same-epoch carrier already owns this family. Consuming the duplicate "
        "preserves one family decision without suppressing future fresh events.",
    )
    NO_SUBMIT_PROOF_FALSE = (
        "NO_SUBMIT_PROOF_FALSE",
        RejectionCategory.DESIGNED_GATE,
        "Receipt's proof_accepted is False on the no-submit lane.",
    )
    REPLACEMENT_FORECAST_HOOK_DIRECTION_FLIP = (
        "REPLACEMENT_FORECAST_HOOK_DIRECTION_FLIP",
        RejectionCategory.DESIGNED_GATE,
        "Replacement policy may veto but never flip direction without evidence.",
    )
    REPLACEMENT_FORECAST_LIVE_DIRECTION_PROOF_MISSING = (
        "REPLACEMENT_FORECAST_LIVE_DIRECTION_PROOF_MISSING",
        RejectionCategory.DESIGNED_GATE,
        "Live replacement direction requires its proof artifact.",
    )
    REPLACEMENT_FORECAST_LIVE_EXECUTABLE_PROOF_MISSING = (
        "REPLACEMENT_FORECAST_LIVE_EXECUTABLE_PROOF_MISSING",
        RejectionCategory.DESIGNED_GATE,
        "Live replacement execution requires its executable proof artifact.",
    )
    REPLACEMENT_FORECAST_HOOK_UNSUPPORTED = (
        "REPLACEMENT_FORECAST_HOOK_UNSUPPORTED",
        RejectionCategory.DESIGNED_GATE,
        "Replacement hook does not support this event shape.",
    )
    SUBMIT_ABORTED_EDGE_REVERSED = (
        "SUBMIT_ABORTED_EDGE_REVERSED",
        RejectionCategory.DESIGNED_GATE,
        "Submit-time recapture found non-positive marginal utility on the fresh curve.",
    )
    SUBMIT_ABORTED_FAMILY_REVERSED = (
        "SUBMIT_ABORTED_FAMILY_REVERSED",
        RejectionCategory.DESIGNED_GATE,
        "Submit-time family re-rank changed the selected candidate (scope-set "
        "mismatch false-firing fixed cbfa50cc87).",
    )
    SUBMIT_ABORTED_PRICE_MOVED = (
        "SUBMIT_ABORTED_PRICE_MOVED",
        RejectionCategory.DESIGNED_GATE,
        "Submit-time price moved beyond tolerance vs the proven price.",
    )
    SUBMIT_ABORTED_BELOW_MIN_ORDER = (
        "SUBMIT_ABORTED_BELOW_MIN_ORDER",
        RejectionCategory.DESIGNED_GATE,
        "Recaptured admissible stake fell below venue min order.",
    )
    SUBMIT_ABORTED_MODE_FLIPPED = (
        "SUBMIT_ABORTED_MODE_FLIPPED",
        RejectionCategory.DESIGNED_GATE,
        "Fresh-book mode witness diverged from the proven execution_mode_intent; "
        "fail-closed abort + re-rank (never an inline mode rebuild).",
    )
    SUBMIT_ABORTED_ENTRY_PRICE_BELOW_STRATEGY_FLOOR = (
        "SUBMIT_ABORTED_ENTRY_PRICE_BELOW_STRATEGY_FLOOR",
        RejectionCategory.DESIGNED_GATE,
        "Pre-submit certificate replay found the chosen entry price below the "
        "strategy floor. This is a no-side-effect submit abort for low-price "
        "lottery dust, not a certificate build defect.",
    )
    SUBMIT_ABORTED_EXPECTED_PROFIT_BELOW_STRATEGY_FLOOR = (
        "SUBMIT_ABORTED_EXPECTED_PROFIT_BELOW_STRATEGY_FLOOR",
        RejectionCategory.DESIGNED_GATE,
        "Pre-submit certificate replay found the fee-adjusted expected profit "
        "below the strategy floor. No order is posted; the next event re-decides "
        "from fresh price and belief evidence.",
    )
    SUBMIT_ABORTED_EDGE_DENSITY_BELOW_STRATEGY_FLOOR = (
        "SUBMIT_ABORTED_EDGE_DENSITY_BELOW_STRATEGY_FLOOR",
        RejectionCategory.DESIGNED_GATE,
        "Pre-submit certificate replay found submit edge density below the "
        "strategy floor. This rejects capital-inefficient micro-edge orders without "
        "misclassifying the decision as a system build failure.",
    )
    ENTRY_ACTIONABLE_CERTIFICATE = (
        "entry_actionable_certificate",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Executor pre-venue authority guard rejected the persisted live actionable "
        "certificate. After final-intent snapshot recapture is represented separately, "
        "remaining failures are structural certificate/persistence defects, not "
        "market no-edge and not a reason to requeue the same event.",
    )
    FILL_UP_PRESUBMIT_REREAD_ABORT = (
        "FILL_UP_PRESUBMIT_REREAD_ABORT",
        RejectionCategory.DESIGNED_GATE,
        "A same-token fill-up passed admission, but the pre-submit reread found fresh "
        "family exposure that makes the residual unsafe. No venue call has occurred; "
        "the fill-up lease is aborted and the next live event must re-decide from "
        "current position truth.",
    )
    FILL_UP_NO_SUBMIT = (
        "FILL_UP_NO_SUBMIT",
        RejectionCategory.DESIGNED_GATE,
        "Continuous redecision evaluated a held same-token fill-up and deliberately "
        "submitted no order (for example belief did not strengthen, target exposure "
        "is already reached, residual is below venue minimum, or a family lease is "
        "already active).",
    )
    SHIFT_BIN_EXIT_OLD_LEG_PENDING = (
        "SHIFT_BIN_EXIT_OLD_LEG_PENDING",
        RejectionCategory.DESIGNED_GATE,
        "A shift-to-better-bin decision found the old family leg still live. The "
        "correct action is close-before-open: submit or await the old-leg exit, not "
        "open the new bin in parallel.",
    )
    SHIFT_BIN_EXIT_ONLY_COMPLETE = (
        "SHIFT_BIN_EXIT_ONLY_COMPLETE",
        RejectionCategory.DESIGNED_GATE,
        "A shift-bin old-leg exit completed, but the fresh family selection changed "
        "before the new entry. The shift lease is closed without submitting the stale "
        "new-bin entry.",
    )
    SHIFT_BIN_ENTRY_IN_FLIGHT = (
        "SHIFT_BIN_ENTRY_IN_FLIGHT",
        RejectionCategory.DESIGNED_GATE,
        "A shift-bin entry is already submitted, unknown, partially filled, or under "
        "review. The existing command owns the family until venue/chain truth advances.",
    )
    SHIFT_BIN_NO_SUBMIT = (
        "SHIFT_BIN_NO_SUBMIT",
        RejectionCategory.DESIGNED_GATE,
        "Continuous redecision evaluated a sibling-bin shift and deliberately "
        "submitted no order because the shift lease or family exposure made a new "
        "action unsafe or unnecessary.",
    )
    LEGACY_INJECTED_TEST_SUBMIT = (
        "legacy_injected_test_submit",
        RejectionCategory.DESIGNED_GATE,
        "Test-only injected submit path marker (never a live reason).",
    )

    # ----- DESIGNED_GATE :: Day0 input-correctness (NOT a cap) -------------
    # day0_multiangle_critique_2026-06-12 Blind spot C, re-scoped 2026-06-12 per
    # operator anti-over-design directive (no caps, no trip-wires). This is an
    # INPUT-ORDERING correctness check, not a configurable ban window: a day0
    # decision's orderbook snapshot must be newer than the observation state that
    # produced its probability. It fires ONLY when the ordering is genuinely
    # violated (a stale book pricing an already-moved observation) — a real data
    # error, not a throttle. DESIGNED_GATE because it is a deliberate, evidence-
    # based refusal of an incoherent decision input.
    DAY0_QUOTE_PRECEDES_OBSERVATION = (
        "DAY0_QUOTE_PRECEDES_OBSERVATION",
        RejectionCategory.DESIGNED_GATE,
        "Day0 input-ordering violation: the orderbook snapshot used to price the "
        "candidate was captured at/before the observation availability that produced "
        "its probability. The quote prices a stale, pre-update book — an incoherent "
        "decision input, refused on correctness grounds (NOT a time-window cap).",
    )

    # ----- ARTIFICIAL_SUSPECT ---------------------------------------------
    EDLI_LIVE_CERTIFICATE_BUILD_FAILED = (
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "The live certificate could not be built from a candidate that passed the "
        "pipeline — a seam defect until proven otherwise (hash missing, "
        "QUOTE_FEASIBILITY_BID_ASK_REQUIRED, authority blocked...).",
    )
    EDLI_EVENT_BOUND_RECEIPT_SCHEMA_INVALID = (
        "EDLI_EVENT_BOUND_RECEIPT_SCHEMA_INVALID",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "The receipt our own pipeline built fails schema validation.",
    )
    EDLI_EVENT_BOUND_RECEIPT_NOT_NO_SUBMIT = (
        "EDLI_EVENT_BOUND_RECEIPT_NOT_NO_SUBMIT",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Receipt kind mismatch at the no-submit boundary.",
    )
    KELLY_PROOF_MISSING = (
        "KELLY_PROOF_MISSING",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "A candidate that reached sizing has no Kelly proof — the pipeline should "
        "have produced one.",
    )
    NO_SUBMIT_CERTIFICATE_REJECTED = (
        "NO_SUBMIT_CERTIFICATE_REJECTED",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Certificate verification rejected our own no-submit certificate (the "
        "wrong-chain-credential cert wall was this category).",
    )
    EXECUTOR_BOUNDARY_MISSING = (
        "EXECUTOR_BOUNDARY_MISSING",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Executor boundary unavailable at submit time.",
    )
    FORECAST_AUTHORITY_CONNECTION_MISSING = (
        "FORECAST_AUTHORITY_CONNECTION_MISSING",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Forecast authority DB connection absent — wiring defect.",
    )
    TOPOLOGY_AUTHORITY_CONNECTION_MISSING = (
        "TOPOLOGY_AUTHORITY_CONNECTION_MISSING",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Topology authority DB connection absent — wiring defect.",
    )
    CALIBRATION_AUTHORITY_CONNECTION_MISSING = (
        "CALIBRATION_AUTHORITY_CONNECTION_MISSING",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Calibration authority DB connection absent — wiring defect.",
    )
    UNKNOWN_REVIEW_REQUIRED = (
        "UNKNOWN_REVIEW_REQUIRED",
        RejectionCategory.ARTIFICIAL_SUSPECT,
        "Dead-letter stage: an unhandled exception became the reason text. Every "
        "row here is a bug (raw exception text in rejection_reason is the disease "
        "this registry exists to kill).",
    )


_REGISTRY_BY_VALUE: dict[str, RejectionReason] = {m.value: m for m in RejectionReason}


def base_reason(raw: object) -> str:
    """The taxonomy base of a raw rejection_reason string (token before first ':')."""
    return str(raw).split(":", 1)[0].strip()


def lookup_rejection_reason(raw: object) -> RejectionReason | None:
    """Registry member for a raw reason string, or None when unregistered."""
    return _REGISTRY_BY_VALUE.get(base_reason(raw))


def is_registered_rejection_reason(raw: object) -> bool:
    return lookup_rejection_reason(raw) is not None


def classify_rejection_reason(raw: object) -> RejectionCategory:
    """Category for a raw reason string. Unregistered bases are ARTIFICIAL_SUSPECT
    BY DEFINITION: a reason no one declared is a defect signal (free-text leak,
    new spelling, raw exception) until someone registers and categorizes it."""
    member = lookup_rejection_reason(raw)
    if member is not None:
        return member.category
    return RejectionCategory.ARTIFICIAL_SUSPECT
