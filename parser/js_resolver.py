"""
JS/TS import resolution: converts a raw import specifier ("./utils",
"../lib/foo") into an actual file path in the repo.

Unlike Python's dotted-module resolution, JS/TS resolution is filesystem-path
based and has its own quirks:
- extension-less imports ("./utils" -> utils.ts, utils.tsx, utils.js, ...)
- directory imports resolving to an index file ("./lib" -> lib/index.ts)
- bare specifiers ("react", "lodash") are external packages, not local files
- tsconfig path aliases ("@/components/x") are NOT handled here (known
  limitation — would require parsing tsconfig.json's "paths" mapping)
"""

from pathlib import Path

_RESOLVE_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]


def resolve_js_import(spec: str, importing_file: Path, repo_root: Path) -> Path | None:
    """Returns the resolved absolute file path, or None if this is an
    external package (not resolvable within the repo) or genuinely missing."""
    if not (spec.startswith("./") or spec.startswith("../")):
        return None  # bare specifier -> external package (react, lodash, node:fs, ...)

    base = (importing_file.parent / spec).resolve()

    # 1. exact match (spec already includes an extension, e.g. "./types.ts")
    if base.is_file():
        return base

    # 2. try appending each known extension
    for ext in _RESOLVE_EXTENSIONS:
        candidate = base.with_suffix(ext) if base.suffix == "" else Path(str(base) + ext)
        if candidate.is_file():
            return candidate

    # 3. try as a directory with an index file
    if base.is_dir():
        for ext in _RESOLVE_EXTENSIONS:
            candidate = base / f"index{ext}"
            if candidate.is_file():
                return candidate

    return None  # couldn't resolve — external package or genuinely broken import
