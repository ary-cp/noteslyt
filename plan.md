# plan.md — Study Companion

Backend-first build plan. Multilingual RAG tutor: users create multiple **projects**,
each with its own corpus of **sources** and its own **chats**, layered on **three
tiers of memory**.

Scope note: we are building the **backend**. The product UI is out of scope for now —
the existing `frontend/` is legacy and *will* break when the API changes (documented
in Phase H). Verification is done through `scripts/smoke_test.py` plus a minimal API
harness, not through a real UI.

---

## 1. Product definition

```
user
 └── project  ("book" — e.g. "Organic Chemistry", "Thermodynamics")
      ├── source  (pdf · docx · pptx · xlsx · md · csv · txt · image · audio · video · …)
      │    └── chunks  (exact locator: page / slide / mm:ss / sheet / row)
      ├── topic  (project-level; covered by many sources)
      │    └── prerequisite edges
      ├── chat   (named conversation; user may create many)
      │    └── messages
      └── project memory  (mastery, coverage, weak topics)
```

Three memory tiers, exactly as specified:

| Tier | Holds | Table |
|---|---|---|
| **Global** | user name, capability level, thinking level, study pattern, goals | `user_profiles` |
| **Project** | topics covered, topics the user struggles with, mastery, study streak | `project_memory` |
| **Chat** | only the current conversation | `chat_messages` |

Key requirements:
- Each project has a **completely separate RAG corpus**. Retrieval must never cross a
  project boundary, ever.
- Source types are **not limited** to a fixed list — a pluggable parser registry, with a
  generic text-extraction fallback for unknown types.
- A project may hold **many chats**; each chat is independently resumable.

---

## 2. Locked decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Local Postgres only, reached via `DATABASE_URL`** over psycopg3 | The Devpost submission requires a public GitHub repo and a demo video — **it does not require a hosted service**. So there is no hosted-database code path to maintain. We kept a DSN rather than hard-coding host/credentials so moving the DB stays a `.env` change. There was previously *no* DSN support at all: `app/db.py` only reached Postgres through a PostgREST client, which cannot talk to a plain Postgres instance. |
| D2 | **Drop the in-memory DB branch entirely** | `app/db.py` hand-duplicated every method for an in-memory twin, and the two had already drifted: `MasteryRecord(**r)` raised `TypeError` on any new column; `save_quiz` stored `created_at` in one branch and not the other; `vectorstore.upsert` dropped unknown metadata keys in one branch and kept them in the other. Removing the twin deletes a whole bug class. **Consequence:** the smoke test needs a running local Postgres (still zero API keys — `scripts/mock_llm.py` fakes every LLM/embedding/transcription call). |
| D3 | **Own `users` table + JWT we issue** (argon2 + PyJWT, HS256) | Today `user_id` is a random string minted into `localStorage` (`frontend/src/lib/user.ts`) with no auth, and `DELETE /documents/{id}` / `GET /documents/{id}/file` have **no ownership check at all** (`routers/documents.py:105-135`). Self-issued tokens need nothing beyond the local Postgres we already require, so there is no external identity dependency. |
| D4 | **Local disk file storage only — no object storage** | Replaces the Cloudinary integration entirely. Files live at `DATA_DIR/uploads/<project_id>/<source_id>.<ext>`. This also fixes a latent bug: the old code hardcoded `<id>.pdf` on disk, which mislabelled every video, audio clip and spreadsheet. On-disk names derive from the source id, so a hostile upload name cannot escape the upload root. |
| D5 | **Global memory = intake form + LLM auto-refinement** | Profile is seeded by an onboarding form (visible, correctable) and then kept current by an LLM extraction pass over conversations. Never a black box; always editable via `PATCH /profile`. |
| D6 | **Frontend is a test harness only** | No router, no `zustand`, no product UI in this plan. Verification = `scripts/verify_data_layer.py` + `scripts/verify_auth.py` + a throwaway harness page. The existing `frontend/` is frozen and will be left broken by the API changes (see Phase H). |

### Deployment shape

Everything runs on one machine. There is no cloud dependency, no object store, no
external auth provider:

