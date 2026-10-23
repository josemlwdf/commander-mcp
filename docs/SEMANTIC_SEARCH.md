# Local C# Code Navigation

This document describes the implemented local C# index and navigation tools in Handy Coder MCP. Despite the filename, search is lexical rather than embedding-based semantic search.

## 1. Objective

The local index helps a coding agent navigate a C# project by finding relevant methods and types, retrieving current source, and locating likely usages.

### Requirements

- No external search services, APIs, or network requests.
- No embedding models or vector databases.
- Use Python and SQLite FTS5 for storage and full-text search.
- Use a local C# syntax parser for reliable extraction of classes, methods, and references. Prefer Tree-sitter with its C# grammar.
- Integrate into the existing MCP server. Do not create a separate MCP server.
- Store the index in each project's `.handy-coder/` directory.
- Automatically build or update the index during MCP initialization.
- Implement `search_code`, `get_symbol`, and `find_references` as actions of one MCP tool.

## 2. MCP Tool Interface

Expose two tools.

### Tool A: `index_codebase`

Purpose: Build or update the current project's local index.

Call it automatically during MCP initialization. Keep it callable manually to refresh the index when necessary.

It must:
- Resolve the current project's root directory.
- Create `.handy-coder/` if it does not exist.
- Create or open the SQLite database.
- Discover supported C# source files.
- Detect added, changed, and deleted files.
- Parse and index only new or changed files.
- Remove stale index records belonging to deleted files.
- Preserve existing records for unchanged files.
- Report the number of files scanned, parsed, skipped, removed, and failed.

Do not recreate the entire database during ordinary initialization.

### Tool B: `code_navigator`

This single MCP tool provides three actions through an `action` parameter.

Suggested interface:

`code_navigator(action, query=None, symbol=None, file_path=None, limit=10)`

Supported actions:

**Action: `search_code`**

Required: `query`

Search for methods and classes using natural-language descriptions or exact identifiers.

Return ranked results containing:
- Symbol name and fully qualified name.
- Symbol type, such as method, class, constructor, or property.
- Relative file path.
- Starting and ending line numbers.
- Method signature when applicable.
- A short matching snippet.
- A relevance score or ranking explanation.

Return a limited number of results, defaulting to 10 and capped at 50.

**Action: `get_symbol`**

Required: `symbol`

Retrieve a specific indexed method or class.

Support fully qualified names and short names. Allow `file_path` to disambiguate identical names in different files or classes.

Return:
- The exact source code.
- Relative file path.
- Starting and ending line numbers.
- Fully qualified name and symbol type.
- Method signature and containing class where applicable.

Read current source from disk and detect stale index entries before returning it. Do not return outdated source merely because it exists in the database.

**Action: `find_references`**

Required: `symbol`

Find likely calls and other usages of a given symbol.

Return:
- Referenced symbol name.
- Referencing file path and line number.
- Enclosing class and method when available.
- Reference kind, such as method call, member access, or identifier usage.
- A short source snippet.

Clearly distinguish likely textual or syntactic references from confirmed compiler-resolved references. Do not claim full C# semantic accuracy.

Validate required parameters for each action. Return concise, actionable errors for missing parameters, ambiguous symbols, invalid actions, or unavailable source files.

## 3. Project Discovery and Index Storage

Store the database at:

`.handy-coder/code_index.sqlite3`

Use the existing MCP project's workspace configuration to identify the root directory wherever possible. Do not rely blindly on the process working directory.

If necessary, provide a fallback that searches parent directories for known project markers, such as `.git` or Unity's `Assets` and `ProjectSettings` directories.

All paths stored in the database should be relative to the project root. Resolve paths before file access and prevent results from referring to arbitrary paths outside the project.

Index `.cs` files initially, including relevant project source and local package source where appropriate.

Exclude irrelevant or generated directories by default, including:
- `.git`
- `.handy-coder`
- `Library`
- `Temp`
- `obj`
- `bin`
- `Build`
- `Builds`
- `Logs`
- `UserSettings`

Keep the exclusion list configurable. Do not exclude `Packages` wholesale if it contains relevant local C# source.

The index must be project-specific. Two projects must never share or overwrite one another's index.

## 4. SQLite Database Design

Use the standard Python `sqlite3` module.

Create the following logical tables. Names may be adjusted to fit the existing project's conventions.

### `metadata`

Store:
- Database schema version.
- Project root or project identifier.
- Last successful indexing timestamp.

### `files`

Store:
- Stable file ID.
- Relative file path.
- File content hash.
- File size and modification timestamp.
- Last indexing timestamp.
- Optional indexing error status.

### `symbols`

Store:
- Symbol ID and owning file ID.
- Symbol name and fully qualified name.
- Symbol type.
- Namespace and containing class.
- Method signature or declaration.
- Documentation comments.
- Starting and ending line numbers.
- Indexed source text.
- Normalized searchable text.

Index methods individually rather than treating an entire large file as a single search result. Also index class declarations and useful member declarations.

### `references`

Store:
- Source symbol ID when available.
- Referenced identifier or target name.
- Owning file ID.
- Line number.
- Reference type.
- Short contextual snippet.

### Full-text search index

Create an SQLite FTS5 virtual table over the symbol name, qualified name, signature, documentation, normalized searchable text, and relative path.

Use SQLite's built-in BM25 ranking with greater weight assigned to symbol names and signatures than to method bodies.

Use SQLite transactions for consistent database updates. Add the required indexes for file IDs, qualified names, and reference lookups.

