# Optional feature setup

This project ships optional tools that depend on local configuration, extra Python packages, or external services.

Use `get_capabilities` first to confirm what is configured and currently available.
For optional tools, each `tools.<name>` entry may include `missing_requirements` and `setup_docs` to speed troubleshooting.

## LSP (`lsp_symbol_lookup`)

### Required environment variable

- `HANDY_CODER_LSP_COMMAND`

Set this to a local language server command (no shell wrapping). Example:

```bash
export HANDY_CODER_LSP_COMMAND='pylsp'
```

### Request position format

`Position` uses **0-based** coordinates:

- `line`: first line is `0`
- `character`: first character is `0`

Example: line 10, first character:

```json
{"line": 9, "character": 0}
```

### Common failures

- `not_configured`: env var missing/empty.
- startup failure: command not installed or not executable.
- empty result with `supported: true`: no definition found at that source location.

## DAP (`debug_session_step`)

### Required environment variables

- `HANDY_CODER_DAP_HOST` (optional, defaults to `127.0.0.1`)
- `HANDY_CODER_DAP_PORT` (required)
- `HANDY_CODER_DAP_START_REQUEST` (required JSON)

Example:

```bash
export HANDY_CODER_DAP_HOST=127.0.0.1
export HANDY_CODER_DAP_PORT=5678
export HANDY_CODER_DAP_START_REQUEST='{"command":"launch","arguments":{"program":"app.py","cwd":"."}}'
```

Supported actions: `start`, `step_over`, `step_into`, `inspect_vars`, `stop`.

Troubleshooting:

- ensure adapter listens on loopback host/port
- ensure JSON in `HANDY_CODER_DAP_START_REQUEST` parses correctly
- ensure any `program`/`cwd` in arguments resolves inside workspace

## Browser automation (`browser_action`)

### Requirements

- `playwright` Python package installed
- Chromium installed for Playwright

Install example:

```bash
python -m pip install playwright
python -m playwright install chromium
```

Actions:

- `navigate` (requires `url`)
- `click` (requires `selector`)
- `screenshot`
- `get_html`
- `close`

Common failures:

- dependency missing (`playwright` not installed)
- browser launch failed (Chromium missing or incompatible runtime)
- blocked private-network URL (except localhost)

## Semantic search (`vector_search_codebase`)

### Required configuration

- `HANDY_CODER_EMBEDDING_MODEL` set to a locally available model
- `sentence-transformers` installed

```bash
export HANDY_CODER_EMBEDDING_MODEL='all-MiniLM-L6-v2'
python -m pip install sentence-transformers
```

Notes:

- automatic model downloads are disabled
- indexing is local-only and limited to project files/size limits

Common failures:

- `not_configured` when model env var is missing
- `dependency_missing` when `sentence-transformers` is not installed

## Web search and docs (`web_fetch_docs`)

`web_fetch_docs` can fetch public URLs directly. Search mode also requires:

- `HANDY_CODER_SEARCH_URL`

The endpoint should accept `q=<query>` and return JSON results.

Example:

```bash
export HANDY_CODER_SEARCH_URL='https://your-search-service.example/search'
```

If unset, direct URL fetch still works but `search_query` mode returns a clear unsupported/not-configured response.

## Test execution setup (`run_tests` / pytest)

For reliable pytest execution via `run_tests`:

- run from the workspace root
- keep tests in `test/` or `tests/`
- when tests import local packages (for example `from lib import support`), add `test/__init__.py`

Common pytest import failure:

- `ModuleNotFoundError: No module named 'lib'`

Recommended fixes:

1. create `test/__init__.py` (package marker)
2. ensure the tool invokes pytest as `python -m pytest` (the server does this)
3. run `run_tests` with `filter_path` to isolate failing targets while troubleshooting

## Quick verification

After configuration, call `get_capabilities` and verify expected flags under:

- `tools.*.available`
- `tools.*.configured` (where applicable)
- `tools.*.missing_requirements` (empty for fully configured optional tools)
- `tools.*.setup_docs` (for quick setup references)
- `env.*`