```
├── Postgres (localhost:5432, pgvector)   — users, projects, sources, topics, chunks, memory
├── DATA_DIR/uploads/<project_id>/        — original PDFs, decks, videos, audio, spreadsheets
└── an OpenAI-compatible endpoint          — chat / embeddings / transcription (or the offline mock)
```

This is a deliberate simplification, not a shortcut: judge reproducibility is scored
under "System Evaluation" and "Technical Implementation", and "clone → install Postgres →
`scripts/migrate.py` → run" works for anyone.

---

## 3. Current state (verified against the repo)

Working today:
- FastAPI backend, `app/main.py` composition root; single-screen Vite/React frontend.
- PDF ingestion: Firecrawl `/v2/parse` (per-page markdown) or local pdf-inspector/anydoc
  (`app/ingestion/parsers.py`).
- Page-aware chunking — a chunk never crosses a page boundary (`app/ingestion/chunker.py:123-127`).
- RAG with a retrieval gate (`rag.py:113-117`) and a server-side citation audit that
  strips any `[Page N]` not actually retrieved (`rag.py:71-99`).
- Bayesian Knowledge Tracing per `(user_id, document_id, topic_id)` (`app/learner/bkt.py`),
  MCQ generation + grading (`app/learner/quiz.py`).
- Per-capability provider config: `LLM_*` / `EMBEDDING_*` / `TRANSCRIPTION_*` each resolve
  their own base URL + key + model, with boot-time warnings and `/health` reporting the
  resolved endpoint. Mixing Groq chat + OpenAI embeddings + Groq Whisper is `.env`-only.

Gaps that this plan closes:
- **No tenancy.** `document_id` is the only scope. `db.list_documents()` (`db.py:84`) has no
  filter argument at all and returns every document in the deployment. `/health` counts
  globally (`main.py:115`).
- **No auth.** See D3.
- **No chat persistence.** `RAGService.__init__` has no `db` handle (`rag.py:103`); there is
  no message store; history is client-side only and re-sent in full on every request.
- **PDF only.** `routers/documents.py:61-62` hard-returns 415 for anything else; the
  frontend gates on `file.type === "application/pdf"` (`UploadDropzone.tsx:36`).
- **Topics are per-document and not globally unique** — two PDFs both yield a topic `introduction`.
- `topics` / `topic_edges` tables exist in `0001_init.sql:52-64` and are **never read or
  written by any Python code**.
- Dead code: `VectorStore.__init__` accepts `collection_name` and never uses it;
  `db.get_quiz` returns a dict missing `created_at` on the in-memory path.

### Facts to keep in mind while implementing
- `chunk id = f"{document_id}:{chunk_index}"` (`ingestion/pipeline.py:71`) — globally unique
  only because the id is a uuid.
- `db.get_document()` (`db.py:77`) is the *sole* existence check in the whole read path.
- `/quiz/answer` takes **no** `document_id` (`schemas.py:89-93`); its scope is inherited
  entirely from the stored quiz row, so that table must carry the new scope columns.
- `MasteryRecord(**r)` (`db.py:119`) is not tolerant of extra columns — use the explicit
  field-by-field mapping pattern already at `db.py:103-110`.
- `DocumentOut(**doc.__dict__)` (`routers/documents.py:49`) means every new row field must
  be added to `app/schemas.py` **and** `frontend/src/lib/types.ts` together.

---

## 4. Target data model

Replaces `supabase/migrations/0001_init.sql`. There is no live data to preserve — the
`documents` table does not even exist in the currently configured project — so this is a
clean break, not a data migration.

Directory is `migrations/` (plain Postgres), applied in order by a small runner
that records applied versions in `schema_migrations`.

