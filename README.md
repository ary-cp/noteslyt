# NotesLyt — source-cited AI tutor + adaptive assessment

> **Track D · Personalized Tutoring & Adaptive Learning**
> A learner creates **projects** ("books"), each with its own corpus of sources —
> lecture videos, textbooks, slide decks, spreadsheets — its own topics, its own chats,
> and its own memory. Every answer cites the exact page, slide or timestamp it came from,
> and an adaptive quiz (Bayesian Knowledge Tracing) targets what you haven't mastered.

Database and uploads stay on your machine. AI requests use the providers configured
in `.env`; fully offline operation requires local providers.

## Status

The active product includes the React workspace, project-scoped streaming chat,
server-side memory, multimodal ingestion, quizzes, exams and flashcards. Images
use direct multimodal embeddings; image chat, quizzes and flashcards send original pixels
to the configured vision-capable chat model. Topic extraction saves a project-level
concept list from the image without creating OCR text or caption embeddings.
Older indexed images get topics when a quiz is generated. Lecture videos keep
their transcript segments and additionally caption one keyframe per few
segments with the vision model (`VIDEO_*` in `.env`) — slide and diagram
content becomes searchable, cited `[Visual M:SS]` text on the same timestamp;
a text-only chat model degrades to transcript-only with a warning. Segment
citations carry their end time, so the viewer replays the cited clip range.
Every PDF page is additionally rendered to PNG and embedded as an
`[Image N]` chunk next to the text chunks, so charts and diagrams stay
retrievable even where the text parse loses them (`PDF_PAGE_IMAGES=false`
disables it; slides need LibreOffice).

The dashboard lives at `/`; `/projects` is the authenticated JSON API. The
`/console` route is a developer harness. Studio summaries, guides and mind maps
currently assemble topic metadata rather than synthesizing the full corpus.

Generation runs in-process; restarting the API can interrupt background jobs.
Flashcards validate source locators and retry malformed output, preserving a
smaller valid set when material cannot support the requested count.

## Layout

```
noteslyt/
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
│   │   ├── video_frames.py    lecture keyframes → vision captions (visual track as citable text)
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
└── frontend/                      Vite · React 19 · TypeScript · Tailwind v4 (active product)
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
docker run -d --name noteslyt-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=StudyCompanion `
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

### 4 · Frontend

```powershell
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

The product UI is the project dashboard and study workspace. Build it with
`npm run build` so FastAPI can serve it at `http://localhost:8000/`.
The optional backend test console lives at `/console`.

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
5. **Frontend bridge**: `[Page N]` renders as a chip with a `filename · label`
   tooltip → clicking opens the source in the sidebar reader, scrolled to the
   cited lines (highlighted) or at least the cited page marker.

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

