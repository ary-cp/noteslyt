"""
Tier-1 global memory: the learner profile.

Holds what the learner told us about themselves — capability level, thinking
level, goals — plus counters refined later from real behaviour (Phase E).

Two writers, one table:
  * `upsert_basics`  — explicit user input (onboarding form, `PATCH /profile`)
  * `observe_study_pattern` — counters bumped by the backend as the learner uses
    the app (Phase E)

The rule that matters (plan.md D5): an explicit user choice is never silently
overwritten by inference. `observe_study_pattern` only touches `study_pattern`,
which is observational, while `capability_level` / `thinking_level` stay under
the learner's control.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .base import Repo, _coerce

_FIELDS = (
    "user_id", "capability_level", "thinking_level", "goals",
    "study_pattern", "strengths", "updated_at",
)

# Only these values are accepted, so the prompt builder can trust the field.
CAPABILITY_LEVELS = ("unknown", "beginner", "intermediate", "advanced")
THINKING_LEVELS = ("unknown", "concrete", "analytical", "abstract")


@dataclass
class ProfileRecord:
    user_id: str
    capability_level: str = "unknown"
    thinking_level: str = "unknown"
    goals: str = ""
    study_pattern: dict[str, Any] = field(default_factory=dict)
    strengths: list = field(default_factory=list)
    updated_at: Any = None


def _clamp(value: str, allowed: tuple[str, ...], fallback: str = "unknown") -> str:
    candidate = (value or "").strip().lower()
    return candidate if candidate in allowed else fallback


class ProfileRepo(Repo):
    def upsert_basics(
        self,
        user_id: str,
        *,
        capability_level: str | None = None,
        thinking_level: str | None = None,
        goals: str | None = None,
    ) -> ProfileRecord:
        """
        Write the user's explicit choices. `None` means "leave unchanged", so a
        partial profile edit never wipes what is already known.
        """
        existing = self.get(user_id)
        merged = {
            "capability_level": _clamp(
                capability_level if capability_level is not None
                else (existing.capability_level if existing else "unknown"),
                CAPABILITY_LEVELS,
            ),
            "thinking_level": _clamp(
                thinking_level if thinking_level is not None
                else (existing.thinking_level if existing else "unknown"),
                THINKING_LEVELS,
            ),
            "goals": (
                goals if goals is not None
                else (existing.goals if existing else "")
            ),
        }
        row = self._fetch_one(
            f"insert into user_profiles (user_id, capability_level, thinking_level, goals) "
            f"values (%(user_id)s, %(capability_level)s, %(thinking_level)s, %(goals)s) "
            f"on conflict (user_id) do update set "
            f"capability_level = excluded.capability_level, "
            f"thinking_level = excluded.thinking_level, "
            f"goals = excluded.goals, "
            f"updated_at = now() "
            f"returning {', '.join(_FIELDS)}",
            {"user_id": user_id, **merged},
        )
        return _to_profile(row)  # type: ignore[return-value]

    def get(self, user_id: str) -> ProfileRecord | None:
        row = self._fetch_one(
            f"select {', '.join(_FIELDS)} from user_profiles where user_id = %s",
            (user_id,),
        )
        return _to_profile(row)

    def quiz_progress(self, user_id: str) -> dict:
        """Evidence from saved first answers, including quizzes taken before profile UI existed."""
        return self._fetch_one(
            "select count(*) as answered, "
            "coalesce(sum(case when a.correct then 1 else 0 end), 0) as correct, "
            "count(distinct q.topic_id) as topics_practiced, "
            "count(distinct a.quiz_id) as quizzes_practiced "
            "from answers a join questions q on q.id = a.question_id "
            "join quizzes z on z.id = a.quiz_id join projects p on p.id = z.project_id "
            "where a.user_id = %s and z.user_id = %s and p.user_id = %s",
            (user_id, user_id, user_id),
        ) or {"answered": 0, "correct": 0, "topics_practiced": 0, "quizzes_practiced": 0}

    def observe_study_pattern(self, user_id: str, **counters: int) -> None:
        """
        Increment observational counters (e.g. `sessions=1`, `quizzes=3`).

        Read-modify-write inside one transaction: Postgres has no numeric
        `jsonb + jsonb` addition, so the merge happens in Python. Deliberately
        does NOT touch capability_level / thinking_level — those belong to
        the learner.
        """
        deltas = {k: int(v) for k, v in counters.items() if v}
        if not deltas:
            return
        with self.db.transaction() as conn:
            conn.execute(
                "insert into user_profiles (user_id) values (%s) on conflict (user_id) do nothing",
                (user_id,),
            )
            row = conn.execute(
                "select study_pattern from user_profiles where user_id = %s for update",
                (user_id,),
            ).fetchone()
            stored = row["study_pattern"] if row else None
            if isinstance(stored, str):
                try:
                    stored = json.loads(stored) if stored else {}
                except ValueError:
                    stored = {}
            pattern = dict(stored or {})
            for key, delta in deltas.items():
                try:
                    current = int(pattern.get(key, 0))
                except (TypeError, ValueError):
                    current = 0
                pattern[key] = current + delta
            conn.execute(
                "update user_profiles set study_pattern = %s::jsonb, updated_at = now() "
                "where user_id = %s",
                (json.dumps(pattern), user_id),
            )

    # --------------------------------------- refinement writers (Phase E.25)
    def add_strengths(self, user_id: str, items: list[str], cap: int = 12) -> list[str]:
        """Union inferred strengths, order-preserving, capped. Never removes."""
        cleaned = [str(s).strip()[:120] for s in items if str(s).strip()][:cap]
        profile = self.get(user_id) or self.upsert_basics(user_id)
        merged = list(profile.strengths or [])
        for s in cleaned:
            if s.lower() not in {str(x).lower() for x in merged}:
                merged.append(s)
        merged = merged[:cap]
        with self.db.transaction() as conn:
            conn.execute(
                "update user_profiles set strengths = %s::jsonb, updated_at = now() where user_id = %s",
                (json.dumps(merged), user_id),
            )
        return merged

    def maybe_upgrade_capability(self, user_id: str, level: str | None) -> tuple[bool, str]:
        """Raise `capability_level` on confident evidence — never lower it.

        There is no separate "explicit choice" flag, so any stored non-unknown
        value is treated as explicit: refinement may set it from `unknown` or
        raise it, but never downgrade. Returns (changed, current value).
        """
        if not level:
            current = self.get(user_id)
            return False, (current.capability_level if current else "unknown")
        proposed = _clamp(level, CAPABILITY_LEVELS)
        if proposed == "unknown":
            current = self.get(user_id)
            return False, (current.capability_level if current else "unknown")
        rank = {v: i for i, v in enumerate(CAPABILITY_LEVELS)}
        profile = self.get(user_id) or self.upsert_basics(user_id)
        if rank[proposed] <= rank.get(profile.capability_level, 0):
            return False, profile.capability_level
        self.upsert_basics(user_id, capability_level=proposed)
        return True, proposed


def _to_profile(row: dict[str, Any] | None) -> ProfileRecord | None:
    if row is None:
        return None

    def _parsed(value: Any, fallback: Any) -> Any:
        if value is None or value == "":
            return fallback
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return fallback
        return value

    return ProfileRecord(
        user_id=_coerce(row["user_id"]),
        capability_level=row["capability_level"],
        thinking_level=row["thinking_level"],
        goals=row["goals"] or "",
        study_pattern=_parsed(row["study_pattern"], {}),
        strengths=_parsed(row["strengths"], []),
        updated_at=row["updated_at"],
    )