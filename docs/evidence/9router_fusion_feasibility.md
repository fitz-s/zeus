# 9router Fusion Feasibility Reference

**Scope:** Read-only investigation into whether the 9router local proxy
(`~/.9router/preproxy/preproxy.js`) can be extended to FUSE two LLM upstreams
(Anthropic Opus 4.8 + an OpenAI-compatible GPT-5.5) on a single inbound request.

**Files investigated:**
- `~/.9router/preproxy/preproxy.js` (1301 lines) — the interception layer
- `~/.9router/preproxy/upstream-shim.js` (143 lines) — the loopback bridge
- `~/.npm-global/lib/node_modules/9router/app/src/mitm/server.js` (minified, ~60 lines raw) — MITM
- `~/.npm-global/lib/node_modules/9router/app/.next-cli-build/server/chunks/5221.js` — main inference handler
- `~/.npm-global/lib/node_modules/9router/app/.next-cli-build/server/chunks/7686.js` — DB/settings layer
- `~/.npm-global/lib/node_modules/9router/app/.next-cli-build/server/chunks/8202.js` — provider adapters
- `~/.9router/db/data.sqlite` — SQLite state (schema only; no secret values printed)

---

## 1. INBOUND INTERCEPTION & FORWARD PATH

### Detection

`preproxy.js` listens on `127.0.0.1:20128` (the value of `ANTHROPIC_BASE_URL`).
Every incoming HTTP request enters the single `http.createServer` handler at **line 1223**:

```js
const server = http.createServer((req, res) => {   // line 1223
  const url = req.url || "/";
  if (isOauthPath(url)) {
    // OAuth/profile paths → api.anthropic.com with token substitution
    forward(req, res, oauthTarget);
  } else {
    forward(req, res, {             // line 1258
      protocol: "http:",
      host: UPSTREAM_HOST,          // 127.0.0.1
      port: UPSTREAM_PORT,          // 20129  (the real 9router next-server)
      substituteAuth: true,
      bearer: INFERENCE_BEARER,
    });
  }
});
```

The route for ALL non-OAuth traffic (including `/v1/messages`, `/v1/chat/completions`,
`/v1/responses`, `/v1/messages/count_tokens`) is the `forward()` call at **line 1258**.

### Inference Detection — INFERENCE_PATHS regex

```js
// line 158
const INFERENCE_PATHS = /\/v1\/(messages(\/count_tokens)?|chat\/completions|responses)(\?|$)/;

function shouldInterceptForEffortCap(req) {          // line 166
  return req.method === "POST" && INFERENCE_PATHS.test(req.url || "");
}
```

NOTE: The regex has no `^` anchor by design (comment at line 153). Because
`ANTHROPIC_BASE_URL=/v1` and claude-cli appends `/v1/messages`, the real path
arrives as `/v1/v1/messages` — the anchorless regex matches that correctly.

### Full Forward Path for POST /v1/messages

```
http.createServer handler (line 1223)
  → isOauthPath() → false
  → forward(req, res, upstream-at-20129)   (line 1099)
    → shouldInterceptForEffortCap() → true  (line 1101)
    → interceptForEffortCap(req, res, target)  (line 1002)
```

### interceptForEffortCap — Body Accumulation and Mutation (lines 1002–1097)

This is the **central intercept function**. It:

1. **Buffers the entire inbound body** into `chunks[]` (lines 1004–1015). Cap is
   `BODY_INTERCEPT_CAP` (default 64 MB). After `req.on("end")` the full body is in
   `original = Buffer.concat(chunks)`.

2. **Decompresses** gzip/deflate/br (lines 1030–1043).

3. **Mutates the body** via `applyEffortDowngrade(original)` (line 1044) — strips
   `[1m]` suffix from model string, downgrades `xhigh→high` effort for non-Opus,
   adds Gemini tool-cache breakpoint. Returns `{ body, mutated, modelTag }`.

4. **Strips/injects context-1m beta** in headers and body (lines 1049–1059).

5. **Synthetic response short-circuit** (lines 1076–1088): if `isGeminiSubagentLoop()`
   returns true, calls `endWithSyntheticMessage(req, res, ...)` and returns immediately
   — never reaching the upstream.

