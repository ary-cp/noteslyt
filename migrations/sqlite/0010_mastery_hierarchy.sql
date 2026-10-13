-- NotesLyt — SQLite port of 0010_mastery_hierarchy.sql.

alter table topics
    add column subtopics text not null default '[]';

alter table project_memory
    add column easiness real not null default 2.5;

alter table project_memory
    add column repetitions integer not null default 0;

alter table project_memory
    add column interval_days real not null default 0;

alter table project_memory
    add column due_at text;
