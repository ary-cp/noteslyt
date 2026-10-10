"""Tier-3 chat memory: server-side chats + messages — Phase E.22/E.23.

A chat survives reloads and device changes; the client never sends `history`.
Windowing (`MEMORY_CHAT_WINDOW` verbatim + rolling `summary` past
`MEMORY_SUMMARIZE_THRESHOLD`) is computed by the service in `app/chat.py` —
this repo only stores, counts and pages.

Ownership mirrors projects: every lookup joins `projects` so another
learner's chat id returns None (the router turns that into 404, never 403).
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Json

from .base import Repo, to_record, to_records
from .models import ChatMessageRecord, ChatRecord

_CHAT_FIELDS = (
    "id", "project_id", "user_id", "title", "summary",
    "summarized_through", "created_at", "last_message_at",
)
_CHAT_COLUMNS = ", ".join(_CHAT_FIELDS)

_MSG_FIELDS = ("id", "chat_id", "role", "content", "citations", "grounded", "created_at")
_MSG_COLUMNS = ", ".join(_MSG_FIELDS)


class ChatRepo(Repo):
    # ------------------------------------------------------------ chats
    def create(self, project_id: str, user_id: str, title: str = "New chat") -> ChatRecord:
        row = self._fetch_one(
            f"insert into chats (id, project_id, user_id, title) values (%s, %s, %s, %s) "
            f"returning {_CHAT_COLUMNS}",
            (str(uuid.uuid4()), project_id, user_id, (title or "New chat")[:120]),
        )
        return to_record(ChatRecord, row)  # type: ignore[return-value]

    def list_for_project(self, project_id: str, user_id: str) -> list[dict[str, Any]]:
        """Newest first, with message counts — no N+1 for the chat list."""
        rows = self._fetch_all(
            "select c.id, c.project_id, c.title, c.created_at, c.last_message_at, "
            "count(m.id)::int as message_count "
            "from chats c left join chat_messages m on m.chat_id = c.id "
            "join projects p on p.id = c.project_id "
            "where c.project_id = %s and p.user_id = %s "
            "group by c.id order by c.last_message_at desc",
            (project_id, user_id),
        )
        return [{**r, "id": str(r["id"]), "project_id": str(r["project_id"])} for r in rows]

    def get_owned(self, chat_id: str, user_id: str) -> ChatRecord | None:
        row = self._fetch_one(
            f"select {', '.join('c.' + f for f in _CHAT_FIELDS)} from chats c "
            f"join projects p on p.id = c.project_id "
            f"where c.id = %s and p.user_id = %s",
            (chat_id, user_id),
        )
        return to_record(ChatRecord, row)

    def rename(self, chat_id: str, user_id: str, title: str) -> ChatRecord | None:
        row = self._fetch_one(
            f"update chats c set title = %s from projects p "
            f"where c.id = %s and c.project_id = p.id and p.user_id = %s "
            f"returning {', '.join('c.' + f for f in _CHAT_FIELDS)}",
            ((title or "New chat")[:120], chat_id, user_id),
        )
        return to_record(ChatRecord, row)

    def delete_owned(self, chat_id: str, user_id: str) -> bool:
        return (
            self._execute(
                "delete from chats c using projects p "
                "where c.id = %s and c.project_id = p.id and p.user_id = %s",
                (chat_id, user_id),
            )
            > 0
        )

    # ---------------------------------------------------------- messages
    def add_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
        grounded: bool | None = None,
    ) -> ChatMessageRecord:
        row = self._fetch_one(
            f"insert into chat_messages (id, chat_id, role, content, citations, grounded) "
            f"values (%s, %s, %s, %s, %s, %s) returning {_MSG_COLUMNS}",
            (str(uuid.uuid4()), chat_id, role, content, Json(citations or []), grounded),
        )
        self._execute("update chats set last_message_at = now() where id = %s", (chat_id,))
        return to_record(ChatMessageRecord, row)  # type: ignore[return-value]

    def list_messages(self, chat_id: str, limit: int = 50, offset: int = 0) -> list[ChatMessageRecord]:
        rows = self._fetch_all(
            f"select {_MSG_COLUMNS} from chat_messages "
            f"where chat_id = %s order by created_at asc limit %s offset %s",
            (chat_id, max(1, min(limit, 200)), max(0, offset)),
        )
        return to_records(ChatMessageRecord, rows)  # type: ignore[return-value]

    def count_messages(self, chat_id: str) -> int:
        with self.db.connection() as conn:
            row = conn.execute(
                "select count(*) as n from chat_messages where chat_id = %s", (chat_id,)
            ).fetchone()
        return int(row["n"])

    def recent_messages(self, chat_id: str, limit: int = 10) -> list[ChatMessageRecord]:
        rows = self._fetch_all(
            f"select {_MSG_COLUMNS} from chat_messages where chat_id = %s "
            "order by created_at desc, id desc limit %s",
            (chat_id, max(1, min(limit, 200))),
        )
        return to_records(ChatMessageRecord, list(reversed(rows)))

    def unsummarized_messages(self, chat_id: str, through_id: str | None, keep_recent: int):
        rows = self._fetch_all(
            "with ranked as (select *, row_number() over (order by created_at desc, id desc) as rn "
            "from chat_messages where chat_id=%s) "
            f"select {_MSG_COLUMNS} from ranked where rn > %s "
            "and (%s::uuid is null or (created_at,id) > "
            "(select created_at,id from chat_messages where id=%s)) "
            "order by created_at, id limit 50",
            (chat_id, keep_recent, through_id, through_id),
        )
        return to_records(ChatMessageRecord, rows)

    def user_message_count(self, chat_id: str) -> int:
        with self.db.connection() as conn:
            row = conn.execute(
                "select count(*) as n from chat_messages where chat_id = %s and role = 'user'",
                (chat_id,),
            ).fetchone()
        return int(row["n"])

    def recent_user_messages(self, user_id: str, limit: int = 20) -> list[str]:
        """Latest learner utterances across all their chats — refinement input."""
        rows = self._fetch_all(
            "select m.content from chat_messages m join chats c on c.id = m.chat_id "
            "where c.user_id = %s and m.role = 'user' "
            "order by m.created_at desc limit %s",
            (user_id, limit),
        )
        return [r["content"] for r in rows]

    def set_summary(self, chat_id: str, summary: str, through_id: str) -> None:
        self._execute(
            "update chats set summary = %s, summarized_through = %s where id = %s",
            (summary[:4000], through_id, chat_id),
        )