Check during initialization that FTS5 is available in the active SQLite build. If unavailable, report a clear initialization error rather than silently providing an incomplete index.

## 5. C# Parsing and Search Normalization

Use Tree-sitter locally with a compatible C# grammar to discover syntax boundaries.

Extract at least:
- Classes, structs, interfaces, and records.
- Methods and constructors.
- Properties and relevant fields.
- Method invocations and member accesses.
- Identifier usages and enclosing symbol information.
- XML documentation comments when available.

Preserve correct line ranges, including nested blocks and multiline declarations. Do not depend on simple brace-counting or regular expressions as the primary method-boundary detector.

### Identifier normalization

Split common C# naming formats into searchable tokens.

Examples:

- `ClampOutOfBoundsMovement` → `clamp`, `out`, `bounds`, `movement`
- `OnActionReceived` → `on`, `action`, `received`
- `find_references` → `find`, `references`
- `SoccerAgent.ClampOutOfBoundsMovement` → searchable class and method components

Index both original identifiers and normalized tokens.

### Search ranking

Use a combination of:

1. Exact symbol-name matches.
2. Exact or strong qualified-name matches.
3. Signature and identifier matches.
4. Documentation and comment matches.
5. Body-content full-text matches.
6. File-path matches.

Use FTS5 BM25 for full-text ranking and an additional explicit preference for exact identifier matches.

Build safe FTS query expressions from normalized terms rather than passing unprocessed user input directly as FTS syntax. Use parameterized SQL queries.

Try a broader fallback query when the initial query returns no results. Do not invent semantic similarity: this version is a ranked lexical search system, not an embedding-based semantic search engine.

## 6. Incremental Indexing

During initialization:

1. Discover eligible C# files.
2. Determine which files are new, changed, unchanged, or deleted.
3. Parse new and changed files.
4. Replace the indexed records belonging to successfully processed changed files.
5. Remove records for deleted files.
6. Update indexing metadata.
7. Report indexing statistics.

Use SHA-256 hashes, or an equally reliable change-detection strategy, so file changes are detected correctly. Hashing source files while skipping parsing for unchanged content is acceptable.

Do not rebuild the entire index on each server startup.

Handle individual file errors without abandoning the entire project scan. Record errors, log useful diagnostics, and continue indexing other files.

Use transactions or savepoints so failed updates do not leave partially replaced symbol data.

If indexing is interrupted, the next initialization should be able to recover and finish updating the index.

## 7. Symbol Retrieval and Reference Accuracy

`get_symbol` must retrieve the current source of the selected symbol and return its actual line range.

If the index is stale, refresh the relevant file and resolve the symbol again before returning source.

For `find_references`, use syntax-tree information to identify invocation targets, member-access targets, and other relevant identifier usages. Associate each occurrence with its enclosing method when possible.

Do not attempt to implement a complete C# compiler or semantic resolver. Overloads, interfaces, inheritance, aliases, and dynamic dispatch can make exact reference resolution impossible with syntax alone.

Where a reference is uncertain, label it as a likely usage rather than a confirmed call.

## 8. MCP Integration

Before implementation, inspect the existing MCP server structure, SDK version, initialization lifecycle, project-root discovery, logging, and tool-registration conventions.

Integrate with those conventions instead of rewriting the server architecture.

Call the indexing function during the existing initialization lifecycle. If the server framework does not offer a suitable startup hook, use the smallest compatible initialization mechanism.

Avoid indexing synchronously on every incoming search request.

Keep diagnostic logs on stderr or the existing diagnostic logger. Never print debugging output to stdout when using stdio MCP transport.

Return structured, concise tool results suitable for an LLM to consume. Do not return entire files from search results; reserve complete source retrieval for `get_symbol`.

## 9. Testing and Acceptance Criteria

Create tests using a small temporary C# project with nested classes, overloaded methods, comments, multiple source files, and known call sites.

The implementation is accepted when all of the following pass:

1. On first initialization, the index database is created under the project's `.handy-coder/` directory.
2. On subsequent initialization, unchanged files are not reparsed.
3. Added, changed, renamed, and deleted files are reflected correctly.
4. Each of the three `code_navigator` actions works through the same MCP tool.
5. An exact method-name query ranks that method highly.
6. A natural-language query such as "prevent movement farther outside the pitch" can retrieve relevant code containing corresponding movement-boundary logic when its identifiers, comments, or method body provide matching terms.
7. An exact `get_symbol` query returns the correct source, file, and line range.
8. Duplicate method names can be disambiguated by qualified name or file path.
9. `find_references` returns likely call sites and source locations, while clearly identifying its limitations.
10. Invalid parameters, empty queries, missing symbols, inaccessible files, and malformed input are handled cleanly.
11. Two different projects maintain independent databases.
12. The indexer never modifies project source files.
13. No network service, embedding model, or external search API is required.
14. The existing MCP server and its unrelated tools continue to work.

Add a small set of realistic search queries from actual coding-agent tasks and evaluate whether the correct methods appear among the first five results.

## 10. Integration and limitations

The server registers `index_codebase` and `code_navigator` and runs an incremental index during startup. The database is workspace-local at `.handy-coder/code_index.sqlite3`; the indexer does not modify C# source files.

Search uses SQLite FTS5 over symbol and source text. It splits identifiers into lexical tokens, but does not use embeddings or infer concepts that are absent from the indexed text. `find_references` reports syntax-based candidates and cannot resolve overloads, inheritance, aliases, or dynamic dispatch like a C# compiler.

The code-index tests are in `test/test_code_index.py`; the server tool registrations are covered by `test/test_tool_schemas.py`.