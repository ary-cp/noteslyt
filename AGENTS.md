# AGENTS.md

NotesLyt: FastAPI backend at repo root (`app/`) and production Vite/React
frontend in `frontend/`. Treat both as active product code: keep API contracts
typed in `frontend/src/lib/types.ts` and `api.ts`, and verify frontend changes
with `npm run build`.

## Commands (use uv, never pip)

- Install/sync backend env: `uv sync` (creates root `.venv`; `.python-version` pins 3.12)
- Apply DB migrations: `uv run python scripts/migrate.py` (`--status`, `--reset`)
- Run API: `uv run uvicorn app.main:app --reload --port 8000`
- Frontend: `cd frontend; npm install; npm run dev` (typecheck: `npm run typecheck`)
- There is no pytest/lint config in this repo; do not invent one.

## Verification

Data layer (no server needed, needs a running local Postgres):

    uv run python scripts/verify_data_layer.py    # must print DATA LAYER VERIFIED

Auth + tenancy over HTTP (needs a running server, and `AUTH_SECRET_KEY` set):

    uv run python scripts/verify_auth.py          # must print AUTH + TENANCY VERIFIED

Settings layer over HTTP (needs a running server + migrated DB):

    uv run python scripts/verify_settings.py     # must print SETTINGS VERIFIED

Re-embed layer, in-process (creates + drops a scratch `noteslyt_p3test` DB):

    uv run python scripts/verify_reembed.py      # must print REEMBED VERIFIED

SQLite + Chroma layer, in-process (temporary DATA_DIR, no Postgres):

    uv run python scripts/verify_sqlite.py       # must print SQLITE VERIFIED

First-run setup, in-process (needs a PostgreSQL DSN in DATABASE_URL; creates
+ drops scratch `noteslyt_sutest*` DBs):

    uv run python scripts/verify_setup.py        # must print SETUP VERIFIED

These six are the primary backend checks. Together they
cover migrations, argon2 auth, token handling, ownership, project-scoped retrieval,
the tenancy guarantee, cascade deletes, the retrieval gate, the admin
settings layer (first-user-admin, locked-vs-editable keys, encrypted secrets,
hot-reload, drift status), the drift lifecycle (non-blocking degraded boot,
409 guards with revert hints, background re-embed job + force, ground-truth
tracking in `system_meta`), backend parity (SQLite+Chroma matches Postgres
semantics: CRUD, project-scoped retrieval, tenancy, drift, job, overlay,
cascade clearing both homes), and first-run setup (fresh DBs need onboarding,
upgrades are backfilled complete by migration 0009, complete endpoint is
admin-only).

HTTP smoke test — `uv run python scripts/smoke_test.py`. For a zero-key run start
`uv run python scripts/mock_llm.py` first and set
`LLM_BASE_URL=http://127.0.0.1:9999/v1`, `LLM_API_KEY=x`, `EMBEDDING_MODEL=mock`,
`EMBEDDING_MODE=cloud` (mock runs must stay cloud — local mode would download
the vendored engine instead of using the mock).
If your `EMBEDDING_DIM` is not 1536 (e.g. 768 for nomic/EmbeddingGemma), also set
`MOCK_EMBEDDING_DIM` to the same value so the mock emits the right width.
Covers upload → async ingestion → project topics → project-scoped retrieval →
tenancy → cascade delete. Must print `SMOKE TEST PASSED`.

Frontend — `frontend/` is the production UI. Run `npm run build` before shipping;
the FastAPI app serves `frontend/dist` (or `frontend/build`) on the same port
when present. `src/harness/TestConsole.tsx` remains a developer test console.
`scripts/verify_console_flow.py` replays its HTTP sequence against a running server.
Admin server settings live in `src/components/layout/AdminSettings.tsx`
(rendered inside `SettingsModal` for `is_admin` users only); the drift banner
is `src/components/layout/DriftBanner.tsx` (polls `GET /system/settings/status`,
refreshes immediately on any drift-409 via the `noteslyt:drift-change` event).
Secrets are write-only in the UI — the API never returns plaintext.
First-run onboarding is `src/components/layout/SetupWizard.tsx` (rendered from
`App.tsx` when an admin signs in with setup incomplete; `GET/POST
/system/setup/*`, flag in `system_meta`, backfilled by migration 0009).
Model pickers auto-list via admin-only `POST /system/models`
(`{base_url, api_key?}` in the body — never GET, so keys stay out of URL
logs; bare hosts retry with `/v1`).

Evaluation — `eval/` needs the same running server + mock as the smoke test:

    uv run python eval/run_ragas.py        # must print EVAL PASSED
    uv run python eval/simulate_students.py  # must print SIMULATION PASSED

`run_ragas.py` scores `eval/testset.json` (faithfulness, relevancy, context
precision/recall, refusal accuracy; gates are faithfulness ≥ 0.8 and refusal
100%). It uses this repo's own LLM judge, falling back to deterministic
keyword/citation checks when the endpoint cannot judge (the mock) — the method
used is reported per item. `simulate_students.py` runs seeded synthetic
profiles across separate projects and reports mastery gain, question repetition
(< 5%) and cross-project isolation.

