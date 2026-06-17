# Self-Hosted Fusion (Opus 4.8 + GPT-5.5) via 9router — Investigation & Plan

Created: 2026-06-15
Authority basis: operator request 2026-06-15 ("investigate OpenRouter Fusion; can we
self-implement fusion of Opus 4.8 + GPT-5.5 via proxy or similar").
Companion evidence: `docs/evidence/9router_fusion_feasibility.md` (line-level proxy map).

## 1. What OpenRouter Fusion actually is (the thing to replicate)

`openrouter/fusion` (OpenAI-compatible slug). A **single-layer Mixture-of-Agents +
judge**, response-level only:

1. A **panel** of 3–5 expert models answers the prompt **in parallel** (web search +
   fetch enabled).
2. A **judge model** receives all candidate answers and writes a **structured
   deliberation** — *consensus / contradictions / partial coverage / unique insights /
   blind spots* — then writes the final answer from that structure.
3. Panel default = `Quality` preset; `Budget` = cheaper members. Override panel +
   judge via the fusion plugin's `analysis_models` (panel) and `model` (judge) fields.
4. Priced as the **sum** of all underlying completions.

OpenRouter's own attribution: **~¾ of the lift is the synthesis step, ¼ is panel
diversity.** Implication for us: the judge/synthesis prompt is where the value is —
not just "call two models."

There is **no logit-level fusion** — APIs return sampled tokens only, never
log-probs, and Opus/GPT use different tokenizers. Every viable design is
**response-level**.

## 2. Feasibility verdict

**Mechanically possible, response-level, confirmed against the live proxy.** Every
primitive needed already exists in `~/.9router/preproxy/preproxy.js`:

| Need | Existing primitive | Line |
|---|---|---|
| Buffer full inbound Claude request | `interceptForEffortCap` (already buffers body) | 1002 |
| Single best injection point | after body mutation, before gemini check | ~1059 |
| GPT response → Claude format (incl tool_calls→tool_use) | `openAiMessageToClaudeMessage` | 484 |
| Emit complete Claude SSE (text + tool_use) | `writeClaudeMessageSse` | 568 |
| Emit text-only Claude SSE | `endWithSyntheticMessage` | 427 |
| Per-request settings gate (precedent: rtkEnabled) | `execFileSync sqlite3` 30s-cached read | 85–109 |
| Model-alias rewrite precedent | `resolveClaudeSlotOverride` (haiku slot) | 188–196 |

The **one real gap**: no `openai-compatible-*` / GPT provider is configured in the
9router DB yet (`providerConnections` has claude/antigravity/gemini/codex/github/cursor
only). GPT-5.5 base-URL + key must be provisioned.

## 3. The fusion algorithm we build (mirrors OpenRouter)

```
inbound Claude request (messages, system, [tools])
   ├─►  Panel A = Opus 4.8   (native Anthropic call)         ┐ run in
   └─►  Panel B = GPT-5.5    (openai-compatible call)         ┘ parallel
                       │
                       ▼
   Judge = Opus 4.8 : prompt = original task
                    + "Candidate A (Opus): …"
                    + "Candidate B (GPT-5.5): …"
                    + instruction: produce structured deliberation
                      (consensus / contradictions / coverage / unique / blind-spots),
                      then write the FINAL answer.
                       │
                       ▼
   stream judge's final answer back as Claude SSE
   usage = A + B + judge   (sum, like OpenRouter)
```

Panel of exactly 2 (Opus + GPT-5.5) is fewer than OpenRouter's 3–5, but it is the
pair the operator asked for and keeps cost/latency bounded. Easy to add a 3rd later
(Gemini is already provisioned).

## 4. Architecture options

| | **0. Use OpenRouter's real Fusion** | **A. Proxy-alias transparent fusion** | **B. Local fusion tool (MCP/endpoint)** |
|---|---|---|---|
| Build effort | ~none (add 1 upstream) | medium-high | low-medium |
| How invoked | `/model` → openrouter/fusion bridge | `/model` → `fusion` alias; proxy fans out | Opus calls `fusion(question)` tool deliberately |
| Control of panel/judge | `analysis_models`+`model` override | total (our code) | total (our code) |
| Data egress | → OpenRouter + their providers | → Anthropic + our GPT endpoint | → Anthropic + our GPT endpoint |
| Cost | OpenRouter markup, sum of members | sum of 3 completions (no markup) | sum of 3 completions |
| Streaming/keepalive complexity | none (their problem) | **high** (await-both, keepalive, buffer 1M) | **none** (tool result is plain text; main model streams normally) |
| tool_use divergence problem | n/a (their chat endpoint) | **must strip tools** (text-only v1) | **avoided** (Opus stays the driver) |
| Fits Claude Code agent loop | poorly (whole turn becomes fusion) | as a deliberate "answer model" | **naturally** (consult mid-task) |

**Recommendation: B as v1, A as v2.** B is lower-risk, sidesteps the two hardest
proxy problems (streaming+keepalive, tool_use divergence) entirely, and fits the
agentic loop — Opus drives, calls fusion for a hard sub-question, integrates the
synthesized answer. A is the faithful "switch to a fusion model" UX (closest to
OpenRouter) but pays full streaming/tool complexity; build it once B proves the
fusion/judge quality is worth it. **0** is the fallback if we decide not to self-host
(loses Opus4.8+GPT5.5 exact control and ships data to OpenRouter).

## 5. Recommended build — v1 = Option B (local fusion tool)

A tiny standalone service + a Claude Code MCP tool `fusion`. No preproxy surgery.

**Components**
1. `fusion-server` (small Node service, ~150 LOC) exposing `POST /fuse {prompt, context?, preset?}`:
   - Reads GPT-5.5 base-URL/key + Opus access from config (reuse 9router DB or a local `.env`).
   - Fan-out: `Promise.allSettled([callOpus(prompt), callGpt55(prompt)])`, each with its own timeout; degrade to whichever returns.
   - Judge: `callOpus(judgePrompt(prompt, candidateA, candidateB))` with the structured-deliberation template (§3).
   - Returns `{ final, deliberation, usage, ran:[...] }`.
2. MCP registration so Claude Code sees a `fusion` tool: `fusion(question, context?)` → returns the judge's final answer (+ optionally the deliberation). The orchestrator calls it for research/hard-reasoning steps.

**Why this shape:** the fused result enters the conversation as an ordinary tool
result — Opus keeps streaming, no SSE/keepalive/buffering/tool-divergence work. The
agent decides when fusion is worth 3× the tokens.

**Latency/cost per call:** `max(opus, gpt55) + judge(opus)` ≈ 2× single-model wall
time; cost ≈ 2 Opus + 1 GPT-5.5 completions. Acceptable because it's invoked
deliberately, not every turn.

## 6. v2 — Option A (proxy-alias transparent fusion), if desired later

Insert at `preproxy.js` line ~1059 (confirmed in-scope: `body`, `modelTag`, `req`,
`res`, `target`):

```js
const _settings = readSettings();              // new 30s-cached sqlite read (precedent: refreshToken)
if (isFusionModel(json.model) || (_settings.fusionEnabled && /opus/i.test(modelTag))) {
  return runFusion(req, res, body, modelTag, _settings.fusionConfig);
}
```

`runFusion`:
1. Parse body; **strip `tools`/`tool_choice`** (v1 text-only — avoids tool_use
   divergence; reuse `forceGeminiSubagentTextReturn` pattern at line 355).
2. Start SSE keepalive (`res.write(":keepalive\n\n")` every ~15s) — NOT currently in
   the proxy; required so Claude Code's 180s stall-kill doesn't fire during the
   buffer-both wait.
3. Parallel: Opus via `forwardBuffered` into a buffer; GPT-5.5 via `https.request` to
   the openai-compatible endpoint → `openAiMessageToClaudeMessage`.
4. Judge (Opus) with the §3 structured template.
5. `clearInterval(keepalive)`; emit final answer via `writeClaudeMessageSse` (line 568).
6. `usage` = sum.

Trigger via `ANTHROPIC_DEFAULT_OPUS_MODEL=fusion-opus-gpt` (rides the existing
`resolveClaudeSlotOverride` mechanism) or a `fusionEnabled` settings flag.

## 7. Config

Add to `~/.9router/db/data.sqlite` `settings.data` JSON (open-ended, no migration —
same as `rtkEnabled`):

```json
{
  "fusionEnabled": false,
  "fusionConfig": {
    "panel":  ["claude/claude-opus-4-8", "openai-gpt55/gpt-5.5"],
    "judge":  "claude/claude-opus-4-8",
    "preset": "quality",
    "perCallTimeoutMs": 120000,
    "maxCandidateChars": 24000
  }
}
```

GPT-5.5 provider — add a `providerConnections` row:
`provider='openai-gpt55', authType='api-key', data={"apiKey":"<redacted>","providerSpecificData":{"baseUrl":"https://<gpt55-endpoint>/v1"}}`.

## 8. Hard problems + mitigations

1. **tool_use divergence** (two models, different tool calls, stateful `tool_use_id`
   echo) → v1 is **text-only** (strip tools). Option B avoids it structurally (Opus
   drives the tool loop, fusion only answers sub-questions).
2. **Streaming / TTFT** (must await both before judge) → Option B has none (tool
   result, main model streams). Option A needs keepalive SSE + buffering full Opus
   (possibly MB) in memory.
3. **Latency × cost** = max(panel)+judge, cost = sum → deliberate invocation only;
   never the default coding loop.
4. **GPT-5.5 provisioning** — must add provider row + key; data egresses to the GPT
   endpoint. Operator decision (which endpoint, key).
5. **Quality risk** — a weak judge prompt loses the ¾ synthesis lift. The
   structured-deliberation template (§3) is the load-bearing artifact; iterate it.

## 9. Phased rollout + validation

- **P0 — provision:** add GPT-5.5 provider; smoke-test a raw GPT-5.5 call through our
  stack returns valid output.
- **P1 — fusion core (Option B):** build `fusion-server` + judge template; CLI test
  `fuse "<hard question>"` → inspect deliberation + final.
- **P2 — quality gate:** run a fixed set of ~20 hard research/reasoning prompts;
  blind-compare Fusion vs Opus-alone vs GPT-alone (judge-of-record = human/operator).
  Ship only if Fusion ≥ best single model on the set (mirror OpenRouter's own bar).
- **P3 — wire as MCP tool**; measure latency + token cost per call.
- **P4 (optional) — Option A** transparent alias, once P2 proves the lift.

## 9b. MEASURED — proof-of-concept results (2026-06-15)

Ran a real fusion against the live proxy (`/tmp/fbench/fusion_bench3.py`): panel =
Opus 4.8 (`claude-opus-4-8`) ∥ GPT (`codex` combo → gpt-5.4; 5.5 not wired yet, same
economics), judge = Opus with the §3 structured template. One hard pricing prompt.

| Metric | Measured |
|---|---|
| Panel wall-clock (parallel) | 9.2s (Opus 9.2s ∥ GPT 3.3s — bounded by Opus) |
| Judge wall-clock | 17.4s (hit the 900 max_tokens ceiling → truncated) |
| **Total wall-clock** | **~28s** vs ~9s single Opus → **~3× latency** |
| Total tokens (in/out) | 969 / 1480 ≈ 2449 → **~4.4× a single Opus call** (floor; judge capped) |
| Quality | High — genuine structured synthesis; judge correctly ranked candidates + merged unique points |

**Token model for a real turn:** input ≈ `3·P + Ca + Cb` (panel reads P twice, judge
re-reads P + both candidates), output ≈ `Ca + Cb + F`. For large-context turns that's
~3× input. **This is the decisive argument for Option B over A:** a transparent
proxy-fuse (A) sends the *entire* Claude Code context P through 3 completions every
turn; the deliberate tool (B) fuses only a focused sub-question (small P), so it's far
cheaper for the same synthesis lift.

**New constraint discovered — same-provider concurrency 400s:** the OAuth `claude`
slot (1 active account) returns an instant empty-body `HTTP 400` when two `claude`
requests fire back-to-back/concurrently (reproduced deterministically; a retry
succeeds). Fusion firing panel-Opus + judge-Opus (both `claude`) MUST serialize +
backoff-retry same-provider calls, OR use a non-`claude` judge, OR add a 2nd claude
account. The PoC judge needed 3 retries. Build implication: a small concurrency-aware
retry layer (on 400/429/5xx) is mandatory, plus a ≥1s inter-call gap for same provider.

**Verdict from the PoC:** efficiency is **acceptable for deliberate hard-question /
research use** (~3× latency, ~3–5× tokens) and the synthesis quality is real — but it
is **disqualifying as a transparent every-turn loop**. → Build **Option B** (deliberate
fusion tool), judge = Opus, panel calls serialized-with-retry. GPT-5.4 via `codex`
works today with zero provisioning; swap to GPT-5.5 when/if a provider is added.

## 10. Prerequisites / open decisions (need operator)

1. **GPT-5.5 endpoint + key** — which OpenAI-compatible provider/base-URL? (Azure,
   OpenAI direct, a reseller?) Data leaves to it — confirm that's acceptable.
2. **Architecture** — B (local tool, recommended) vs A (proxy alias) vs 0 (use
   OpenRouter's real Fusion).
3. **Judge** — Opus 4.8 (recommended; native format, strongest synthesizer) vs GPT-5.5
   vs alternate.
4. **Scope of v1** — text-only fusion (recommended) confirmed acceptable, i.e. fusion
   is a "deep-answer" mode, not a tool-executing turn.
```
