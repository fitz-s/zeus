# Operator Decision Brief — P0 Hardening (O1–O5)

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: [fix_plan.md §6](docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/fix_plan.md), grep evidence collected on `main` HEAD `2a8902c`.

This brief gives concrete options + recommendation for the 5 decisions that block P0 promotion. Each option carries cost/risk so you can override quickly. **My recommended choice is marked ★.** I am proceeding under the recommended choices unless you redirect.

---

## O1 — V2 SDK / package / version pin

### Evidence

- Current declared floor in [requirements.txt:14](requirements.txt:14): `py-clob-client>=0.25`
- Currently installed in venv: `py-clob-client==0.34.6` (Polymarket Engineering, https://github.com/Polymarket/py-clob-client)
- Module path: `.venv/lib/python3.14/site-packages/py_clob_client/client.py` — has no `__version__` attr at module level (introspected)
- No V1/V2 split lives in the SDK constructor that we have evidence of; V2 cutover treats the URL `https://clob.polymarket.com` as the production endpoint after switchover (per PR #18 narrative — unconfirmed by URL)

### Options

| ID | Choice | Cost | Risk |
|----|--------|------|------|
| O1-a | Pin exact: `py-clob-client==0.34.6` | One-line edit | Pin freezes us out of vendor patches; needs a vendor-update process |
| O1-b ★ | Floor bump: `py-clob-client>=0.34,<0.40` | One-line edit | Low; bands in tested-floor without freezing patches |
| O1-c | Keep `>=0.25` | Zero edit | Higher: 0.25→0.34 spans 9 minor versions of vendor changes; if a future install pulls 0.25 it may not match V2 surface |
| O1-d | Vendor SDK source pin via git URL | Larger edit, branch tracking | Highest cost, most control; only if vendor publishes a V2 branch |

### Recommendation: **O1-b**

Lock the floor at the version we have actually exercised in tests (`0.34.6`) and put a soft ceiling at the next minor (`<0.40`) to catch breaking changes early. The V2 preflight in P0.4 then verifies the runtime endpoint matches expected V2 generation, so the SDK pin is a *secondary* defense, not the primary one.

If you want stricter pinning (`==0.34.6`), say so; the edit is identical in shape.

---

## O2 — `runtime_posture` lifecycle ownership and edit policy

### Evidence

- Existing posture-like surfaces:
  - `state/LIVE_LOCK` — file-existence flag
  - `state/auto_pause_failclosed.tombstone` — operator tombstone
  - `state/control_plane.json` — runtime control surface
  - `state/cancel-signal-state.json` — runtime signal
- Existing entry-mode flag: `ZEUS_MODE` env var (validated at [src/main.py:472](src/main.py:472)); cannot start daemon without it
- No file currently named `runtime_posture.*` anywhere
- `architecture/` carries committed YAML manifests; `state/` carries runtime-mutable JSON

### Options

| ID | Choice | Owner | Edit policy | Audit |
|----|--------|-------|-------------|-------|
| O2-a | New env var `ZEUS_BRANCH_POSTURE` (mirror of `ZEUS_MODE`) | operator | env at launch | shell history only |
| O2-b | New committed YAML `architecture/runtime_posture.yaml` (per-branch defaults) + per-runtime override `state/runtime_posture.json` | operator (commit) + operator (state edit) | git PR for default; manual edit for override | git log + state file mtime |
| O2-c ★ | New committed YAML `architecture/runtime_posture.yaml` keyed by branch name; runtime reads it directly; *no override path* | operator (commit only) | git PR | git log |
| O2-d | Reuse `ZEUS_MODE` and add new mode values like `live_no_new_entries`, `exit_only` | operator | env at launch | shell history |

### Recommendation: **O2-c**

The whole point of K1+K2 is that posture *is law*, not runtime convenience. A committed YAML file means:

- Default posture per branch is reviewable and can be required-PR-approved.
- No "operator turned it off without PR" path exists.
- Audit trail is the same as authority manifests (git log).
- INV-26 (the law backing the posture flag) ties to the YAML by path; testing is straightforward.

O2-b's split (committed default + uncommitted override) is appealing for emergencies but recreates the very problem we are fixing: a second authority plane that drifts. If you need an emergency override path, do it as a commit, not a state-file mutation.

O2-a (env var) is operationally fine but requires every launch shell to do the right thing. We have evidence from past Zeus failures that env-var-only gates leak.

If branch list grows large or you need runtime override, escalate to O2-b.

---

## O3 — CLOB V2 cutover date evidence and authority

### Evidence

- PR #18 cites `2026-04-28 ~11:00 UTC` based on "current public Polymarket migration documentation" — no URL or retrieval timestamp was captured in the PR
- I cannot reach external documentation in this environment without confirming the user permits a `WebFetch` against a specific URL (Polymarket has not been a previously visited domain for this session, and the global note flags `WebSearch` as broken)
- No cached evidence of the date exists anywhere in `docs/`

### Options

| ID | Choice | Risk |
|----|--------|------|
| O3-a | Operator captures the URL + retrieval timestamp + page version into `work_log.md` before P0 lands | Low; 5 min of operator time |
| O3-b ★ | Treat the date as **configuration**, not literal; preflight gate verifies endpoint identity, not date; date-driven behavior comes only from operator-confirmed URL | Lowest |
| O3-c | I attempt a WebFetch against `https://docs.polymarket.com` or the official GitHub README and embed the citation | Medium: external dependency, potential captcha/firewall, and citation rot |

### Recommendation: **O3-b**

The V2 preflight should not be date-gated. It should be **endpoint-identity-gated**: at startup, the preflight calls a known V2-only endpoint and asserts it returns a V2-shaped response. If the endpoint is V1-shaped, fail closed; if V2, allow placement. This makes the cutover date irrelevant to runtime behavior — the system follows reality, not a calendar.

If you do want a date-gated alarm (e.g. "if it's after 2026-04-28 and preflight still returns V1, page operator"), put it in observability, not in the entry-block path.

If you want me to try O3-c, give me explicit go-ahead with the target URL.

---

## O4 — `slice_policy` / `reprice_policy` / `liquidity_guard` removal

### Evidence

- Field definitions: [src/contracts/execution_intent.py:20-22](src/contracts/execution_intent.py:20)
- Creation sites: [src/execution/executor.py:133-135](src/execution/executor.py:133)
- Consumer sites (only **logging branches**, no behavior):
  - [src/execution/executor.py:151](src/execution/executor.py:151) → `if intent.liquidity_guard: logger.info(...)`
  - [src/execution/executor.py:154](src/execution/executor.py:154) → `if intent.slice_policy == "iceberg": logger.info(...)`
- Test references:
  - [tests/test_pre_live_integration.py:22-24](tests/test_pre_live_integration.py:22)
  - [tests/test_executor_typed_boundary.py:68-70](tests/test_executor_typed_boundary.py:68)

### Options

| ID | Choice | Edit count | Behavior change |
|----|--------|------------|------------------|
| O4-a ★ | Drop the 3 fields from `ExecutionIntent`, drop the 2 logging branches in `executor.py`, drop them from 2 tests | ~6 sites | Loses 2 log lines; no behavior change |
| O4-b | Keep fields, force constants `slice_policy="single_shot"`, `reprice_policy="static"`, `liquidity_guard=False`; keep logging branches but they go silent | ~3 sites | No semantic change, more dead code |
| O4-c | Move the 3 fields into a separate `FutureCapabilityHints` dataclass that is *not* consumed by executor | ~6 sites + a new file | More structure, no behavior gain |

### Recommendation: **O4-a**

Per Fitz Constraint #1 ("make the category impossible"), the right fix is deletion. The fields exist solely for log-line decoration; deleting them removes K3 entirely. Future capability code can re-introduce a typed surface when there is real implementation behind it.

I have already verified that the only consumers are 2 `logger.info` lines in `executor.py`. Tests are the only other touch.

---

## O5 — `INV-##` and `NC-##` id allocation

### Evidence

- INV ids currently in use: 1–10, 13–22 (gaps at 11, 12 — historical, do not refill)
- NC ids currently in use: 1–15

### Recommendation (no real choice, just allocation)

| New id | Statement (P0 wording) | Enforced by |
|--------|------------------------|-------------|
| `INV-23` | A degraded portfolio projection must never export `authority="VERIFIED"`; the export must use a distinct non-VERIFIED label. | tests + AST/manifest test in `tests/test_architecture_contracts.py` |
| `INV-24` | `place_limit_order` is gateway-only; non-gateway call sites are forbidden. | semgrep rule `zeus-place-limit-order-gateway-only` |
| `INV-25` | When V2 endpoint preflight fails, no live `place_limit_order` call may be issued for that cycle. | targeted test `test_v2_preflight_blocks_placement` |
| `INV-26` | `runtime_posture` is read-only at runtime; the entry path must consult it; non-NORMAL posture blocks new entry irrespective of `risk_level`. | targeted test `test_runtime_posture_blocks_new_entry` |
| `NC-16` | No direct `place_limit_order` call outside the gateway boundary. | semgrep id `zeus-place-limit-order-gateway-only` |
| `NC-17` | No decorative capability labels in `ExecutionIntent` without an enforcing executor branch. | introspection test `test_execution_intent_no_decorative_labels` |

I will allocate these in the manifest as the first commit of the P0 implementation. If you want different ids or a different policy regarding refilling INV-11/12, override before that commit lands.

---

## Default I am proceeding under

Unless you redirect:

- **O1-b** — `py-clob-client>=0.34,<0.40`
- **O2-c** — committed `architecture/runtime_posture.yaml`, no override path
- **O3-b** — preflight is endpoint-identity-gated, not date-gated
- **O4-a** — drop the 3 capability fields + branches + test refs
- **O5** — allocate `INV-23..26`, `NC-16, NC-17` as listed

Open to override at any point. State the choice id (e.g. "O1-a") and I'll re-route.
