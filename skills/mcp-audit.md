---
name: handy-coder-mcp-audit
description: Audit handy-coder-mcp tools for behavioral failures and usability polish; use when the user requests a full or scoped MCP tool audit.
invokable: true
---

# handy-coder-mcp tool audit

Act as a senior QA engineer and DevEx reviewer. Audit the tools the user selects, or all tools exposed by the running `handy-coder-mcp` server when no scope is given. A request may select tools by exact name, category, or “all.”

IMPORTANT: The handy-coder-mcp tools are already exposed to you for live testing. Begin the audit immediately using the available tools. Do not attempt to start, reconnect to, or reconfigure the MCP server when its tools are already accessible. Do not ask for confirmation before performing safe tests.

## Discovery

1. Inspect the available handy-coder-mcp tools and relevant workspace instructions needed to call them safely. Start with the exposed tool definitions; do not delay testing with unnecessary setup or source inspection.
2. Use the live tool definitions as the authoritative source for available tools, exact names, descriptions, input schemas, required and optional fields, types, and defaults. Do not assume every module in the repository is registered.
3. Present the complete inventory for the selected scope before making any test calls. For an all-tools audit, include every exposed handy-coder-mcp tool, even if a test later must be skipped.
4. Immediately proceed from inventory to live testing in the same workflow. Do not pause for user approval or ask whether to begin.
5. If a tool is unavailable, report the specific limitation and continue auditing the remaining accessible tools. If no relevant tools are exposed, report that blocker without inventing an inventory.
6. Source inspection may supplement live testing to explain observed behavior, but clearly distinguish source-derived conclusions from verified live results.

## Testing

For each inventoried tool, review its name, schema, and description for clarity and consistency. Choose a realistic normal case and, where useful, a boundary or invalid-input case. Match inputs to the tool's actual schema; do not blindly reuse generic parameters.

Execute the live tests immediately using the tools already exposed to you. Do not substitute source inspection, repository test suites, hypothetical reasoning, or mocked results for actual tool calls.

Exercise tools safely:

- Read-only calls can use representative workspace data. Keep outputs bounded and redact secrets or sensitive content.
- For writes or commands, use a disposable directory or fixture under the authorized workspace when possible. Never overwrite or delete user data, run destructive commands, alter external services, commit, push, deploy, or incur costs as part of an audit.
- If a meaningful test would require a destructive, irreversible, or consequential effect, mark it Not tested and explain why; do not ask for authorization mid-audit unless the user explicitly requests that risky test.
- Do not treat a tool's error response to intentionally invalid input as a defect if it is clear, stable, and appropriate. Check whether malformed input is rejected cleanly and whether failures expose useful, safe diagnostics.
- Capture the exact arguments used and the material response shape/content. Preserve error codes and concise error text. Avoid dumping large outputs; summarize them and retain a small exact excerpt where useful.
- Inspect implementation only as needed to explain observed behavior. Distinguish observed facts from inferences about internals.
- Do not run repository test suites or unrelated tools as a substitute for calling the selected MCP tools.

For every tested tool, assess:

- Behavior: Does a representative valid call work and return the expected structure? Do relevant edge cases fail safely?
- Interface clarity: Are the name, parameter names, types, required/optional status, defaults, and descriptions understandable and mutually consistent?
- Input validation: Are missing, malformed, or out-of-range values handled appropriately?
- Failure handling: Are errors actionable, bounded, and safe? Note crashes, hangs, misleading success, or unexpected response formats.

Assign a status:

- Stable: no material bug or usability issue observed in the tested scope.
- Needs Polish: works, but has a specific interface, documentation, consistency, or minor behavior issue.
- Broken: a core use case fails, behaves dangerously, or returns unusable/inconsistent results.
- Not tested: no meaningful safe execution was possible. Do not present this as Stable.

## Report

The final report must be selective: include detailed findings only for tools marked Needs Polish, Broken, or Not tested. Do not add praise or boilerplate entries for stable tools. Include a compact executive status table containing only those flagged tools; state the number of Stable tools omitted so coverage remains clear. If all tested tools are stable, say so plainly and provide an empty-findings summary rather than manufacturing recommendations.

For each flagged tool, use:

### `[tool_name]` — [Needs Polish / Broken / Not tested]

- How it works: Concise description supported by observation and, if relevant, clearly labeled source inspection.
- Description and parameter clarity: Specific schema or wording concern; say “No issue observed” only if another finding caused the flag.
- Test execution: Exact input (or a compact representation), observed output/response shape, and success/error/not-tested result.
- Bug or usability issue: Concrete unexpected behavior or reason the tool needs attention.
- Recommendation: A specific, actionable improvement. Avoid speculative redesigns.

Conclude with the executive table and up to three highest-priority improvements across the MCP. Rank only findings supported by this audit. Mention inventory scope, tested count, stable count, flagged count, and any blockers or safety-related skips. Never imply that untested tools passed.