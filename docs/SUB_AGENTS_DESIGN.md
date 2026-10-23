Subagents for Continue via a custom MCP server: implementation plan
Oct 22, 2026 · @Jose
Goal and constraints
Subagents ship as extra tools on your MCP server, and the server picks a backend the session already allows, so no user adds a key or a setting.
• Who does what. Continue hosts the chat and calls your tools. It has no nested agent loop for tool calls, so your server owns delegation.
• No new credentials. The only inputs are what the server receives today. New required variables: zero.
• Agent mode only. Continue exposes MCP tools only in agent mode (Continue MCP docs).
• Env reaches the server only through its entry. Keys come from the env block, filled by ${{ secrets.NAME }} from ~/.continue/.env or a project .env (Continue config guide).
• Sampling is probably unavailable. A community index of MCP clients lists the Continue CLI client as tools-only, with no sampling (mcp-client-capabilities). The IDE extension is not listed, so the server must detect it at runtime, not assume.
Not yet verified (Phase 0 settles them): Continue's tool-call timeout, its result-size limit, and whether it shows progress notifications.
Assumption to confirm: "credentials it already uses" means the server already receives some keys. If none of them can call an LLM, cooperative mode (Phase 2) still works with zero credentials.
Core design: pick the cheapest backend the session allows
The server resolves one backend after Continue's initialize handshake, in the order below, and every backend sits behind the same five tools.
Order
Backend
Needs
Real isolation
Chosen when
1
Sampling
Client declares sampling
Yes
Continue, or a later version, advertises it
2
Direct reuse
An LLM key the server already receives
Yes, can run in parallel
An existing env var is an LLM provider key
3
Local model
Ollama on localhost
Yes, weaker model
Only if the user opts in with SUBAGENT_LOCAL=1
4
Cooperative
Nothing
No, shares the main chat context
Always available, the floor
The server asks three questions in order and takes the first yes; if all three fail, it runs cooperative mode.
The server reads Continue's own config only when the user opts in with `SUBAGENT_CONTINUE=1` or `SUBAGENT_BACKEND=continue` (see the addendum at the end). It never does so by default, because reading another tool's files without consent is intrusive and the format can change. A missing or unreadable config means no Continue backend.
Architecture and module layout
One SubagentManager owns task state, and two executors run the work: a loop executor that calls an LLM backend, and a cooperative executor that hands the work back to Continue's own model.
src/
  server.ts              MCP wiring; calls resolveBackend() once initialized
  subagents/
    tools.ts             the five MCP tools and their zod schemas
    manager.ts           task state, concurrency, TTL, cancellation
    roles.ts             role registry: prompt, tool allowlist, budgets, output schema
    resolve.ts           capability negotiation and credential detection
    executors/
      loop.ts            LLM loop shared by sampling, direct and local backends
      cooperative.ts     frame stack, scope guard, reminder trailer
    backends/
      sampling.ts        server.createMessage(...)
      direct.ts          adapters over the provider the server already uses
      local.ts           Ollama over localhost (opt-in)
