# Release 0.1.13 — Personalisation + strict exams

**Get it:** [github.com/ary-cp/noteslyt/releases](https://github.com/ary-cp/noteslyt/releases) —
download the installer, launch, create an account, complete the setup wizard.
No Python, Node, Docker, or terminal needed.

## Added

- **Personalisation settings tab** — Appearance, Sources, Shortcuts, and
  Learning profile merged into one tab, with an **accent color picker**
  (preset swatches + any custom color, persisted per browser).
- **Learning Profile page** (`/learning-profile`) — cross-project topic
  mastery, weak vs strong topics, areas of improvement, IRT ability, and the
  history of personalised reports (`GET /profile/overview`, `GET /profile/reports`).
- **BKT + IRT learner model** — per-answer Rasch ability updates alongside BKT,
  LLM narratives on every assessment, LLM-maintained learning-style memo fed
  into every tutor reply (`learner_reports` table, migration `0011`).
- **Strict mock exams** — full-page paper with deferred grading (nothing graded
  until Submit, so no correctness leaks), rules gate, accent timer, copy/right-click
  lock, −5:00 tab-switch penalty, answers revealed only after submit.
- **Study guide topic dropdown** — Summary / Study guide / Cheat sheet pick a
  real in-scope topic instead of free-text filter.
- **Project documentation** — `docs/PROJECT_DOCUMENTATION.md`: architecture,
  grounding method, and learner-model approach.

## Fixed

- **Model picker in admin settings** — refetches when the key changes and
  explains the hidden saved key; no more bogus `/v1/models` retry on versioned
  bases (e.g. Gemini `…/v1beta/openai` 404 without its key).
- **Quiz/exam generation no longer fails** when the embedding endpoint drops
  mid-job (novelty filter skipped, questions stored vector-less).
- **Quiz/exam dialogs** no longer close on backdrop click or Escape
  mid-attempt; the ✕ button still works and answers resume.
- **Dropdown menus render above modals** (portalled) instead of clipping inside
  the dialog scroll container; generation dialog widened to 600px.

## Upgrade notes

- Migrations `0011_personalization` (Postgres + SQLite) apply automatically on
  boot; no action needed.
- Accent choice lives in browser localStorage (`noteslyt:accent`); each device
  picks its own.
- Exam papers generated before this release still open; in-progress modal exams
  resume with saved answers.
