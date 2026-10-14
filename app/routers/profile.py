"""
Global profile (tier-1 memory) read/write.

`GET /profile` returns what the tutor actually knows about the learner, so it is
never a black box (plan.md D5). `PATCH /profile` lets them correct it; the LLM
refinement pass in Phase E only ever appends observational counters, never
overwriting an explicit choice.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..deps import CurrentUser, get_users
from ..repo.profile import CAPABILITY_LEVELS, THINKING_LEVELS, ProfileRepo
from ..repo.users import UserRepo
from ..schemas import OnboardingRequest, ProfileUpdate

router = APIRouter(tags=["profile"])


def _repo(users: UserRepo) -> ProfileRepo:
    return ProfileRepo(users.db)


def _to_response(profile) -> dict:
    return {
        "user_id": profile.user_id,
        "capability_level": profile.capability_level,
        "thinking_level": profile.thinking_level,
        "goals": profile.goals,
        "study_pattern": profile.study_pattern,
        "strengths": profile.strengths,
        "learning_style_notes": getattr(profile, "learning_style_notes", "") or "",
        "irt_ability": float(getattr(profile, "irt_ability", 0.0) or 0.0),
    }


@router.get("/profile")
def get_profile(
    user: CurrentUser,
    users: Annotated[UserRepo, Depends(get_users)],
) -> dict:
    """
    Return the learner's global memory, creating a default (empty) profile on
    first read so callers never have to special-case None.
    """
    repo = _repo(users)
    profile = repo.get(user.id) or repo.upsert_basics(user.id)
    return {**_to_response(profile), "quiz_progress": repo.quiz_progress(user.id)}


@router.patch("/profile")
def update_profile(
    payload: ProfileUpdate,
    user: CurrentUser,
    users: Annotated[UserRepo, Depends(get_users)],
) -> dict:
    """Update the explicit parts of global memory."""
    repo = _repo(users)
    profile = repo.upsert_basics(
        user.id,
        capability_level=payload.capability_level,
        thinking_level=payload.thinking_level,
        goals=payload.goals,
        learning_style_notes=payload.learning_style_notes,
    )
    return {**_to_response(profile), "quiz_progress": repo.quiz_progress(user.id)}


def _report_dict(row) -> dict:
    from ..repo.base import parse_times

    created = getattr(row, "created_at", None)
    if hasattr(created, "isoformat"):
        try:
            created_s = created.isoformat()
        except Exception:
            created_s = str(created)
    else:
        created_s = str(created or "")

    def _as_dict(value):
        import json as _json

        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = _json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except ValueError:
                return {}
        return {}

    def _as_list(value):
        import json as _json

        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            try:
                parsed = _json.loads(value)
                return [str(v) for v in parsed] if isinstance(parsed, list) else []
            except ValueError:
                return []
        return []

    return {
        "id": str(row.id),
        "project_id": str(getattr(row, "project_id", "")),
        "kind": getattr(row, "kind", "quiz") or "quiz",
        "ref_id": str(getattr(row, "ref_id", "") or ""),
        "summary": str(getattr(row, "summary", "") or ""),
        "bkt": _as_dict(getattr(row, "bkt", {})),
        "irt": _as_dict(getattr(row, "irt", {})),
        "strengths": _as_list(getattr(row, "strengths", [])),
        "weaknesses": _as_list(getattr(row, "weaknesses", [])),
        "created_at": created_s,
    }


@router.get("/profile/reports")
def list_reports(
    user: CurrentUser,
    users: Annotated[UserRepo, Depends(get_users)],
    limit: int = 20,
) -> dict:
    """Recent personalised BKT+IRT reports (quiz/exam/chat), newest first."""
    from ..repo.learner_reports import LearnerReportRepo

    repo = LearnerReportRepo(users.db)
    rows = repo.list_for_user(user.id, limit=limit)
    return {"reports": [_report_dict(r) for r in rows]}


@router.get("/profile/overview")
def profile_overview(
    user: CurrentUser,
    users: Annotated[UserRepo, Depends(get_users)],
) -> dict:
    """Aggregated Learning Profile page: mastery, weak/strong, improvements, reports."""
    from ..learner.bkt import label as bkt_label
    from ..learner.irt import ability_label
    from ..repo.learner_reports import LearnerReportRepo
    from ..repo.memory import ProjectMemoryRepo

    db = users.db
    repo = _repo(users)
    profile = repo.get(user.id) or repo.upsert_basics(user.id)
    progress = repo.quiz_progress(user.id)

    # All projects owned by this user; aggregate every topic memory row.
    with db.connection() as conn:
        projects = conn.execute(
            "select id, title from projects where user_id = %s order by created_at desc",
            (user.id,),
        ).fetchall()
    project_titles = {str(r["id"]): str(r.get("title") or "") for r in (projects or [])}

    memory = ProjectMemoryRepo(db)
    topics: list[dict] = []
    for pid in list(project_titles):
        try:
            rows = memory.list_for_project(user.id, pid)
        except Exception:
            continue
        for r in rows:
            topics.append({
                "topic_id": str(r.topic_id),
                "slug": r.slug,
                "name": r.name,
                "description": r.description or "",
                "p_mastery": round(float(r.p_mastery), 4),
                "attempts": int(r.attempts),
                "correct": int(r.correct),
                "coverage_status": r.coverage_status,
                "easiness": float(getattr(r, "easiness", 2.5) or 2.5),
                "repetitions": int(getattr(r, "repetitions", 0) or 0),
                "interval_days": float(getattr(r, "interval_days", 0.0) or 0.0),
                "due_at": r.due_at.isoformat() if getattr(r, "due_at", None) else None,
                "label": memory.mastery_label(float(r.p_mastery)),
                "last_practiced_at": r.last_practiced_at.isoformat() if getattr(r, "last_practiced_at", None) else None,
                "project_id": pid,
                "project_title": project_titles.get(pid, ""),
            })
    topics.sort(key=lambda t: (t["p_mastery"], t["name"]))
    weak = [t for t in topics if t["label"] == "weak"][:10]
    if not weak:
        weak = topics[:5]
    strong = sorted(
        [t for t in topics if t["label"] == "strong"],
        key=lambda t: (-t["p_mastery"], t["name"]),
    )[:10]

    areas: list[str] = []
    for t in weak[:5]:
        pname = f" in {t['project_title']}" if t.get("project_title") else ""
        areas.append(f"Strengthen {t['name']}{pname} (mastery {int(t['p_mastery']*100)}%, {t['attempts']} attempts)")
    # Misconception hints from the newest assessment reports.
    try:
        with db.connection() as conn:
            arows = conn.execute(
                "select report from assessments where user_id = %s order by created_at desc limit %s",
                (user.id, 3),
            ).fetchall()
        import json as _json

        seen = set()
        for ar in (arows or []):
            rep = ar.get("report")
            if isinstance(rep, str):
                try:
                    rep = _json.loads(rep)
                except ValueError:
                    continue
            if not isinstance(rep, dict):
                continue
            for m in (rep.get("misconceptions") or [])[:3]:
                if not isinstance(m, dict):
                    continue
                for pt in (m.get("points") or [])[:2]:
                    text = str(pt).strip()
                    if text and text not in seen and len(areas) < 10:
                        seen.add(text)
                        areas.append(text)
    except Exception:
        pass

    reports = LearnerReportRepo(db).list_for_user(user.id, limit=10)
    ability = float(getattr(profile, "irt_ability", 0.0) or 0.0)
    return {
        "user_id": user.id,
        "capability_level": profile.capability_level,
        "thinking_level": profile.thinking_level,
        "goals": profile.goals,
        "learning_style_notes": getattr(profile, "learning_style_notes", "") or "",
        "irt_ability": ability,
        "irt_label": ability_label(ability),
        "quiz_progress": progress,
        "topics": topics,
        "weak_topics": weak,
        "strong_topics": strong,
        "areas_of_improvement": areas[:10],
        "recent_reports": [_report_dict(r) for r in reports],
    }


@router.get("/profile/levels")
def profile_levels() -> dict:
    """The accepted enum values, so the client renders exactly what we accept."""
    return {
        "capability_levels": list(CAPABILITY_LEVELS),
        "thinking_levels": list(THINKING_LEVELS),
    }