# Optional feature setup

Install JavaScript formatting support with `pip install '.[javascript-format]'`. This provides jsbeautifier formatting for `.js`, `.mjs`, and `.cjs` files; ESLint remains independently optional.

HTML files created or modified by `create_file` and `apply_edits` are automatically prettified with BeautifulSoup (`html.parser`). BeautifulSoup is installed as a required project dependency; formatter failures are reported while the requested file write is kept.

The main server registers `code_format_lint` and `web_fetch_docs` from `lib/extras`, plus `web_search` and local C# navigation tools. Other modules under `lib/extras` are not exposed unless explicitly registered.

Use `get_capabilities` first to confirm what is configured and currently available. Its `tools` section is the runtime source of truth for optional tool availability.
For optional tools, each `tools.<name>` entry may include `missing_requirements` and `setup_docs` to speed troubleshooting.

Tools blocked by missing packages, binaries, environment variables, or adapters return `status: "setup_required"` with `missing_requirements`, `remediation_commands`, and `documentation_url`. Tool-specific `error_code` values may be included for compatibility. Diagnostics and test execution responses include a `detection_trace` describing configured checks or the selected framework.

## Local C# code navigation

The server indexes C# source files during startup and stores the workspace-specific SQLite database at `.handy-coder/code_index.sqlite3`. It updates changed files and removes entries for deleted files; generated and tool-owned directories (including `.git`, `.handy-coder`, `Library`, `Temp`, `obj`, and `bin`) are excluded by default.

The `index_codebase` and `code_navigator` tools use the Tree-sitter C# parser and SQLite FTS5, included in the project dependencies. No remote index, embedding service, or vector database is used. If parser or FTS5 initialization fails, server startup reports the error instead of running without the index.

`code_navigator` supports `search_code`, `get_symbol`, and `find_references`. Search is lexical, not embedding-based; reference results are syntactic candidates and are not compiler-resolved. See [SEMANTIC_SEARCH.md](./SEMANTIC_SEARCH.md) for tool details.

## Web tools

`web_search` uses DuckDuckGo Lite, and `web_fetch_docs` retrieves a known public HTTP(S) URL. Both need network access when called; web fetch blocks private, local, and reserved addresses. Use `web_search` to discover public pages and `web_fetch_docs` to read a known URL.

## Web fetching (`web_fetch_docs`)

`web_fetch_docs` fetches text from a known public HTTP(S) URL. Requests to private, local, or reserved addresses are blocked. Use `offset` to page through long responses.

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
