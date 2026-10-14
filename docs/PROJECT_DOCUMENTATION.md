# NotesLyt — Project Documentation

**Version:** 0.1.13 · **Stack:** FastAPI (Python 3.12) + React/TypeScript (Vite) + Tauri desktop

NotesLyt is a source-grounded AI study workspace. Learners upload documents and media
into *projects*, chat with a tutor that cites the uploaded material, and generate
quizzes, timed mock exams, flashcards, summaries, and study guides. An adaptive
learner model (BKT + SM-2 + IRT) tracks mastery per topic and personalises what to
practice next.

---

## 1. Repository layout

```text
app/                    FastAPI backend
  main.py               Composition root (lifespan, routers, frontend serving)
  config.py             Typed Settings from .env (per-capability providers)
  routers/              HTTP surface: auth, profile, projects, sources, chats,
                        quizzes, system  (all SQL lives in app/repo/)
  repo/                 All SQL. Tenancy checks live here (404, never 403).
  ingestion/            Parsers per file type, locator-aware chunker, topics
  learner/              bkt.py (Bayesian Knowledge Tracing), sm2.py (spaced
                        repetition), irt.py (Rasch ability estimate)
  chat.py               Grounded chat service (retrieval → LLM → citation audit)
  quiz.py               Quiz/exam generation, grading, assessment reports
  llm.py                OpenAI-compatible chat/embedding clients + protocols
  drift.py / reembed.py Embedding-width drift detection + background re-embed
  storage.py            Local-disk file storage (uploads, page renders)
migrations/            Ordered Postgres SQL (0001–0011); mirrored for SQLite in
  sqlite/               migrations/sqlite/
frontend/               React + TypeScript + Vite client
  src/features/         chat/ | studio/ (quiz, exam, flashcards, docs) |
                        viewer/ (PDF/media/source reader) | sources/
  src/components/       layout/ (Settings, Personalisation, Learning Profile…),
                        ui/ (Modal, Dropdown, Controls…)
  src/pages/            Dashboard, Workspace, Auth, LearningProfile
  src-tauri/            Tauri desktop shell (bundled backend, installer)
scripts/                migrate.py, verify_*.py, smoke_test.py, mock_llm.py
eval/                   RAGAS-style eval + synthetic student simulation
```

Architecture rules (enforced by convention, see `AGENTS.md`):

- **All SQL lives in `app/repo/`.** Routers/services never build queries.
- **Retrieval always takes a `project_id`.** There is no search-everything
  default — this is the tenancy guarantee.
- **Ownership is checked in the repo layer** (`get_owned` / `list_for_user`
  require `user_id`); foreign ids return **404, never 403**, so ids are not
  enumerable.
- Topics are **project-level** (`topic_sources` links them to sources); a
  second source on the same concept reuses the row by `(project_id, slug)`.
- Everything downstream of parsing works in `SourceUnit`s
  (`app/ingestion/units.py`); adding a file type = one parser registered in
  `app/ingestion/registry.py`.

---

## 2. Deployment profiles

| Profile | Relational DB | Vectors | Fit |
| --- | --- | --- | --- |
| Local / desktop | SQLite (`DATA_DIR/noteslyt.db`) | Chroma (`DATA_DIR/chroma/`) | Single user, zero install |
| Hosted / shared | PostgreSQL 17 + pgvector | pgvector (`chunks.embedding`) | Multi-user server |

Backend selection is automatic: `DATABASE_URL` set → Postgres, unset → SQLite
(`app/backend.py:create_database`, `Settings.db_backend`). Repo SQL is written
`%s`-style; the SQLite cursor wrapper (`app/sqlite_db.py`) translates
placeholders/casts/`now()`, and `app/repo/base.py:to_record` rehydrates
datetimes/bools/JSON. Only genuinely divergent queries branch on `db.backend`.

Uploaded originals live on local disk at `DATA_DIR/uploads/{project_id}/{source_id}.{ext}`
— filenames derive from the **source id**, never the upload name. Every PDF page is
additionally rendered to PNG and embedded as an `image`-kind chunk so charts stay
searchable (`PDF_PAGE_IMAGES=false` disables).

---

## 3. Grounding method (how answers stay cited)

### 3.1 Ingestion pipeline

`POST /projects/{id}/sources` returns **202** with a `queued` row per file;
ingestion runs as a `BackgroundTask` (parallel when every stage is API-backed,
sequential when any local engine is involved):

1. **Parse** — per-format parser → markdown + `SourceUnit`s with locators
   (page / slide / segment / sheet / image). Supported: `pdf docx pptx xlsx csv
   tsv md txt audio video` + images; anything else is refused at upload.
2. **Chunk** — locator-aware chunker. **Chunks never cross a locator boundary**,
   so every citation resolves to an exact page/slide/segment.