## Auth

- `AUTH_SECRET_KEY` signs our HS256 session tokens **and** derives the settings-key
  encryption. When unset, the server generates one and appends it to `.env`
  (gitignored) at boot instead of refusing to start; a too-short key still
  fails loudly. Generate manually with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"` if you prefer.
- We issue our own HS256 JWTs (`app/deps.py`) instead of using an external identity
  provider, so the same code runs on a plain local Postgres with nothing else
  installed (plan.md D3).
- Send `Authorization: Bearer <token>` on every route except `/health`,
  `/auth/signup`, `/auth/login`, `/profile/levels`.
- The first registered user is admin (`users.is_admin`; backfilled to the earliest
  user by migration 0008). Admin-only routes use `require_admin` (403, not 404 —
  the admin surface is not a secret). `GET /system/settings` never returns secret
  plaintext; `PATCH /system/settings` refuses env-locked keys with 422 and
  hot-reloads AI clients via `app/services.py:build_ai_services` (shared by boot
  and PATCH so they cannot diverge). Secrets are Fernet-encrypted (`enc1:`) with
  a key derived from `AUTH_SECRET_KEY`.
- Ownership is checked with `require_project` / `require_source`, which raise **404**
  (never 403) for another user's ids so they are not enumerable.
- `PATCH /profile` uses `ProfileUpdate` (all fields `None` = unchanged). Do not reuse
  `OnboardingRequest` there — its `"unknown"` defaults would overwrite a level the
  learner already chose.

## Database