All project, source, chat and assessment routes require a bearer token.

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/signup`, `/auth/login` | Create account / issue session |
| GET, POST | `/projects` | List / create owned projects |
| GET, PATCH, DELETE | `/projects/{id}` | Read / update / remove project |
| POST | `/projects/{id}/sources` | Queue source ingestion |
| GET | `/projects/{id}/ingest-status` | Poll processing status |
| GET | `/sources/{id}/file`, `/sources/{id}/content` | Original / parsed text |
| GET, POST | `/projects/{id}/chats` | List / create conversations |
| POST | `/chats/{id}/messages`, `/chats/{id}/messages/stream` | Cited answer / SSE |
| POST | `/projects/{id}/quiz`, `/projects/{id}/exam`, `/projects/{id}/flashcards` | Queue Studio generation |
| GET | `/projects/{id}/studio-jobs` | Generation state, results and errors |
| POST | `/quizzes/{id}/answer` | Atomic, idempotent grading and mastery update |
| GET | `/projects/{id}/learner` | Mastery and assessment history |
| GET | `/assessments/{id}/report` | Frozen post-quiz report: score, per-topic deltas, misconceptions, next-up |
| GET | `/projects/{id}/topic-graph` | Prerequisite nodes + edges for the flow map |
| POST | `/projects/{id}/topic-graph/refresh` | Re-infer prerequisite edges (one LLM call) |
| GET | `/health` | Service configuration and database status |

Additional regression checks: `uv run python scripts/verify_regressions.py`.
These verify source-specific citations, bounded uploads, recent memory, summary
checkpoints, concurrent grading, rollback, MCQ validation, saved image topics,
profile evidence and image-based quizzes/flashcards without AI
provider calls. This check and `verify_data_layer.py` remove only their own
randomized test accounts, including on failure; existing user data is retained.

## Evaluation results (2026-10-10, zero-key run)

Environment: `scripts/mock_llm.py` + `EMBEDDING_MODEL=mock` (768-d), local
Postgres, backend on HTTP. The mock judges by deterministic keyword/citation
checks, so treat absolute numbers as smoke signals — the gates are the claim.

- `scripts/smoke_test.py` → **SMOKE TEST PASSED**: pdf/docx/xlsx/mp3 upload →
  ready → project topics → project-scoped retrieval → tenancy → chats, memory,
  nudges, refusal → quiz grading + mastery update → cascade delete.
- `eval/run_ragas.py` → **EVAL PASSED**: faithfulness **1.000** (gate ≥ 0.8),
  refusal accuracy **100%**, answer relevancy 0.388, context precision/recall
  0.50/1.00 on `eval/testset.json`.
- `eval/simulate_students.py` → **SIMULATION PASSED** (seeded, 3 profiles):
  Adv 3/3 correct, mean mastery gain **+0.3657**; Mid 1/3, **+0.0525**;
  Nov 1/3, **+0.0525**. Question repetition **0/9 (0.0%)** vs the < 5% target.
  Isolation: same probe retrieves disjoint chunk sets (6/6/6), and cross-learner
  quiz answers + learner reads are 404.
- Re-runs on real providers will shift the RAGAS numbers; re-run all three
  before quoting metrics anywhere else.

## Demo flow
1. Create a project and upload a lecture PDF → "Parsing → chunking → embedding → mapping topics" → split screen appears.
2. Click a starter prompt → answer appears with numbered citation chips → click one → the source opens in the sidebar reader, scrolled to the cited lines.
3. Ask something off-topic ("Who won the 2018 World Cup?") → *"I can't find this in your uploaded material."*
4. Switch to **Quiz** → "Start adaptive quiz" → answer → mastery bar moves → "See Page N" → re-read the slide → next set targets a different weak topic.
5. Provider changes in `.env` take effect after restarting the API.

## Branding

The product name is **NotesLyt** — "Notes" in the theme text color, "lyt" in
emerald. Logo assets live in `frontend/public/` (`00-mark.svg` through
`10-variant-*.svg`, served as static files; the folder must stay lowercase
`public/` for Vite) and `favicon.svg` derives from `04-app-icon-green.svg`.
The shared lockup is `frontend/src/components/Brand.tsx` (theme-aware mark +
wordmark); login, the app header and the workspace all use it. The live
Postgres database keeps its `StudyCompanion` name — renaming it would orphan
existing data, so only product-facing names changed.

## Troubleshooting
- **LM Studio `/embeddings` 404** → load an embedding model (e.g. *nomic-embed-text*) in LM Studio, or point `EMBEDDING_BASE_URL` at an OpenAI-compatible embedding server.
- **Refuses too often / too rarely** → tune `MAX_DISTANCE` (higher = more permissive). Set `2.0` to disable the gate and rely on the prompt alone.
- **Scanned PDF, no text** → set `FIRECRAWL_API_KEY`; Firecrawl's `mode: auto` applies OCR.
- **CORS error in the browser** → add your frontend origin to `CORS_ORIGINS`.





Quiz questions are prompted to include the full scenario instead of referring to
unseen examples. Studio renders LaTeX in questions, options, explanations and cards.
The learning profile shows overall mastery as a single percentage; the
post-quiz assessment report is the one place weak topics are named, with
likely misconceptions and what to study next. Citation chips open the source
in the sidebar reader with the cited lines highlighted (page marker fallback
guarantees landing on the cited page). Mind maps render a topics
star or, when prerequisite links exist, a dependency flow built from the
project's topic graph. Studio also offers slide decks and a heuristic study
schedule (weakest-first, expanding revisits) alongside summaries and guides.
Settings summarizes saved quiz attempts across projects. Regenerate older quizzes
to apply new question-writing rules.


## Review notes (2026-10-10)

Completed fixes pass the frontend production build, database checks, focused
regressions, auth/tenancy checks and the HTTP smoke test with the local mock.
The mock now understands source-specific quiz context. This is not a claim
that the following remaining gaps are resolved:

- Chat evidence still coerces a string `"false"` to true, unlike the fixed
  short-answer grader. Malformed classifier output can increase mastery.
- `.env.example` still specifies 1536 embedding dimensions while the current
  schema and default configuration use 768. Fresh setup needs a matching
  embedding model and dimension configuration before migrations/startup.
- Numerical questions with zero tolerance (exact answers) are discarded by
  validation because the tolerance parser accepts only positive numbers.
- Studio generation uses in-process background tasks. Interrupted queued or
  generating jobs have no restart recovery and can remain pending.
- Mock-exam time resets when reopened; the countdown is client-side.
- Settings' automatic-indexing and default-model controls are not connected to
  upload/chat behaviour.
- Summary, study guide and mind map currently assemble topic metadata rather
  than generating a full source-grounded document.
- Flashcard self-ratings are local component state, not persistent review history.

These are outstanding work items, not covered by the passing-path test results.
