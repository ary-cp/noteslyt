-- Learning-model hierarchy + spaced repetition (see app/learner/sm2.py).
--
-- topics.subtopics: [{slug, name, description, concepts: [...], locators: [...]}]
--   emitted at ingest by app/ingestion/topics.py alongside the parent topic and
--   merged across sources by slug, exactly like top-level topics. Units are
--   already tagged per topic in chunks.topic_id; each subtopic additionally
--   carries its own validated locator list, so every content unit resolves to
--   both a topic and (where evidenced) a subtopic without a second LLM pass.
-- project_memory easiness/repetitions/interval_days/due_at: SM-2 state,
--   advanced only by graded quiz answers (chat nudges never touch it).

alter table if exists topics
    add column if not exists subtopics jsonb not null default '[]'::jsonb;

alter table if exists project_memory
    add column if not exists easiness real not null default 2.5,
    add column if not exists repetitions integer not null default 0,
    add column if not exists interval_days real not null default 0,
    add column if not exists due_at timestamptz;