6. **Gemini stream bridge** (line 1089): if the model is Gemini and the client wants
   streaming, `maybeGeminiClaudeStreamBridge()` buffers the upstream response and
   re-emits it as Claude SSE. Returns true if handled.

7. **Forward to upstream** via `forwardBuffered(req, res, target, body)` (line 1090).

### INJECTION POINT for Fusion

The best injection point is **inside `interceptForEffortCap`, after step 4 (body
fully buffered and decoded) and before step 7 (forward to upstream)**. Concretely,
after line 1059 and before the `isGeminiSubagentLoop` check at line 1076:

```js
// FUSION HOOK — insert between lines 1059 and 1076 in interceptForEffortCap:
if (isFusionRequest(body, modelTag)) {
  return await runFusion(req, res, body, modelTag);
}
```

At that point:
- (a) **Full inbound body is in `body: Buffer`** — parse with `JSON.parse(body.toString('utf-8'))`.
- (b) **Fusion handler can issue its own HTTP calls** to two upstreams (Opus via
  `forwardBuffered` or direct `https.request`, GPT-5.5 via `http(s).request` to
  openai-compatible endpoint).
- (c) **Write synthesized response back** using `endWithSyntheticMessage()` or
  `writeClaudeMessageSse()` — both already exist in the file.

### Response writing — SSE vs JSON

Claude Code always sets `Accept: text/event-stream`, so the client always expects
**SSE (Server-Sent Events)**. The proxy writes responses using:

- `res.write("event: ...\ndata: ...\n\n")` — SSE framing
- `res.end()` to close the stream

The non-streaming (JSON) path is guarded by `Accept` header check:

```js
// line 428 in endWithSyntheticMessage:
const stream = forceStream || /\btext\/event-stream\b/i.test(String(req.headers.accept || ""));
```

For `forwardBuffered` (the normal upstream path), the successful response is piped
directly: `upstreamRes.pipe(res)` at line 974 — no buffering on the way out. This
means the **normal path is streaming pass-through**; fusion requires replacing this
with a buffer-both-then-emit pattern.

---

## 2. CLAUDE-FORMAT RESPONSE SYNTHESIS PRIMITIVES

### `endWithSyntheticMessage(req, res, modelTag, text, forceStream)` — lines 427–482

**Purpose:** Emit a complete Claude Messages API response (SSE or JSON) for a plain
`text` string. Used for synthetic Gemini subagent tool-budget exhaustion messages.

**Signature:**
```js
function endWithSyntheticMessage(req, res, modelTag, text, forceStream = false)
```

**SSE output (when `Accept: text/event-stream`)** — emits exactly 6 events, all
inline in `res.write()`:

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_preproxy_<ts>","type":"message",
       "role":"assistant","model":"<modelTag>","content":[],"stop_reason":null,
       "stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"<full text>"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},
       "usage":{"output_tokens":<ceil(text.length/4)>}}

event: message_stop
data: {"type":"message_stop"}
```

Then `res.end()`.

**JSON output (non-SSE):**
```json
{"id":"msg_preproxy_<ts>","type":"message","role":"assistant","model":"<modelTag>",
 "content":[{"type":"text","text":"<full text>"}],"stop_reason":"end_turn",
 "stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":<ceil>}}
```

**Completeness:** YES — emits all 6 required SSE events. `stop_reason` = `"end_turn"`.
Does NOT emit `tool_use` blocks. Text-only.

---

### `writeClaudeMessageSse(res, message)` — lines 568–623

**Purpose:** Emit a complete Claude Messages API SSE response for an already-parsed
Claude-format `message` object. Handles both `text` and `tool_use` content blocks.

**Signature:**
```js
function writeClaudeMessageSse(res, message)
// message shape: { id, type, role, model, content: [{type, text}|{type,id,name,input}], stop_reason, usage }
```

**SSE output sequence:**
```
event: message_start
data: {"type":"message_start","message":{"id":"<id>","type":"message","role":"assistant",
       "model":"<model>","content":[],"stop_reason":null,"stop_sequence":null,"usage":{...}}}

