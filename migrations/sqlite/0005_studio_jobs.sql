-- NotesLyt — 0005: Studio generation jobs (SQLite port).

create table if not exists studio_jobs (
    id text primary key,
    project_id text not null references projects(id) on delete cascade,
    user_id text not null references users(id) on delete cascade,
    kind text not null,
    title text not null,
    status text not null default 'queued' check (status in ('queued','generating','ready','failed')),
    payload text,
    error text not null default '',
    created_at text not null default (datetime('now')),
    updated_at text not null default (datetime('now'))
);
create index if not exists studio_jobs_project_created_idx on studio_jobs(project_id, created_at desc);
