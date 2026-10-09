# Study Companion — source-cited AI tutor + adaptive assessment

> **Track D · Personalized Tutoring & Adaptive Learning**
> A learner creates **projects** ("books"), each with its own corpus of sources —
> lecture videos, textbooks, slide decks, spreadsheets — its own topics, its own chats,
> and its own memory. Every answer cites the exact page, slide or timestamp it came from,
> and an adaptive quiz (Bayesian Knowledge Tracing) targets what you haven't mastered.

Runs entirely on your machine: local Postgres + pgvector for the database and vectors,
local disk for uploads. No cloud account, no hosted service.

## Status

The backend is being rebuilt around multi-project tenancy. See `plan.md` for the full
roadmap; `AGENTS.md` documents how to run and verify it.

| Area | State |
|---|---|
| Postgres + pgvector data layer, migrations | done |
| Auth (argon2 + JWT), per-user project CRUD, tenancy enforcement | done |
| Local file storage for every source type | done |
| Multimodal ingestion + async status polling | PDF only; docx/pptx/xlsx/video in Phase D |
| Project-level topics merged across sources | done |
| Project-scoped chat, 3 memory tiers | Phase E |
| Adaptive assessment, RAGAS evaluation | Phase F–G |
| `frontend/` | frozen legacy; a test console drives the current API |

## Layout

```
study-companion/
├── app/
│   ├── main.py                composition root: builds services, mounts routers
│   ├── config.py              .env → typed Settings (DB / providers / storage / RAG / BKT / auth)
│   ├── db.py                  psycopg3 pool + transaction helpers against local Postgres
│   ├── llm.py                 OpenAI-SDK clients with dynamic BASE_URL + API_KEY, OpenAI-compatible embeddings
│   ├── vectorstore.py         pgvector, project-scoped retrieval (required project_id)
│   ├── storage.py             local disk: DATA_DIR/uploads/{project_id}/{source_id}.{ext}
│   ├── ingestion/
│   │   ├── units.py           SourceUnit — the generalized "page" (page/slide/timestamp/sheet)
│   │   ├── registry.py        file type → parser dispatch (Phase D adds the new types)
│   │   ├── parsers.py         Firecrawl /v2/parse (per-page markdown) ⇄ pdf-inspector/anydoc fallback
│   │   ├── chunker.py         locator-aware chunks (never cross a locator ⇒ exact citations)
│   │   ├── topics.py          project-level topics: 1 LLM call + nearest-centroid assignment
│   │   └── pipeline.py        parse → chunk → embed → topics → pgvector, with status transitions
│   ├── rag.py                 strict grounded answers: retrieval gate · prompt contract · citation audit
│   ├── learner/bkt.py         Bayesian Knowledge Tracing (4 params, 20 lines)
│   ├── repo/                  all SQL lives here (users · projects · sources · topics · chunks · profile)
│   ├── schemas.py             Pydantic API contract
│   ├── deps.py                auth (JWT) + dependency accessors
│   └── routers/               auth.py · profile.py · projects.py · sources.py
├── migrations/                plain SQL, applied in order by scripts/migrate.py
├── scripts/                   migrate.py · verify_data_layer.py · verify_auth.py ·
│                              mock_llm.py · make_sample_pdf.py · smoke_test.py
├── .env.example
└── frontend/                      Vite · React 19 · TypeScript · Tailwind v4 (legacy, frozen)
    ├── src/
    │   ├── App.tsx                split-screen shell + citation bridge
    │   ├── components/
    │   │   ├── ChatPane.tsx       transcript + starter prompts
    │   │   ├── MessageBubble.tsx  user bubble / assistant prose / sources row
    │   │   ├── MarkdownMessage.tsx react-markdown; [Page N] → clickable chip
    │   │   ├── Composer.tsx       auto-growing input
    │   │   ├── PdfViewer.tsx      react-pdf, lazy pages, scroll-to-page + flash
    │   │   ├── QuizPanel.tsx      adaptive MCQs + live mastery bars
    │   │   ├── MasteryBar.tsx
    │   │   ├── TopBar.tsx         document · Chat|Quiz toggle · upload
    │   │   └── UploadDropzone.tsx first-run state
    │   ├── hooks/                 useDocument · useChat · useQuiz
    │   └── lib/                   api.ts · types.ts · citations.ts · user.ts
    ├── index.html · vite.config.ts · tsconfig.json · package.json
```

---

## Quick start (Windows PowerShell)

### 1 · Database (once)

You need Postgres with the **pgvector** extension available. Easiest path is Docker:

```powershell
docker run -d --name study-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=StudyCompanion `
  -p 5432:5432 pgvector/pgvector:pg17
