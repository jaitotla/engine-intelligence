"""
Phase 4/5 backend.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import anthropic
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "parser"))
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))

from dependency_graph import build_graph  # noqa: E402
from score import score_repo, _to_repo_relative  # noqa: E402
from reading_order import compute_reading_order  # noqa: E402

app = FastAPI(title="Engine Intelligence API")

# In production (Render), set ALLOWED_ORIGINS to the deployed frontend's
# URL, e.g. "https://engine-intelligence.vercel.app". Defaults cover local dev.
_allowed_origins = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GitHub URL cloning: lets the public demo analyze any repo, not just a
# local path. Guardrails exist to keep the shared demo instance up, not to
# limit cost (that's a separate, deliberate decision) — an oversized clone
# or a runaway git process would degrade the demo for every visitor, not
# just whoever triggered it.
_CLONE_DIR = (Path(tempfile.gettempdir()) / "engine-intel-clones").resolve()
_CLONE_DIR.mkdir(exist_ok=True)
_MAX_CLONE_AGE_SECONDS = 2 * 60 * 60  # sweep clones older than this
_MAX_SOURCE_FILES = 5000  # ~1.7x Django's ~2,900 — comfortably covers real repos
_CLONE_TIMEOUT_SECONDS = 90
_CLONE_DEPTH = 500

_SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}


def _sweep_old_clones():
    now = time.time()
    for child in _CLONE_DIR.iterdir():
        try:
            if child.is_dir() and (now - child.stat().st_mtime) > _MAX_CLONE_AGE_SECONDS:
                shutil.rmtree(child, ignore_errors=True)
        except FileNotFoundError:
            pass  # already removed by a concurrent request


def _count_source_files(root: Path) -> int:
    count = 0
    for p in root.rglob("*"):
        if p.suffix in _SOURCE_EXTENSIONS and p.is_file():
            if "node_modules" in p.parts or ".git" in p.parts:
                continue
            count += 1
    return count


def _clone_github_repo(url: str) -> Path:
    _sweep_old_clones()

    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    dest = _CLONE_DIR / url_hash

    if dest.exists():
        dest.touch()  # bump mtime so the sweep treats this clone as fresh
        return dest

    try:
        subprocess.run(
            ["git", "clone", "--depth", str(_CLONE_DEPTH), url, str(dest)],
            check=True,
            capture_output=True,
            timeout=_CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=f"Clone timed out after {_CLONE_TIMEOUT_SECONDS}s — this repo may be too large for the live demo.",
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(dest, ignore_errors=True)
        stderr = e.stderr.decode(errors="replace")[:300] if e.stderr else "unknown error"
        raise HTTPException(status_code=400, detail=f"Could not clone repo: {stderr}")

    # File-count guard runs AFTER the clone (there's no way to know a repo's
    # file count before fetching it) but BEFORE the expensive parsing/git-log
    # mining pass, so an oversized repo fails fast rather than burning
    # server time on the shared instance.
    file_count = _count_source_files(dest)
    if file_count > _MAX_SOURCE_FILES:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=f"This repo has {file_count} source files, over the {_MAX_SOURCE_FILES}-file limit for the live demo. Try a smaller repo, or run this locally instead (see SETUP.md).",
        )

    return dest


class AnalyzeRequest(BaseModel):
    repo_path: str | None = None   # local path — used for local dev/testing (see SETUP.md)
    github_url: str | None = None  # public repo URL — used by the live demo
    max_commits: int = 500
    exclude_dirs: list[str] = []


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    if req.github_url:
        repo_root = _clone_github_repo(req.github_url)
    elif req.repo_path:
        repo_root = Path(req.repo_path).expanduser().resolve()
        if not repo_root.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {repo_root}")
    else:
        raise HTTPException(status_code=400, detail="Provide either repo_path or github_url")

    if not (repo_root / ".git").exists():
        raise HTTPException(status_code=400, detail=f"Not a git repository: {repo_root}")

    exclude_set = set(d.strip() for d in req.exclude_dirs if d.strip())

    graph = build_graph(repo_root, exclude_dirs=exclude_set)
    if len(graph.reports) == 0:
        raise HTTPException(
            status_code=400,
            detail=f"No supported files (.py, .js, .jsx, .ts, .tsx) found under {repo_root}.",
        )

    debt = score_repo(repo_root, max_commits=req.max_commits, exclude_dirs=exclude_set, graph=graph)

    hotspot_by_path = {fs.path: fs for fs in debt.file_scores}

    nodes = []
    for abs_path, report in graph.reports.items():
        rel_path = _to_repo_relative(abs_path, repo_root)
        fs = hotspot_by_path.get(rel_path)
        nodes.append({
            "id": rel_path,
            "loc": report.loc,
            "complexity": fs.complexity if fs else 0,
            "churn": fs.churn if fs else 0,
            "hotspot_score": fs.hotspot_score if fs else 0,
            "num_functions": len(report.functions),
            "parse_error": report.parse_error,
        })

    edges = []
    for abs_src, targets in graph.edges.items():
        src_rel = _to_repo_relative(abs_src, repo_root)
        for abs_tgt in targets:
            tgt_rel = _to_repo_relative(abs_tgt, repo_root)
            edges.append({"source": src_rel, "target": tgt_rel})

    # Reading order needs repo-relative edges/reverse_edges (graph stores absolute paths)
    rel_edges = {}
    rel_reverse = {}
    for abs_src, targets in graph.edges.items():
        src_rel = _to_repo_relative(abs_src, repo_root)
        rel_edges[src_rel] = {_to_repo_relative(t, repo_root) for t in targets}
    for abs_f, deps in graph.reverse_edges.items():
        f_rel = _to_repo_relative(abs_f, repo_root)
        rel_reverse[f_rel] = {_to_repo_relative(d, repo_root) for d in deps}
    hotspot_scores_rel = {fs.path: fs.hotspot_score for fs in debt.file_scores}
    reading_order = compute_reading_order(rel_edges, rel_reverse, hotspot_scores_rel)

    return {
        "repo": str(repo_root),
        "summary": {
            "num_files": len(nodes),
            "num_edges": len(edges),
            "num_hotspots": len([fs for fs in debt.file_scores if fs.hotspot_score > 0]),
            "num_boundary_violations": len(debt.boundary_violations),
        },
        "graph": {"nodes": nodes, "edges": edges},
        "hotspots": [
            {"path": fs.path, "score": fs.hotspot_score, "complexity": fs.complexity,
             "churn": fs.churn, "loc": fs.loc, "num_functions": fs.num_functions}
            for fs in debt.file_scores if fs.hotspot_score > 0
        ],
        "hidden_coupling": [
            {"file_a": a, "file_b": b, "co_change_count": c}
            for a, b, c in debt.hidden_coupling[:50]
        ],
        "boundary_violations": [
            {"module_a": a, "module_b": b, "detail": d}
            for a, b, d in debt.boundary_violations
        ],
        "reading_order": [
            {"path": e.path, "position": e.position, "reason": e.reason,
             "hotspot_score": e.hotspot_score, "in_cycle": e.in_cycle}
            for e in reading_order
        ],
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


class FileContentsRequest(BaseModel):
    repo_path: str
    path: str  # repo-relative path, as shown in the graph/hotspot list


def _group_by_class(classes: list[str], functions) -> dict:
    """Groups a flat function list into {class_name: [methods]} plus a
    separate top-level-functions list, preserving encounter order."""
    methods_by_class: dict[str, list] = {name: [] for name in classes}
    top_level: list = []
    for f in functions:
        entry = {"name": f.name, "complexity": f.complexity}
        if f.class_name and f.class_name in methods_by_class:
            methods_by_class[f.class_name].append(entry)
        else:
            top_level.append(entry)
    return {
        "classes": [{"name": name, "methods": methods_by_class[name]} for name in classes],
        "top_level_functions": top_level,
    }


@app.post("/api/file-contents")
def file_contents(req: FileContentsRequest):
    repo_root = Path(req.repo_path).expanduser().resolve()
    file_path = (repo_root / req.path).resolve()

    # guard against path traversal outside the repo
    if repo_root not in file_path.parents and file_path != repo_root:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")

    if file_path.suffix == ".py":
        sys.path.insert(0, str(Path(__file__).parent.parent / "parser"))
        from ast_parser import parse_file as parse_py_file
        report = parse_py_file(file_path)
        grouped = _group_by_class(report.classes, report.functions)
        return {**grouped, "parse_error": report.parse_error}
    elif file_path.suffix in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        from js_parser import parse_file as parse_js_file
        report = parse_js_file(file_path)
        grouped = _group_by_class(report.classes, report.functions)
        return {**grouped, "parse_error": report.parse_error}
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_path.suffix}")


class SummarizeRequest(BaseModel):
    repo_path: str
    path: str
    provider: str = "ollama"
    model: str = "qwen2.5:7b"


SUMMARIZE_SYSTEM_PROMPT = """You are helping a new engineer understand a source code file
they've never seen before, as part of onboarding onto a codebase.

