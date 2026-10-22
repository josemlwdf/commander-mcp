# Task Breakdown

_Generated: 2026-10-22T15:32:25+00:00_

Source goal:

> Implement improvements from the comprehensive audit report: fix pytest import-path issue by adding test/__init__.py, improve test setup documentation in docs/SETUP.md, and add clearer deprecation messaging for handy_coder_code_search_ripgrep in schema/docs and runtime response where appropriate.

Use this format for each line so status is explicit:
`- [ ] [status: non_started|interrupted|complete|failed] <task>`

- [ ] [status: complete] Add pytest packaging/import-path fix by creating `test/__init__.py` and ensure `run_tests` executes pytest in a way that preserves workspace imports.
- [ ] [status: complete] Add `run_tests` preflight or post-failure guidance for common pytest import errors (e.g., `ModuleNotFoundError: No module named 'lib'`).
- [ ] [status: complete] Update documentation (`docs/SETUP.md` and relevant README sections) with explicit pytest setup requirements and troubleshooting guidance.
- [ ] [status: complete] Improve `code_search_ripgrep` deprecation messaging with a clear migration path to `search_text` in tool description/docs.
- [ ] [status: complete] Validate changes with targeted tests/commands and summarize results.
