"""Optional local embedding-backed code search."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from pydantic import Field
from .. import support
from ..support import inside, describe


SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".sh", ".sql", ".md", ".toml", ".yaml", ".yml",
}
MAX_FILES = 200
MAX_FILE_BYTES = 128 * 1024
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}


def register(mcp):
    @mcp.tool(description=describe(
        "Search source semantically with local model HANDY_CODER_EMBEDDING_MODEL; downloads are disabled. "
        "When to use: Find code by concept or intent."
    ))
    def vector_search_codebase(
        natural_language_query: Annotated[str, Field(description="Non-empty natural-language description of the code or behavior to find.")],
        top_k: Annotated[int, Field(description="Maximum number of ranked file matches to return, from 1 to 20.")]=5,
    ) -> dict:
        if not natural_language_query.strip():
            raise ValueError("natural_language_query cannot be empty")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        model_path = os.environ.get("HANDY_CODER_EMBEDDING_MODEL", "").strip()
        if not model_path:
            return {"supported": False, "message": "Set HANDY_CODER_EMBEDDING_MODEL to an already installed local model to enable semantic search."}
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return {"supported": False, "message": "Semantic search requires the optional sentence-transformers package; no dependency is installed automatically."}

        model = SentenceTransformer(model_path, local_files_only=True)
        paths = []
        for base, directories, files in os.walk(support.ROOT):
            directories[:] = sorted(name for name in directories if name not in SKIP_DIRS)
            for name in sorted(files):
                candidate = Path(base) / name
                if candidate.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                try:
                    path = inside(candidate, must_exist=True)
                    if path.stat().st_size <= MAX_FILE_BYTES:
                        paths.append(path)
                except (OSError, ValueError):
                    continue
                if len(paths) >= MAX_FILES:
                    break
            if len(paths) >= MAX_FILES:
                break

        documents = []
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if text:
                documents.append((str(path.relative_to(support.ROOT)), text[:MAX_FILE_BYTES]))
        if not documents:
            return {"supported": True, "matches": [], "indexed_files": 0}

        vectors = model.encode(
            [natural_language_query, *(text for _, text in documents)],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        scores = vectors[1:] @ vectors[0]
        ranked = sorted(range(len(documents)), key=lambda index: float(scores[index]), reverse=True)[:top_k]
        matches = []
        for index in ranked:
            rel, text = documents[index]
            matches.append({
                "path": rel,
                "score": round(float(scores[index]), 6),
                "snippet": text[:1200],
            })
        return {"supported": True, "matches": matches, "indexed_files": len(documents)}
