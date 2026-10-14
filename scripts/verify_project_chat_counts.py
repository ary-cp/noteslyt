"""Project cards count live chats and never include another user's chats."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.sqlite_db import SqliteDatabase
from app.repo.projects import ProjectRepo

with tempfile.TemporaryDirectory() as tmp:
    db = SqliteDatabase(SimpleNamespace(sqlite_path=Path(tmp)/'counts.db', sqlite_migrations_dir=Path(__file__).resolve().parents[1]/'migrations/sqlite'))
    with db.transaction() as conn:
        conn.execute('create table projects (id text primary key, user_id text)')
        conn.execute('create table chats (id text primary key, project_id text, user_id text)')
        for pid, uid in [('a','alice'),('empty','alice'),('b','bob')]:
            conn.execute('insert into projects values (%s,%s)',(pid,uid))
        for cid,pid,uid in [('1','a','alice'),('2','a','alice'),('3','b','bob'),('4','a','bob')]:
            conn.execute('insert into chats values (%s,%s,%s)',(cid,pid,uid))
    repo=ProjectRepo(db)
    assert repo.chat_counts_for_user('alice') == {'a':2}
    assert repo.chat_counts_for_user('bob') == {'b':1}
    assert repo.chat_counts_for_user('unknown') == {}
    with db.transaction() as conn:
        conn.execute('delete from chats where id=%s',('1',))
    assert repo.chat_counts_for_user('alice') == {'a':1}
    db.close()
print('PROJECT CHAT COUNTS VERIFIED: create/delete totals, empty projects, user isolation')
