"""
Authentication: signup, login, whoami, onboarding.

Phase B replaces the anonymous `localStorage` id that used to scope the learner
model. That id was never verified, so anyone who guessed a document id could read
or delete another learner's material.

Design notes:
  * `/auth/login` returns the same error for an unknown email and a wrong password
    so the endpoint cannot be used to enumerate accounts.
  * Argon2 hashing is deliberately expensive; `verify_password` short-circuits on a
    missing user *without* hashing so a login attempt for an unknown email is fast,
    but we still return a generic error.
  * Onboarding is optional and idempotent — a learner can skip it and fill it in later.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status

from ..deps import AuthError, CurrentUser, create_access_token, get_settings, get_users
from ..repo.users import UserRepo, verify_password
from ..schemas import (
    LoginRequest,
    OnboardingRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Incorrect email or password."


def _token_response(user, settings) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id, settings),
        expires_in=settings.auth_token_ttl_s,
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name,
                     is_admin=getattr(user, "is_admin", False)),
    )


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    users: Annotated[UserRepo, Depends(get_users)],
    settings=Depends(get_settings),
) -> TokenResponse:
    """
    Register and return a session token, so the client is logged in immediately.

    A duplicate email returns 409 rather than a different message for "exists",
    which would leak account existence on a registration endpoint.
    """
    if len(payload.password) < settings.password_min_length:
        raise AuthError(
            f"Password must be at least {settings.password_min_length} characters.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    user = users.create(payload.email, payload.password, payload.display_name.strip())
    if user is None:
        raise AuthError(
            "That email is already registered.",
            status.HTTP_409_CONFLICT,
        )
    log.info("signup      : %s (%s)", user.email, user.id)
    return _token_response(user, settings)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    users: Annotated[UserRepo, Depends(get_users)],
    settings=Depends(get_settings),
) -> TokenResponse:
    """Exchange email + password for a session token."""
    row = users.get_by_email_with_hash(payload.email)
    if row is None:
        # Same message as a wrong password: do not reveal that the email is free.
        raise AuthError(GENERIC_LOGIN_ERROR)
    if not verify_password(row["password_hash"], payload.password):
        raise AuthError(GENERIC_LOGIN_ERROR)

    user = users.get(str(row["id"]))
    if user is None:
        raise AuthError(GENERIC_LOGIN_ERROR)
    return _token_response(user, settings)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """Validate the caller's token and echo back their identity."""
    return UserOut(id=user.id, email=user.email, display_name=user.display_name,
                   is_admin=getattr(user, "is_admin", False))


@router.post("/onboarding", status_code=status.HTTP_204_NO_CONTENT)
def onboarding(
    payload: OnboardingRequest,
    user: CurrentUser,
    users: Annotated[UserRepo, Depends(get_users)],
) -> None:
    """
    Seed tier-1 global memory (plan.md D5).

    Returns 204 with no body: the response is only about acceptance, and
    `GET /profile` is the canonical read. Explicit user input always wins over
    anything inferred from conversation history later.
    """
    from ..repo.profile import ProfileRepo

    ProfileRepo(users.db).upsert_basics(
        user.id,
        capability_level=payload.capability_level,
        thinking_level=payload.thinking_level,
        goals=payload.goals,
    )
    log.info(
        "onboarding  : %s capability=%s thinking=%s",
        user.id, payload.capability_level, payload.thinking_level,
    )