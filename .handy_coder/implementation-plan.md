# Implementation Plan

_Generated: 2026-10-22T15:32:25+00:00_

## User Task

Implement improvements from the comprehensive audit report: fix pytest import-path issue by adding test/__init__.py, improve test setup documentation in docs/SETUP.md, and add clearer deprecation messaging for handy_coder_code_search_ripgrep in schema/docs and runtime response where appropriate.

## Context

Keep changes minimal and production-safe. Validate with targeted tests/commands.

## Orchestrator Workflow

- [ ] Spawn `subagent_run(role="generalist", ...)` to assess scope, assumptions, and risks.
- [ ] Save the generalist output into this implementation plan under **Generalist Assessment**.
- [ ] Spawn `subagent_run(role="planner", ...)` using this plan as context to produce non-interfering tasks.
- [ ] Write planner output into `.handy_coder/tasks.md` with `[ ]` before each task item.
- [ ] Execute tasks one-by-one by spawning `coder` subagents for implementation details.
- [ ] Track task outcomes (`non_started`, `interrupted`, `complete`, `failed`) in `tasks.md`.

## Orchestrator Memory Policy

- Keep only minimal coordination context in the parent thread (current task, status, blockers).
- Delegate substantial analysis or implementation to subagents instead of solving inline.
- Prefer another subagent run whenever work spans multiple files or requires more than one reasoning step.

## Generalist Assessment

Scope is low-to-medium and isolated: one runtime behavior tweak in `run_tests`, one package-marker file in `test/`, and documentation/deprecation wording updates. Main risk is changing pytest invocation in environments where `pytest` is only available on PATH and not in the active interpreter; mitigate by falling back to plain `pytest` when `python -m pytest` is unavailable.

## Validation Strategy

- Run `run_tests(test_framework="pytest")` to confirm test discovery/import behavior is no longer blocked by `ModuleNotFoundError: lib` collection errors.
- Run targeted unit tests around `run_tests` behavior: `pytest test/test_core_feedback.py -k run_tests -q`.
- Manually inspect updated docs and deprecation copy for migration clarity (`code_search_ripgrep` -> `search_text`).
