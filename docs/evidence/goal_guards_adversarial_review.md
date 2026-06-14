# Adversarial review — native /goal park-state suppressor hooks

Date: 2026-06-13
Reviewer: critic (adversarial mode — escalated; see Verdict Justification)
Scope: the 4 PreToolUse goal-guard hooks + shared detector + settings registration.
Authority of findings: every Critical/High is **proven by reading code + real on-disk
transcript evidence**, not suspected. Suspected items are labelled SUSPECTED.

## VERDICT: REVISE

The design is fundamentally sound and the hard requirements are *mostly* met. The
perf rewrite (1MB tail-read) and the sh fast-path resolve the two worst issues I
found in the first-pass (whole-file 195MB read; node-spawn on every Bash). The deny
contract is correct for Claude Code 2.x. But there is **one genuine false-positive
surface that violates Hard Requirement #1** (detector branch #2 can flip ACTIVE from
a pasted /goal block in a user message), and that same branch is **dead code for
legitimate activation** — a strict-loss asymmetry that mandates its removal. Two
lower-severity fail-open gaps remain.

---

## Ground truth established (so findings are not speculation)

Verified against real transcripts under `~/.claude/projects/-Users-leofitz-zeus/`:

- **Real `goal_status` attachment shape** (decisive):
  `{"type":"attachment","attachment":{"type":"goal_status","met":false,"sentinel":true,"condition":"..."}}`
  → detector branch #1 field access (`o.attachment.type`, `o.attachment.met`,
  `o.attachment.condition`) is **correct against reality**. `met` observed as both
  `false` (active) and `true` (cleared) in the same session. PROVEN.
- **Real `/goal` command record**: `type:"user"`, string content =
  `<command-name>/goal</command-name>...<command-args>...</command-args>` (len ~175).
  It contains **neither "Goal set:" nor "Goal cleared:" nor `<local-command-stdout>`**.
  PROVEN by reading the record.
- **The `<local-command-stdout>Goal set:` record** is a SEPARATE `type:"user"`
  string record that does **NOT** carry the `command-name>/goal<` tag. PROVEN.
- **Claude Code serializes PreToolUse stdin with `JSON.stringify` (no spaces)**:
  502/502 real occurrences are the compact `"run_in_background":true`. PROVEN.
- **Real transcripts reach 195 MB**; max single record = 0.15 MB; 0 records > 1 MB. PROVEN.
- **Deny contract** (`hookSpecificOutput.{hookEventName,permissionDecision:"deny",
  permissionDecisionReason}`, exit 0) is the documented Claude Code shape and the
  model **continues** (sees reason, not aborted). PROVEN via docs.

---

## CRITICAL findings (block normal running OR silently let the goal idle)

### C1 — Detector branch #2 is a pure false-positive surface: a pasted /goal block in a USER message flips ACTIVE with no goal active
`lib/goal-active.mjs:111-122`

**Trigger (proven reachable):** any `type:"user"` record whose STRING content contains
both `command-name>/goal<` and the text `Goal set:` sets `lastActive`. A human pasting
a /goal command block as prose, OR a user message quoting a transcript/this very review,
satisfies both substrings in one string. Simulated and confirmed: branch #2 fires →
verdict ACTIVE → ALL FOUR guards now deny park-states during NORMAL (no-goal) operation.
This violates **Hard Requirement #1 (must not block normal running)**.

**Why it matters more than a normal false-positive:** branch #2 is **DEAD CODE for
legitimate activation**. Proven across every real transcript: the genuine `/goal`
command record carries the tag but NOT "Goal set:"/"Goal cleared:" (those live in a
separate, tag-less `<local-command-stdout>` record). So the `tag AND "Goal set:"`
conjunction is *never* satisfied by a real /goal invocation — branch #2 contributes
**zero** legitimate detection. The only records that satisfy the conjunction are
contamination (a single string holding both). Branch #2 therefore has **negative
value**: 0 true positives, ≥1 reachable false positive. The single grep "hit" for the
conjunction across all transcripts was *this review's own subagent transcript* quoting
the source — i.e. literally the contamination class.