// For each content block (index 0..N):
//   If text block:
event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"<text>"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

//   If tool_use block:
event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"<id>","name":"<name>","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{...full input...}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},
       "usage":{"output_tokens":<n>}}

event: message_stop
data: {"type":"message_stop"}
```

**Completeness:** YES — full SSE sequence for all Anthropic block types including
`tool_use`. The entire `input` JSON is emitted as a single `input_json_delta` chunk
(not streamed incrementally). Sets `writeHead(200, ...)` itself. Calls `res.end()`.

---

### `buildSyntheticFindingsText(json)` — lines 399–425

**Purpose:** Build a plain text summary when Gemini subagents hit tool budget.
Returns a string (not SSE). Extracts the original task from the first user message
and the last 12 evidence fragments from all messages.

**Signature:**
```js
function buildSyntheticFindingsText(json)  // json = parsed Claude request body
// Returns: string (INCOMPLETE: ... prefix + task + evidence)
```

Not directly relevant to fusion, but illustrates how the proxy synthesizes a text
response from the existing conversation context.

---

### `providerJsonToClaudeMessage(obj, modelTag)` — lines 562–566

**Purpose:** Convert a provider's native response JSON to Claude `message` format.
Tries native Anthropic format first, then OpenAI, then Gemini.

```js
function providerJsonToClaudeMessage(obj, modelTag) {
  if (!obj || typeof obj !== "object") return null;
  if (obj.type === "message" && Array.isArray(obj.content)) return obj;  // already Claude
  return openAiMessageToClaudeMessage(obj, modelTag) || geminiBodyToClaudeMessage(obj, modelTag);
}
```

---

### `openAiMessageToClaudeMessage(obj, modelTag)` — lines 484–523

**Purpose:** Convert an OpenAI chat/completions JSON response to Anthropic Messages
format. Handles `tool_calls` → `tool_use` blocks.

**Signature:**
```js
function openAiMessageToClaudeMessage(obj, modelTag)
// obj: OpenAI response JSON (with obj.choices[0].message)
// Returns: Claude message object { id, type, role, model, content, stop_reason, usage }
```

**Input shape expected:**
```json
{
  "id": "chatcmpl-...",
  "choices": [{
    "message": {
      "content": "text response",
      "tool_calls": [{"id": "call_...", "function": {"name": "...", "arguments": "{...}"}}]
    },
    "finish_reason": "stop" | "tool_calls"
  }],
  "usage": {"prompt_tokens": N, "completion_tokens": M}
}
```

**Output shape:**
```json
{
  "id": "<obj.id or generated>",
  "type": "message",
  "role": "assistant",
  "model": "<obj.model or modelTag>",
  "content": [
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "<call.id>", "name": "<fn.name>", "input": {...}}
  ],
  "stop_reason": "tool_use" | "end_turn",
  "stop_sequence": null,
  "usage": {"input_tokens": N, "output_tokens": M}
}
```

**Key detail:** `stop_reason` is `"tool_use"` if `tool_calls.length > 0` or
`finish_reason === "tool_calls"`; otherwise `"end_turn"`. This is the correct
Anthropic convention. Tool arguments are `JSON.parse`d; parse failure produces
`{arguments: rawString}`.

---

## 3. CALLING AN OPENAI-COMPATIBLE UPSTREAM (GPT-5.5)

### How the 9router next-server reaches non-Anthropic providers

The next-server (port 20129, built Next.js app) handles the actual provider calls.
Provider routing is in **chunk 8202.js** (the `UnifiedProvider` class):

**URL construction for `openai-compatible-*` providers** (chunk 8202.js):
```js
buildUrl(a, b, c=0, d=null) {
  if (this.provider?.startsWith?.("openai-compatible-")) {
    let base = (d?.providerSpecificData?.baseUrl || "https://api.openai.com/v1").replace(/\/$/, "");
    let path = this.provider.includes("responses") ? "/responses" : "/chat/completions";
    return `${base}${path}`;
  }
  // ...
}
```

**Auth headers for `openai-compatible-*` providers** (chunk 8202.js `buildHeaders`):
```js
// Default case in the switch:
c.Authorization = `Bearer ${a.apiKey || a.accessToken}`;
// (anthropic-compatible gets x-api-key; openai-compatible gets Bearer token)
```

**Settings call structure** (chunk 5221.js `u()` function — the inner per-model handler):
```js
let { provider, model } = await resolveProviderModel(modelName); // e.g. "openai-compatible-myprovider", "gpt-5.5"
// ... picks credentials from providerConnections for that provider
// passes to j.w() which calls UnifiedProvider.execute()
```

### What would it take to call an OpenAI-compatible endpoint from inside preproxy.js

The preproxy itself does NOT currently call OpenAI-compatible endpoints — it only
calls `127.0.0.1:20129` (the next-server). The next-server handles provider dispatch.

For a fusion interceptor embedded in preproxy.js, calling GPT-5.5 directly requires:

1. **Read config from SQLite** — the preproxy already does this via `execFileSync("sqlite3", ...)`.
   A fusion config could be stored in `settings.data` (the same JSON blob that holds
   `rtkEnabled`, `cavemanEnabled`) under a key like `fusionConfig`:
   ```json
   { "fusionEnabled": true, "fusionConfig": { "gpt55BaseUrl": "https://...", "gpt55Model": "gpt-5.5" } }
   ```
   Read pattern (same as `refreshToken`, line 89–107):
   ```js
   const sql = "SELECT json_extract(data, '$.fusionConfig') FROM settings WHERE id=1";
   const out = execFileSync("sqlite3", [DB_PATH, sql], { encoding: "utf-8", timeout: 5000 }).trim();
   const fusionConfig = out ? JSON.parse(out) : null;
   ```
   **API keys** would be stored in `providerConnections.data` (JSON blob). The preproxy
   would read them with:
   ```js
   const sql = "SELECT json_extract(data,'$.apiKey') FROM providerConnections WHERE provider='openai-gpt55' AND isActive=1 LIMIT 1";
   ```

2. **Make the outbound call** — use Node's built-in `https.request` (already required
   at line 17) with `Authorization: Bearer <key>` and body as `chat/completions` JSON.

3. **Parse the response** — use `openAiMessageToClaudeMessage(parsed, modelTag)`
   (already in the file, line 484).

### Database schema relevant to OpenAI provider configuration

**`settings` table:**
```sql
CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
-- data is a JSON blob; current keys include:
-- cloudEnabled, tunnelEnabled, rtkEnabled, cavemanEnabled, cavemanLevel,
-- comboStrategy, comboStrategies, providerStrategies, ...
-- Key of interest for fusion: fusionEnabled (bool), fusionConfig (object)
```

**`providerConnections` table:**
```sql
CREATE TABLE providerConnections (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,      -- e.g. "claude", "antigravity", "codex", "openai-compatible-gpt55"
  authType TEXT NOT NULL,      -- "oauth" | "api-key"
  name TEXT,                   -- display name
  email TEXT,
  priority INTEGER,
  isActive INTEGER DEFAULT 1,
  data TEXT NOT NULL,          -- JSON; contains accessToken, apiKey, providerSpecificData
  createdAt TEXT NOT NULL,
  updatedAt TEXT NOT NULL
);
```
`data` JSON for an API-key provider: `{"apiKey": "sk-...", "providerSpecificData": {"baseUrl": "https://..."}}`

**`providerNodes` table:**
```sql
CREATE TABLE providerNodes (
  id TEXT PRIMARY KEY,
  type TEXT,     -- "openai-compatible", "anthropic-compatible", "custom-embedding"
  name TEXT,
  data TEXT NOT NULL,   -- JSON; provider-specific config
  createdAt TEXT NOT NULL,
  updatedAt TEXT NOT NULL
);
```

**`combos` table:**
```sql
CREATE TABLE combos (
  id TEXT PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,   -- e.g. "codex", "gemini-pro", "gh_opus"
  kind TEXT,
  models TEXT NOT NULL,        -- JSON array, e.g. ["cx/gpt-5.4", "gh/gpt-5.4"]
  createdAt TEXT NOT NULL,
  updatedAt TEXT NOT NULL
);
```

**`apiKeys` table:**
```sql
CREATE TABLE apiKeys (
  id TEXT PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,   -- the bearer key clients use to authenticate to 9router
  name TEXT,
  machineId TEXT,
  isActive INTEGER DEFAULT 1,
  createdAt TEXT NOT NULL
);
```

### Currently configured providers

From `providerConnections`:
- `claude` (oauth) — 3 accounts (1 active)
- `antigravity` (oauth) — 3 accounts (2 active)
- `gemini-cli` (oauth) — 3 accounts (2 active)
- `codex` (oauth) — 2 accounts (1 active)
- `github` (oauth) — 1 active
- `cursor` (oauth) — 1 active

**No `openai-compatible-*` or GPT provider is currently configured in
`providerConnections` or `providerNodes`.** A GPT-5.5 fusion target would need to
be added. The preproxy reads this via `sqlite3` CLI; the next-server reads it via
its own SQLite adapter.

---

## 4. TOOL-USE HANDLING

### Existing "remove tool affordances" logic

`forceGeminiSubagentTextReturn(json)` — lines 355–378:
```js
function forceGeminiSubagentTextReturn(json) {
  const out = { ...json };
  delete out.tools;         // removes tool definitions
  delete out.tool_choice;
  delete out.toolChoice;
  // adds max_tokens minimum
  // appends a system-reminder message telling the model to return text only
  return Buffer.from(JSON.stringify(out), "utf-8");
}
```

This is only invoked when `isGeminiSubagentLoop()` returns true (tool budget
exhausted). Normally, tools pass through unchanged to the upstream.

### How tools travel through the proxy

Under normal operation:
1. Claude Code sends a request body with `tools: [...]` and possibly `tool_choice`.
2. `applyEffortDowngrade()` reads the body JSON (line 270) but only mutates `model`,
   `effort`, and optionally adds a Gemini cache breakpoint. **Tools are not stripped.**
3. `stripContext1mFromBody()` only touches `betas`/`anthropic_beta` fields.
4. `forwardBuffered()` sends the body to 20129 unchanged.

So **tools pass through the preproxy unchanged** in the normal path.

### The hard problem for fusion: tool_use divergence

When two models are called in parallel, both can emit `tool_use` blocks:

- **Opus** returns `tool_use` blocks for Claude-native tools (with Claude tool IDs
  `toolu_...`).
- **GPT-5.5** returns `tool_calls` → translated to Claude `tool_use` by
  `openAiMessageToClaudeMessage()` (line 484) with IDs like `toolu_gemini_<ts>_0`.

Claude Code is stateful: it expects to receive tool results back to the SAME model
that issued the tool call, and the `tool_use_id` in subsequent `tool_result` blocks
must match what the model returned. If both models emit different tool calls:

- The client sees a synthesized response that is an amalgam of both.
- On the next turn, the client sends `tool_result` blocks. Those results go to...
  which model? The fusion interceptor would need to track what was issued and route
  results to both (or neither) upstreams — a stateful multiplexing problem.

**`writeClaudeMessageSse()` already handles `tool_use` blocks in synthesis output**
(lines 588–598). It emits them faithfully. But the fusion author must resolve the
stateful routing problem described above.

### Current synthetic-response paths and tool_use

- `endWithSyntheticMessage()`: text-only, never emits `tool_use`.
- `writeClaudeMessageSse()`: emits `tool_use` blocks if present in `message.content`.
- `providerJsonToClaudeMessage()` + `openAiMessageToClaudeMessage()`: translate
  GPT tool_calls into Claude tool_use blocks correctly.

For a fusion design that avoids tool-use turn complexity, the safest approach is:
**call both models with tools stripped from the body** (like `forceGeminiSubagentTextReturn`
does), fuse their text outputs only, and emit a text-only synthesized response. This
sidesteps the stateful divergence problem entirely.

---

## 5. STREAMING & TIMEOUT CONSTRAINTS

### Current forward path type

For the normal Anthropic/upstream path (`forwardBuffered`, line 886):
- The **request body is fully buffered** before sending upstream (the entire purpose
  of `interceptForEffortCap`).
- The **response is streamed through** via `upstreamRes.pipe(res)` at line 974.
  The client starts receiving SSE events as soon as the upstream sends them.

For the Gemini stream bridge (`maybeGeminiClaudeStreamBridge`, line 625):
- The **entire upstream response is buffered** (`chunks[]`, lines 641–660).
- Then re-emitted as Claude SSE via `writeClaudeMessageSse()`.
- This is the existing precedent for full-buffer-then-reformat.

### What fusion requires: await-both-then-emit

For fusion, the interceptor must:
1. Issue **two parallel upstream calls** (Opus via `forwardBuffered` into a buffer,
   GPT-5.5 via direct `https.request`).
2. **Buffer both complete responses** before emitting anything to the client.
3. Fuse the two responses.
4. Emit the fused response via `writeClaudeMessageSse()` or `endWithSyntheticMessage()`.

This replaces the streaming pass-through with a fully buffered path — same pattern
as `maybeGeminiClaudeStreamBridge`. The key change: both upstream calls must complete
before any bytes are written to `res`.

### Timeout handling

Current timeouts:
- `LC_UPSTREAM_STALL_MS` = 165000 ms (line 818): per-socket inactivity timeout.
  Fires if no bytes received after 165 seconds. Set on the `upstream` socket via
  `upstream.setTimeout(LC_UPSTREAM_STALL_MS, ...)` at line 992.
- `LC_HARD_DEADLINE_MS` = 150000 ms (line 821): wall-clock ceiling across all
  retries. Checked at line 950.
- `LC_RETRY_MAX_ATTEMPTS` = 8 (line 814).
- `LC_RETRY_TOTAL_BUDGET_MS` = 30000 ms (line 817): total retry wait cap.

For fusion, the effective latency is `max(opus_latency, gpt55_latency) + fuse_time`.
Both upstream calls would need individual stall guards. The `LC_HARD_DEADLINE_MS`
ceiling applies independently to each. The client-visible latency is strictly
bounded by the slower model.

### Client keepalive during buffering

Claude Code will timeout if it receives no bytes. The preproxy does not currently
send keepalive SSE comments (`:\n\n`) during upstream calls. For fusion (which could
buffer 60–120 seconds), a keepalive heartbeat loop would be needed:

```js
// Send SSE keepalive comments to prevent client timeout during both-upstream wait
const keepalive = setInterval(() => { res.write(": keepalive\n\n"); }, 15000);
// ... after both responses complete and fusion emits, clearInterval(keepalive)
```

---

## 6. MODEL SELECTION / ROUTING

### Model routing architecture (next-server side)

From **chunk 5221.js** (`handleSingleModel` inner function `u()`):

```
POST /v1/messages (body.model = "provider/model" or combo name)
  → t() main handler (line ~line 55221 module)
    → d_("modelName") → check if combo in combos table
    → if combo: Pr() → iterate models[] with fallback strategy
    → else: mA("modelName") → resolveProviderModel()
              → parses "provider/model" prefix
              → looks up openai-compatible / anthropic-compatible providerNodes
              → returns { provider, model }
    → per-credential loop → picks account from providerConnections
    → h.mt() = getSettings() → reads settings.data JSON from SQLite
    → j.w() = UnifiedProvider.execute()