3. **Embed** — chunk vectors via the configured embedding endpoint
   (`JinaEmbedder` / `GeminiNativeEmbedder` share the OpenAI-compatible text
   path; `EMBEDDING_PROTOCOL` selects the media wire shape).
4. **Topics** — project-level topic extraction, merged by slug across sources.
5. Chunk ids are `uuid5(source_id, index)`: re-ingesting a source **overwrites**
   its chunks instead of duplicating them. Expected failures are recorded on the
   `sources` row (`status_detail`), never raised.

### 3.2 Chat turn (`app/chat.py:ChatService.send`)

1. Persist user message → rewrite follow-ups into standalone questions.
2. Retrieve **within one project, READY sources only** (`ready_ids` is the
   boundary; queued/parsing sources can never answer). An optional
   `source_ids` filter narrows further.
3. Assemble tier-1 (profile + learner memo) and tier-2 (weakest-first mastery)
   memory blocks under a strict character budget, then call the chat model with
   numbered `CONTEXT` excerpts (+ attached image/audio evidence where retrieved).
4. **Citation audit** (`app/citations.py`): citations are **stripped server-side
   unless the locator was actually retrieved**. A citation-less answer is
   retried once with a reminder; persistent failure (or no reliable answer)
   yields exactly `I can't find this in your uploaded material.`
5. General knowledge, when used, goes in a separate `**Outside your sources:**`
   paragraph — never cited.

Post-turn bookkeeping (never fails the turn): BKT nudges from chat evidence
(§4.3), rolling conversation summary, observational counters, periodic profile
refinement + learner-memo refresh, and a personalised chat report (§4.5).

### 3.3 Assessment grounding

Quiz/exam questions are generated **from retrieved chunks**, must cite one exact
locator (kind/value/source_id validated locally), and pass shape checks
(MCQ: 4 distinct options + integer key; short-answer: key points + sample;
numerical: value + tolerance). Near-duplicate stems are filtered against the
learner's recent stems (cosine ≥ `quiz_similarity_max` regenerates once, then
drops). If the embedder is unreachable, generation **degrades** (unfiltered,
vector-less) instead of failing the job.

---

## 4. Learner-model approach

Three memory tiers, from slow (who you are) to fast (this conversation):

| Tier | Store | Updated by |
| --- | --- | --- |
| T1 — global profile | `user_profiles` (goals, strengths, habits, learner memo, IRT ability) | Learner edits + LLM refinement |
| T2 — project mastery | `project_memory` per (user, project, topic): BKT `p_mastery`, attempts, SM-2 schedule | Graded answers (full update), chat (bounded nudge) |
| T3 — conversation | `chats.summary` + recent messages | Rolling summariser past `memory_summarize_threshold` |

### 4.1 BKT — Bayesian Knowledge Tracing (`app/learner/bkt.py`)

One number per (learner, topic): **P(learned)**. Each answer applies a Bayesian
evidence step then a learning transition:

```text
correct: P(L|obs) = P(L)(1−slip) / [P(L)(1−slip) + (1−P(L))·guess]
wrong:   P(L|obs) = P(L)·slip    / [P(L)·slip    + (1−P(L))(1−guess)]
P(L)' = P(L|obs) + (1 − P(L|obs)) · transit
```

Defaults: `p_init=0.30, p_transit=0.15, p_slip=0.10, p_guess=0.25` (all tunable in
admin settings). Per-item **calibration** adjusts evidence strength: MCQ guess
rate = 1/N options, constructed responses ≈ 0.4× base; harder items slip more,
so missing a hard question counts less. UI bands: `weak < 0.45 ≤ developing <
0.80 ≤ strong`; `mastered` additionally needs ≥ 3 attempts.

### 4.2 SM-2 — spaced repetition (`app/learner/sm2.py`)

Graded answers also tick SM-2 state (`easiness`, `repetitions`, `interval_days`,
`due_at`) per topic. Chat nudges **never touch** SM-2 (exposure ≠ retrieval
practice). The Studio shows due-review counts; quizzes target weakest-first
topics (diagnostic spread across topics on cold start).

### 4.3 Chat evidence — bounded nudges

Each grounded turn is classified by the LLM against the project topic list
(`CLASSIFY_SYSTEM`); each signal moves mastery **±5% of the remaining distance**,
counters untouched. A nudged row flips `not_started → in_progress`, which is how
"chat exposure without assessment" is detected.

### 4.4 IRT — Rasch ability (`app/learner/irt.py`)

One global number per learner: **ability θ (logits)**, persisted in
`user_profiles.irt_ability`. Item difficulty `d ∈ [0,1]` maps to `b=(d−0.5)·4`;
`P(correct) = sigmoid(θ−b)`; each graded answer takes one gradient step
(`lr=0.35`, clamped to ±3). Bands mirror BKT (`weak / developing / strong`).
IRT complements BKT: BKT says *which topics* are learned, IRT says *how strong
the learner is overall* (used in reports and the Learning Profile header).