The examples use TypeScript with the official MCP SDK; the same split maps one to one onto the Python SDK.
• Manager. Single owner of task state. In stdio mode one process serves one Continue window, so one manager per process is the session.
• Role registry. Where your domain knowledge lives. Start with explorer, reviewer and planner, then replace them with roles that fit your server.
• Executors. Same input and output shapes, so the tools never know which backend ran.
Tool contract: the same five tools in every mode
A stable tool list means Continue never has to refresh tools when the backend changes; only the status in the response differs.
Tool
Input
Returns
subagent_run
role, task, optional context, deliverable, wait (default true)
completed, running, delegated_to_host or failed
subagent_status
task_id
state, turns used, backend
subagent_result
task_id, optional offset
full result, paged
subagent_complete
task_id, result
digest plus a resume line; cooperative mode only
subagent_cancel
task_id
final state
The three shapes subagent_run can return (values are examples):
{ "status": "completed", "task_id": "t_1", "backend": "direct:anthropic", "result": "...", "usage": { "turns": 3, "tokens": 4210 } }
{ "status": "running", "task_id": "t_1", "poll": "subagent_status" }
{ "status": "delegated_to_host", "task_id": "t_1", "instructions": "You are now acting as ..." }
Tool descriptions tell the model what to do with each status: use result when completed, poll when running, follow instructions and call subagent_complete when delegated. The backend field is always returned, so the user can see which credential or model did the work.
Phase 0: one-day spike to verify what Continue allows
A probe build answers four open questions before any real code exists.
[ ] Add a probe tool that writes getClientVersion() and getClientCapabilities() to stderr once initialized. Run it from the IDE extension and from the CLI.
[ ] If sampling is declared, call createMessage with a 20-token prompt. Record whether Continue asks for approval, errors, or answers.
[ ] Add a sleep tool and run it for 30, 60, 120 and 300 seconds. Record where Continue gives up, with and without notifications/progress.
[ ] Return 2,000, 20,000 and 100,000 characters from a tool. Record where Continue truncates.
[ ] Start the server with only the env vars it receives today. Confirm each one reaches process.env.
Question
Result
Decides
Declares sampling?
fill in
Whether the sampling backend is live or future-proofing
Tool timeout
fill in
The wait budget before returning running
Result size limit
fill in
Result cap and page size
Progress shown?
fill in
Whether to send progress notifications
Exit criterion: all four rows filled, for both the IDE extension and the CLI.
Phase 1: foundations, resolver and manager
This phase delivers the plumbing every backend shares, tested with a fake backend so no LLM is needed.
1. Role registry. One object per role: name, systemPrompt, allowedTools (names from your server's own tools, read-only by default), maxTurns, maxTokens, outputSchema (zod).
2. Manager. A Map<taskId, Task> with states queued | running | done | failed | cancelled, a concurrency cap, a TTL sweep, and one AbortController per task.
3. Resolver. Runs once from the SDK's oninitialized callback, when client capabilities are known. EXISTING_LLM_ENV lists only variables your server already reads.
4. Tools. Register the five tools with zod input schemas and the status contract.
5. Stdout rule. In stdio mode stdout carries JSON-RPC, so every log line goes to stderr.
// resolve.ts
const EXISTING_LLM_ENV = [
  { env: "ANTHROPIC_API_KEY", provider: "anthropic" },
  { env: "OPENAI_API_KEY", provider: "openai" },
]; // keep only what the server already reads today

export async function resolveExecutor(server: Server): Promise<Executor> {
  const mode = process.env.SUBAGENT_BACKEND ?? "auto"; // optional override, never required
  const caps = server.getClientCapabilities();
  if ((mode === "auto" || mode === "sampling") && caps?.sampling)
    return new LoopExecutor(new SamplingBackend(server, !!caps.sampling.tools));
  const hit = EXISTING_LLM_ENV.find(e => process.env[e.env]);
  if ((mode === "auto" || mode === "direct") && hit)
    return new LoopExecutor(new DirectBackend(hit.provider, process.env[hit.env]!));
  if (process.env.SUBAGENT_LOCAL === "1" && (await ollamaUp()))
    return new LoopExecutor(new OllamaBackend());
  return new CooperativeExecutor();
}
Done when: the fake backend passes the contract tests for all five tools, and the resolver picks the right executor for each capability and env combination.
Phase 2: cooperative mode, the zero-credential floor
This mode needs no LLM access at all: the server turns Continue's own model into the subagent and polices its scope, so ship it first because it works in every session.
1. Open a frame. subagent_run pushes {id, role, task, parentGoal, toolCallsLeft} onto a per-session stack and returns delegated_to_host with the role instructions.
2. Instruction template. State the goal, the allowed tools, the call budget and the exact result format. End with: finish by calling subagent_complete, and do not resume the parent task before that.
3. Scope guard. Every other tool on your server checks the open frame. A tool outside the role's allowlist, or past the call budget, returns an error telling the model to complete the frame.
4. Reminder trailer. While a frame is open, each tool response from your server ends with one line, such as [subagent t_1 open: call subagent_complete when done]. This rescues models that forget.
5. Close the frame. subagent_complete validates result against the role's schema, pops the frame, stores the full text, and returns a short digest plus a resume line quoting the parent goal.
6. Clean up. A TTL closes stale frames, and subagent_cancel closes one on demand.
What this mode does not give you: parallel runs, a separate context window, or control over tools that belong to Continue or to other MCP servers. It gives structure, a scoped role and a clean hand-back. Say so in the tool description so the model does not promise more.
Done when: a scripted client that declares no sampling and sets no LLM env var completes a frame end to end, including the out-of-scope and forgot-to-complete cases.
Phase 3: loop backends with real isolation
These backends run the subagent's own LLM loop inside the server and return only the final result, so the main chat stays small and several tasks can run at once.
// executors/loop.ts
async function runLoop(b: Backend, role: Role, task: string, signal: AbortSignal) {
  const msgs: Msg[] = [user(task)];
  for (let turn = 0; turn < role.maxTurns; turn++) {
    const out = await b.complete({ system: role.systemPrompt, messages: msgs,
                                   tools: role.tools, maxTokens: role.maxTokens, signal });
    msgs.push(out.assistant);
    if (!out.toolCalls.length) return out.text;
    for (const c of out.toolCalls) msgs.push(await execTool(role, c)); // allowlist enforced here
  }
  throw new BudgetExceeded("maxTurns");
}
• Sampling backend. Call server.createMessage with includeContext: "none" and a modelPreferences hint. If the client did not declare sampling.tools, use a strict JSON protocol instead: the model answers {"tool": ..., "args": ...} or {"final": ...}, and anything else counts as a failed turn.
• Direct backend. Two thin adapters behind complete(): the Anthropic Messages API and an OpenAI-compatible chat API. Keep the model id in one constant per provider and use the provider's small tier; an optional SUBAGENT_MODEL can override it.
• Local backend. The OpenAI-compatible adapter pointed at http://127.0.0.1:11434/v1, enabled only by SUBAGENT_LOCAL=1.
• Tools inside the loop. Only your server's own tools, filtered by role.allowedTools. The subagent cannot touch Continue's file tools, so it returns findings or proposed patches and the main agent applies them.
• Waiting. wait=true blocks up to the budget measured in Phase 0, minus a margin. Past that it returns running and the model polls subagent_status. Send notifications/progress when the client supplies a progress token and Phase 0 showed it helps.
Done when: one role returns a valid result through the sampling stub, the direct adapter and the local adapter, and a runaway tool loop stops at maxTurns.
Phase 4: safety, cost control and observability
Direct reuse spends the user's existing credential without a new opt-in, so the server caps spend, shows which backend ran, and treats subagent output as untrusted.
Limit
Proposed default
Why
Depth
1 (subagents cannot call subagent_run)
No recursive fan-out
Turns per task
8
Stops runaway loops
Concurrent tasks
3
Bounds parallel spend
Tasks per session
20
Bounds total spend
Wall-clock per task
120 s
Must sit inside the timeout measured in Phase 0
Result size
8,000 characters, rest paged via subagent_result
Keeps the main context small
These are starting points; tune them with the Phase 0 numbers.
• Transparency. Every result carries backend and usage. The first direct run in a session adds one line naming the credential it used.
• Opt-out without config files. SUBAGENT_BACKEND=cooperative forces the zero-spend path. It is optional and documented, never required.
• Untrusted output. Wrap subagent results in a fenced block and tell the main model to treat them as data, so a subagent that read hostile content cannot steer the parent.
• Read-only by default. A role that can change state needs an explicit allowedTools entry, and none of the starter roles get one.
• Secrets. Never put keys in prompts, results or logs.
• Logging. One JSON line per task on stderr: id, role, backend, turns, tokens, ms, outcome.
Phase 5: tests and acceptance criteria
The release gate is a fresh setup with only today's env vars where subagent_run works and nobody changed a setting.
Test
How
Passes when
Contract
Run the five tools against a fake backend, for every executor
Same status shapes everywhere
Capability negotiation
Scripted MCP client with and without sampling
Resolver picks sampling, then falls through
Credential matrix
Env with none, one and several LLM vars
Correct choice each time; no new variable needed
Budgets
Fake backend that never stops calling tools
Stops at maxTurns; queue holds at the concurrency cap
Scope guard
Cooperative frame, call a tool outside the allowlist
Error names the open frame
Forgotten completion
Model never calls subagent_complete
TTL closes the frame; trailer reminders appear
Stdout hygiene
Read the server's stdout during a run
Only JSON-RPC, no log lines
Live check
Continue agent mode with each model your users run
Proposed target: 9 of 10 cooperative tasks end with subagent_complete
Release gate: all rows pass, the Phase 0 matrix is filled, and a clean machine with only today's env block completes one task in each available mode.
Continue config and rollout
The user's Continue config stays exactly as it is today; the subagent code reads the same env block the server already receives.
# .continue/mcpServers/my-mcp.yaml  (unchanged)
name: my-mcp
version: 0.0.1
schema: v1
mcpServers:
  - name: my-mcp
    command: node
    args: ["/abs/path/to/dist/server.js"]
    env:
      EXISTING_KEY: ${{ secrets.EXISTING_KEY }}   # no new lines for subagents
Release in three steps, so each can be reverted alone:
1. v1: cooperative only. Phases 0 to 2. No spend, works everywhere.
2. v1.1: direct reuse. Phase 3 with the direct backend, auto-selected when an existing LLM key is present.
3. v1.2: sampling and local. Sampling turns itself on when Continue advertises it; local stays opt-in.
Add a short README section covering what the five tools do, how to read the backend field, and the optional SUBAGENT_BACKEND override. Add a line to the main tool descriptions telling the model when to delegate: broad searches, independent reviews, summaries of large inputs, and not single quick lookups.
Risks, open questions and sources
The main risk is that Continue's real limits are unmeasured, which is why Phase 0 comes first.
Risk
Effect
Mitigation
Tool timeout shorter than a subagent run
Calls fail mid-task
Measure in Phase 0; return running and poll
Model skips subagent_complete
Frame stays open
Reminder trailer, TTL, scope guard
Existing key spent without a fresh opt-in
Surprise cost
Caps, backend in every result, one-line notice, SUBAGENT_BACKEND override
Subagent limited to the server's own tools
Cannot edit files itself
Return patches for the main agent to apply
Sampling absent in Continue
Sampling backend sits idle
Kept as future-proofing; costs one if
Open questions for you
• Is one of the credentials the server already receives an LLM provider key? If not, cooperative mode is your only zero-credential path.
• Is the server stdio or HTTP? HTTP needs session ids in the manager.
• TypeScript or Python? The layout carries over; the snippets are TypeScript.
• Which of your server's tools may a subagent call?
Sources (read through search results, not opened in full; as of 22 Oct 2026)
• Continue MCP docs
• Continue config guide: secrets
• Continue config reference
• MCP client capabilities index
• MCP sampling with tools, Go SDK pull request
• Continue MCP config reference, PolicyLayer

## Addendum: Continue backend and the `unavailable` status

This addendum supersedes the sections above where they conflict.

- **No backend means `unavailable`.** When auto selection finds no sampling, provider key, Continue model or Ollama, `subagent_run` returns `status: "unavailable"`. It creates no task and opens no frame, so the parent is never locked out of its own tools. The parent should do the work itself and not retry.
- **Cooperative is explicit only.** `delegated_to_host` and its frame (five-minute TTL, tool allowlist) happen only when `SUBAGENT_BACKEND=cooperative` is set.
- **Continue backend.** `SUBAGENT_CONTINUE=1` (or `SUBAGENT_BACKEND=continue`) reuses a chat model from the user's Continue config. Auto order: sampling, provider key in the environment, Continue, Ollama. It spends the user's own credits or local model.
- **Trust.** The workspace `.continue/config.yaml` is untrusted, so `${{ secrets.* }}` references in it are not resolved. Keys and header values are redacted from error text and never appear in `get_capabilities`.
- **Loop write gate.** Loop backends run read-only roles. Roles that can write or commit need `SUBAGENT_LOOP_WRITE=1`, because loop tool calls run in a server thread with a deadline and are not isolated in the worker process.
- **Cancel.** `subagent_cancel(confirm=true)` sets a per-task event that the loop checks each turn and before each tool call. An HTTP request already in flight still completes.

See `docs/AGENT_README.md` for the full list of environment variables.