### `migrations/0001_core.sql` — tenancy + corpus
```sql
create extension if not exists vector;
create extension if not exists pgcrypto;   -- gen_random_uuid()

create table users (
    id            uuid primary key default gen_random_uuid(),
    email         text not null unique,
    password_hash text not null,
    display_name  text not null,
    created_at    timestamptz not null default now()
);

create table projects (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references users(id) on delete cascade,
    title       text not null,
    subject     text default '',
    description text default '',
    archived    boolean not null default false,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index on projects(user_id, created_at desc);

create table sources (
    id          uuid primary key default gen_random_uuid(),
    project_id  uuid not null references projects(id) on delete cascade,
    filename    text not null,
    source_type text not null,          -- pdf|docx|pptx|xlsx|md|csv|txt|image|audio|video|other
    status      text not null default 'queued',  -- queued|parsing|embedding|ready|failed
    status_detail text default '',
    parser      text default '',
    units       integer not null default 0,        -- pages/slides/sheets/segments
    duration_s  real,
    chunk_count integer not null default 0,
    file_url    text,
    warnings    jsonb not null default '[]',
    created_at  timestamptz not null default now()
);
create index on sources(project_id, created_at desc);

create table topics (
    id          uuid primary key default gen_random_uuid(),
    project_id  uuid not null references projects(id) on delete cascade,
    slug        text not null,
    name        text not null,
    description text default '',
    created_at  timestamptz not null default now(),
    unique (project_id, slug)
);
create index on topics(project_id);

create table topic_sources (   -- where a project-level topic is actually covered
    topic_id  uuid not null references topics(id) on delete cascade,
    source_id uuid not null references sources(id) on delete cascade,
    locators  jsonb not null default '[]',   -- [{"kind":"page","value":3}, ...]
    primary key (topic_id, source_id)
);

create table topic_edges (
    project_id uuid not null references projects(id) on delete cascade,
    topic_id   uuid not null references topics(id) on delete cascade,
    prereq_id  uuid not null references topics(id) on delete cascade,
    primary key (topic_id, prereq_id)
);

create table chunks (
    id            uuid primary key default gen_random_uuid(),
    project_id    uuid not null references projects(id) on delete cascade,
    source_id     uuid not null references sources(id)  on delete cascade,
    topic_id      uuid references topics(id) on delete set null,
    locator_kind  text not null default 'page',   -- page|slide|segment|sheet|row|section
    locator_value text not null default '',
    page          integer,                        -- kept for page locators (citation compat)
    start_ts      real,
    end_ts        real,
    chunk_index   integer not null,
    heading       text not null default '',
    text          text not null,
    embedding     vector(1536)
);
create index chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops);
create index chunks_project_idx   on chunks(project_id);
create index chunks_source_idx    on chunks(source_id);
create index chunks_topic_idx     on chunks(project_id, topic_id);
```

**Project-scoped retrieval** — `filter_project_id` is **required**; there is deliberately no
"search everything" default (today `document_id=None` in `vectorstore.py:90` means exactly
that, which is a tenancy hole).

```sql
create or replace function match_chunks (
    query_embedding  vector(1536),
    match_count      int,
    filter_project_id uuid,                    -- REQUIRED
    filter_source_ids uuid[] default null,     -- narrow to specific sources
    filter_topic_ids  uuid[] default null      -- narrow to specific topics
)
returns table (
    id uuid, source_id uuid, topic_id uuid, text text,
    locator_kind text, locator_value text, page integer,
    start_ts real, end_ts real, heading text, similarity float
)
language sql stable as $$
    select c.id, c.source_id, c.topic_id, c.text, c.locator_kind, c.locator_value,
           c.page, c.start_ts, c.end_ts, c.heading,
           1 - (c.embedding <=> query_embedding) as similarity
    from chunks c
    where c.project_id = filter_project_id
      and (filter_source_ids is null or c.source_id = any (filter_source_ids))
      and (filter_topic_ids  is null or c.topic_id  = any (filter_topic_ids))
    order by c.embedding <=> query_embedding
    limit match_count;
$$;
```

