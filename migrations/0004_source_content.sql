-- Study Companion — 0004: parsed source text + ready-only retrieval note.
--
-- `source_contents` holds the parser's markdown for one source, so the viewer
-- can show the learner exactly what the model ingested ("Parsed" tab) next to
-- the original file ("Original" tab). It lives in its own table — not on
-- `sources` — so listing a project's sources never drags kilobytes of text
-- through the list query; only GET /sources/{id}/content reads this table.
--
-- Retrieval scoping (chat/quiz only ever see `ready` sources) is enforced in
-- the services via `sources.ready_ids()` + `match_chunks(filter_source_ids)`,
-- not in the SQL function, so `match_chunks` keeps its signature and the
-- data-layer verification (which queries the store directly) is unaffected.

create table if not exists source_contents (
    source_id  uuid primary key references sources(id) on delete cascade,
    markdown   text not null default '',
    truncated  boolean not null default false,
    chars      integer not null default 0,
    updated_at timestamptz not null default now()
);
