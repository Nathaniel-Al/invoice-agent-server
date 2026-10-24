"""SQLite persistence: tasks, idempotency records, decision cache."""
import json
import os
import sqlite3
import threading

from .config import DB_PATH

_d = os.path.dirname(DB_PATH)
if _d:
    os.makedirs(_d, exist_ok=True)

_db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_db.executescript("""
pragma journal_mode=wal;
create table if not exists tasks(id text primary key, principal text not null, data text not null);
create table if not exists msgs(principal text, mid text, hash text, task_id text, primary key(principal, mid));
create table if not exists cache(key text primary key, val text not null);
create index if not exists tasks_p on tasks(principal);
""")
_lock = threading.Lock()


def q(sql, args=()):
    with _lock:
        return _db.execute(sql, args).fetchall()


def commit(principal, task, mid=None, h=None):
    """Atomically store task state together with its idempotency record."""
    with _lock:
        _db.execute("begin immediate")
        try:
            _db.execute(
                "insert into tasks(id,principal,data) values(?,?,?) "
                "on conflict(id) do update set data=excluded.data",
                (task["id"], principal, json.dumps(task, ensure_ascii=False)))
            if mid is not None:
                _db.execute("insert or ignore into msgs values(?,?,?,?)", (principal, mid, h, task["id"]))
            _db.execute("commit")
        except Exception:
            _db.execute("rollback")
            raise


def load_task(principal, tid):
    r = q("select data from tasks where id=? and principal=?", (tid, principal))
    return json.loads(r[0][0]) if r else None


def message_record(principal, mid):
    r = q("select hash, task_id from msgs where principal=? and mid=?", (principal, mid))
    return (r[0][0], r[0][1]) if r else None


def list_tasks(principal):
    return [json.loads(r[0]) for r in q("select data from tasks where principal=? order by rowid desc", (principal,))]


def cache_get(key):
    r = q("select val from cache where key=?", (key,))
    return json.loads(r[0][0]) if r else None


def cache_put(key, val):
    q("insert or replace into cache values(?,?)", (key, json.dumps(val, ensure_ascii=False)))