### `migrations/0002_memory.sql` — the three tiers
```sql
-- Tier 1: global
create table user_profiles (
    user_id          uuid primary key references users(id) on delete cascade,
    capability_level text default 'unknown',   -- beginner|intermediate|advanced|unknown
    thinking_level   text default 'unknown',   -- concrete|analytical|abstract|unknown
    goals            text default '',
    study_pattern    jsonb not null default '{}',  -- session habits, best time, quiz-vs-chat
    strengths        jsonb not null default '[]',
    updated_at       timestamptz not null default now()
);

-- Tier 2: project
create table project_memory (
    user_id          uuid not null references users(id) on delete cascade,
    project_id       uuid not null references projects(id) on delete cascade,
    topic_id         uuid not null references topics(id) on delete cascade,
    p_mastery        real not null default 0.30,
    attempts         integer not null default 0,
    correct          integer not null default 0,
    coverage_status  text not null default 'not_started',  -- not_started|in_progress|mastered
    last_practiced_at timestamptz,
    updated_at       timestamptz not null default now(),
    primary key (user_id, project_id, topic_id)
);

-- Tier 3: chat
create table chats (
    id         uuid primary key default gen_random_uuid(),
    project_id uuid not null references projects(id) on delete cascade,
    user_id    uuid not null references users(id)  on delete cascade,
    title      text not null default 'New chat',
    summary    text default '',        -- rolling summary of messages older than the window
    summarized_through uuid,           -- last message_id folded into `summary`
    created_at timestamptz not null default now(),
    last_message_at timestamptz not null default now()
);
create index on chats(project_id, last_message_at desc);

create table chat_messages (
    id         uuid primary key default gen_random_uuid(),
    chat_id    uuid not null references chats(id) on delete cascade,
    role       text not null check (role in ('user','assistant')),
    content    text not null,
    citations  jsonb not null default '[]',
    grounded   boolean,
    created_at timestamptz not null default now()
);
create index on chat_messages(chat_id, created_at);
```

### `migrations/0003_assessment.sql`
`quizzes` (now carrying `project_id` + `user_id`), `questions` (split out of the current
`jsonb` blob: type, stem, options, answer key, rubric, `topic_id`, source locator,
`stem_embedding vector(1536)` for novelty detection), `assessments`.

`quizzes` must keep `project_id` — `/quiz/answer` (`schemas.py:89-93`) takes no scope
parameter and inherits its scope from the stored row, so that row is the security boundary.

---

## 5. Memory architecture

### Assembly for one chat turn
```
[system] global memory block      <- user_profiles, capped to N facts
[system] project memory block     <- weak topics, coverage, current mastery summary
[system] chat summary             <- rolling summary of turns older than the window
[messages] last N turns verbatim  <- N = MEMORY_CHAT_WINDOW
[context] retrieved chunks        <- project-scoped, with locators
```
Token budget is explicit and configured (`MEMORY_*` in `.env`), not implicit. Global and
project blocks are **always truncated** — memory must never crowd out retrieved context,
which would break grounding.

### Tier 3 — chat memory
- `chats` and `chat_messages` are server-side, so a chat survives reload and device change
  (today impossible — `useChat.ts:23-25` wipes on document change).
- Windowing: keep the last `MEMORY_CHAT_WINDOW` turns verbatim; when the chat exceeds
  `MEMORY_SUMMARIZE_THRESHOLD` messages, fold the older ones into `chats.summary` and set
  `summarized_through`. Summarization is one cheap LLM call.
- The client stops sending `history` entirely. `POST /chat` → `POST /chats/{id}/messages`
  (**breaking change**, §7).
- Query rewriting: today retrieval uses only the current question (`rag.py:111-112`) and
  history is never used to expand it. With server-side history we can rewrite follow-up
  questions ("what about that?" → standalone) before embedding. This materially improves
  multi-turn retrieval and is a prerequisite for the 3-tier design to feel coherent.

### Tier 2 — project memory
- BKT re-keyed from `(user_id, document_id, topic_id)` to `(user_id, project_id, topic_id)` —
  mastery now spans all of a project's sources, which is the whole point of the tier.
- `coverage_status` derived: `not_started` (0 attempts, no chat evidence) → `in_progress` →
  `mastered` (`p_mastery ≥ 0.8` and `attempts ≥ 3`).
- Chat contributes signals: after each grounded turn a small classification pass flags
  correct restatement vs. misconception and applies a bounded BKT nudge.