- **Two backends, one API.** `DATABASE_URL` set (postgresql://…) → local
  Postgres + pgvector (website). Unset → SQLite + ChromaDB under `DATA_DIR`
  (desktop default, zero-install); `sqlite:///...` pins an explicit file.
  `Settings.db_backend` decides; `app/backend.py:create_database` /
  `create_vector_store` are the only construction sites.
- **pgvector is required on the Postgres path.** If `create extension vector`
  fails, the extension files aren't installed: use the `pgvector/pgvector:pg17`
  Docker image or the OS package.
- `DATABASE_URL` is **required for Postgres**; boot selects SQLite without it.
  There is no in-memory fallback (`plan.md` D2).
- Repo SQL stays `%s`-style: the SQLite cursor wrapper (`app/sqlite_db.py`)
  translates placeholders/casts/`now()`, and `app/repo/base.py:to_record`
  rehydrates datetimes/bools/JSON. Truly divergent queries branch on
  `db.backend` (grep `backend ==`); `match_chunks` and `vector(N)` DDL are
  Postgres-only (Chroma is dim-flexible, width truth lives in `system_meta`).
- `EMBEDDING_DIM` is **optional** (experimental branch): unset means auto-detect
  (one `/embeddings` probe at boot wins; fresh migrations are templated to it).
  Set it only to pin a website deployment to a known width. Drift (model swap
  without re-embed) still refuses to boot — see `GET /system/settings/status`.
- Migrations are plain SQL in `migrations/NNNN_name.sql`, applied in filename order and
  tracked in `schema_migrations`.
- Drift never blocks boot (experimental): the server starts degraded, vector
  routes (upload, chat send/stream, quiz/exam/flashcards create, answer) return
  **409** with re-embed/revert hints (`app/deps.py:require_no_drift`, evaluated
  fresh per call), and `POST /system/reembed` (admin, 202 + progress) resizes +
  re-ingests via the shared `app/reembed.py` core (the CLI wraps the same code).
  Ground truth lives in `system_meta` (written on every ingest + job).
  `GET /system/settings/status` never probes unless `?probe=true` — probing
  wakes the embedding server, which evicts a running transcription ingest
  (single-resident local engines).
- Native audio/video modes fall back to transcript (loudly, persisted) when the
  endpoint proves it cannot encode media bytes (`NativeUnsupportedError`:
  text-signature or HTTP 4xx). Transport errors (5xx/timeouts) still fail boot.
- Multimodal wire shapes are per-protocol classes in `app/llm.py`
  (`JinaEmbedder`, `GeminiNativeEmbedder`, both sharing the OpenAI-compatible
  text path); `EMBEDDING_PROTOCOL` (auto/llama_cpp/jina/gemini, admin-editable)
  selects the media shape. Text never leaves the compat endpoint.

## Files

- Uploaded originals live on local disk at `DATA_DIR/uploads/{project_id}/{source_id}.{ext}`.
  `data/` is gitignored — the demo corpus must be re-fetchable, not committed.
- On-disk names derive from the **source id**, never the uploaded filename, so a
  hostile name (`../../etc/passwd`) cannot escape the upload root. The original
  filename is kept in the `sources` row.
- `app/storage.py` owns extension→type→MIME mapping. Do not re-derive it elsewhere.

## Branding

- Product name is **NotesLyt**. The live Postgres database keeps its
  `StudyCompanion` name (existing data); do not rename it.
- Logo suite in `frontend/public/` (served statically; the folder must stay
  lowercase `public/` for Vite). `favicon.svg` derives from
  `04-app-icon-green.svg`.
- Shared lockup: `frontend/src/components/Brand.tsx` — theme-aware mark
  (`02-app-icon-black.svg` on dark, `03-app-icon-white.svg` on light),
  wordmark "Notes" in `var(--text-primary)` + "lyt" in emerald. Reuse it for
  any new header/login surface instead of inventing logos.
- Browser storage keys are `noteslyt:*`; renaming them logs users out.

## Architecture rules

- **All SQL lives in `app/repo/`.** Routers and services never build queries.
- **Retrieval always takes a `project_id`.** `match_chunks` requires it and there is
  no "search everything" default. Never add one — it is the tenancy guarantee.
- Ownership is checked in the repo layer: `get_owned` / `list_for_user` require a
  `user_id`. Return 404 (not 403) for another user's ids so they are not enumerable.
- Topics are **project-level**, not per-file (`topic_sources` links them to sources).
  A topic's natural key is `(project_id, slug)`, so a second source covering the same
  concept reuses the row and merges locators. Do not reintroduce per-source topics.
- Everything downstream of parsing works in terms of a `SourceUnit` and its locator
  (`app/ingestion/units.py`). Adding a file type means writing one parser and
  registering it in `app/ingestion/registry.py` — not touching the chunker, schema,
  pipeline or citation code.

## Ingestion

- `POST /projects/{id}/sources` returns **202** with a `queued` row per file; ingestion
  runs on a `BackgroundTask`. Clients poll `GET /projects/{id}/ingest-status` (cheap)
  or `GET /sources/{id}` (full row) until the status settles.
- `sources.source_type` stores the **format id** (`pdf`, `docx`, `xlsx`, `audio`,
  `video`, …) because the parser registry dispatches on it. Use `media_kind()` to
  derive a viewer class — do not store the coarse kind.
- Chunk ids are `uuid5(source_id, index)` so re-ingesting a source overwrites its
  chunks instead of duplicating them.
- `ingest_source()` never raises for an expected failure; it records the reason on
  the row. Failures must always be readable by the learner.
- `pdf` `docx` `pptx` `xlsx` `csv` `tsv` `md` `txt` `audio` `video` have parsers
  (`app/ingestion/registry.py`); images are indexed with the configured
  multimodal embedding endpoint without generating captions.
  Unregistered types (doc/ppt/xls/zip/…) are refused at upload with a reason.
- Every PDF page is additionally rendered to PNG (`app/ingestion/page_images.py`,
  stored via `FileStorage.page_image_*`, auto-cleaned with the source) and
  embedded as an `image`-kind chunk (`[Image N]`) next to the text chunks —
  retrieval matches both. Chunk indices sit above `PAGE_IMAGE_INDEX_BASE` so
  re-ingests upsert stably. Text-only endpoints ingest text-only with a
  warning; slides need LibreOffice (`soffice`), else text-only. Disable with
  `PDF_PAGE_IMAGES=false`.

## Gotchas

- `.env` lives at repo root (copy from `.env.example`); `app/config.py` reads it via
  pydantic-settings. Each capability has its own `*_BASE_URL`/`*_API_KEY`/`*_MODEL`
  (`LLM_*`, `EMBEDDING_*`, `TRANSCRIPTION_*`, `FIRECRAWL_API_URL`) — mixing providers
  (Groq chat + OpenAI embeddings + Groq Whisper) is env-only, no code changes. Unset
  `*_BASE_URL`/`*_API_KEY` fall back to the `LLM_*` values; `Settings.provider_warnings()`
  fires at boot when a `*_BASE_URL` points at a different host than its missing key.
  A "local (no Firecrawl key)" parser in `/health` is expected, not a bug.
- **`EMBEDDING_DIM` must equal the model's output width *and* the live column.**
  `scripts/migrate.py` compares against the live `chunks.embedding` width when the
  table exists (else the SQL text), and the server refuses to boot on drift.
  After a model swap (e.g. 1536-d OpenAI → 768-d nomic/EmbeddingGemma): set the new
  width, then `uv run python scripts/re_embed.py` (`--check` first) — it resizes the
  column + `match_chunks` and re-ingests every stored file. The mock emits
  `MOCK_EMBEDDING_DIM` (default 1536); ingestion catches model-vs-config drift and
  puts the fix in `sources.status_detail`.
  On experimental, `EMBEDDING_DIM` may be unset (auto-detect); admin overrides live
  in the `app_settings` table (explicit env > DB > default — see
  `app/settings_overlay.py`). Never return secret plaintext from settings endpoints.
- Citations are stripped server-side unless the locator was actually retrieved —
  preserve this contract. Chunkers must never cross a locator boundary (page/slide/
  segment/sheet) or citations break.
- `frontend/src/lib/types.ts` mirrors the active API models in `app/schemas.py`.
  Update the project-scoped types and `frontend/src/lib/api.ts` when backend
  contracts change; legacy types may be removed when their consumers are migrated.
- PowerShell (Windows): no `&&`; use `;`. `curl` is an alias for Invoke-WebRequest —
  use `Invoke-WebRequest -UseBasicParsing`. Assigning `""` to an env var *deletes* it.
- `frontend/` is a separate npm package, not an npm workspace of the root.