You will be shown the source of one file, and usually an OUTLINE of its full
structure (classes, methods, functions). The outline is extracted mechanically
by a parser from the ENTIRE file, so it is complete even when the source below
it was cut off.

Summarize, in plain English, what the file does — its overall purpose and the
main things it's responsible for.

- Keep it to 3-5 sentences.
- Focus on WHAT the file does and WHY it likely exists, not a line-by-line walkthrough.
- Cover the whole outline, not just the code you can read. If the source was
  truncated, you may describe the unseen classes and functions, but only at the
  level their names and place in the outline support. Never invent specific
  behavior for code you were not shown.
- If part of the file was truncated, say so briefly.
- Plain, direct language — no filler, no restating the obvious (e.g. don't say
  "this is a Python file").
"""

_MAX_SUMMARIZE_CHARS = 12000


def _outline_for(file_path: Path) -> str | None:
    """Compact text outline of a file's classes/methods/functions, built by the
    parser from the WHOLE file (so it stays complete when the source sent to
    the LLM is truncated). Returns None for unsupported types or parse errors."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "parser"))
    if file_path.suffix == ".py":
        from ast_parser import parse_file as parse_any
    elif file_path.suffix in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        from js_parser import parse_file as parse_any
    else:
        return None

    report = parse_any(file_path)
    if report.parse_error:
        return None
    grouped = _group_by_class(report.classes, report.functions)

    def names(entries):
        # collapse repeated names (e.g. overloads) into "name (xN)"
        counts: dict[str, int] = {}
        for e in entries:
            counts[e["name"]] = counts.get(e["name"], 0) + 1
        return ", ".join(n if c == 1 else f"{n} (x{c})" for n, c in counts.items())

    lines = []
    for cls in grouped["classes"]:
        lines.append(f"class {cls['name']}: {names(cls['methods']) or '(no methods)'}")
    if grouped["top_level_functions"]:
        lines.append(f"top-level functions: {names(grouped['top_level_functions'])}")
    return "\n".join(lines) or None