- `GET /projects/{id}/memory` returns coverage, weakest topics, and study streak for the UI.

### Tier 1 — global memory
- Seeded by `POST /auth/onboarding` (display name, capability, thinking level, goals).
- Maintained by a background extraction pass over recent conversations, writing
  `study_pattern` counters and `strengths`, with `capability_level` changed only on
  confident evidence and never silently downgraded below an explicit user choice.
- Every field is readable and editable at `GET /profile` / `PATCH /profile`.

---

## 6. Source types

`app/ingestion/sources/` becomes a registry: `(extension, mime) → parser → [SourceUnit]`.
A `SourceUnit` is the generalized `ParsedPage`:

```python
@dataclass
class SourceUnit:
    index: int
    locator_kind: str   # page | slide | segment | sheet | row | section
    locator_value: str
    text: str
    start_ts: float | None = None
    end_ts: float | None = None
    assets: list[str] = field(default_factory=list)
```

| Type | Library | Locator | Notes |
|---|---|---|---|
| pdf | existing pdf-inspector / Firecrawl | `page` | unchanged path |
| docx | python-docx | `section` | heading detection seeds topics |
| pptx | python-pptx | `slide` | speaker notes included |
| xlsx | openpyxl | `sheet` (+`row` for wide tables) | header row + typed row text |
| md / txt / csv | stdlib | `section` / `row` | near-zero cost |
| audio / video | transcription client | `segment` (`start_ts`/`end_ts`) | already built (`app/transcription.py`) |
| image | Multimodal image embedding | `image` | configured embedding endpoint required |
| anything else | generic text extraction | `section` | attempts best-effort, records a `warning` |

Citations generalize from `[Page N]` to `[Page 14] [Slide 7] [12:34] [Sheet Operating Costs]`.
The existing audit contract is preserved — a citation is stripped server-side unless that
locator was actually retrieved (`rag.py:71-99`). `CitationOut` becomes
`{locator_kind, locator_value, page?, source_id, heading, snippet}`.

**Invariant, generalized:** the chunker never crosses a locator boundary — one page, one
slide, one segment, one sheet. This is what makes citations exact; do not break it.

---

## 7. API surface

```
# auth
POST   /auth/signup              {email, password, display_name}
POST   /auth/login               {email, password} -> {token, user}
GET    /auth/me
POST   /auth/onboarding          seeds global memory (D5)
GET    /profile                  -> full global memory, human-readable
PATCH  /profile                  user-editable; never overwritten silently

# projects
POST   /projects                 {title, subject, description}
GET    /projects                 own projects + aggregate stats (topic/source counts, mastery)
GET    /projects/{id}            404 unless owned by caller
PATCH  /projects/{id}
DELETE /projects/{id}            cascades sources, chunks, topics, chats, memory
GET    /projects/{id}/memory     tier 2

# sources
POST   /projects/{id}/sources    multipart, multiple files -> queued source rows (202)
GET    /projects/{id}/sources
GET    /sources/{id}             poll `status` until ready|failed
DELETE /sources/{id}
GET    /sources/{id}/file        stream from local disk (correct Content-Type)
GET    /projects/{id}/topics
POST   /projects/{id}/topics/reindex

# chats
POST   /projects/{id}/chats      {title?}
GET    /projects/{id}/chats
GET    /chats/{id}               + message history (paginated)
PATCH  /chats/{id}               rename
DELETE /chats/{id}
POST   /chats/{id}/messages      {content} -> grounded answer + citations
                                        <-- replaces POST /chat; client no longer sends history

# assessment
POST   /projects/{id}/quiz       {topic_id?, n_questions?}
POST   /quizzes/{quiz_id}/answer {question_id, answer}   (MCQ index | short text | number)
GET    /projects/{id}/learner
GET    /assessments/{id}/report

GET    /health                   resolved provider endpoints + db/migration status
```

Every project-scoped route resolves ownership through `get_current_user` and returns 404
(not 403) for another user's project, so ids are not enumerable.

