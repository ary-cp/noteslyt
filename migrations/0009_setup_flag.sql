-- First-run setup flag for the admin onboarding wizard.
--
-- Existing installs (users already present) are NOT fresh: mark setup complete
-- so the wizard never interrupts an upgrade. Fresh databases have no users
-- yet, so no row is written and the first admin gets the wizard after signup,
-- where they enter API keys / endpoints before anything else.
insert into system_meta (key, value)
select 'setup.completed', 'true'
where exists (select 1 from users)
  and not exists (select 1 from system_meta where key = 'setup.completed');
