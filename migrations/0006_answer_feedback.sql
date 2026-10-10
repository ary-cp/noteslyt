-- Preserve the original grading feedback for retries and quiz resume.
-- Existing answers retain their original score and an empty feedback value.
alter table answers add column if not exists feedback text not null default '';