**Ingestion is asynchronous.** A 60-page PDF plus a lecture video takes minutes; the current
synchronous `/upload` cannot serve multi-source projects. `POST /sources` writes a
`status='queued'` row and returns `202`; a background worker advances
`queued → parsing → embedding → ready|failed` and the client polls `GET /sources/{id}`.
`BackgroundTasks` for v1 — note Celery/RQ as the scale-up path.

---

## 8. Phases

### Phase A — Data layer & local Postgres (foundation, blocking)
1. **pgvector locally.** `create extension vector` fails unless the extension files are
   installed. Document both routes: the `pgvector/pgvector:pg17` Docker image, or the OS
   package. This is the single most likely thing to block setup.
2. `DATABASE_URL` in `app/config.py`; fix the dead line at `.env.example:107`. Add
   `MIGRATIONS_DIR`. Boot fails loudly if the DSN is unreachable or the `vector` extension
   is missing.
3. `scripts/migrate.py` — ordered `migrations/*.sql`, tracked in `schema_migrations`.
   Plain runner, no Alembic (the repo already hand-writes SQL and Alembic is dead weight here).
4. `app/db.py` → psycopg3 `ConnectionPool`; repositories split into `app/repo/{users,
   projects,sources,topics,chunks,chats,memory,quizzes}.py`. **Delete the in-memory branch**
   (D2). Row→dataclass mapping is explicit field-by-field, tolerant of extra columns
   (replacing `MasteryRecord(**r)`, `db.py:119`).
5. `app/vectorstore.py` → pgvector via `match_chunks` with the required `project_id`.
6. Embedding-dimension guard: `vector(1536)` is baked into the migration while
   `EMBEDDING_DIM` is config — assert they agree at boot and fail with a clear message.

### Phase B — Auth ✅ DONE
7. `users` table, argon2-cffi hashing, PyJWT issue/verify, `AUTH_SECRET_KEY` + expiry config.
8. `get_current_user` dependency; signup/login/me/onboarding.
9. Ownership enforced on **every** mounted route. Fixes the live holes: `DELETE /documents/{id}`
   and `GET /documents/{id}/file` had no ownership check (`routers/documents.py:105-135`);
   both routers are unmounted until Phase C, so the hole is closed by construction.
10. Retire `frontend/src/lib/user.ts`'s anonymous id from the request contract.

Delivered: `app/deps.py` (JWT + `get_current_user` + `require_project`/`require_source`),
`app/routers/auth.py`, `app/routers/profile.py`, `app/routers/projects.py`,
`app/repo/profile.py`. Server refuses to boot without a valid `AUTH_SECRET_KEY` or
with an unreachable database. Verified by `scripts/verify_auth.py` (44 checks).

### Phase C — Multi-project tenancy ✅ DONE
11. ✅ Schema applied; old `documents`/`mastery` tables retired (renamed to `sources` /
    `project_memory`).
12. ✅ Project CRUD + cascade delete, including a disk sweep so deleting a project
    cannot leak uploaded files. `/health` reports the DB target and migration state.
13. ✅ Ingestion re-keyed to `project_id` + `source_id`. Chunk ids are `uuid5(source_id,
    index)` — deterministic, so re-ingest overwrites instead of duplicating.
14. ✅ Topics are project-level with `(project_id, slug)` as the natural key and a
    `topic_sources` join. A second source covering the same concept reuses the topic row
    and merges its locators.
15. ✅ Async ingestion: `POST /projects/{id}/sources` → `202` + `queued` row, work on a
    `BackgroundTask`, status polled via `GET /projects/{id}/ingest-status`.
16. ✅ `FileStorage` local-only (see D4).

Delivered: `app/ingestion/units.py` (`SourceUnit`), `registry.py` (parser dispatch),
rewritten `chunker.py` (locator-aware), `topics.py` (locator-aware, project-mergeable),
`pipeline.py` (`ingest_source`), `app/repo/sources.py` status methods,
`app/routers/sources.py`. Deleted the un-importable document-scoped code
(`routers/{documents,chat,quiz}.py`, `learner/quiz.py`); chat and assessment are
rebuilt project-scoped in Phases E and F.

