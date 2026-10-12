"""User repository + password hashing (argon2).

Identity is a real `users` row with a real password hash, issued as a JWT by the
auth router (plan.md D3). This replaces the anonymous localStorage id that had no
auth and let anyone read or delete another user's documents by guessing an id.
"""

from __future__ import annotations

import uuid
from typing import Any

from .base import Repo, to_record
from .models import UserRecord

_FIELDS = ("id", "email", "display_name", "created_at", "is_admin")
_COLUMNS = ", ".join(_FIELDS)


def hash_password(password: str) -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError

    try:
        return PasswordHasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


class UserRepo(Repo):
    def create(self, email: str, password: str, display_name: str) -> UserRecord | None:
        """Register a user. Returns None if the email is already taken.

        The first user in an empty table becomes admin (single-user desktop
        installs need no extra step; concurrent double-signups may rarely both
        win the race — the first admin can demote in a later phase).
        """
        try:
            with self.db.transaction() as conn:
                count = conn.execute("select count(*) as n from users").fetchone()["n"]
                row = conn.execute(
                    f"insert into users (id, email, password_hash, display_name, is_admin) "
                    f"values (%s, %s, %s, %s, %s) returning {_COLUMNS}",
                    (str(uuid.uuid4()), email.strip().lower(), hash_password(password),
                     display_name, count == 0),
                ).fetchone()
        except Exception as exc:  # unique violation
            if "duplicate key" in str(exc).lower() or "unique" in str(exc).lower():
                return None
            raise
        return to_record(UserRecord, row)

    def get_by_email_with_hash(self, email: str) -> dict[str, Any] | None:
        return self._fetch_one(
            "select id, email, display_name, password_hash, created_at, is_admin "
            "from users where email = %s",
            (email.strip().lower(),),
        )

    def get(self, user_id: str) -> UserRecord | None:
        row = self._fetch_one(f"select {_COLUMNS} from users where id = %s", (user_id,))
        return to_record(UserRecord, row)

    def display_name(self, user_id: str) -> str:
        row = self._fetch_one("select display_name from users where id = %s", (user_id,))
        return row["display_name"] if row else "Learner"