# Study Companion — source-cited AI tutor + adaptive assessment

> **Track D · Personalized Tutoring & Adaptive Learning**
> Upload lecture slides → ask anything → every answer cites the exact page and the PDF jumps there → an adaptive quiz (Bayesian Knowledge Tracing) targets what you haven't mastered.

```
study-companion/
├── app/
│   ├── main.py                composition root: builds services, mounts routers
│   ├── config.py              .env → typed Settings (LLM / embeddings / Firecrawl / RAG / BKT)
│   ├── llm.py                 OpenAI-SDK clients with dynamic BASE_URL + API_KEY, local-embedding fallback
│   ├── ingestion/
│   │   ├── parsers.py         Firecrawl /v2/parse (per-page markdown) ⇄ pdf-inspector/anydoc fallback
│   │   ├── chunker.py         page-aware chunks (a chunk never crosses a page ⇒ exact citations)
│   │   ├── topics.py          knowledge components: 1 LLM call + nearest-centroid assignment
│   │   └── pipeline.py        parse → chunk → embed → topics → ChromaDB
│   ├── vectorstore.py         ChromaDB (persistent, cosine)
│   ├── rag.py                 strict grounded answers: retrieval gate · prompt contract · citation audit
│   ├── learner/
│   │   ├── bkt.py             Bayesian Knowledge Tracing (4 params, 20 lines)
│   │   └── quiz.py            weakest-topic MCQ generation + server-side grading
│   ├── db.py                  SQLite: documents · mastery · quizzes
│   ├── schemas.py             Pydantic API contract (mirrored in frontend/src/lib/types.ts)
│   ├── deps.py                FastAPI dependency accessors
│   └── routers/               documents.py (/upload) · chat.py (/chat) · quiz.py (/quiz, /learner)
├── scripts/                   mock_llm.py · make_sample_pdf.py · smoke_test.py
├── .env.example
└── frontend/                      Vite · React 19 · TypeScript · Tailwind v4
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

### 1 · Backend

```powershell
uv sync                         # creates .venv, installs from uv.lock
Copy-Item .env.example .env     # then edit: pick ONE provider block
uv run uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/docs> for the interactive API.

### 2 · Frontend

```powershell
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

### 3 · Provider presets (`.env`)

| Provider | LLM_BASE_URL | Embeddings |
|---|---|---|
| **LM Studio** (local, free) | `http://localhost:1234/v1` · key `lm-studio` | `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5` (load it in LM Studio) |
| **Groq** (fast cloud Llama) | `https://api.groq.com/openai/v1` · key `gsk_…` | `EMBEDDING_PROVIDER=local` — Groq has no embeddings API; MiniLM runs on CPU (~80 MB one-time download) |
| **OpenAI** | `https://api.openai.com/v1` · key `sk-…` | `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL=text-embedding-3-small` |

Set `FIRECRAWL_API_KEY=fc-…` to parse PDFs with Firecrawl (`/v2/parse`, per-page markdown, OCR for scanned slides). Leave it empty and the local pdf-inspector/anydoc parser is used — same page-exact citations, fully offline.

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

---

## How the three hard parts work

### Exact page citations
1. **Firecrawl `/v2/parse`** is called with `parsers: [{type: "pdf", pages: true}]`, so the response carries `pages[] = {pageNumber, markdown}` — markdown per *physical* page. (Fallback: local pdf-inspector per-page markdown (anydoc fallback).)
2. **The chunker never crosses a page boundary.** Every chunk is stored in ChromaDB with `page` metadata that is exact by construction.
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
| `GET` | `/health` | active provider / model / parser |

---

## Demo script (2 minutes)
1. Drop the lecture PDF → "Parsing → chunking → embedding → mapping topics" → split screen appears.
2. Click a starter prompt → answer appears with `Page N` chips → click one → right pane glides to the slide.
3. Ask something off-topic ("Who won the 2018 World Cup?") → *"I can't find this in your uploaded material."*
4. Switch to **Quiz** → "Start adaptive quiz" → answer → mastery bar moves → "See Page N" → re-read the slide → next set targets a different weak topic.
5. Swap `.env` from LM Studio to Groq live: nothing else changes.

## Troubleshooting
- **`chroma-hnswlib` build error on Windows** → use Python 3.11/3.12 (`uv python pin 3.12`).
- **LM Studio `/embeddings` 404** → load an embedding model (e.g. *nomic-embed-text*) in LM Studio, or set `EMBEDDING_PROVIDER=local`.
- **Refuses too often / too rarely** → tune `MAX_DISTANCE` (higher = more permissive). Set `2.0` to disable the gate and rely on the prompt alone.
- **Scanned PDF, no text** → set `FIRECRAWL_API_KEY`; Firecrawl's `mode: auto` applies OCR.
- **CORS error in the browser** → add your frontend origin to `CORS_ORIGINS`.