Verified by `scripts/smoke_test.py` (SMOKE TEST PASSED) against two mock servers,
plus a direct check that a second source reuses all existing topic rows.

### Phase D — Source types & citations
17. Parser registry + `SourceUnit`; implement docx, pptx, xlsx, md, csv, txt.
18. Audio/video → `Transcriber` → `segment` units with `start_ts`/`end_ts`.
19. Generalized citation labels + `audit_citations` over all locator kinds, preserving the
    strip-if-not-retrieved contract.
20. Images/VLM captions behind a config flag (cost + latency gate).
21. Viewer abstraction in the harness: PDF / `<video>` seek to `#t=` / `<audio>` / text.

### Phase E — Three-tier memory
22. `chats` + `chat_messages`; server-side history; `POST /chats/{id}/messages`.
23. Chat windowing: recent-`N`-verbatim + rolling summary (`chats.summary`,
    `summarized_through`).
24. Query rewriting from chat history before embedding — fixes the "what about that?" class
    of retrieval miss.
25. Global memory: `user_profiles`, onboarding, bounded LLM refinement, `GET/PATCH /profile`.
26. Project memory: BKT re-keyed to project scope, `coverage_status`, chat-derived nudges,
    `GET /projects/{id}/memory`.
27. Memory assembly with explicit token budgeting (`MEMORY_*`).

### Phase F — Adaptive assessment
28. Quiz scope moves document → project. Question types: MCQ, short-answer (LLM rubric
    grading against the source chunk), numerical (tolerance).
29. Novelty via `questions.stem_embedding` — exclude cosine ≥ 0.9 near-duplicates per user.
30. Answer verification: independent second LLM call validates the key + explanation against
    the source; failures dropped and regenerated, rate logged.
31. Cited feedback on every answer; diagnostic cold start; post-assessment report.

### Phase G — Evaluation
32. `eval/testset.json` over the team's real material, spanning multiple sources per project,
    including off-material queries that must be refused.
33. `eval/run_ragas.py` — faithfulness, answer relevancy, context precision/recall.
    Targets: faithfulness ≥ 0.8, refusal accuracy 100%.
34. `eval/simulate_students.py` — synthetic profiles across **different projects**; report
    mastery gain, question repetition rate (< 5%), and — the key result — that retrieval
    never leaks across project boundaries.

### Phase H — Verification harness, docs, submission
35. **Backend is the deliverable.** Verification:
    - `scripts/verify_data_layer.py` — migrations, ownership, project-scoped retrieval, tenancy, cascade
    - `scripts/verify_auth.py` — signup/login, tokens, cross-user access over HTTP
    - `scripts/smoke_test.py` — full ingest → chat → quiz → mastery flow (from Phase C)
    - `scripts/api_probe.py` — manual endpoint poking
    - A **minimal** harness page for visual/manual confirmation only. Not a product UI.
36. The existing `frontend/` is **left frozen and knowingly broken** by the
    `POST /chat` → `POST /chats/{id}/messages` change and the tenancy re-key. Do not spend
    effort porting it before the backend is done; `frontend/src/lib/types.ts` stops mirroring
    `app/schemas.py` from Phase C onward and the AGENTS.md invariant is updated to say so.
37. `docs/architecture.md` — tenancy model, memory assembly, locator/citation contract,
    provider config.
38. **Submission pack** (the Devpost deliverables):
    - Make the repo public, push everything to `main`, add a LICENSE.
    - README written for a judge arriving cold: what it does, architecture, setup from a
      clean clone, the `.env` table, honest per-phase status, and the eval results table.
    - Seed the demo corpus (MIT OpenCourseWare / NPTEL material) via a script so the demo
      is reproducible.
    - Demo video: screen recording of the local app, 3–10 min, showing upload → cited chat
      → refusal → assessment → report.

---

## 9. Verification

Backend-first. The frontend contributes nothing beyond manual confirmation.

```powershell
# 0. one-time: local Postgres with pgvector
docker run -d --name study-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=StudyCompanion `
  -p 5432:5432 pgvector/pgvector:pg17

# 1. schema
uv run python scripts\migrate.py

