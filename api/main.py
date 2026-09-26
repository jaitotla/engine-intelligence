"""
Phase 4 backend: exposes the Phase 1-3 analysis pipeline over HTTP so the
React frontend can request an analysis and render it interactively.
"""

import os
import sys
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

app = FastAPI(title="Engine Intelligence API")

# Vite's dev server runs on 5173 by default — allow it to call this API locally.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    repo_path: str
    max_commits: int = 2000


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    repo_root = Path(req.repo_path).expanduser().resolve()
    if not repo_root.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {repo_root}")
    if not (repo_root / ".git").exists():
        raise HTTPException(status_code=400, detail=f"Not a git repository: {repo_root}")

    graph = build_graph(repo_root)
    if len(graph.reports) == 0:
        raise HTTPException(
            status_code=400,
            detail=f"No supported files (.py, .js, .jsx, .ts, .tsx) found under {repo_root}.",
        )

    debt = score_repo(repo_root, max_commits=req.max_commits)

    # Build the graph payload, with relative paths so it lines up with the
    # hotspot/coupling data (which already uses repo-relative paths)
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
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


class ExplainRequest(BaseModel):
    path: str
    complexity: int
    churn: int
    hotspot_score: float
    loc: int
    num_functions: int
    depends_on: list[str]
    depended_on_by: list[str]
    hidden_coupling: list[str]  # e.g. ["other_file.py (12x)"]
    provider: str = "ollama"     # "ollama" (free, local) or "anthropic" (paid, higher quality)
    model: str = "qwen2.5:7b"    # only used when provider == "ollama"


EXPLAIN_SYSTEM_PROMPT = """You are explaining a code-risk report to a new engineer joining this project.

You will be given ONLY measured facts about one file: complexity, churn, dependency
counts, and coupling data. These numbers came from static analysis and git history
mining — not from reading the file's actual logic, since you were not shown the
source code.

STRICT RULES:
- Only reason from the numbers given. Never claim to know what the code does,
  what bugs it might have, or why it was written a certain way — you cannot see it.
- Do not invent specifics (no "this file probably handles authentication" or
  similar guesses about content/purpose).
- Explain what the MEASUREMENTS mean and why they matter for someone about to
  work in this codebase: what "high complexity" and "high churn" together imply
  about risk, what the dependency counts imply about blast radius, what hidden
  coupling implies about undocumented relationships.
- Keep it to 3-5 sentences. Plain, direct language — no hedging filler.
- If a number is unremarkable (e.g. zero coupling, low complexity), say so
  plainly rather than manufacturing concern.
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
Hidden coupling (changes together with, but no import relationship): {', '.join(req.hidden_coupling) or 'none'}
"""

    if req.provider == "ollama":
        explanation = _call_ollama(EXPLAIN_SYSTEM_PROMPT, user_content, req.model)
    elif req.provider == "anthropic":
        explanation = _call_anthropic(EXPLAIN_SYSTEM_PROMPT, user_content)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    return {"path": req.path, "provider": req.provider, "explanation": explanation}