-- NotesLyt — 0004: parsed source text (SQLite port).
-- Same rationale as the Postgres original: the viewer reads this table, the
-- list queries never touch it.

create table if not exists source_contents (
    source_id  text primary key references sources(id) on delete cascade,
    markdown   text not null default '',
    truncated  integer not null default 0,
    chars      integer not null default 0,
    updated_at text not null default (datetime('now'))
);
