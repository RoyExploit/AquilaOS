"""Phase 1 memory: project + task + event log, backed by SQLite so it
survives across runs. Long-term/skill/preference memory layers are
Phase 2+ (this gives Core Brain and the Repair Engine a real, queryable
history to work from instead of an in-memory list that vanishes on exit).
"""
import sqlite3
import json
import time
from pathlib import Path


class MemoryManager:
    def __init__(self, db_path: str = "agentos_memory.sqlite3"):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT, created_at REAL, status TEXT, plan_json TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER, ts REAL, kind TEXT, action_id TEXT, detail TEXT
            )""")

    def create_project(self, goal: str, plan: dict) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO projects (goal, created_at, status, plan_json) VALUES (?,?,?,?)",
                (goal, time.time(), "planned", json.dumps(plan)),
            )
            return cur.lastrowid

    def log(self, project_id: int, kind: str, action_id: str = "", detail: str = ""):
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (project_id, ts, kind, action_id, detail) VALUES (?,?,?,?,?)",
                (project_id, time.time(), kind, action_id, detail),
            )

    def set_status(self, project_id: int, status: str):
        with self._conn() as c:
            c.execute("UPDATE projects SET status=? WHERE id=?", (status, project_id))

    def history(self, project_id: int):
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, kind, action_id, detail FROM events WHERE project_id=? ORDER BY ts",
                (project_id,),
            ).fetchall()
        return [{"ts": r[0], "kind": r[1], "action_id": r[2], "detail": r[3]} for r in rows]
