# Optional feature setup

This project ships optional tools that depend on local configuration, extra Python packages, or external services. Only `code_format_lint` and `web_fetch_docs` are registered as optional tools by the main server. Other modules under `lib/extras` are not exposed unless explicitly registered.

Use `get_capabilities` first to confirm what is configured and currently available. Its `tools` section is the runtime source of truth for optional tool availability.
For optional tools, each `tools.<name>` entry may include `missing_requirements` and `setup_docs` to speed troubleshooting.

Tools blocked by missing packages, binaries, environment variables, or adapters return `status: "setup_required"` with `missing_requirements`, `remediation_commands`, and `documentation_url`. Tool-specific `error_code` values may be included for compatibility. Diagnostics and test execution responses include a `detection_trace` describing configured checks or the selected framework.

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
