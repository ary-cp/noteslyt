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
    return _to_response(profile)


@router.patch("/profile")
def update_profile(
    payload: ProfileUpdate,
    user: CurrentUser,
    users: Annotated[UserRepo, Depends(get_users)],
) -> dict:
    """
    Update the explicit parts of global memory.

    Omitted fields are left alone, so editing only the goals cannot quietly
    downgrade a capability level the learner already chose.
    """
    repo = _repo(users)
    profile = repo.upsert_basics(
        user.id,
        capability_level=payload.capability_level,
        thinking_level=payload.thinking_level,
        goals=payload.goals,
    )
    return _to_response(profile)


@router.get("/profile/levels")
def profile_levels() -> dict:
    """The accepted enum values, so the client renders exactly what we accept."""
    return {
        "capability_levels": list(CAPABILITY_LEVELS),
        "thinking_levels": list(THINKING_LEVELS),
    }