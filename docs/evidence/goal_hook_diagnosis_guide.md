# Diagnosis: native `/goal` does not fire (agent goes idle instead of starting the next turn)

Claude Code v2.1.177. Evidence-based, sourced. Where a doc is silent, marked **[inferred]**
with the runtime evidence that supports the inference.

## Sources

- `/goal` feature page — https://code.claude.com/docs/en/goal
- Hooks reference — https://code.claude.com/docs/en/hooks
- Hooks guide (prompt-based hooks, combine-results, block cap) — https://code.claude.com/docs/en/hooks-guide
- Model config (small fast model / Haiku / `[1m]` / gateway) — https://code.claude.com/docs/en/model-config
- CHANGELOG — https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md
- Runtime evidence: this machine's transcripts under
  `/Users/leofitz/.claude/projects/-Users-leofitz-zeus/*.jsonl` (the `goal_status`
  attachment and the `stop_hook_summary` system records), and live runs of the three
  custom Stop hooks.

---

## The mechanism, established first (so the answers are unambiguous)

`/goal` is **"a wrapper around a session-scoped prompt-based Stop hook"** (goal page,
"How evaluation works", verbatim). After each turn the condition + conversation is sent
to **"your configured small fast model, which defaults to Haiku"** (same section). The
model returns yes/no + a short reason. A **"no" tells Claude to keep working**; a "yes"
clears the goal.

A prompt-based Stop hook returns **`{"ok": false, "reason": ...}`** to keep working, or
`{"ok": true}` to allow the stop (hooks-guide, "Prompt-based hooks", verbatim:
*"`Stop` and `SubagentStop`: the `reason` is fed back to Claude so it keeps working"*).
So **the only thing that makes `/goal` start a new turn is the evaluator producing the
equivalent of `ok:false` / a Stop block.** If that block is never produced, the session
goes idle. Everything below is about why the block is or isn't produced.

Runtime confirmation of the wrapper, on THIS machine: transcripts contain a
`goal_status` attachment

```json
{ "type": "goal_status", "met": false, "condition": "<the goal text>", "reason": "<evaluator's last reason>" }
```

and the Stop batch that fires each turn is recorded as a `stop_hook_summary` system
record whose `hookInfos` lists the three user command hooks **plus two
`{"command":"callback"}` entries** — the internal managed hooks. The goal evaluator is
one of those `callback` hooks. This proves the evaluator and the command hooks run **in
the same Stop batch**.

---

## 1. PRECEDENCE — command Stop hooks vs. `/goal`. Do they coexist? Can `continue:true` block the goal?

**They coexist, and a command hook returning `{"continue":true}` cannot prevent `/goal`
from starting the next turn.** Cited mechanism:

- Hooks-guide "Combine results from multiple hooks" (verbatim): *"When multiple hooks
  match the same event, **every hook's command runs to completion before Claude Code
  merges the results.** One hook returning `deny` does not stop sibling hooks from
  executing."* So there is **no coexistence conflict and no veto**: the command hooks and
  the evaluator both run, then results are merged.