# 2. verify the data layer (no server needed)
uv run python scripts\verify_data_layer.py      # must print DATA LAYER VERIFIED

# 3. fake every external capability (no API keys, no GPU)
uv run python scripts\mock_llm.py               # $env:MOCK_LLM_PORT=9998 for a 2nd "provider"

# 4. backend
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/StudyCompanion"
$env:AUTH_SECRET_KEY="<64 chars — see .env.example>"
$env:LLM_BASE_URL="http://127.0.0.1:9999/v1"; $env:LLM_API_KEY="x"; $env:EMBEDDING_MODEL="mock"
uv run uvicorn app.main:app --port 8000

# 5. auth + tenancy over HTTP
uv run python scripts\verify_auth.py             # must print AUTH + TENANCY VERIFIED

# 6. full end-to-end (from Phase C)
uv run python scripts\smoke_test.py             # must print SMOKE TEST PASSED
```

`scripts/smoke_test.py` grows to cover, in order:
signup/login → onboarding → create project A and B → upload pdf + docx + xlsx + mp3 into A →
poll to `ready` → **assert A's retrieval cannot see B's chunks** → create 2 chats in A, assert
isolated histories → assert global memory updated → assert project memory/mastery updated →
quiz + grade → short-answer grading → citation audit rejects an injected fake page →
delete project A and assert cascade.

Per phase:
- A–F: smoke test must print `SMOKE TEST PASSED` before a phase is done.
- G: `uv run python eval/run_ragas.py`, `uv run python eval/simulate_students.py`.
- Any `app/schemas.py` change: `cd frontend; npm run typecheck` only while the legacy
  frontend still compiles (see D6 / Phase H.36).

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| **`pgvector` not installed on local Postgres** — silently blocks everything | Verified at boot with an actionable error; document both install routes in the README |
| **Lost files if the demo machine is wiped** | `data/` is gitignored by design; the seed script must be re-runnable, and the demo corpus should be fetched from a public source (MIT OCW / NPTEL) rather than checked in |
| **`vector(1536)` vs actual `EMBEDDING_DIM`** — wrong dims fail at insert, not at boot | `scripts/migrate.py` refuses to run if they disagree; documented in `.env.example` |
| **Cost/latency on long video** — transcription + embedding a 90-min lecture is slow | Per-type caps (`MAX_VIDEO_MB`); transcription stays API-per-capability so it can point at a local model for the demo |
| **HNSW + post-filter by project** can lose recall on a large table | Fine at hackathon scale; if it grows, switch to ivfflat with a per-project partitioner |
| **Memory tiers crowding out retrieved context** would silently break grounding | Explicit token budget; global/project blocks always truncated (Phase E.27) |
| **Deleting the in-memory branch breaks the zero-key path** as previously documented | Accepted (D2); smoke test needs a local Postgres. `AGENTS.md` verification notes updated |
| **Legacy `frontend/` breaks** on the chat/tenancy API change | Intentionally frozen (D6, Phase H.36) — not a regression we spend time on |
| **Project memory fragmenting per upload** if topics stay per-source | Non-negotiable: `topics` are project-level with a `topic_sources` join (Phase C.14) |
| Async ingestion needs a real worker eventually | `BackgroundTasks` is fine at demo scale; Celery/RQ noted as the path up |

## Invariants (do not break)

1. A chunk never crosses a locator boundary (page / slide / segment / sheet).
2. A citation is stripped server-side unless that locator was actually retrieved.
3. Retrieval always takes a required `project_id`. No cross-project search, ever.
4. `frontend/src/lib/types.ts` mirrors `app/schemas.py` — **only while the legacy frontend
   is still being compiled** (see D6).
5. `PARSER=auto|firecrawl|local` stays the PDF parser switch; Firecrawl only with a key.
6. Every capability (`LLM_*`, `EMBEDDING_*`, `TRANSCRIPTION_*`, `FIRECRAWL_*`) keeps its own
   base URL / key / model, swappable without code changes.
7. The smoke test must keep passing with **zero API keys** (local Postgres + `mock_llm.py`).
