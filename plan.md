# plan.md — Study Companion (Multimodal AI Hackathon 2026, Track D)

Personalized tutoring & adaptive learning over your own course material, with
source-cited answers, an adaptive learner model, and framework-based evaluation.

## Current state (verified against the repo)

Existing (root, uv-managed):

- FastAPI backend at `app/` (`app/main.py` composition root).
- PDF ingestion: Firecrawl `/v2/parse` (per-page markdown) when `FIRECRAWL_API_KEY` is set,
  else local **pdf-inspector** with **anydoc** fallback (`app/ingestion/parsers.py`).
- Page-aware chunking (a chunk never crosses a page boundary) → ChromaDB (`app/ingestion/chunker.py`, `app/vectorstore.py`).
- RAG chat with strict grounding: retrieval gate, prompt contract, citation audit that strips
  `[Page N]` labels not in the retrieved set (`app/rag.py`).
- Bayesian Knowledge Tracing per (user, topic) + adaptive MCQs + grading (`app/learner/`).
- SQLite registry (documents, mastery, quizzes) + `app/config.py` env-driven settings.
- React/Vite/TS frontend in `frontend/`: PDF viewer with page-jump on citation chips,
  chat, quiz panel. `frontend/src/lib/types.ts` mirrors `app/schemas.py`.
- `scripts/smoke_test.py` + `scripts/mock_llm.py` give a zero-key end-to-end check.

Missing vs. challenge requirements:

- Video / slide-deck / textbook ingestion; timestamp citations; image/diagram extraction.
- Topic & prerequisite graph; subtopics.
- Short-answer & numerical questions, student-chosen scope, question novelty, answer verification, post-assessment report.
- Diagnostic intake for new students; mastery updated from conversations.
- RAGAS-based evaluation + simulated-student personalization study.
- Optional features: topic flow map, revision material, study schedule.

## Invariants (do not break)

- Chunker never crosses a page/slide/segment boundary — citations depend on it.
- Citations are stripped server-side unless the page/timestamp was actually retrieved.
- `frontend/src/lib/types.ts` changes land in the same commit as `app/schemas.py`.
- `PARSER=auto|firecrawl|local` stays the parser switch; Firecrawl only activates with a key.
- Zero-key smoke test must keep passing via `scripts/mock_llm.py`.

## Phase 1 — Multimodal knowledge base

1. **Unified source model**: refactor `app/ingestion/pipeline.py` around `SourceUnit`:
   `{source_type: pdf|slides|video, doc_id, topic_ids[], location: page|slide|start_ts, content, assets[]}`.
   Chunks keep exact location metadata (`page` | `slide` | `start_ts`,`end_ts`).
2. **Slide decks**: `.pptx` ingestion with python-pptx → per-slide text; `slide` number plays the role of `page`; slide images rasterized for figure captions.
3. **Textbooks**: same PDF path as slides (pdf-inspector → anydoc), plus chapter-heading detection for topic seeding.
4. **Video** (API-first, hosting-safe):
   - No bundled whisper dependency — the deploy target stays light (serverless/free tier safe).
   - New config in `app/config.py` / `.env.example`:
     - `TRANSCRIPTION_PROVIDER=api|local` (default `api`)
     - API mode: OpenAI-compatible `POST /audio/transcriptions` with
       `TRANSCRIPTION_BASE_URL` (defaults to `LLM_BASE_URL`),
       `TRANSCRIPTION_API_KEY` (defaults to `LLM_API_KEY`),
       `TRANSCRIPTION_MODEL`.
       Documented preset: **Groq** (`https://api.groq.com/openai/v1`, `whisper-large-v3-turbo`, free tier).
       Same wire format works with OpenAI and LM Studio.
     - Local mode: point `TRANSCRIPTION_BASE_URL` at a self-hosted faster-whisper-server or
       whisper.cpp `server` (both expose `/v1/audio/transcriptions`) — env-only switch, no code changes.
   - Timed segments `(start, end, text)` feed the same chunker → `[mm:ss]` citations.
   - Keyframes extracted with ffmpeg every N s; frames captioned by the configured vision LLM.
5. **Images/diagrams/figures** (PDF pages, slide images, video keyframes): rasterize → VLM caption
   (OpenAI-compatible vision endpoint via configured `LLM_*` base URL, or local) → caption stored as
   chunk text linked to the figure's page/slide/timestamp.
6. **Topics, subtopics, prerequisites**: extend `app/ingestion/topics.py` — one LLM call per document
   produces topics with subtopics and prerequisite edges; stored in new SQLite tables `topics`/`topic_edges`;
   every chunk tagged with topic ids. New endpoints: `GET /documents/{id}/topics`, `GET /topics/{id}`.

## Phase 2 — Grounding & citations

