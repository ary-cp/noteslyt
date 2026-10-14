<p align="center">
  <img src="frontend/public/01-primary-logo.svg" alt="NotesLyt" width="360" />
</p>

<p align="center">
  <strong>AI-powered study workspace for understanding, organizing, and reviewing your learning materials.</strong>
</p>

<p align="center">
  Source-grounded chat · searchable documents · quizzes · exams · flashcards · local desktop app
</p>

<p align="center">
  <strong>Just want to study?</strong> Download the installer from
  <a href="https://github.com/ary-cp/noteslyt/releases">GitHub Releases</a> —
  no Python, Node, Docker, or commands needed. Everything below is for running
  from source.
</p>

---

NotesLyt turns study materials into a searchable, citation-aware workspace. Add documents and media to a project, ask questions against selected sources, and create study activities from the material. The application includes a FastAPI service and a React/TypeScript web client, with an optional Tauri desktop application for single-user local use.

## Contents

- [Capabilities](#capabilities)
- [What's new in 0.1.13](#whats-new-in-0113)
- [Install without commands (desktop installer)](#install-without-commands-desktop-installer)
- [Choose a deployment](#choose-a-deployment)
- [Requirements](#requirements)
- [Quick start: local SQLite](#quick-start-local-sqlite)
- [Use PostgreSQL for a hosted or shared server](#use-postgresql-for-a-hosted-or-shared-server)
- [Run the web client separately](#run-the-web-client-separately)
- [Build the desktop application](#build-the-desktop-application)
- [Configure AI providers](#configure-ai-providers)
- [API and authentication](#api-and-authentication)
- [Data, backups, and operations](#data-backups-and-operations)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

## Capabilities

- Organize sources, chats, and learning activity in projects.
- Ask questions with source citations and project-scoped retrieval.
- Parse PDF, DOCX, PPTX, XLSX, CSV, TSV, Markdown, text, audio, and video sources.
- Generate quizzes, timed mock exams, flashcards, summaries, and study guides.
- Adaptive learner model: BKT mastery per topic, SM-2 spaced repetition, and an
  IRT ability estimate, with a personalised report after every quiz, exam, and
  conversation (see [Project Documentation](docs/PROJECT_DOCUMENTATION.md)).
- Dedicated Learning Profile page: mastery across projects, weak/strong topics,
  areas of improvement, and report history.
- Strict exam mode: full-page paper, deferred grading (no correctness leaks),
  free answer changes, no-copy lock, tab-switch time penalty, end-only reveal.
- Personalisation settings: theme, accent color, chat defaults, auto-indexing,
  and an LLM-maintained "how you learn" memo that feeds every tutor reply.
- Configure chat, embedding, and transcription providers independently.
- Run local embedding and transcription engines when configured; use compatible hosted APIs when preferred.
- Use a local desktop application backed by on-device SQLite and Chroma, or run the web application with PostgreSQL and pgvector.

## What's new in 0.1.13

- **Personalisation tab**: Appearance, Sources, Shortcuts, and Learning profile
  merged into one settings tab; accent color picker (presets + custom color).
- **Learning Profile page** (`/learning-profile`): cross-project mastery, weak
  vs strong topics, areas of improvement, IRT ability, and personalised reports.
- **BKT + IRT learner model**: per-answer ability updates, LLM narratives on
  every assessment, LLM-maintained learning-style memo in the tutor prompt.
- **Strict mock exams**: separate page, deferred grading, rules gate, accent
  timer, copy lock, −5:00 tab-switch penalty, answers revealed only at submit.
- **Study guides take a topic dropdown** instead of free-text filter.
- **Model picker fix**: admin settings refetch on key change and explain the
  hidden saved key (notably Gemini's `/v1beta/openai` endpoint, which 404s
  without its key).
- **Robust generation**: quiz/exam generation degrades instead of failing when
  the embedding endpoint is unreachable mid-job.
- Full write-up in [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md)
  and [docs/RELEASE_0.1.13.md](docs/RELEASE_0.1.13.md).

## Install without commands (desktop installer)

If you don't want to touch a terminal, database, or dependency:

1. Open [github.com/ary-cp/noteslyt/releases](https://github.com/ary-cp/noteslyt/releases).
2. Download the installer for your OS from the latest release (Windows: `.msi`
   or `.exe`; other platforms as published) under `frontend/src-tauri/target/release/bundle/`
   equivalents attached to the release.
3. Run it, launch **NotesLyt**, create an account — the first account is admin —
   and complete the setup wizard (paste your chat/embedding provider keys).

The desktop app bundles the backend, uses local SQLite + Chroma storage, and
needs no Python, uv, Node, Rust, Docker, or ffmpeg. Local AI engines download
on first use only if you enable local modes. To keep your data on uninstall,
back up the app-data directory first (see [Data, backups, and operations](#data-backups-and-operations)).

## Choose a deployment

| Profile | Relational database | Vector search | Best fit |
| --- | --- | --- | --- |
| Local web development or desktop | SQLite | Chroma | One user or a local development environment; no database service to install |
| Hosted or shared web service | PostgreSQL | pgvector | Multiple accounts using one centrally operated service |

Both profiles keep uploaded originals on the server's local filesystem under `DATA_DIR/uploads`. In a hosted deployment, mount persistent storage for `DATA_DIR` and protect it like the database. SQLite and Chroma are convenient for a single local instance; use PostgreSQL and pgvector when serving multiple users from a shared server.

**Running tutorial — pick one:**
- *No terminal, just study:* [install the desktop app](#install-without-commands-desktop-installer) (SQLite inside, zero setup).
- *From source, one user:* [Quick start: local SQLite](#quick-start-local-sqlite) — no database server; leave `DATABASE_URL` unset.
- *From source, shared server:* [Use PostgreSQL](#use-postgresql-for-a-hosted-or-shared-server) — set `DATABASE_URL`, everything else is identical.

The full architecture, grounding, and learner-model write-up lives in [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md).

## Requirements

### Backend

- Python 3.12 (the repository pins the version in `.python-version`).
- [uv](https://docs.astral.sh/uv/) for Python environment and dependency management.
- An AI provider for chat and embeddings. A local provider can be used instead of a cloud API; see [Configure AI providers](#configure-ai-providers).
- PostgreSQL 17 with pgvector only when using the PostgreSQL profile.

### Frontend and desktop builds

- Node.js and npm. Use the version supported by the installed Tauri CLI.
- Rust and the platform's native build tools for Tauri. On Windows, install Visual Studio Build Tools with the C++ workload and the WebView2 runtime.
- For audio/video parsing in dev, fetch the pinned builds once:
  `npm run ffmpeg:fetch --prefix frontend` (or set `FFMPEG_PATH` / `FFPROBE_PATH`).
  The desktop installer ships these binaries, so end users need nothing on `PATH`.

End users installing a packaged desktop build do not need Python, uv, Node.js, Rust, or ffmpeg. Local AI engines and their model weights may be downloaded on first use when local modes are enabled.

## Quick start: local SQLite

This is the shortest path for running the API and web UI locally. Leaving `DATABASE_URL` unset selects SQLite and Chroma automatically.

From the repository root in PowerShell:

```powershell
uv sync
Copy-Item .env.example .env
```

Edit `.env` and configure a working chat model and embedding model. The example file contains provider templates. For a local LM Studio server, for example, set the `LLM_*` values to the endpoint and model served by LM Studio. For a cloud provider, use its OpenAI-compatible URL and API key. Keep `.env` private; it contains credentials.

Start the API:

```powershell
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Once a production frontend build exists in `frontend/dist`, FastAPI serves that UI from the same origin. For active frontend development, run the separate Vite client as described below.

On first start, the service creates its data directories and applies pending database migrations. If `AUTH_SECRET_KEY` is empty, the service generates one and attempts to persist it in the root `.env`; keep that key stable across restarts and backups.

By default, local state is stored in `./data`:

```text
data/
├── noteslyt.db     # SQLite relational database
├── chroma/          # Chroma vector index
├── uploads/         # uploaded source files
├── llama/           # managed local embedding engine/model, if enabled
└── audio-cpp/       # managed local transcription engine/model, if enabled
```

Set `DATA_DIR` in `.env` to move these files to another writable location. Do not commit `data/` or `.env` to source control.

## Use PostgreSQL for a hosted or shared server

PostgreSQL mode requires PostgreSQL 17 with the pgvector extension available.

**Tutorial:**

1. Start a local database with Docker Desktop and the pgvector image:

```powershell
docker run --name noteslyt-postgres --detach `
  --env POSTGRES_USER=noteslyt `
  --env POSTGRES_PASSWORD=replace-with-a-strong-password `
  --env POSTGRES_DB=noteslyt `
  --publish 5432:5432 `
  --volume noteslyt-postgres-data:/var/lib/postgresql/data `
  pgvector/pgvector:pg17
```

2. Set the connection string in the root `.env` file (URL-encode special characters in the password when necessary):

```dotenv
DATABASE_URL=postgresql://noteslyt:replace-with-a-strong-password@127.0.0.1:5432/noteslyt
DATA_DIR=./data
```

3. Start the API with the same command as SQLite:

```powershell
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The selected database backend is determined by `DATABASE_URL`: PostgreSQL uses pgvector; when it is absent, the app uses SQLite and Chroma. Startup applies pending migrations (check with `uv run python scripts/migrate.py --status`). The upload directory remains filesystem-based in both modes, so a shared server needs persistent storage for `DATA_DIR/uploads` as well as durable PostgreSQL backups.

For an internet-facing deployment, run behind a TLS-terminating reverse proxy, restrict database and filesystem access, use strong secrets, configure appropriate CORS origins, and set resource limits and persistent volumes. Do not expose the development server directly to the public internet. Background ingestion and generation jobs run in the API process; plan for retries if that process restarts during a job.

## Run the web client separately

Use this workflow when changing the React UI. Start the backend in one terminal, then from `frontend/` in another:

```powershell
cd frontend
npm install
npm run dev
```

Vite runs at [http://localhost:5173](http://localhost:5173) and uses `http://localhost:8000` as the development API by default. To use another backend, set `VITE_API_URL` before starting Vite. For a production frontend served from FastAPI on the same origin, leave `VITE_API_URL` unset when building:

```powershell
npm run build
```

The build output is `frontend/dist/`. FastAPI serves it when present.

## Build the desktop application

The Tauri desktop profile starts a bundled FastAPI backend and defaults to local SQLite/Chroma storage. It is intended for a single local user. The installer bundles the Python backend runtime; users do not need to install Python separately.

Install frontend dependencies once:

```powershell
cd frontend
npm install
```

Run the desktop app in development:

```powershell
npm run tauri:dev
```

Create a distributable installer/build:

```powershell
npm run tauri:build
```

Tauri runs the frontend production build and backend bundling as part of its build process. Build prerequisites include Rust, Visual Studio C++ Build Tools, and WebView2. Installer formats and output paths depend on the host operating system and Tauri bundle configuration; Windows artifacts are written under `frontend/src-tauri/target/release/bundle/`.

The desktop app stores user data separately from the installed application when its configured data directory is writable, with an application-data fallback. Back up that data directory to preserve the local database, vector index, and uploaded files. Do not delete it when uninstalling if the user wants to retain their data.

## Configure AI providers

Each capability has separate configuration, so chat, embeddings, and transcription can use different compatible providers:

| Capability | Environment variables | Purpose |
| --- | --- | --- |
| Chat and generation | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Answers, study material generation, and other model-assisted tasks |
| Embeddings | `EMBEDDING_MODE`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, optional `EMBEDDING_DIM` | Search vectors for parsed source content |
| Transcription | `TRANSCRIPTION_MODE`, `TRANSCRIPTION_BASE_URL`, `TRANSCRIPTION_API_KEY`, `TRANSCRIPTION_MODEL` | Speech-to-text for audio and video |

`EMBEDDING_MODE=cloud` uses the configured embedding endpoint. `EMBEDDING_MODE=local` manages a local llama.cpp embedding engine and model files under `DATA_DIR` (or `EMBEDDING_LOCAL_DIR`). Likewise, transcription can use a cloud-compatible endpoint or a locally managed audio.cpp engine with `TRANSCRIPTION_MODE=local`. Local model files can be large and may require substantial memory; the initial download requires network access.

An embedding model's output width must match the stored vector width. Leave `EMBEDDING_DIM` unset for auto-detection unless you need to pin it. When switching embedding models, use the application's re-embedding workflow so stored vectors are regenerated for the selected model.

`FIRECRAWL_API_KEY` is optional. Without it, the configured local parser path is used where supported. Keep API keys in environment variables or `.env`, never in source control. See [.env.example](.env.example) for advanced options and provider-specific examples.

## API and authentication

FastAPI provides an interactive OpenAPI reference at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) and ReDoc at `/redoc`. The API includes:

- `GET /health` for service and dependency status.
- `POST /auth/signup` and `POST /auth/login` for account creation and login.
- Authenticated project, source, chat, profile, quiz, assessment, and settings routes.

Signup and login return a bearer token. Send it as `Authorization: Bearer <token>` on authenticated requests. Ownership is enforced for user and project resources. Refer to `/docs` for request/response schemas and the full endpoint list.

For a same-origin deployment, serve the built frontend through FastAPI. If hosting the frontend separately, set `VITE_API_URL` at frontend build time to the API origin and configure the backend's CORS allowlist for the frontend origin.

## Data, backups, and operations

- **SQLite profile:** back up the SQLite database, Chroma directory, uploaded originals, and configuration. Capture them together while the app is stopped or quiescent so the backup is consistent.
- **PostgreSQL profile:** use regular PostgreSQL dumps or managed database backups, and separately back up the `DATA_DIR` upload files. Preserve the matching database and files as one recovery point.
- **Secrets:** back up `.env` securely. Preserve `AUTH_SECRET_KEY`; changing it invalidates existing login tokens and may make encrypted settings unreadable.
- **Migrations:** the app applies pending schema migrations on startup. `uv run python scripts/migrate.py --status` reports migration state. `uv run python scripts/migrate.py` applies migrations manually. The `--reset` option is destructive and drops application data; use it only for an intentional local reset.
- **Logs and health:** startup logs report selected database backend, provider endpoints, parser mode, and storage location. Check `/health` and `/docs` when diagnosing a running service.
- **Privacy:** original files are stored on the configured filesystem. Parsed content sent for chat, embeddings, transcription, or parsing is handled by the providers you configure. Review provider retention policies before using sensitive material with a hosted API.

## Verification

Frontend production build and typecheck:

```powershell
cd frontend
npm run typecheck
npm run build
```

Backend checks require a configured database and the environment described in [AGENTS.md](AGENTS.md):

```powershell
uv run python scripts/verify_data_layer.py
uv run python scripts/verify_auth.py
```

The end-to-end smoke test can use the included mock model server. Follow the exact environment requirements in [AGENTS.md](AGENTS.md), start `scripts/mock_llm.py` in a separate terminal, then run:

```powershell
uv run python scripts/smoke_test.py
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API will not start | Review startup output for database connection, `AUTH_SECRET_KEY`, and provider configuration errors. |
| Chat or ingestion reports provider errors | Confirm the selected provider is running/reachable and that its URL, key, model name, and embedding width match the provider. |
| PostgreSQL connection fails | Confirm the service is running, the database exists, credentials are URL-encoded as needed, and the pgvector extension is available. |
| Audio/video processing fails | Confirm `ffmpeg` and `ffprobe` are installed and available on `PATH`; configure a supported transcription provider. |
| Frontend cannot reach the API | In Vite development, confirm the backend is on port 8000 or set `VITE_API_URL`; for separate production origins, check CORS configuration. |
| Desktop app does not start | Check the packaged backend logs and writable user-data directory. During development, run `npm run tauri:dev` from `frontend/` so its configured build tools and backend bundler are available. |

## Repository layout

```text
app/                 FastAPI API, services, repositories, ingestion, and storage
migrations/          Ordered SQL schema migrations
scripts/             Migration, verification, and development utilities
frontend/            React, TypeScript, Vite, and Tauri application
data/                Local runtime data (ignored by Git)
```

## License

No license has been added to this repository yet. Add a `LICENSE` file before redistributing NotesLyt or accepting external contributions under an open-source license.


### Transcript spelling corrections

Open an audio/video source and choose **Correct a name or spelling**. Enter the
current transcript spelling and a confirmed replacement. All matching whole words
in that source are updated, then its search index is rebuilt. Original recordings
and segment timestamps are retained. Existing chat messages keep their original
answers; new answers use the corrected transcript.

The API is `POST /sources/{id}/transcript-spellings` with
`{"replacements": {"Myra": "Mira"}}`. Corrections are ownership-checked and stored
beside that source, survive fresh parsing, and are deleted with the source.
A complete saved transcript is required. Homophones need a confirmed spelling;
audio alone cannot determine how someone spells their name.