```

The **model string in the request body is the routing key**. The next-server resolves
it to a provider+model pair by:
1. Checking if it matches a combo name in `combos.name`.
2. Parsing a `prefix/model` alias (e.g. `cx/gpt-5.4` → codex provider).
3. Checking `providerNodes` for `openai-compatible-` or `anthropic-compatible-` prefix nodes.
4. Direct provider name (e.g. `claude/claude-opus-4-8`).

### Triggering fusion via model alias

A special model alias (e.g. `"fusion-opus-gpt"`) could be used as the trigger
**in preproxy.js** — the interceptor checks `json.model` after parsing:

```js
// In interceptForEffortCap, after applyEffortDowngrade:
if (json.model === "fusion-opus-gpt") {
  return await runFusion(req, res, body, modelTag);
}
```

Alternatively, a settings flag `fusionEnabled` could trigger fusion for ALL opus
requests. The model alias approach is cleaner: the user sets
`ANTHROPIC_DEFAULT_OPUS_MODEL=fusion-opus-gpt` in `~/.claude/settings.json`
(the preproxy already reads that file for `haiku` slot override via
`resolveClaudeSlotOverride`/`readClaudeDefaultModel` at lines 170–196).

The **existing `resolveClaudeSlotOverride` pattern** (lines 188–196) is the exact
precedent: it reads `ANTHROPIC_DEFAULT_HAIKU_MODEL` from `~/.claude/settings.json`
and rewrites the body's model string. A `ANTHROPIC_DEFAULT_OPUS_MODEL=fusion-opus-gpt`
would route through that same mechanism, and the fusion check in `interceptForEffortCap`
would detect the alias.

---

## 7. CONFIG/TOGGLE PRECEDENT

### How preproxy reads settings

The preproxy reads from SQLite synchronously via `execFileSync("sqlite3", ...)` on
a **per-request basis with a 30-second cache** (`TOKEN_REFRESH_MS = 30 * 1000`,
line 30). The pattern used for the OAuth token (lines 85–109):

```js
const sql = "SELECT id||'|'||...|'||data FROM providerConnections WHERE ...";
const out = execFileSync("sqlite3", [DB_PATH, sql], { encoding: "utf-8", timeout: 5000 }).trim();
```

For settings, the equivalent would be:
```js
const sql = "SELECT json_extract(data, '$.fusionEnabled') FROM settings WHERE id=1";
const out = execFileSync("sqlite3", [DB_PATH, sql], { encoding: "utf-8", timeout: 5000 }).trim();
const fusionEnabled = out === "1" || out === "true";
```

### How the next-server reads settings

From **chunk 7686.js** `getSettings` / `i()` function:

```js
// i() = exported as getSettings (exported as h.mt in 5221.js)
async function i() {
  return h(await g());
}
function g() {
  let a = (await (0, d.c)()).get("SELECT data FROM settings WHERE id = 1");
  return a ? (0, e.q)(a.data, {}) : {};
}
function h(a) {
  // merges with defaultSettings, fills missing keys
  let b = { ...f, ...a || {} };
  for (let [a, c] of Object.entries(f)) {
    if (void 0 === b[a]) b[a] = c;
  }
  return b;
}
```

`d.c()` is the SQLite adapter. `e.q()` is `JSON.parse(a.data, {})`.

The result is the full `settings.data` JSON object, merged with defaults. It is
called **per-request** (in `u()` at chunk 5221 line ~end): `let i = await (0, h.mt)()`.

### Precedent for fusionEnabled/fusionConfig toggle

`rtkEnabled` and `cavemanEnabled` are written to `settings.data` via
`PATCH /api/settings` from the dashboard (chunk 1574.js):

```js
// Dashboard PATCH call:
await fetch("/api/settings", {
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ rtkEnabled: newValue })
});
```

The `settings.data` JSON blob is open-ended — any new key can be added without a
schema migration. The preproxy would read `fusionEnabled` from it using the same
`sqlite3` CLI pattern it uses for token refresh.

**Implementation pattern for `fusionEnabled`:**
```js
// In preproxy.js, add a cached settings reader (analogous to refreshToken):
let settingsCache = { data: null, fetchedAt: 0 };
function readSettings(force = false) {
  const now = Date.now();
  if (!force && now - settingsCache.fetchedAt < TOKEN_REFRESH_MS) return settingsCache.data;
  try {
    const sql = "SELECT data FROM settings WHERE id=1";
    const out = execFileSync("sqlite3", [DB_PATH, sql], { encoding: "utf-8", timeout: 5000 }).trim();
    settingsCache = { data: out ? JSON.parse(out) : {}, fetchedAt: now };
  } catch (e) {
    console.log(`[preproxy] settings read failed: ${e.message}`);
  }
  return settingsCache.data || {};
}