- The aggregation for "should the turn continue" is a **block-wins / OR-of-blocks** rule,
  not an AND-of-continues. A Stop turn is prevented from ending only when **some** hook
  asserts a block: top-level `{"decision":"block"}`, exit code 2, or a prompt hook's
  `ok:false` (hooks reference Stop decision-control; hooks-guide block-cap section). A
  plain `{"continue":true}` is the **default no-decision** result (hooks reference: *"`continue`
  (boolean, default `true`)"* and *"Exit 0 with no JSON / `continue:true` = no decision;
  Claude continues normally"*). **A no-decision result neither blocks the stop nor
  suppresses another hook's block.**
- Therefore: your three command hooks all returning `{"continue":true,"suppressOutput":true}`
  (verified live below) do **not** cancel the evaluator's `ok:false`. Conversely, they
  cannot *cause* `/goal` to fire either — they are inert with respect to it.
- `continue:false` is the ONE exception that IS a global override: hooks reference,
  verbatim — *"To stop Claude entirely regardless of event type:
  `{ "continue": false, "stopReason": ... }`"*. **None of your three hooks ever emit
  `continue:false`** (verified live), so this override is not in play. If a command Stop
  hook ever returned `continue:false`, it WOULD hard-stop the session and suppress the
  goal — but that is not your situation.
- **`suppressOutput:true` does not matter here.** It only *"hides the hook's stdout from
  the transcript"* (hooks reference, verbatim). It has zero effect on the
  block/continue decision.

Live verification of your three command Stop hooks (real Stop payload, exit code shown):

```
session-handoff.mjs  -> {"continue":true,"suppressOutput":true}   exit 0
persistent-mode.mjs  -> {"continue":true,"suppressOutput":true}   exit 0
code-simplifier.mjs  -> {"continue":true}                          exit 0
```

`persistent-mode.mjs` source confirms it only emits a continuation block when an OMC mode
(ralph/autopilot/ultrawork/…) is active; in the no-active-mode path it returns the benign
no-decision result. **Verdict: your command hooks are NOT fighting `/goal`. They are
correctly configured and inert.** Rule the precedence theory OUT.

## 2. ORDER / SHORT-CIRCUIT — same batch? can a command hook short-circuit the evaluator? does `stop_hook_active` skip it?

- **Same batch: YES.** Hooks-guide (verbatim): *"all matching hooks run in parallel."*
  The `stop_hook_summary` record on this machine lists the command hooks and the
  `callback` (managed evaluator) hooks together with per-hook `durationMs`, confirming a
  single parallel batch.
- **Short-circuit: NO.** Hooks-guide (verbatim): *"every hook's command runs to completion
  before Claude Code merges the results. One hook returning `deny` does not stop sibling
  hooks from executing."* A command hook that finishes first **cannot** prevent the
  evaluator from running. There is no first-wins short-circuit on Stop.
- **`stop_hook_active` loop-guard:** documented in the hooks-guide "Stop hook hits the
  block cap" section (verbatim): *"Claude Code overrides a Stop hook after it blocks 8
  times in a row without progress … Parse the `stop_hook_active` field … exit early if
  it's `true`."* Overridable via `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (env-vars + CHANGELOG
  2.1.163: *"stop hooks that block repeatedly looping forever — the turn now ends with a
  warning after 8 consecutive blocks"*).
  - Whether the **goal evaluator itself** is subject to this 8-block cap is
    **[inferred]**: `/goal` IS a prompt-based Stop hook, and the cap is described as a
    property of Stop hooks generally, so a goal that has blocked 8 turns "without
    progress" would hit the cap and the turn would end with a warning — which is NOT your
    symptom (you see silent idle, no warning). So the cap is unlikely to be your cause
    **unless** a stale long-running goal already exhausted it earlier in the session.
  - `stop_hook_active:true` causes well-written *command* hooks to allow the stop; it is
    a guard the hook author honors, not an automatic skip of the evaluator. **[inferred]**
    the managed evaluator handles its own loop accounting; not separately documented.

## 3. SILENT-FAILURE MODES — every way `/goal` is active yet silently does not start the next turn

Documented + inferred, each tagged:

1. **`disableAllHooks` / `allowManagedHooksOnly` set** — goal page (verbatim):
   *"`/goal` is also unavailable when `disableAllHooks` is set at any settings level or
   when `allowManagedHooksOnly` is set in managed settings. In each case, the command
   tells you why instead of silently doing nothing."* And CHANGELOG **2.1.141**:
   *"Fixed `/goal` silently hanging when `disableAllHooks` or `allowManagedHooksOnly` is
   set — now shows a clear message instead of an indicator that never resolves."*
   **You verified neither is set, and on 2.1.177 this fails LOUD anyway. RULED OUT.**
2. **Workspace not trusted** — goal page (verbatim): *"`/goal` runs only in workspaces
   where you have accepted the trust dialog, because the evaluator is part of the hooks
   system."* You confirmed the workspace is trusted. **RULED OUT.**
3. **Evaluator (small-fast / Haiku) model call fails or returns an unusable body** —
   THE prime suspect. See the fail-open analysis below and §the proxy finding. **OPEN.**
4. **Goal already achieved / cleared / `/clear` ran** — goal page: *"Running `/clear` …
   also removes any active goal"*; *"A goal that was already achieved or cleared is not
   restored."* If the evaluator returned `met:true` once, the goal is gone and every later
   turn idles silently with no indicator. **CHECK the transcript (step in §5).**
5. **Block cap already consumed** — §2; would normally show a warning, but a prior burst
   could have ended the goal quietly earlier in the session. **CHECK.**
6. **The condition is trivially "already met"** — the evaluator *"can only judge what
   Claude has already surfaced in the conversation"* (goal page). A loosely written
   condition the model reads as satisfied returns `met:true` → goal clears → idle. On
   this machine, the only `met` value ever recorded across all transcripts is `false`,
   so this is not the historical pattern here, but a newly set vague goal could still hit
   it. **[inferred from runtime.]**

### Fail-open vs fail-closed on evaluator model failure

- The docs **do not state** the prompt-hook failure mode for Stop explicitly
  (hooks reference: *"The behavior for prompt and agent hooks on timeout is not
  specified"* — **not documented**).
- The nearest documented analog is HTTP hooks (hooks reference, verbatim): *"Connection
  failure or timeout: **non-blocking error, execution continues**."*
- **[inferred, high confidence]** the goal evaluator **fails OPEN**: if the small-fast
  model call errors, times out, or returns a body Claude Code can't parse into
  `{ok:...}`/`{met:...}`, no block is produced, the Stop batch merges to "no block," the
  turn ends, **and the session goes idle with no error shown.** This exactly matches the
  reported symptom and the runtime signature below. A fail-CLOSED evaluator would instead
  loop forever (and hit the 8-block cap warning), which is NOT what you see.

### Does a `[1m]` session model or the proxy break the evaluator silently?

- The **session model being `claude-opus-4-8[1m]` is NOT the evaluator's model.** The
  evaluator runs on the **small fast model = Haiku**, resolved from
  `ANTHROPIC_DEFAULT_HAIKU_MODEL` (model-config, verbatim: that var is *"The model to use
  for `haiku`, or **background functionality**"*). The `/goal` evaluator is exactly such
  a background side-query. So `[1m]` on Opus is a red herring for the evaluator path.
  (Note: model-config also says *"Claude Code strips the `[1m]` suffix before sending the
  model ID to your provider"* — `[1m]` is a client-side context flag, not something the
  proxy must understand.)
- **The proxy is the live suspect.** `ANTHROPIC_BASE_URL` *"changes where requests are
  sent, not which model answers them"* (model-config, verbatim) — it routes the
  evaluator's Haiku side-query through `http://127.0.0.1:20128/v1`. Two CHANGELOG entries
  are directly on point:
  - **2.1.141** *and* **2.1.161** (verbatim, both): *"Fixed background side-queries
    sending an unavailable Haiku model ID on Bedrock/Vertex/Foundry/**gateway** when no
    `ANTHROPIC_SMALL_FAST_MODEL` override is set."* The `/goal` evaluator IS a background
    side-query, and a custom-base-URL proxy is a "gateway." You DO set
    `ANTHROPIC_DEFAULT_HAIKU_MODEL` (the modern replacement for the deprecated
    `ANTHROPIC_SMALL_FAST_MODEL`), so the *model-id* half is configured — but this entry
    shows background side-queries are the historically fragile path through a gateway.
  - The risk that remains is **transport/shape**, not model-id: your curl proved the
    proxy returns **HTTP 200 to a real `POST /v1/messages` with the Haiku id** (verified
    in this diagnosis). HTTP 200 is necessary but **not sufficient** — the evaluator
    needs a parseable Anthropic Messages body (and, if it streams, a well-formed SSE
    stream). A proxy that returns 200 with an OpenAI-shaped body, a wrapped/transformed
    body, an empty body, or a stream the client can't assemble will make the evaluator
    silently yield no decision → fail-open → idle. **This is the single most likely root
    cause; see executive summary.**
- **No CHANGELOG entry between 2.1.139 and 2.1.177 describes "/goal not firing" or a
  goal+Stop-hook interaction bug.** The only `/goal` fixes in range are 2.1.141 (the
  disableAllHooks loud-message fix) and 2.1.154 (a status-chip re-render cosmetic fix).
  The relevant systemic entries are the **2.1.141 / 2.1.161 gateway background-side-query**
  fixes and **2.1.163** (Stop-hook block cap). So your symptom is not a known unpatched
  `/goal` bug at 2.1.177 — it points at the evaluator's model call in your environment.

## 4. STATE — where the active goal is persisted, the JSON shape, and what the indicator reads

- **Persistence:** the active goal is **session-scoped and stored in the session
  transcript**, not in a separate goal file. goal page (verbatim): *"A goal that was still
  active when a session ended is restored when you resume that session with `--resume` or
  `--continue`."* On this machine the goal lives as a `goal_status` **attachment record**
  in
  `/Users/leofitz/.claude/projects/-Users-leofitz-zeus/<session-id>.jsonl`.
  A filesystem scan found **no** standalone `*goal*` state file; the dirs `sessions/`,
  `session-env/`, `tasks/` were empty of goal state. **[inferred from runtime]** the goal
  is reconstructed from the transcript on resume (consistent with the doc's note that on
  resume *"the turn count, timer, and token-spend baseline all reset"* — i.e. only the
  condition is carried, the counters are not persisted separately).
- **Exact JSON shape** (observed on this machine, the authoritative shape to grep for):

  ```json
  { "type": "goal_status",
    "met": false,
    "condition": "<the goal condition text, up to 4000 chars>",
    "reason": "<the evaluator's most recent yes/no reason>" }
  ```

  A variant carries a `sentinel` field instead of `reason`
  (`{type, met, condition, sentinel}`) — **[inferred]** the sentinel marks a
  set/initial record vs. an evaluated record.
- **What the `◎ /goal active` indicator and `/goal` status read:** the goal page says the
  status shows the condition, elapsed time, turns evaluated, token spend, and *"the
  evaluator's most recent reason."* That **most-recent reason is the `reason` field of the
  latest `goal_status` attachment** — i.e. the indicator/status view is a render of the
  latest `goal_status` record in the transcript. To see exactly what your goal is
  registered as for a given session id, grep that session's `.jsonl` for `"goal_status"`
  (commands in §5).

## 5. DIAGNOSIS STEPS — ordered, concrete

Run these in order; stop when one explains it.

1. **Confirm a goal is actually registered for THIS session.** Get the session id
   (`/status`), then:
   ```
   grep -E '"goal_status"' /Users/leofitz/.claude/projects/-Users-leofitz-zeus/<SESSION_ID>.jsonl | tail -3 | python3 -m json.tool
   ```
   - No `goal_status` record at all → the goal was never set in this session (or `/clear`
     wiped it). Re-run `/goal <condition>`.
   - Latest record has `"met": true` → **the goal already completed/cleared**; that is why
     it idles. Set a new, stricter goal. (Historically on this machine every recorded
     `met` is `false`, so a sudden `met:true` is the thing to look for.)
   - Latest record `"met": false` with a sensible `reason` → the evaluator IS producing a
     keep-working decision; go to step 4 (the block may be getting lost) — but first
     re-check you're not at the block cap (step 3).

2. **Read the evaluator's last reason directly** — it is the `reason` field from step 1's
   record, the same text `/goal` status shows. If `reason` is empty/garbled/missing on an
   otherwise-active goal, suspect an evaluator body the client couldn't parse (step 5).

3. **Rule out the 8-block cap.** If the goal had been blocking many turns, the turn ends
   with a "Stop hook blocked too many consecutive times" warning and the goal effectively
   stalls. If you saw that warning, raise it for this session:
   `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP=<n> claude --resume` (env-vars). If you did NOT see the
   warning, the cap is not your cause.

4. **Confirm the Stop batch is producing/honoring a block.** Inspect the latest
   `stop_hook_summary` for the session:
   ```
   grep -E '"stop_hook_summary"' /Users/leofitz/.claude/projects/-Users-leofitz-zeus/<SESSION_ID>.jsonl | tail -1 | python3 -m json.tool
   ```
   - `hookInfos` should contain your three command hooks **plus `callback` entries** (the
     managed evaluator). If `callback` is absent while a goal is active, the evaluator
     isn't being scheduled — re-set the goal / restart the session.
   - `"preventedContinuation": false` while the goal is supposed to be unmet is the
     **idle signature**: the batch merged to "no block." Combined with step 1 showing
     `met:false`, that means the evaluator's keep-working decision is **not reaching the
     merge** — almost always an evaluator model-call failure (step 5). (On this machine
     `preventedContinuation` is `false` in all 1048 recorded summaries.)

5. **Test the evaluator's ACTUAL model call through your proxy** (not just a 200 probe).
   The evaluator needs a parseable Anthropic Messages body. Reproduce a real non-streaming
   call AND a streaming call and inspect the body shape:
   ```
   # non-streaming: must return a JSON body with top-level "content":[{"type":"text",...}]
   curl -s -X POST "$ANTHROPIC_BASE_URL/messages" \
     -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
     -d '{"model":"'"$ANTHROPIC_DEFAULT_HAIKU_MODEL"'","max_tokens":16,
          "messages":[{"role":"user","content":"reply with the single word ok"}]}' | python3 -m json.tool
   # streaming: must emit a well-formed text/event-stream (message_start ... content_block_delta ... message_stop)
   curl -s -N -X POST "$ANTHROPIC_BASE_URL/messages" \
     -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
     -d '{"model":"'"$ANTHROPIC_DEFAULT_HAIKU_MODEL"'","max_tokens":16,"stream":true,
          "messages":[{"role":"user","content":"reply ok"}]}'
   ```
   If either returns 200 but the **body is OpenAI-shaped, wrapped, empty, or a malformed
   stream**, that is the root cause: the evaluator can't extract a decision → fails open →
   the session idles. Fix at the proxy: make `$ANTHROPIC_BASE_URL/messages` return a
   spec-compliant Anthropic Messages response (and SSE if the client streams) for the
   Haiku id.

6. **Bisect with a clean environment** to separate proxy from config. Launch a throwaway
   session pointed at the real API (or with customizations disabled) and set a tiny goal:
   ```
   env -u ANTHROPIC_BASE_URL claude        # or: claude --safe-mode
   /goal reply DONE then the turn is complete
   ```
   - Goal fires here but not under the proxy → **confirmed proxy/evaluator-transport**
     (the most likely outcome given the evidence).
   - Goal still idles even direct → re-check `disableAllHooks`/trust at every scope and the
     block cap; then file `/feedback` with the session id and the `goal_status` +
     `stop_hook_summary` records from steps 1 and 4.

7. **Command-hook hygiene (so they never fight `/goal`)** — already correct in your setup,
   stated for completeness:
   - Keep every no-active-work Stop hook returning the **no-decision** result
     (`{"continue":true}` / exit 0) — NOT `continue:false` and NOT a bare
     `{"decision":"block"}`. Your three hooks already do this.
   - **Never** emit `{"continue":false}` from a Stop hook unless you intend to hard-stop
     the whole session (it overrides everything, including `/goal`).
   - If any custom Stop hook ever blocks, make it honor `stop_hook_active:true` and exit 0,
     so it doesn't consume the shared 8-block cap that `/goal` also draws on.

---

## Bottom line

Your command Stop hooks are exonerated (precedence §1, live-verified). The evaluator runs
in the same Stop batch as them (§2, proven by the `callback` hooks in `stop_hook_summary`).
The documented loud-failure modes (disableAllHooks / untrusted / block-cap warning) are
ruled out. What remains is the evaluator's **small-fast (Haiku) background side-query
routed through your `ANTHROPIC_BASE_URL` proxy**, whose failure mode is **fail-open →
silent idle**, matching every observed signature (`met:false` + `preventedContinuation:false`
on every turn). HTTP 200 is not enough; verify the proxy returns a spec-compliant Anthropic
Messages body (and SSE) for the Haiku id (§5 step 5).
