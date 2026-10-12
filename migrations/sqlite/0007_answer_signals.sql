-- Persist rubric match/miss signals per answer (SQLite port).
-- SQLite has no ADD COLUMN IF NOT EXISTS; re-runs are no-ops via
-- schema_migrations tracking, so plain ADD COLUMN is correct here.
alter table answers add column missed text not null default '[]';
alter table answers add column matched text not null default '[]';
