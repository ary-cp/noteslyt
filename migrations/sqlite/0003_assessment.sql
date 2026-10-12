-- NotesLyt — assessment (SQLite port of 0003_assessment.sql).
--
-- `stem_embedding` is JSON text here (a float list), compared in Python with
-- numpy — no vector column, no index. ChromaDB holds only chunk vectors;
-- quiz novelty never needed an index.

create table if not exists quizzes (
    id         text primary key,
    project_id text not null references projects(id) on delete cascade,
    user_id    text not null references users(id)   on delete cascade,
    topic_id   text references topics(id) on delete set null,
    created_at text not null default (datetime('now'))
);
create index if not exists quizzes_project_idx on quizzes (project_id, created_at desc);
create index if not exists quizzes_user_idx    on quizzes (user_id, topic_id);

create table if not exists questions (
    id            text primary key,
    quiz_id       text not null references quizzes(id) on delete cascade,
    topic_id      text references topics(id) on delete set null,
    source_id     text references sources(id) on delete set null,
    seq           integer not null default 0,
    qtype         text not null default 'mcq',
    stem          text not null,
    options       text not null default '[]',
    answer_key    text not null default '{}',
    explanation   text not null default '',
    locator_kind  text not null default 'page',
    locator_value text not null default '',
    difficulty    real not null default 0.5,
    stem_embedding text
);
create index if not exists questions_quiz_idx  on questions (quiz_id, seq);
create index if not exists questions_topic_idx on questions (topic_id);

create table if not exists answers (
    id          text primary key,
    quiz_id     text not null references quizzes(id)  on delete cascade,
    question_id text not null references questions(id) on delete cascade,
    user_id     text not null references users(id)    on delete cascade,
    given       text not null default '',
    correct     integer not null,
    p_before    real not null,
    p_after     real not null,
    created_at  text not null default (datetime('now'))
);
create index if not exists answers_quiz_idx  on answers (quiz_id);
create index if not exists answers_user_idx  on answers (user_id, created_at desc);

create table if not exists assessments (
    id         text primary key,
    project_id text not null references projects(id) on delete cascade,
    user_id    text not null references users(id)   on delete cascade,
    label      text not null default '',
    quiz_ids   text not null default '[]',
    report     text not null default '{}',
    created_at text not null default (datetime('now'))
);
create index if not exists assessments_project_idx on assessments (project_id, created_at desc);
