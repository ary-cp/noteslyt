"""SM-2 spaced repetition (SuperMemo-2, Wozniak 1990) — per-topic review state.

Each graded quiz answer is a review event for its topic:

  correct   → quality 4: repetitions grow, the interval stretches
              (1 day, then 6, then last-interval × easiness).
  incorrect → quality 0: repetitions reset, back to a 1-day interval.

Easiness starts at 2.5, drifts ±0.1 per review, and never drops below 1.3.
`due_at` (last review + interval) drives "due for review" surfacing; chat
nudges deliberately never touch this state — conversation is exposure, not
retrieval practice, so only test events move the schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MIN_EASINESS = 1.3


def review(
    easiness: float,
    repetitions: int,
    interval_days: float,
    correct: bool,
    now: datetime | None = None,
) -> tuple[float, int, float, datetime]:
    """Advance SM-2 state; returns (easiness, repetitions, interval_days, due_at)."""
    try:
        easiness = float(easiness)
    except (TypeError, ValueError):
        easiness = 2.5
    try:
        repetitions = int(repetitions or 0)
    except (TypeError, ValueError):
        repetitions = 0
    try:
        interval_days = float(interval_days or 0.0)
    except (TypeError, ValueError):
        interval_days = 0.0
    now = now or datetime.now(timezone.utc)

    if correct:
        quality = 4
    else:
        quality = 0
    easiness = max(MIN_EASINESS, round(easiness + (0.1 if correct else -0.2), 4))
    if quality < 3:
        repetitions = 0
        interval_days = 1.0
    else:
        repetitions += 1
        if repetitions == 1:
            interval_days = 1.0
        elif repetitions == 2:
            interval_days = 6.0
        else:
            interval_days = round(interval_days * easiness, 2)
    due_at = now + timedelta(days=interval_days)
    return easiness, repetitions, interval_days, due_at


def is_due(due_at: datetime | str | None, now: datetime | None = None) -> bool:
    """A topic with no review history is never 'due' — only scheduled reviews are."""
    if due_at is None:
        return False
    if isinstance(due_at, str):
        try:
            due_at = datetime.fromisoformat(due_at)
        except ValueError:
            return False
    now = now or datetime.now(timezone.utc)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at <= now
