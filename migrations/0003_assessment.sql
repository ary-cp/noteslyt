-- Study Companion — assessment (quizzes, questions, assessments).

-- `quizzes` MUST carry project_id: the answer endpoint takes no scope parameter
-- and inherits its scope entirely from this row, so it is the security boundary.
create table if not exists quizzes (
    id         uuid primary key default gen_random_uuid(),
    project_id uuid not null references projects(id) on delete cascade,
    user_id    uuid not null references users(id)   on delete cascade,
    topic_id   uuid references topics(id) on delete set null,
    created_at timestamptz not null default now()
);
create index if not exists quizzes_project_idx on quizzes (project_id, created_at desc);
create index if not exists quizzes_user_idx    on quizzes (user_id, topic_id);

-- Split out of the old jsonb blob so novelty detection has something to embed.
create table if not exists questions (
    id            uuid primary key default gen_random_uuid(),
    quiz_id       uuid not null references quizzes(id) on delete cascade,
    topic_id      uuid references topics(id) on delete set null,
    source_id     uuid references sources(id) on delete set null,
    seq           integer not null default 0,
    qtype         text not null default 'mcq',   -- mcq|short_answer|numerical
    stem          text not null,
    options       jsonb not null default '[]'::jsonb,
    answer_key    jsonb not null default '{}'::jsonb,
    explanation   text not null default '',
    locator_kind  text not null default 'page',
    locator_value text not null default '',
    difficulty    real not null default 0.5,
    stem_embedding vector(768)
);
create index if not exists questions_quiz_idx  on questions (quiz_id, seq);
create index if not exists questions_topic_idx on questions (topic_id);

create table if not exists answers (
    id          uuid primary key default gen_random_uuid(),
    quiz_id     uuid not null references quizzes(id)  on delete cascade,
    question_id uuid not null references questions(id) on delete cascade,
    user_id     uuid not null references users(id)    on delete cascade,
    given       text not null default '',
    correct     boolean not null,
    p_before    real not null,
    p_after     real not null,
    created_at  timestamptz not null default now()
);
create index if not exists answers_quiz_idx  on answers (quiz_id);
create index if not exists answers_user_idx  on answers (user_id, created_at desc);

create table if not exists assessments (
    id         uuid primary key default gen_random_uuid(),
    project_id uuid not null references projects(id) on delete cascade,
    user_id    uuid not null references users(id)   on delete cascade,
    label      text not null default '',
    quiz_ids   jsonb not null default '[]'::jsonb,
    report     jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists assessments_project_idx on assessments (project_id, created_at desc);