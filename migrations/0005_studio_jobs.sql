create table if not exists studio_jobs (
    id uuid primary key,
    project_id uuid not null references projects(id) on delete cascade,
    user_id uuid not null references users(id) on delete cascade,
    kind text not null,
    title text not null,
    status text not null default 'queued' check (status in ('queued','generating','ready','failed')),
    payload jsonb,
    error text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists studio_jobs_project_created_idx on studio_jobs(project_id, created_at desc);