**Confidence:** HIGH (proven by transcript inspection + simulation).

**Realist check:** worst case = the user pastes a /goal block (or this review) and then
NORMAL operation is silently throttled (no AskUserQuestion, no bg bash, no persistent
Monitor) until an INACTIVE marker out-indexes it — which, since no real goal is running,
**never happens** (no goal_status:met=true is ever emitted). So the false-positive is
*sticky* for the rest of the session. Not a transient blip. Severity stays CRITICAL.

**Fix (minimal):** delete branch #2 entirely (lines 111-122). The structural
`goal_status` attachment (branch #1) is emitted EVERY turn including the first goal
turn (verified: goal_status at line 971 precedes the cmd record at 973 in a real
session), so branch #2 covers nothing branch #1 misses. If you want belt-and-suspenders
first-turn coverage, replace branch #2 with a check on the **`<local-command-stdout>`
record** (the tag-less user-string record that actually says `Goal set:`/`Goal cleared:`)
— but gate it so a single record cannot contain BOTH set and cleared (real stdout
records contain exactly one). Do NOT key off the `command-name>/goal<` tag — that tag
never co-occurs with the set/cleared text in a legitimate record.

---

## HIGH findings (perf / contract correctness)

### H1 — Tail-read silently disables protection if the LAST goal marker is >1 MB from EOF (fail-open false-negative)
`lib/goal-active.mjs:32-47`

**Trigger:** `readTail` reads only the final 1 MB. The verdict depends on
`lastActive > lastInactive`. If a live goal goes a long stretch with >1 MB of
transcript appended AFTER its most recent `goal_status:met=false` and BEFORE the
current call, the active marker scrolls out of the tail → detector returns inactive →
guards silently allow park-states → goal can idle. **This is the exact failure the
hooks exist to prevent.**

**Is it reachable?** Largely NOT under normal goal operation — proven: a live goal
emits a `goal_status` attachment EVERY turn, max single record is 0.15 MB, and 1 MB
holds ~650 recent lines. So the most-recent active marker is always a few KB from EOF.
**BUT** the guarantee is "every turn emits goal_status" — if that native invariant ever
changes (goal_status emitted less often, or a single mega tool_result >1 MB lands
between the last marker and EOF), protection vanishes with no signal. The code comment
*asserts* the every-turn invariant but nothing verifies it.

**Confidence:** MEDIUM (mechanism proven; reachability low under current native
behavior, but undefended against a behavior change). 

**Realist check:** mitigated by the native every-turn emission; detection = goal idles
(visible). Downgraded from CRITICAL to HIGH. **Mitigated by:** native /goal emits
goal_status each turn, keeping the marker within the tail in practice.

**Fix:** raise `TAIL_BYTES` to e.g. 4 MB (still ~9ms), OR — robustly — scan the tail
and if NO `goal_status` marker is found in it, fall back to a bounded reverse scan or
a larger read before concluding inactive. Cheapest: bump to 4–8 MB.

### H2 — `readTail` `Buffer.toString("utf-8")` can split a multi-byte UTF-8 char at the 1 MB boundary
`lib/goal-active.mjs:39-43`

**Trigger:** the read starts at an arbitrary byte offset (`size - TAIL_BYTES`); if that
offset lands mid-UTF-8-sequence, the first decoded chars are replacement chars. The
code drops the partial FIRST LINE (`indexOf("\n")`), which incidentally also discards
any leading mangled bytes — so in practice the corruption is confined to the dropped
first line. PROVEN: 0 parse failures in the tail of the 195MB file.

**Confidence:** HIGH that it's currently harmless (the first-line drop masks it).
**Severity:** LOW-leaning-HIGH only as latent fragility — flagged so a future edit that
removes the first-line drop doesn't reintroduce corruption. No fix required now; add a
comment that the first-line drop is load-bearing for UTF-8 safety, not just JSON safety.

### H3 — `transcript-unreadable` and `no-transcript` both fail OPEN (inactive)
`lib/goal-active.mjs:62, 68`; and every guard's `catch -> allow()`

**Trigger:** any detector error (transcript path missing, read error, statSync race
on a rotating file) → `{active:false}` → guards allow park-states. During an ACTIVE
goal this silently disables protection. Fail-open is the right call for Requirement #1
(never block normal use is the higher priority), but it means **every detector bug
degrades to "protection silently off," never "protection visibly stuck on."**

**Confidence:** HIGH (by reading). **Severity:** HIGH as a design property to
acknowledge, not a bug to fix — fail-open is correct here; the residual risk is that
bugs are invisible. **Recommendation:** keep fail-open; add a one-line stderr breadcrumb
ONLY on the unreadable/error path (NOT the allow path — see L1) so silent disablement
is at least greppable in hook logs. Optional.

---

## MEDIUM findings

### M1 — sh fast-path bypass on non-`JSON.stringify` spacing of `run_in_background` (defense-in-depth gap, not currently reachable)
`goal-guard-bgbash.sh:9-12`

The `case` matches only `"run_in_background":true` and `"run_in_background": true`.
Variants `"run_in_background" : true` and `"run_in_background":  true` (two spaces)
**bypass → exit 0 → backgrounded bash allowed under an active goal** (fail-open).
PROVEN by test. **However:** Claude Code emits stdin via `JSON.stringify` (compact,
zero-space) — 502/502 real samples are the compact form. The bypassing variants are
**not producible by the real serializer**, so the hole is theoretical.

**Confidence:** HIGH the variants bypass; HIGH they're unreachable via the real harness.
**Severity:** MEDIUM (latent — breaks if Claude Code ever changes its serializer or a
proxy reformats the JSON; the `ANTHROPIC_BASE_URL=127.0.0.1:20128` proxy in settings is
exactly such a reformat risk worth noting).
**Fix:** make the match whitespace-tolerant, e.g. delegate the decision to node
unconditionally on any `run_in_background` substring, or use a `grep -E
'"run_in_background"[[:space:]]*:[[:space:]]*true'` test. Cheap and closes the class.

### M2 — Foreground blocking Bash (`sleep`, `wait`, blocking `nc`, `tail -f` w/o &) is NOT guarded — a real park-state slips through
`goal-guard-bgbash.sh:9-12` + `.mjs:52`

The bgbash guard early-exits on anything that isn't `run_in_background:true`. A
**foreground** `sleep 99999` (or `wait`, or a blocking read) holds the turn open
indefinitely → no Stop event → /goal never re-drives → idle with goal active. This is
the SAME park-state class the hooks target, via a door left open. PROVEN: foreground
bash never reaches the detector.

**Confidence:** HIGH. **Severity:** MEDIUM — the model rarely foregrounds an unbounded
sleep, but it's a real category gap and the model under a goal may well run a blocking
`wait`/poll. **Realist check:** lower frequency than bg bash; **Mitigated by:** model
behavior rarely foregrounds infinite blocks. Kept MEDIUM.
**Fix:** in the bgbash guard (or a small addition), also inspect foreground commands
for obvious unbounded blockers (`\bsleep\s+\d{4,}`, bare `wait`, `tail -f` without
redirect-and-background). This is heuristic and risks false positives, so scope it
tightly or accept the gap explicitly in the design doc.

### M3 — Background `Agent` (run_in_background) is unguarded by design — verify the "self-resolving" claim holds for a HUNG agent
`goal-guard-bgbash.mjs:12-13` (comment); no `Agent` matcher in settings (PROVEN: matchers = WebSearch,WebFetch,AskUserQuestion,Monitor,CronCreate|ScheduleWakeup,Bash)

The design intentionally exempts background Agents ("self-complete and notify"). That's
correct for a healthy agent. But a background Agent that **hangs** (infinite loop,
deadlocked on its own park-state, waiting on a network call with no timeout) keeps the
parent in `hasPendingBackgroundTask` (persistent-mode.mjs:199-208) → parent idles with
goal active. The exemption assumes liveness it can't guarantee.

**Confidence:** MEDIUM (mechanism proven; depends on sub-agent hanging).
**Severity:** MEDIUM. **Mitigated by:** `isFreshTimestamp` TTL in
`hasPendingBackgroundTask` — a stale task stops counting after `PENDING_ASYNC_STATE_STALE_MS`,
so a hung agent's suppression eventually self-clears. SUSPECTED that TTL bounds it;
worth confirming the TTL value. Acceptable to leave as a documented limitation.

---

## LOW findings

### L1 — Allow path is clean (Requirement #2 MET) — confirm no future stderr creep
`all guards`: `allow()` = bare `process.exit(0)`, no stdout/stderr. The sh fast-path
`exit 0` is also silent. **Requirement #2 (zero context pollution on allow) is MET.**
PROVEN by reading. Flagging only to lock it in: do NOT add the H3 breadcrumb to the
allow path — restrict any logging to the error/deny paths.

### L2 — `met` non-boolean values all map to ACTIVE
`lib/goal-active.mjs:98-99`: only `met===true` is INACTIVE; `null`/missing/`1`/`"true"`
→ ACTIVE. Safe direction (a malformed goal_status only appears mid-goal), but a native
change that emits `met:null` on clear would stick ACTIVE. SUSPECTED non-issue; note it.

### L3 — Monitor boundary `<=300000` is intentional, NOT off-by-one
`goal-guard-monitor.mjs:85`: `timeout > MAX_MS` denies → exactly 300000 allowed,
300001 denied. PROVEN. Matches the documented Monitor default cap. No action.

### L4 — Slug fallback is actually CORRECT (prompt's hint was wrong)
`lib/goal-active.mjs:55`: `cwd.replace(/[^A-Za-z0-9]/g,"-")` reproduces Claude Code's
real project-slug exactly (each non-alnum → one `-`, so `/.claude` → `--claude`).
PROVEN against real dir names incl. worktree/dot-dir double-dash cases. The fallback
only triggers when `transcript_path` is absent (rare). No action — the suspected
false-negative here does NOT exist.

---

## What's missing (gaps, unstated assumptions)

- **No verification that `goal_status` is emitted every turn** — H1 and the tail design
  both *depend* on this native invariant; nothing asserts or monitors it. If native
  /goal changes emission cadence, protection silently degrades.
- **No coverage for foreground blocking work** (M2) — the park-state taxonomy in the
  design lists 4 states but the bgbash door only covers the background variant.
- **No coverage for a hung background Agent** (M3) — exemption assumes liveness.
- **No self-test / unit fixtures committed** — the prompt says the alternation + Bash
  matchers are "unit-tested only." I could not find committed tests under the hooks dir
  proving the `CronCreate|ScheduleWakeup` alternation dispatches. The regex-alternation
  matcher syntax is valid Claude Code matcher form (same family as `Edit|Write` used
  live in this very settings.json PostToolUse), so it SHOULD dispatch — but "should"
  from analogy, not from a live-confirmed CronCreate/ScheduleWakeup denial. SUSPECTED OK.
- **Proxy reformat risk** (M1) — `ANTHROPIC_BASE_URL` points at a local proxy; if any
  layer re-serializes tool_input JSON with spacing, the sh fast-path's exact-match
  bypasses. Untested against the actual proxy output.

## Multi-perspective notes
- **Executor:** under a (correctly-detected) goal, the deny reasons are actionable and
  tell the model exactly what to do instead (PushNotification, detached nohup, bounded
  Monitor). Good. But a FALSE active (C1) gives the model deny reasons for tools it
  legitimately needs with no goal — confusing and blocking.
- **Stakeholder:** the hooks deliver the stated outcome (keep /goal driving) for the
  common cases; the residual gaps (M2/M3) are the long-tail park-states the original
  observation enumerated, so the job is ~80% done, not 100%.
- **Skeptic:** the strongest argument against the whole approach is that it patches the
  SYMPTOM (specific park tools) rather than the disease (the Stop hook's
  `hasPendingOwnedAsyncWork -> continue` deferral in persistent-mode.mjs:807). A
  structural fix would make native /goal re-drive even a parked session, killing the
  entire bug category. The hooks are a reasonable per-instance guard, but they will
  always be one park-state behind (M2/M3 prove it). Worth recording as the real fix.

## Verdict justification
REVISE, not REJECT: the core mechanism, deny contract, perf rewrite, and detector
branch #1 are correct and the two hard requirements are met EXCEPT for C1. C1 alone
warrants revision (it can block normal running and is dead-for-purpose). H1/M1/M2/M3
are gaps to close or consciously accept. Escalated to ADVERSARIAL mode after finding
C1 (a Requirement-violating false positive) — expanded scope to the sh serializer
reachability, the proxy reformat risk, foreground/Agent park-state gaps, and the
tail-read invariant dependency. Realist check downgraded the tail-read issue from
CRITICAL to HIGH (native every-turn emission keeps the marker in-tail) and kept C1 at
CRITICAL (the false positive is sticky for the whole session, never self-clearing).

To upgrade to ACCEPT: delete/replace detector branch #2 (C1); bump TAIL_BYTES or add a
no-marker-found fallback (H1); make the sh match whitespace-tolerant (M1); and either
close or explicitly document the foreground-block (M2) and hung-Agent (M3) gaps.

## Open questions (unscored)
- Does the `CronCreate|ScheduleWakeup` alternation matcher actually fire a denial in a
  live session? (analogy to `Edit|Write` says yes; not live-confirmed here.)
- What is `PENDING_ASYNC_STATE_STALE_MS` — does it bound the hung-Agent suppression (M3)?
- Does the local `ANTHROPIC_BASE_URL` proxy re-serialize tool_input JSON spacing (M1)?

---

## Re-review 2026-06-13

Re-audited the CURRENT on-disk `lib/goal-active.mjs` (goal_status-attachment-only) and
`goal-guard-bgbash.sh` (8MB tail, spaced variants). Every claim re-verified against the
files + real transcripts under `~/.claude/projects/-Users-leofitz-zeus/`. Did NOT trust
the revision summary — ran the actual current decision core against adversarial inputs
and invoked the real `.sh` end-to-end.

### VERDICT: ACCEPT

All prior Critical/High closed and verified by reading + execution. No new defect of
Critical or High severity introduced. The goal_status-only simplification is strictly
better than the prior two-branch design — it removed the false-positive surface AND
closed a latent false-negative (see RR-NOTE-2). Residuals are documented, low-frequency,
and correctly biased toward "never block normal running."

### Re-review answers (all PROVEN, not suspected)

1. **Is C1 fully closed with goal_status-only? YES.** `lib/goal-active.mjs:97-104`
   requires `o.type==="attachment" && o.attachment.type==="goal_status"`. Ran the verbatim
   current core against: (a) user STRING pasting the full goal_status JSON, (b) assistant
   prose quoting it, (c) tool_result whose text is a literal attachment record, (d) a
   pasted /goal command block. ALL → inactive. Only a genuine `type:"attachment"` record
   (which only Claude Code emits) flips ACTIVE. The deleted branch #2 is gone; its dead-
   for-purpose/forgeable asymmetry is eliminated. C1 CLOSED.

2. **New false-positive/false-negative from goal_status-only + met===false-only? NONE.**
   - False-positive: the only ACTIVE-flip from non-Claude content would be a bare,
     unwrapped attachment line at the top level of the .jsonl — not producible by paste/
     echo (those nest inside user/assistant content). Not reachable.
   - False-negative: verified across ALL real transcripts that `goal_status.met` is
     ALWAYS a boolean — 486 `false`, 12 `true`, zero null/missing/non-boolean. So
     `met===false`-only never mis-reads a real active goal as inactive. The "missing/
     malformed → inactive" defensive branch never fires on real data; it only governs a
     hypothetical future shape, and biasing it inactive correctly upholds Requirement #1.

3. **First-turn gap acceptable? YES (documented, inherent, mitigated).** goal_status is a
   turn-boundary attachment (verified: preceding records are `type:"system"`/`type:"user"`,
   re-injected each turn by the native /goal loop). Before the first Stop there is no
   goal_status, so turn 1 of a brand-new goal is unguarded. This is intrinsic to the
   mechanism the whole design rests on — not closable without a different signal — and the
   /goal directive text itself instructs the agent not to park that turn. Closing it would
   require keying off /goal command TEXT, which is exactly the forgeable surface C1 removed.
   Leave open as documented. Do NOT reintroduce a text-based first-turn branch.

4. **Is 8MB sufficient for H1? YES (empirically; optional fallback offered).** Measured the
   last active goal_status marker's distance from EOF across all 5 real sessions incl. the
   187MB one: max = **1.44 MB** (187MB session = 0.46 MB). 8 MB is ~5.5x the empirical
   worst case. The operator's observed "1–4 MB" is a plausible mid-turn transient during a
   large tool-result append; 8 MB absorbs it. The no-marker-found escalation fallback is
   NOT required for current behavior but remains the robust answer against a single >8MB
   turn (the documented residual). RECOMMENDATION (optional, not blocking): if a future
   turn routinely emits >8MB before a guarded call, add: "if the 8MB tail contains zero
   goal_status markers AND filesize>8MB, do one larger read (or reverse-scan) before
   concluding inactive." Cheap insurance; not needed today.

### Verified-fixed (re-confirmed by reading + execution)
- **C1** `goal-active.mjs:97-104` — attachment-type gate; 4 contamination shapes → inactive. CLOSED.
- **L2** `goal-active.mjs:106-114` — only `met===false` activates; true/missing/malformed → inactive. CLOSED.
- **H1** `goal-active.mjs:44` — `TAIL_BYTES=8MB`; readTail correctness re-verified (partial-first-line drop intact, last record present, 0 parse failures). CLOSED (residual documented).
- **H2** `goal-active.mjs:54-56` — comment now states the first-line drop is load-bearing for UTF-8 boundary safety. CLOSED.
- **M1** `goal-guard-bgbash.sh:15-19` — spaced variants added; tested all 4 spacings → CHECK-GOAL, `false`/no-key → FAST-ALLOW. End-to-end: cleared-goal bg bash → EMPTY stdout (Requirement #2 met); active-goal bg bash → valid deny contract. CLOSED.

### M1 proxy-path claim — CONFIRMED CORRECT
Agreed: `ANTHROPIC_BASE_URL` is NOT in the hook-stdin path. Hook stdin is an OS pipe
written by the local Claude Code CLI (compact `JSON.stringify`); the proxy only sees the
`/v1/messages` LLM-API HTTP traffic. Compact form is guaranteed for hook stdin, so the
M1 spacing concern cannot reach it through the proxy. The added spaced variants are
correct belt-and-suspenders against a future local-serializer change. No action.

### Accepted documented limitations — AGREE, do not close
- **M2** (foreground blocking bash, e.g. `sleep 99999`) — heuristic foreground-blocker
  detection risks false-positives on legitimate long foreground commands, which would
  violate Requirement #1 (the higher priority). Accepting the gap is the correct trade.
- **M3** (hung background Agent) — self-clears via the pending-async freshness TTL
  (`hasPendingBackgroundTask` gates on `isFreshTimestamp`, persistent-mode.mjs:205-208),
  so suppression is bounded. Acceptable as documented.

### RR-NOTE-1 — first-turn gap is the ONLY residual detection gap, and it is unavoidable
The design's detection is now exactly as strong as the native goal_status signal and no
stronger — which is the correct ceiling. Anything stronger requires trusting forgeable
text. Correct call.

### RR-NOTE-2 — the C1 fix also closed a latent FALSE-NEGATIVE (bonus, verified)
Two real sessions (`01b0f773`, `1a701b37`) carry genuine goal_status attachments with
full conditions but ZERO `/goal` command records — active goals inherited via session
resume (`--continue`/`--resume`). The deleted branch #2 (keyed on the /goal command tag
text) would have read these as INACTIVE and left a resumed goal unguarded. goal_status-
only detection tracks them correctly. The simplification improved correctness in both
directions. Verified by inspecting the attachment records.

### No new issues found
Re-scanned for: orphan goal_status from a non-/goal mechanism (none — every goal_status
file traces to /goal or a resume), condition leak on inactive (none — condition nulled
when `!active`), latest-wins regressions (active→cleared→active sequencing correct),
allow-path pollution (empty stdout confirmed end-to-end). Clean.
