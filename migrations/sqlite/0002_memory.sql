-- NotesLyt — the three memory tiers (SQLite port of 0002_memory.sql).

-- ==================================================== tier 1: global
create table if not exists user_profiles (
    user_id          text primary key references users(id) on delete cascade,
    capability_level text not null default 'unknown',
    thinking_level   text not null default 'unknown',
    goals            text not null default '',
    study_pattern    text not null default '{}',
    strengths        text not null default '[]',
    updated_at       text not null default (datetime('now'))
);


-- ==================================================== tier 2: project
create table if not exists project_memory (
    user_id           text not null references users(id)   on delete cascade,
    project_id        text not null references projects(id) on delete cascade,
    topic_id          text not null references topics(id)   on delete cascade,
    p_mastery         real not null default 0.30,
    attempts          integer not null default 0,
    correct           integer not null default 0,
    coverage_status   text not null default 'not_started',
    last_practiced_at text,
    updated_at        text not null default (datetime('now')),
    primary key (user_id, project_id, topic_id)
);
create index if not exists project_memory_project_idx on project_memory (project_id, p_mastery);


-- ==================================================== tier 3: chat
create table if not exists chats (
    id                  text primary key,
    project_id          text not null references projects(id) on delete cascade,
    user_id             text not null references users(id)   on delete cascade,
    title               text not null default 'New chat',
    summary             text not null default '',
    summarized_through  text,
    created_at          text not null default (datetime('now')),
    last_message_at     text not null default (datetime('now'))
);
create index if not exists chats_project_idx on chats (project_id, last_message_at desc);

create table if not exists chat_messages (
    id         text primary key,
    chat_id    text not null references chats(id) on delete cascade,
    role       text not null check (role in ('user', 'assistant')),
    content    text not null,
    citations  text not null default '[]',
    grounded   integer,
    created_at text not null default (datetime('now'))
);
create index if not exists chat_messages_chat_idx on chat_messages (chat_id, created_at);