// In interceptForEffortCap (after body mutations, before synthetic check):
const settings = readSettings();
if (settings.fusionEnabled && modelTag && /opus/i.test(modelTag)) {
  return await runFusion(req, res, body, modelTag, settings.fusionConfig);
}
```

---

## FEASIBILITY VERDICT

### (a) Which fusion approaches are mechanically possible

**Response-level synthesis** (the only feasible approach) is mechanically possible.
All required primitives exist in `preproxy.js`:

1. `interceptForEffortCap` already buffers the full inbound body — no new
   buffering infrastructure needed.
2. `openAiMessageToClaudeMessage()` translates GPT-5.5 response to Claude format.
3. `writeClaudeMessageSse()` emits a complete, valid Claude SSE response including
   tool_use blocks.
4. `endWithSyntheticMessage()` is simpler (text-only fusion).
5. The settings toggle precedent (`rtkEnabled`, `cavemanEnabled`) shows exactly how
   to gate fusion with a per-request SQLite read.

Logit-level fusion (combining probability distributions) is mechanically impossible
across API boundaries — the APIs return only sampled tokens, not log-probabilities.

### (b) Single best injection point

**Function:** `interceptForEffortCap` (line 1002)
**Line:** After line 1059 (after all body mutations and header strips complete),
before line 1076 (the `isGeminiSubagentLoop` check).

Exact position:

```js
// CURRENT line 1060 area (after stripContext1mFromBody block):
if (_bodyStripped) { body = _bodyStripped; }