@app.post("/api/summarize")
def summarize(req: SummarizeRequest):
    repo_root = Path(req.repo_path).expanduser().resolve()
    file_path = (repo_root / req.path).resolve()

    if repo_root not in file_path.parents and file_path != repo_root:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")

    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read file: {e}")

    truncated = len(source) > _MAX_SUMMARIZE_CHARS
    if truncated:
        source = source[:_MAX_SUMMARIZE_CHARS]

    user_content = f"File: {req.path}\n"
    outline = _outline_for(file_path)
    if outline:
        user_content += f"\nOUTLINE OF THE FULL FILE (complete, parser-extracted):\n{outline}\n"
    if truncated:
        user_content += f"\n(source below is truncated to the first {_MAX_SUMMARIZE_CHARS} characters)\n"
    user_content += f"\n---\n{source}\n---"

    if req.provider == "ollama":
        summary = _call_ollama(SUMMARIZE_SYSTEM_PROMPT, user_content, req.model)
    elif req.provider == "anthropic":
        summary = _call_anthropic(SUMMARIZE_SYSTEM_PROMPT, user_content)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    return {"path": req.path, "provider": req.provider, "summary": summary, "truncated": truncated}


class ExplainRequest(BaseModel):
    path: str
    complexity: int
    churn: int
    hotspot_score: float
    loc: int
    num_functions: int
    depends_on: list[str]
    depended_on_by: list[str]
    hidden_coupling: list[str]
    provider: str = "ollama"
    model: str = "qwen2.5:7b"


EXPLAIN_SYSTEM_PROMPT = """You are explaining a code-risk report to a new engineer joining this project.

You will be given ONLY measured facts about one file: complexity, churn, dependency
counts, and coupling data. These numbers came from static analysis and git history
mining — not from reading the file's actual logic, since you were not shown the
source code.

STRICT RULES:
- Only reason from the numbers given. Never claim to know what the code does,
  what bugs it might have, or why it was written a certain way — you cannot see it.
- Do not invent specifics.
- Explain what the MEASUREMENTS mean and why they matter for someone about to
  work in this codebase.
- Keep it to 3-5 sentences. Plain, direct language.
- If a number is unremarkable, say so plainly rather than manufacturing concern.
"""


def _call_ollama(system_prompt: str, user_content: str, model: str) -> str:
    try:
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "stream": False,
            },
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502,
            detail="Could not reach Ollama at localhost:11434 — is 'ollama serve' running?",
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Ollama error: {resp.text}")
    return resp.json()["message"]["content"]


def _call_anthropic(system_prompt: str, user_content: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not set. Set it in your shell before starting the API.",
        )
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")


@app.post("/api/explain")
def explain(req: ExplainRequest):
    user_content = f"""File: {req.path}

Hotspot score: {req.hotspot_score:.0f} (complexity x churn)
Max function complexity: {req.complexity}
Commits that touched this file: {req.churn}
Lines of code: {req.loc}
Number of functions: {req.num_functions}

Depends on {len(req.depends_on)} files: {', '.join(req.depends_on) or 'none'}
Depended on by {len(req.depended_on_by)} files: {', '.join(req.depended_on_by) or 'none'}
Hidden coupling: {', '.join(req.hidden_coupling) or 'none'}
"""

    if req.provider == "ollama":
        explanation = _call_ollama(EXPLAIN_SYSTEM_PROMPT, user_content, req.model)
    elif req.provider == "anthropic":
        explanation = _call_anthropic(EXPLAIN_SYSTEM_PROMPT, user_content)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    return {"path": req.path, "provider": req.provider, "explanation": explanation}