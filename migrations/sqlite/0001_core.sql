-- NotesLyt — core tenancy + corpus schema (SQLite port of 0001_core.sql).
--
-- Same tables, SQLite types: uuid -> TEXT (ids are generated in Python),
-- timestamptz -> TEXT (ISO strings via datetime('now')), jsonb -> TEXT.
-- Vectors do NOT live here: the `chunks` table keeps text + metadata for
-- topic grounding/counts/cascades, while similarity lives in ChromaDB
-- (see app/vectorstore_chroma.py). There is deliberately no `embedding`
-- column and no `match_chunks` function on this backend.

-- ============================================================ identity
create table if not exists users (
    id            text primary key,
    email         text not null unique,
    password_hash text not null,
    display_name  text not null,
    created_at    text not null default (datetime('now'))
);


-- ============================================================ tenancy
-- A project is the user's "book": one subject, its own corpus, its own chats.
create table if not exists projects (
    id          text primary key,
    user_id     text not null references users(id) on delete cascade,
    title       text not null,
    subject     text not null default '',
    description text not null default '',
    archived    integer not null default 0,
    created_at  text not null default (datetime('now')),
    updated_at  text not null default (datetime('now'))
);
create index if not exists projects_user_idx on projects (user_id, created_at desc);


-- ============================================================ sources
-- Any uploaded file. `status` drives async ingestion.
create table if not exists sources (
    id            text primary key,
    project_id    text not null references projects(id) on delete cascade,
    filename      text not null,
    source_type   text not null default 'other',
    status        text not null default 'queued',
    status_detail text not null default '',
    parser        text not null default '',
    units         integer not null default 0,
    duration_s    real,
    chunk_count   integer not null default 0,
    file_url      text,
    warnings      text not null default '[]',
    created_at    text not null default (datetime('now'))
);
create index if not exists sources_project_idx on sources (project_id, created_at desc);

-- The ingestion worker claims queued rows with exactly this pattern.
create index if not exists sources_pending_idx on sources (status) where status in ('queued', 'failed');


-- ============================================================ topics (project-level)
-- Topics belong to the PROJECT, not to a single file.
create table if not exists topics (
    id          text primary key,
    project_id  text not null references projects(id) on delete cascade,
    slug        text not null,
    name        text not null,
    description text not null default '',
    created_at  text not null default (datetime('now')),
    unique (project_id, slug)
);
create index if not exists topics_project_idx on topics (project_id);

-- Which sources cover a topic, and where inside them.
create table if not exists topic_sources (
    topic_id  text not null references topics(id) on delete cascade,
    source_id text not null references sources(id) on delete cascade,
    locators  text not null default '[]',
    primary key (topic_id, source_id)
);
create index if not exists topic_sources_source_idx on topic_sources (source_id);

create table if not exists topic_edges (
    project_id text not null references projects(id) on delete cascade,
    topic_id   text not null references topics(id) on delete cascade,
    prereq_id  text not null references topics(id) on delete cascade,
    primary key (topic_id, prereq_id)
);


-- ============================================================ chunks
-- Text + metadata mirror of the Chroma collection (no embedding column).
-- `locator_*` generalizes page/slide/timestamp/row citations.
create table if not exists chunks (
    id            text primary key,
    project_id    text not null references projects(id) on delete cascade,
    source_id     text not null references sources(id)  on delete cascade,
    topic_id      text references topics(id) on delete set null,
    locator_kind  text not null default 'page',
    locator_value text not null default '',
    page          integer,
    start_ts      real,
    end_ts        real,
    chunk_index   integer not null default 0,
    heading       text not null default '',
    text          text not null
);

create index if not exists chunks_project_idx   on chunks (project_id);
create index if not exists chunks_source_idx    on chunks (source_id);
create index if not exists chunks_topic_idx     on chunks (project_id, topic_id);
