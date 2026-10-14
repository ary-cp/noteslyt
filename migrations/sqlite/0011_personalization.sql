-- Personalisation (SQLite port of 0011_personalization.sql).

alter table user_profiles
    add column learning_style_notes text not null default '';

alter table user_profiles
    add column irt_ability real not null default 0.0;

create table if not exists learner_reports (
    id         text primary key,
    user_id    text not null references users(id) on delete cascade,
    project_id text not null references projects(id) on delete cascade,
    kind       text not null default 'quiz',
    ref_id     text not null default '',
    summary    text not null default '',
    bkt        text not null default '{}',
    irt        text not null default '{}',
    strengths  text not null default '[]',
    weaknesses text not null default '[]',
    created_at text not null default (datetime('now'))
);
create index if not exists learner_reports_user_idx on learner_reports (user_id, created_at desc);
create index if not exists learner_reports_project_idx on learner_reports (project_id, created_at desc);
