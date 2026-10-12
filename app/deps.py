"""
Session tokens and the current-user dependency.

We issue our own HS256 JWTs rather than delegating to an external identity
provider, so auth works with nothing installed but a local Postgres (plan.md D3).
Swapping the issuer later means reimplementing `get_current_user` only — every
router already consumes it.

Security properties that matter here:
  * Passwords are hashed with argon2id and never logged or returned.
  * `get_current_user` rejects anonymous access outright. There is no "dev mode
    that trusts a header", because that is exactly how the pre-Phase-B build
    ended up with no ownership checks at all.
  * `require_owned_*` helpers return 404, never 403, for another user's ids so
    resources are not enumerable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .repo.models import ProjectRecord, SourceRecord

log = logging.getLogger(__name__)

# auto_error=False so a missing header gives our own message, not a bare 403.
_bearer = HTTPBearer(auto_error=False)


class AuthError(HTTPException):
    def __init__(self, detail: str, status_code: int = status.HTTP_401_UNAUTHORIZED):
        super().__init__(status_code=status_code, detail=detail)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request):
    return request.app.state.db


def get_store(request: Request):
    return request.app.state.store


def get_storage(request: Request):
    return request.app.state.storage


def get_transcriber(request: Request):
    return request.app.state.transcriber


def get_users(request: Request):
    return request.app.state.users


def get_projects(request: Request):
    return request.app.state.projects


def get_sources(request: Request):
    return request.app.state.sources


def get_topics(request: Request):
    return request.app.state.topics


def get_profiles(request: Request):
    return request.app.state.profiles


def get_pipeline(request: Request):
    return request.app.state.pipeline


def get_chats(request: Request):
    return request.app.state.chats


def get_memory(request: Request):
    return request.app.state.memory


def get_chat_service(request: Request):
    return request.app.state.chat


def get_quizzes(request: Request):
    return request.app.state.quizzes


def get_quiz_service(request: Request):
    return request.app.state.quiz


def get_studio(request: Request):
    return request.app.state.studio


# ============================================================ token issuing
def create_access_token(user_id: str, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.auth_token_ttl_s)).timestamp()),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm=settings.auth_algorithm)


def decode_access_token(token: str, settings: Settings) -> str:
    """Return the user id, or raise AuthError. Never trust the payload unverified."""
    if not settings.auth_secret_key:
        raise AuthError("Authentication is not configured on this server.")
    try:
        payload = jwt.decode(token, settings.auth_secret_key, algorithms=[settings.auth_algorithm])
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired — please log in again.") from None
    except jwt.InvalidTokenError:
        raise AuthError("Invalid session token.") from None
    subject = payload.get("sub")
    if not subject:
        raise AuthError("Invalid session token.")
    return str(subject)


# ====================================================== current user
async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
):
    """The authenticated user, or 401.

    Every project-scoped route depends on this. There is deliberately no
    anonymous fallback — the previous build let anyone read or delete another
    learner's documents by guessing an id.
    """
    if credentials is None or not credentials.credentials:
        raise AuthError("Authentication required.")
    settings: Settings = request.app.state.settings
    user_id = decode_access_token(credentials.credentials, settings)
    user = request.app.state.users.get(user_id)
    if user is None:
        # Token is validly signed but the user is gone — treat as unauthorized
        # rather than leaking that the account does not exist.
        raise AuthError("Session is no longer valid.")
    return user


CurrentUser = Annotated[object, Depends(get_current_user)]


async def require_admin(user: CurrentUser):
    """Admin-only gate for operator endpoints (settings, engine updates...).

    403 (not 404) is deliberate: unlike resource ids, the existence of an
    admin surface is not a secret, and 403 tells a legitimate non-admin user
    to ask their admin/host instead of retrying. The first registered user is
    admin; website hosters pin config via `.env` (locked keys) instead.
    """
    if not getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


AdminUser = Annotated[object, Depends(require_admin)]


def require_no_drift(request: Request) -> None:
    """409 when the embedding space drifted (model swap without re-embed).

    Evaluated FRESH on every guarded call (three cheap indexed queries, no
    probe): a boot-time snapshot would miss drift that appears later (admin
    edits ground truth only via ingest/job/PATCH, but staleness here fails
    open into corrupt retrieval — the one failure this guard exists to stop).
    On DB error the guard allows the request through so the endpoint surfaces
    the real database error instead of masking it.

    Vector writes AND reads would fail or silently corrupt below this point
    (wrong-width insert / wrong-space retrieval), so guarded routes refuse
    with the fix instead: re-embed, or revert to the previous model. Reads
    (history, topics, files, settings, status) stay open — the app loads and
    the banner explains.
    """
    try:
        from .drift import compute_drift

        state = compute_drift(request.app.state.settings, request.app.state.db)
    except Exception:
        return
    if state.drift:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Embedding drift: " + (state.detail or "re-embed required."),
                "reembed": "POST /system/reembed (admin)",
                "status": "GET /system/settings/status",
                **({"revert_to": state.revert_to} if state.revert_to else {}),
            },
        )


NoDrift = Annotated[None, Depends(require_no_drift)]


# ================================================ ownership-scoped lookups
def require_project(projects, project_id: str, user_id: str) -> ProjectRecord:
    """404 unless the project belongs to this user."""
    project = projects.get_owned(project_id, user_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found.",
        )
    return project


def require_source(sources, source_id: str, user_id: str) -> SourceRecord:
    """404 unless the source belongs to one of this user's projects."""
    source = sources.get_owned(source_id, user_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found.",
        )
    return source


def require_chat(chats, chat_id: str, user_id: str):
    """404 unless the chat belongs to one of this user's projects."""
    chat = chats.get_owned(chat_id, user_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found.",
        )
    return chat


def require_quiz(quizzes, quiz_id: str, user_id: str):
    """404 unless the quiz is the caller's — the answer endpoint's boundary."""
    quiz = quizzes.get_quiz_owned(quiz_id, user_id)
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found.",
        )
    return quiz