### 4.5 Personalised reports (`learner_reports`)

After **every quiz/exam and every grounded chat turn**, the backend freezes a
report row (`learner_reports`: kind, ref summary, BKT deltas, IRT snapshot,
strengths/weaknesses). Quiz/exam reports add an LLM narrative grounded in the
score, mastery movement, ability movement, and misconception points mined from
rubric misses. Reports are best-effort background writes — they never block a
turn or an answer. The **Learning Profile page** (`/learning-profile`,
`GET /profile/overview`) aggregates them with cross-project mastery, weak/strong
topics, and areas of improvement.

### 4.6 LLM learner memo (prompt analyser)

`user_profiles.learning_style_notes` is a 2–4 sentence memo describing *how* the
learner learns (pace, preferred representations, question depth, confidence). It
is rewritten by the LLM from recent questions (every ~6 chat turns + each
refinement pass), editable by the learner, and injected into every tutor prompt
as `How this learner learns: …`. Explicit profile choices are never silently
overwritten: refinement only appends counters/strengths and raises capability on
confident evidence.

### 4.7 Mock exams — deferred grading

Exams run as a **full page** (`/project/:id/exam/:quizId`), not a dialog.
Answers are held **client-side** (plus a localStorage draft, refresh-safe) and
**nothing is graded until Submit** — so mastery, the profile bar, and reports
cannot leak correctness mid-exam, and any answer stays changeable. Exam
conditions: rules gate, accent timer (2 min/question), copy/right-click disabled,
tab switch −5:00, `beforeunload` guard, scores + feedback + assessment report
only after submit. (Practice quizzes keep immediate per-question feedback.)

---

## 5. Auth, tenancy, and admin

- Own HS256 JWTs (`app/deps.py`); first registered user is admin
  (`users.is_admin`, backfilled by migration 0008).
- `Authorization: Bearer <token>` on everything except `/health`,
  `/auth/signup`, `/auth/login`, `/profile/levels`.
- Admin-only routes use `require_admin` (403). `GET /system/settings` never
  returns secret plaintext; `PATCH /system/settings` refuses env-locked keys
  (422) and hot-reloads AI clients. Secrets are Fernet-encrypted (`enc1:`) with
  a key derived from `AUTH_SECRET_KEY` (auto-generated into gitignored `.env`
  when unset).
- First-run onboarding: admin setup wizard (`SetupWizard.tsx`,
  `GET/POST /system/setup/*`, backfilled complete by migration 0009); model
  pickers list via admin-only `POST /system/models` (body, never GET).

## 6. Drift and re-embed

`EMBEDDING_DIM` unset = auto-detect (one probe at boot wins). A model swap
without re-embedding **never blocks boot**: the server starts degraded, vector
routes return **409** with re-embed/revert hints, and `POST /system/reembed`
(admin, 202 + progress) resizes columns and re-ingests via the shared
`app/reembed.py` core. Ground truth lives in `system_meta`; the banner polls
`GET /system/settings/status` (probe is opt-in — waking the embedding server
evicts a running transcription ingest).

## 7. API surface (selected)

```text
/auth/*                    signup, login, me, onboarding
GET|PATCH /profile         global memory (+ quiz_progress, memo, ability)
/profile/overview          Learning Profile aggregation
/profile/reports           recent personalised reports
/projects*                 CRUD + topics + ingest-status + sources upload (202)
/chats*                    project chats, messages + SSE stream, edit/resend
/projects/{id}/quiz|exam|flashcards   → 202 StudioJob, background generation
/quizzes/{id}/answer       grade + BKT/SM-2/IRT update (+ assessment on completion)
/projects/{id}/learner     per-project mastery, weakest, streak, recent quizzes
/assessments/{id}/report   frozen post-assessment report
/system/*                  llama/audio-cpp status, setup, models, settings, reembed
```

Frontend contract mirrors live in `frontend/src/lib/types.ts` + `api.ts`.

## 8. Running and verification

- Zero-install path: `uv sync` → copy `.env.example` → set provider keys →
  `uv run uvicorn app.main:app --reload --port 8000` (SQLite + Chroma, no DB
  server). Desktop installer (Tauri) needs nothing else installed.
- Hosted path: `DATABASE_URL=postgresql://…` (pgvector image `pgvector/pgvector:pg17`)
  → same boot (migrations apply on startup).
- Primary checks: `scripts/verify_sqlite.py`, `scripts/verify_reembed.py`,
  `scripts/verify_data_layer.py`, `scripts/verify_auth.py`,
  `scripts/verify_settings.py`, `scripts/verify_setup.py`, `scripts/smoke_test.py`
  (with `scripts/mock_llm.py`), `eval/run_ragas.py`, `eval/simulate_students.py`,
  frontend `npm run typecheck` + `npm run build`. See `AGENTS.md` for exact
  environments.
