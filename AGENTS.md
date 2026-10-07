# AGENTS.md

Study Companion: FastAPI backend at repo root (`app/`), Vite/React frontend in `frontend/`.

## Commands (use uv, never pip)

- Install/sync backend env: `uv sync` (creates root `.venv`; `.python-version` pins 3.12)
- Run API: `uv run uvicorn app.main:app --reload --port 8000`
- Frontend: `cd frontend; npm install; npm run dev` (typecheck: `npm run typecheck`, build: `npm run build`)
- There is no pytest/lint config in this repo; do not invent one.

## Verification (the only real end-to-end check)

`uv run python scripts/smoke_test.py` requires a running server. For a zero-key run:

1. `uv run python scripts/mock_llm.py` (fakes chat/embeddings/Firecrawl on :9999)
2. set `LLM_BASE_URL=http://127.0.0.1:9999/v1`, `LLM_API_KEY=x`, `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL=mock`
3. `uv run uvicorn app.main:app --port 8000`
4. `uv run python scripts/smoke_test.py` — must print SMOKE TEST PASSED

## Gotchas

- `.env` lives at repo root (copy from `.env.example`); `app/config.py` reads it via pydantic-settings. Parser selection: `PARSER=auto|firecrawl|local`. Local parser is pdf-inspector with an anydoc fallback; Firecrawl is only used when `FIRECRAWL_API_KEY` is set. A "local (no Firecrawl key)" parser in `/health` is expected, not a bug.
- `[Page N]` citations in answers are stripped server-side unless N was in the retrieved chunks — preserve this contract. `frontend/src/lib/types.ts` mirrors `app/schemas.py`; update both together.
- Chunker never crosses a page boundary by design; keep it that way or citations break.
- ChromaDB/SQLite/uploads persist under `./data/` (gitignored); delete `data/` to reset state.
- PowerShell (Windows): no `&&`; use `;`. `curl` is an alias for Invoke-WebRequest — use `Invoke-WebRequest -UseBasicParsing`.
- `frontend/` is a separate npm package, not an npm workspace of the root.
