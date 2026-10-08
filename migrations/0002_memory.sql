-- Study Companion — the three memory tiers (plan.md section 5).

-- ==================================================== tier 1: global
-- "user name, user capability / thinking level, study pattern".
-- Seeded by onboarding, refined by the LLM, always user-editable (plan.md D5).
create table if not exists user_profiles (
    user_id          uuid primary key references users(id) on delete cascade,
    capability_level text not null default 'unknown',
    thinking_level   text not null default 'unknown',
    goals            text not null default '',
    study_pattern    jsonb not null default '{}'::jsonb,
    strengths        jsonb not null default '[]'::jsonb,
    updated_at       timestamptz not null default now()
);


-- ==================================================== tier 2: project
-- Mastery per topic, scoped to the project (not to a single file) so the
-- learner model spans every source in the book.
create table if not exists project_memory (
    user_id           uuid not null references users(id)   on delete cascade,
    project_id        uuid not null references projects(id) on delete cascade,
    topic_id          uuid not null references topics(id)   on delete cascade,
    p_mastery         real not null default 0.30,
    attempts          integer not null default 0,
    correct           integer not null default 0,
    coverage_status   text not null default 'not_started',
    last_practiced_at timestamptz,
    updated_at        timestamptz not null default now(),
    primary key (user_id, project_id, topic_id)
);
create index if not exists project_memory_project_idx on project_memory (project_id, p_mastery);


-- ==================================================== tier 3: chat
create table if not exists chats (
    id                  uuid primary key default gen_random_uuid(),
    project_id          uuid not null references projects(id) on delete cascade,
    user_id             uuid not null references users(id)   on delete cascade,
    title               text not null default 'New chat',
    summary             text not null default '',
    summarized_through  uuid,
    created_at          timestamptz not null default now(),
    last_message_at     timestamptz not null default now()
);
create index if not exists chats_project_idx on chats (project_id, last_message_at desc);

create table if not exists chat_messages (
    id         uuid primary key default gen_random_uuid(),
    chat_id    uuid not null references chats(id) on delete cascade,
    role       text not null check (role in ('user', 'assistant')),
    content    text not null,
    citations  jsonb not null default '[]'::jsonb,
    grounded   boolean,
    created_at timestamptz not null default now()
);
create index if not exists chat_messages_chat_idx on chat_messages (chat_id, created_at);