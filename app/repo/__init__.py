"""
Repositories — the only place SQL lives.

Routers and services call these; they never build queries themselves. Keeping the
Postgres surface in one directory also makes the tenancy scoping (`project_id`,
`user_id`) auditable at a glance, which matters because it is the core guarantee.
"""

from .base import Repo, to_record, to_records
from .chats import ChatRepo
from .memory import ProjectMemoryRepo
from .projects import ProjectRepo
from .quizzes import QuizRepo
from .sources import SourceRepo
from .studio import StudioRepo
from .topics import TopicRepo
from .users import UserRepo, hash_password, verify_password

__all__ = [
    "Repo",
    "to_record",
    "to_records",
    "ChatRepo",
    "ProjectMemoryRepo",
    "ProjectRepo",
    "QuizRepo",
    "SourceRepo",
    "StudioRepo",
    "TopicRepo",
    "UserRepo",
    "hash_password",
    "verify_password",
]
