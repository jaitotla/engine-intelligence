# Setup & Run

## Backend

```bash
cd engine-intel
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Start the API — **use `python -m uvicorn`, not the bare `uvicorn` command.**
If you have both Anaconda and a venv on your PATH, the bare `uvicorn` command
can silently resolve to Anaconda's copy instead of your venv's, and fail with
a confusing `ModuleNotFoundError` for a package you already installed.

```bash
cd api
python -m uvicorn main:app --port 8000
```

## Frontend

In a separate terminal:

```bash
cd engine-intel/frontend
npm install
npm run dev
```

Open http://localhost:5173.

## Optional: LLM explanation/summarization

The "Explain this file" and "Summarize this file" features need one of:

**Local (free) — Ollama:**
```bash
ollama serve                     # in its own terminal, keep it running
ollama pull qwen2.5:7b            # or any model you prefer
```

**Claude API (paid, higher quality):**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```
Set this *before* starting `uvicorn`, in the same terminal — it won't be
picked up if set after the server's already running, or in a different tab.
Get a key at console.anthropic.com; note this uses paid API credits.

## Running the tests

```bash
cd engine-intel
source venv/bin/activate
pip install -r requirements-dev.txt
cd tests
python -m pytest -v
```
26 tests, covering every real bug found during development (import
resolution in both Python and JS/TS, module-boundary detection, reading-order
correctness, the exclude-patterns feature, and a performance regression).

## A note on tree-sitter

`requirements.txt` pins `tree-sitter==0.21.3` deliberately. `tree-sitter-languages`
(the pre-built grammar package used for JS/TS/JSX/TSX parsing) is not
compatible with `tree-sitter >= 0.22`'s API change and fails to import at all
on a newer version. This is a real, current limitation of that package, not
an arbitrary pin — if you see a `TypeError` from `tree_sitter_languages` on
import, this is why.

## Supported languages

Python (`.py`), JavaScript/JSX (`.js`, `.jsx`, `.mjs`, `.cjs`), and
TypeScript/TSX (`.ts`, `.tsx`). Jupyter notebooks (`.ipynb`) are not parsed —
their code is stored as JSON, not plain source. TypeScript path aliases
(`@/components/Foo`) are not resolved — only relative imports (`./`, `../`).

## Analyzing a repo

The tool reads a **local path**, not a GitHub URL — clone the repo you want
to analyze first:
```bash
git clone https://github.com/psf/requests.git
```
Then paste its local path (e.g. `/Users/you/repos/requests`) into the input
box in the UI. The target must be a real git repository (have a `.git`
folder) — churn and coupling analysis need real commit history.

## Deploying the live demo

The public demo accepts a **GitHub URL directly** and clones it server-side
(the input box auto-detects whether you typed a URL or a local path). This
needs no code change from local dev — same codebase, different input.

**Backend — Render (free tier):**
1. Push this repo to GitHub, connect it to a new Render Web Service.
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
   (binding `0.0.0.0` and reading `$PORT` from the environment is required —
   Render assigns the port at runtime and won't route traffic to `localhost`)
4. Set environment variables in Render's dashboard:
   - `ANTHROPIC_API_KEY` — your key, for the Claude API explain/summarize option
   - `ALLOWED_ORIGINS` — your deployed frontend's URL once you have it (step below), e.g. `https://engine-intelligence.vercel.app`
5. Note Render's free-tier behavior: spins down after 15 minutes idle,
   ~30-60s cold start on the next request, 750 free instance-hours/month.

**Frontend — Vercel (free tier):**
1. Import this repo's `frontend/` directory as a new Vercel project (framework preset: Vite).
2. Set the environment variable `VITE_API_URL` to your Render backend's URL, e.g. `https://engine-intel-api.onrender.com`.
3. Deploy. Vercel rebuilds automatically on push.

**Guardrails on the live demo** (in `api/main.py`, not configurable via
environment variables — edit the constants directly if you want different
limits): repos are capped at 5,000 combined Python/JS/TS source files and
cloned to a depth of 500 commits, with a 90-second clone timeout. These
exist to keep the shared instance responsive for every visitor, not to
limit API cost — the Explain/Summarize buttons are intentionally left
unlimited on the public demo, so each call uses your Anthropic API key
directly. Ollama does not work on the public deployment — it runs on your
own machine, which a visitor's browser has no way to reach — so only the
Claude API option is usable there.
