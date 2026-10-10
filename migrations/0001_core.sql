-- Study Companion — core tenancy + corpus schema (Postgres + pgvector).
--
-- One local Postgres instance holds everything:
--   DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/StudyCompanion
--
-- EMBEDDING_DIM must match the `vector(...)` width below AND settings.embedding_dim.
-- `scripts/migrate.py` asserts this before applying anything.

create extension if not exists vector;
create extension if not exists pgcrypto;   -- gen_random_uuid()


-- ============================================================ identity
create table if not exists users (
    id            uuid primary key default gen_random_uuid(),
    email         text not null unique,
    password_hash text not null,
    display_name  text not null,
    created_at    timestamptz not null default now()
);


-- ============================================================ tenancy
-- A project is the user's "book": one subject, its own corpus, its own chats.
create table if not exists projects (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references users(id) on delete cascade,
    title       text not null,
    subject     text not null default '',
    description text not null default '',
    archived    boolean not null default false,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists projects_user_idx on projects (user_id, created_at desc);


-- ============================================================ sources
-- Any uploaded file. `status` drives async ingestion (plan.md section 7).
create table if not exists sources (
    id            uuid primary key default gen_random_uuid(),
    project_id    uuid not null references projects(id) on delete cascade,
    filename      text not null,
    source_type   text not null default 'other',
    status        text not null default 'queued',
    status_detail text not null default '',
    parser        text not null default '',
    units         integer not null default 0,
    duration_s    real,
    chunk_count   integer not null default 0,
    file_url      text,
    warnings      jsonb not null default '[]'::jsonb,
    created_at    timestamptz not null default now()
);
create index if not exists sources_project_idx on sources (project_id, created_at desc);

-- The ingestion worker claims queued rows with exactly this pattern.
create index if not exists sources_pending_idx on sources (status) where status in ('queued', 'failed');


-- ============================================================ topics (project-level)
-- Topics belong to the PROJECT, not to a single file, so "Photosynthesis" is one
-- topic covered by both the PDF and the lecture video (plan.md section 1).
create table if not exists topics (
    id          uuid primary key default gen_random_uuid(),
    project_id  uuid not null references projects(id) on delete cascade,
    slug        text not null,
    name        text not null,
    description text not null default '',
    created_at  timestamptz not null default now(),
    unique (project_id, slug)
);
create index if not exists topics_project_idx on topics (project_id);

-- Which sources cover a topic, and where inside them.
create table if not exists topic_sources (
    topic_id  uuid not null references topics(id) on delete cascade,
    source_id uuid not null references sources(id) on delete cascade,
    locators  jsonb not null default '[]'::jsonb,
    primary key (topic_id, source_id)
);
create index if not exists topic_sources_source_idx on topic_sources (source_id);

create table if not exists topic_edges (
    project_id uuid not null references projects(id) on delete cascade,
    topic_id   uuid not null references topics(id) on delete cascade,
    prereq_id  uuid not null references topics(id) on delete cascade,
    primary key (topic_id, prereq_id)
);


-- ============================================================ chunks
-- `locator_*` generalizes the old single `page` column so a chunk can point at a
-- slide, a transcript timestamp or a spreadsheet row as well as a page.
create table if not exists chunks (
    id            uuid primary key default gen_random_uuid(),
    project_id    uuid not null references projects(id) on delete cascade,
    source_id     uuid not null references sources(id)  on delete cascade,
    topic_id      uuid references topics(id) on delete set null,
    locator_kind  text not null default 'page',
    locator_value text not null default '',
    page          integer,
    start_ts      real,
    end_ts        real,
    chunk_index   integer not null default 0,
    heading       text not null default '',
    text          text not null,
    embedding     vector(768)
);

create index if not exists chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops);
create index if not exists chunks_project_idx   on chunks (project_id);
create index if not exists chunks_source_idx    on chunks (source_id);
create index if not exists chunks_topic_idx     on chunks (project_id, topic_id);


-- ============================================================ retrieval
-- `filter_project_id` is REQUIRED and there is deliberately no "search everything"
-- default — a project boundary is the whole tenancy guarantee (plan.md invariant 3).
create or replace function match_chunks (
    query_embedding   vector(768),
    match_count       integer,
    filter_project_id uuid,
    filter_source_ids uuid[] default null,
    filter_topic_ids  uuid[] default null
)
returns table (
    id            uuid,
    source_id     uuid,
    topic_id      uuid,
    text          text,
    locator_kind  text,
    locator_value text,
    page          integer,
    start_ts      real,
    end_ts        real,
    heading       text,
    similarity    real
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