```

If you already run Postgres locally, just create the database and enable the extension:

```sql
CREATE DATABASE StudyCompanion;
\c StudyCompanion
CREATE EXTENSION vector;
```

### 2 · Backend

```powershell
uv sync                                       # creates .venv, installs from uv.lock
Copy-Item .env.example .env                   # set DATABASE_URL, AUTH_SECRET_KEY, providers
uv run python scripts\migrate.py              # apply migrations/000*.sql
uv run uvicorn app.main:app --reload --port 8000
```

Generate an auth key first: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
The server refuses to start without one.

Open <http://localhost:8000/docs> for the interactive API.

### 3 · Verify it works

```powershell
uv run python scripts\verify_data_layer.py    # must print DATA LAYER VERIFIED
uv run python scripts\verify_auth.py          # must print AUTH + TENANCY VERIFIED (needs the server)
```

### 4 · Frontend (test console)

```powershell
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

A manual console for the current API: health, auth, global memory, projects,
**source upload with live ingestion progress**, project topics, and a one-click
tenancy check. It is a harness for confirming the backend, not the product UI.

### 5 · Providers (`.env`)

Every AI capability has its **own** base URL, API key and model, so you can mix
providers freely — nothing is hard-coded and no code change is ever needed.

| Capability | Base URL | API key | Model |
|---|---|---|---|
| chat (LLM) | `LLM_BASE_URL` | `LLM_API_KEY` | `LLM_MODEL` |
| embeddings | `EMBEDDING_BASE_URL` | `EMBEDDING_API_KEY` | `EMBEDDING_MODEL` |
| speech-to-text | `TRANSCRIPTION_BASE_URL` | `TRANSCRIPTION_API_KEY` | `TRANSCRIPTION_MODEL` |

An unset `EMBEDDING_BASE_URL` / `TRANSCRIPTION_BASE_URL` (and its `_API_KEY`)
inherits the `LLM_*` value, so a single local server only needs the three `LLM_*`
lines. **Models never inherit** — model ids are provider-specific.

Pointing the three at different providers is env-only, e.g. chat on one local
server, embeddings on another, Whisper wherever is cheapest:

```dotenv
# any OpenAI-compatible server (LM Studio, llama.cpp, Ollama, vLLM, …)
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
LLM_MODEL=local-model

# embeddings from a different server — Groq has no /embeddings endpoint
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small

# inherits LLM_BASE_URL / LLM_API_KEY when unset
TRANSCRIPTION_MODEL=whisper-large-v3-turbo
```

Provider shortcuts: **LM Studio** `http://localhost:1234/v1` (key `lm-studio`) ·
**Groq** `https://api.groq.com/openai/v1` (key `gsk_…`, chat + Whisper, **no**
`/embeddings`) · **OpenAI** `https://api.openai.com/v1` (key `sk-…`, all three).

If you point a `*_BASE_URL` at a different host and forget its `*_API_KEY`, the
fallback would send the chat key there and fail with an opaque 401 — so the
server logs a warning at boot, and `GET /health` reports the endpoint each
capability actually resolved to (`llm_base_url`, `embedding_endpoint`,
`transcription`). API keys are never logged or returned.

**Zero-key run:** `scripts/mock_llm.py` fakes all three capabilities on
`http://127.0.0.1:9999/v1`, so the whole app runs with no API keys and no GPU.

Parsing has its own API URL too: `FIRECRAWL_API_URL`. Set `FIRECRAWL_API_KEY=fc-…`
to parse PDFs with Firecrawl (`/v2/parse`, per-page markdown, OCR for scanned
slides). Leave it empty and the local pdf-inspector/anydoc parser is used — same
page-exact citations, fully offline.

### 4 · Rehearse with zero API keys

```powershell
# terminal 1 — fake OpenAI-compatible server
uv run python scripts\mock_llm.py
# terminal 2 — backend pointed at the mock
$env:LLM_BASE_URL="http://127.0.0.1:9999/v1"; $env:LLM_API_KEY="x"; $env:EMBEDDING_PROVIDER="openai"; $env:EMBEDDING_MODEL="mock"
uv run uvicorn app.main:app --port 8000
# terminal 3 — end-to-end check (upload → cited answer → refusal → quiz → mastery update)
uv run python scripts\smoke_test.py
```

To rehearse a **mixed** provider setup offline, run one mock per provider and
point each capability at its own port:

