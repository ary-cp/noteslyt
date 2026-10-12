-- NotesLyt — admin-managed settings + first-user admin (Phase 2, experimental).
--
-- `app_settings` holds admin-editable overrides (LLM/embedding/transcription
-- endpoints, RAG/memory/BKT tuning...). Resolution order is:
--   explicit env/.env  >  app_settings row  >  built-in default.
-- A key set in the operator's environment is "locked": the API refuses PATCHes
-- for it and the UI shows it read-only. An empty website .env therefore falls
-- through to whatever the admin configured in-app; a fully-pinned .env locks
-- everything and in-app settings become read-only.
--
-- Secrets (API keys) are Fernet-encrypted at rest (`enc1:` prefix), with the
-- key derived from AUTH_SECRET_KEY — see app/settings_overlay.py. No new env
-- var was needed and there is nothing else to back up.
--
-- `system_meta` holds machine-managed facts (e.g. embedding.last_model /
-- embedding.last_dim after a successful ingest) that the drift banner compares
-- against. Admins never write these directly.

create table if not exists app_settings (
    key         text primary key,
    value       text not null,
    updated_at  timestamptz not null default now()
);

create table if not exists system_meta (
    key         text primary key,
    value       text not null,
    updated_at  timestamptz not null default now()
);

-- First user is admin: single-user desktop installs get an admin with no extra
-- step; website hosters either claim the first signup or pin everything via
-- .env (which locks the UI anyway). Existing installs backfill the earliest
-- registered user below, and UserRepo.create() grants admin when the table is
-- empty, so the invariant "at least one admin while users exist" holds.
alter table users add column if not exists is_admin boolean not null default false;

update users set is_admin = true
where id = (select id from users order by created_at asc limit 1)
  and not exists (select 1 from users where is_admin = true);