7. Generalized citation labels: `[Page 14]`, `[Slide 7]`, `[12:34]`.
   - `app/rag.py` citation audit accepts all three label classes but still strips anything not retrieved.
   - `frontend/src/lib/citations.ts` renders page chips, slide chips, timestamp chips.
   - PDF viewer unchanged; new `VideoViewer` (react-player or native `<video>`) seeking to `#t=`.
8. Unsupported queries: keep retrieval gate + one corrective retry; `grounded: false` responses are
   rendered distinctly in the UI (already half-built — finish it).

## Phase 3 — Adaptive assessment

9. **Question types** (`app/learner/quiz.py`): MCQ (exists), short-answer (rubric-graded by LLM,
   must cite source chunk), numerical (answer + tolerance/exact match). Every question tagged:
   `topic_id`, `source` (page/slide/timestamp), `difficulty` (derived from BKT mastery + source heuristics).
10. **Student-chosen scope**: `/quiz` accepts optional `topic_ids[]`; default = weakest topics.
11. **Novelty**: store question stem embeddings per (user, document); exclude near-duplicates
    (cosine ≥ 0.9) from future assessments.
12. **Answer verification**: second, independently-prompted LLM call validates the answer key and
    explanation against the source chunk; failures are dropped & regenerated; rate logged.
13. **Cited feedback**: every `/quiz/answer` returns the source location of the correct answer.
14. **Post-assessment report**: `GET /assessments/{id}/report` — per-topic mastery delta, weakest
    topics, likely misconceptions (LLM clusters wrong-answer explanations), re-read targets with citations.
15. **Cold start**: `POST /learner/{user}/{doc}/diagnostic` — short mixed-difficulty quiz to seed BKT
    priors; optional intake turn in chat seeds topic familiarities.

## Phase 4 — Learner model

16. Keep BKT. Additionally: conversation-driven mastery nudges — in `app/rag.py`, an LLM
    self-check flags correct restatements / misconceptions in the user's message; apply small
    transit/slip updates. Persist in existing `mastery` table.
17. Cold-start priors come from the diagnostic (Phase 3.15); new users default to `BKT_P_INIT`
    when no history exists.

## Phase 5 — Evaluation (RAGAS) & simulated students

18. `eval/` package (outside `app/`):
    - `eval/testset.json`: ~30 items over the team-chosen course material — grounded questions with
      known page/slide/timestamp + off-material queries that must be refused.
    - `eval/run_ragas.py`: hits the real API, scores `faithfulness`, `answer_relevancy`,
      `context_precision`, `context_recall` with RAGAS (judge LLM = configured provider), plus a binary
      refusal-accuracy check on off-material items. Writes `eval/results.md`.
    - Acceptance targets: faithfulness ≥ 0.8, refusal accuracy 100%.
19. `eval/simulate_students.py`: N synthetic profiles (per-topic skill vectors), K sessions each:
    diagnostic → quiz → chat → BKT update. Report mastery gain per session, question repetition rate
    (target < 5%), and coverage balance. Seeded RNG → reproducible.

## Phase 6 — Optional features (picked)

20. **Topic flow map**: `GET /documents/{id}/topic-graph` → React Flow nodes/edges from
    `topic_edges`, nodes colored by current user mastery.
21. **Revision material**: `POST /revision` → weak topics → citation-linked flashcards + markdown brief,
    persisted and viewable in a new frontend tab.
22. **Study schedule**: `POST /schedule` (weak topics + exam date) → day-wise plan using an
    exponential forgetting model on (mastery, last-practiced); exportable as markdown/JSON.

## Phase 7 — Documentation & demo

23. `docs/architecture.md`: ingestion flow, grounding method, citation audit, learner-model math,
    transcription provider switch, eval results table.
24. Demo checklist (3–10 min video): upload a slide PDF **and** a lecture video → chat with page
    chip click and `[mm:ss]` jump → off-material refusal → adaptive quiz incl. short-answer →
    post-assessment report → topic flow map.

## Verification per phase

- Phases 1–4: extend `scripts/mock_llm.py` (fake `/audio/transcriptions`, slide/video fixtures) and
  `scripts/smoke_test.py` (slides + video upload, timestamp citation assertion, short-answer grading,
  repetition check). Must print `SMOKE TEST PASSED`.
- Phase 5: `uv run python eval/run_ragas.py`, `uv run python eval/simulate_students.py`.
- Frontend: `npm run typecheck` on every `app/schemas.py` change.
- Local dev always: `uv run python scripts/smoke_test.py` before finishing a phase.

## Risks / watch-outs

- Transcription: API default keeps hosting free-tier-safe; local whisper is opt-in via env only.
- VLM captioning adds ingestion latency — caption once, cache by content hash.
- RAGAS needs a judge LLM — reuse the configured provider; no new required keys.
- `ffmpeg` must be available for keyframe extraction (document it; degrade gracefully if absent).
- Keep the zero-key path working: every new external call must be faked in `scripts/mock_llm.py`.
