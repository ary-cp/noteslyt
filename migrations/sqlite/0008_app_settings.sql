-- NotesLyt — admin-managed settings + first-user admin (SQLite port of 0008).
-- Same contract as the Postgres original: explicit env > app_settings > default.

create table if not exists app_settings (
    key         text primary key,
    value       text not null,
    updated_at  text not null default (datetime('now'))
);

create table if not exists system_meta (
    key         text primary key,
    value       text not null,
    updated_at  text not null default (datetime('now'))
);

alter table users add column is_admin integer not null default 0;

update users set is_admin = 1
where id = (select id from users order by created_at asc limit 1)
  and not exists (select 1 from users where is_admin = 1);