```powershell
# terminal 1 — "Groq" (chat + whisper)
uv run python scripts\mock_llm.py
# terminal 2 — "OpenAI" (embeddings only)
$env:MOCK_LLM_PORT=9998; uv run python scripts\mock_llm.py
# terminal 3 — mixed wiring
$env:LLM_BASE_URL="http://127.0.0.1:9999/v1"
$env:LLM_API_KEY="chat-key"; $env:LLM_MODEL="mock-chat-model"
$env:EMBEDDING_PROVIDER="openai"
$env:EMBEDDING_BASE_URL="http://127.0.0.1:9998/v1"
$env:EMBEDDING_API_KEY="embed-key"; $env:EMBEDDING_MODEL="mock-embed-model"
$env:TRANSCRIPTION_BASE_URL="http://127.0.0.1:9999/v1"
$env:TRANSCRIPTION_MODEL="mock-whisper-model"
uv run uvicorn app.main:app --port 8000
```

---

## How the three hard parts work

### Exact page citations
1. **Firecrawl `/v2/parse`** is called with `parsers: [{type: "pdf", pages: true}]`, so the response carries `pages[] = {pageNumber, markdown}` — markdown per *physical* page. (Fallback: local pdf-inspector per-page markdown (anydoc fallback).)
2. **The chunker never crosses a page boundary.** Every chunk is stored in pgvector with `page` metadata that is exact by construction.
3. **Context is labelled** `[Page N]` per excerpt; the system prompt allows citing *only* those labels.
4. **Citation audit** after generation: any `[Page N]` not in the retrieved set is stripped (a model cannot cite what it did not see); an answer with zero valid citations triggers one corrective retry.
5. **Frontend bridge**: `[Page N]` → `[Page N](#page=N)` → rendered as a chip → `onCite(N)` → `PdfViewer` scrolls to page N and flashes it.

### Strict refusal
- **Retrieval gate**: if the best chunk's cosine distance exceeds `MAX_DISTANCE`, the API returns the refusal sentence *without* calling the LLM.
- **Prompt contract**: one exact refusal sentence, no outside knowledge, no apologies.
- The response carries `grounded: false` so the UI renders it distinctly.

### Adaptive assessment
- At ingestion, **one LLM call** turns the page outline into 4–8 *knowledge components*; chunks are assigned by cosine similarity (+ a bonus when the LLM listed that page) — no per-chunk LLM calls.
- The **learner model** is Bayesian Knowledge Tracing: one `P(mastery)` per (student, topic), updated per answer with `p_slip / p_guess / p_transit`.
- `/quiz` picks the **weakest topic** (ties → least practised), generates MCQs **only from that topic's chunks**, shuffles options server-side and keeps the answer key on the server until `/quiz/answer`.
- Every question carries the page it was written from → wrong answer → "See Page N" → the slide to re-read.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/upload` | multipart PDF → parse → chunk → embed → topics → index |
| `GET` | `/documents`, `/documents/{id}` | registry incl. topics |
| `GET` | `/documents/{id}/file` | original PDF (streamed to the viewer) |
| `DELETE` | `/documents/{id}` | remove vectors, mastery, quizzes, file |
| `POST` | `/chat` | `{document_id, question, history}` → `{answer, citations[], grounded}` |
| `POST` | `/quiz` | `{user_id, document_id, topic_id?}` → MCQs for the weakest topic |
| `POST` | `/quiz/answer` | grade + BKT update → `{correct, explanation, page, p_before, p_after, mastery[]}` |
| `GET` | `/learner/{user}/{doc}` | mastery snapshot |
| `GET` | `/health` | resolved chat / embeddings / transcription endpoints, models, parser |

---

## Demo script (2 minutes)
1. Drop the lecture PDF → "Parsing → chunking → embedding → mapping topics" → split screen appears.
2. Click a starter prompt → answer appears with `Page N` chips → click one → right pane glides to the slide.
3. Ask something off-topic ("Who won the 2018 World Cup?") → *"I can't find this in your uploaded material."*
4. Switch to **Quiz** → "Start adaptive quiz" → answer → mastery bar moves → "See Page N" → re-read the slide → next set targets a different weak topic.
5. Swap `.env` from LM Studio to Groq live: nothing else changes.

## Troubleshooting
- **`chroma-hnswlib` build error on Windows** → use Python 3.11/3.12 (`uv python pin 3.12`).
- **LM Studio `/embeddings` 404** → load an embedding model (e.g. *nomic-embed-text*) in LM Studio, or point `EMBEDDING_BASE_URL` at an OpenAI-compatible embedding server.
- **Refuses too often / too rarely** → tune `MAX_DISTANCE` (higher = more permissive). Set `2.0` to disable the gate and rely on the prompt alone.
- **Scanned PDF, no text** → set `FIRECRAWL_API_KEY`; Firecrawl's `mode: auto` applies OCR.
- **CORS error in the browser** → add your frontend origin to `CORS_ORIGINS`.




