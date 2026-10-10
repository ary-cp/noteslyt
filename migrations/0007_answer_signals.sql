-- Persist rubric match/miss signals per answer so post-assessment reports can
-- name likely misconceptions. Existing answers keep their score; only new
-- answers carry signals (old rows read back as empty lists).
alter table answers add column if not exists missed jsonb not null default '[]'::jsonb;
alter table answers add column if not exists matched jsonb not null default '[]'::jsonb;
