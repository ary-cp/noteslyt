-- Personalisation: LLM-maintained learner memo, IRT ability, per-turn reports.
--
-- user_profiles.learning_style_notes: short LLM-written memo ("prefers charts,
-- slow deliberate pace, out-of-the-box questions ...") refreshed after chats.
-- Editable by the learner; fed into the tutor prompt as LEARNER MEMORY.
-- user_profiles.irt_ability: global Rasch ability in logits (see app/learner/irt.py),
-- updated on every graded quiz answer alongside the per-topic BKT update.
--
-- learner_reports: one personalised report row per quiz/exam completion and per
-- chat conversation (written best-effort in the background; never blocks a turn).
-- kind is 'quiz' | 'exam' | 'chat'. ref_id points at the quiz/assessment or chat.
-- bkt/irt/strengths/weaknesses are JSON snapshots for the Learning Profile page.

alter table if exists user_profiles
    add column if not exists learning_style_notes text not null default '',
    add column if not exists irt_ability real not null default 0.0;

create table if not exists learner_reports (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    project_id uuid not null references projects(id) on delete cascade,
    kind       text not null default 'quiz' check (kind in ('quiz', 'exam', 'chat')),
    ref_id     text not null default '',
    summary    text not null default '',
    bkt        jsonb not null default '{}'::jsonb,
    irt        jsonb not null default '{}'::jsonb,
    strengths  jsonb not null default '[]'::jsonb,
    weaknesses jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists learner_reports_user_idx on learner_reports (user_id, created_at desc);
create index if not exists learner_reports_project_idx on learner_reports (project_id, created_at desc);