// === INSERT FUSION HOOK HERE ===
const _settings = readSettings();
if (_settings.fusionEnabled && modelTag && /opus/i.test(modelTag)) {
  return runFusion(req, res, body, modelTag, _settings.fusionConfig);
}
// === END FUSION HOOK ===

// CURRENT line 1061 (1m-trace logging):
try { const _h1m = ...
```

At this point: `body` is a fully decoded, possibly-mutated Buffer; `modelTag` is
the normalized model string; `req.headers` has been patched; `target` (the 9router
upstream) is in scope from the enclosing `interceptForEffortCap(req, res, target)`
call.

### (c) Top 3 hardest problems

**1. Streaming-vs-await-both (latency ceiling)**

The normal response to Claude Code is a streaming SSE pipe. Fusion requires holding
the response until BOTH upstreams have completed. The client-visible latency is
`max(Opus_latency, GPT55_latency)` plus fusion compute. Opus 1M requests can run
60–120+ seconds. GPT-5.5 can run 30–90 seconds. Fused latency = up to 120+ seconds,
during which the client must either receive keepalive SSE comments or it will timeout
at the Claude Code level (180s stall-kill). This requires adding SSE keepalive logic
(`:keepalive\n\n` writes at ~15s intervals) not currently present in the preproxy.
It also means the preproxy must buffer the complete Opus response (which can be
megabytes for 1M-context requests) in memory.

**2. tool_use divergence between models**

Both models receive the same tool definitions. Either or both can return `tool_use`
blocks. If they disagree (different tool names, different arguments, or one uses
a tool the other does not), the fusion layer must resolve which tool calls to emit
in the synthesized response. Claude Code is stateful: `tool_use_id` values in the
response must be echoed back in subsequent `tool_result` turns. If the fused response
combines tool_use blocks from both models with different IDs, the next request will
carry `tool_result` blocks for IDs that may not be meaningful to the model that
receives the follow-up call. The safe solution is to strip tools before calling both
models (text-only fusion), but this removes the agentic capability from the fused
turn.

**3. GPT-5.5 auth/config not yet present in 9router DB**

No `openai-compatible-*` provider is configured in `providerConnections` or
`providerNodes`. The preproxy would need to read the GPT-5.5 API key and base URL
from somewhere — either `settings.data.fusionConfig` or a new `providerConnections`
row with `provider='openai-gpt55'`. The preproxy has no existing mechanism to read
per-provider API keys from the DB (it currently only reads OAuth tokens for `claude`
provider). New SQLite query logic must be added. Additionally, the preproxy uses the
Node `http`/`https` modules for outbound calls — an HTTPS call to
`api.openai.com/v1/chat/completions` works fine with the built-in `https.request`,
but response buffering + error handling must be written from scratch (no existing
helper for direct-to-provider calls from the preproxy layer).
