# Canonical tool inventory

This inventory reflects the tools registered by `handy_coder_mcp.py` via module `register()` calls.

## Core tools

1. `read_files`
2. `apply_edits`
3. `terminal_exec`
4. `search_text`
5. `code_search_ripgrep` *(deprecated alias of `search_text`)*
6. `find_files`
7. `code_get_diagnostics`
8. `run_tests`
9. `git_manage`
10. `defer_commit`
11. `get_capabilities`
12. `skills_manage`
13. `orchestrator_bootstrap`
14. `orchestrator_update_task_status`
15. `create_file`

## Optional/extra tools

16. `lsp_symbol_lookup` *(requires `HANDY_CODER_LSP_COMMAND`)*
17. `vector_search_codebase` *(requires `HANDY_CODER_EMBEDDING_MODEL` + `sentence-transformers`)*
18. `code_format_lint` *(uses installed lint/format binaries)*
19. `web_fetch_docs` *(search mode requires `HANDY_CODER_SEARCH_URL`)*
20. `debug_session_step` *(requires DAP env vars and adapter)*
21. `browser_action` *(requires Playwright + Chromium)*

## Subagent tools

22. `subagent_run`
23. `subagent_status`
24. `subagent_result`
25. `subagent_complete`
26. `subagent_cancel`

## Notes

- `code_search_ripgrep` is intentionally kept as a compatibility alias and does **not** use ripgrep internally. Prefer `search_text` for new usage; migration is a direct rename with the same arguments.
- Optional tools are still registered, but may return unsupported/not-configured responses when dependencies are missing.
