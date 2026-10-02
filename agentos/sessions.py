"""Chat sessions: one conversation = one task/project (ChatGPT-style).

Each session is a JSON file under chats/ holding its own title, project
folder, and message list, so switching tasks means switching sessions --
the LLM gets this session's history only, never another task's. chats/ is
gitignored (it is local conversation data).
"""
import json
import os
import time
import uuid

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "chats")

MAX_CONTEXT_MESSAGES = 12      # how many messages a prompt carries
MAX_CONTEXT_CHARS = 600        # per message in that context


class SessionStore:
    def __init__(self, directory: str | None = None):
        self.directory = os.path.abspath(directory or DEFAULT_DIR)
        os.makedirs(self.directory, exist_ok=True)

    # ---------------- files ---------------- #

    def _path(self, session_id: str) -> str:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return os.path.join(self.directory, f"{safe}.json")

    def create(self, title: str = "New task", project: str = "") -> dict:
        session = {
            "id": f"{int(time.time())}_{uuid.uuid4().hex[:8]}",
            "title": title,
            "project": project,
            "created": time.time(),
            "updated": time.time(),
            "messages": [],
        }
        self.save(session)
        return session

    def save(self, session: dict) -> None:
        session["updated"] = time.time()
        try:
            with open(self._path(session["id"]), "w", encoding="utf-8") as f:
                json.dump(session, f, ensure_ascii=False, indent=1)
        except OSError:
            pass  # never crash the chat over a persistence hiccup

    def load(self, session_id: str) -> dict | None:
        try:
            with open(self._path(session_id), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict]:
        """All sessions, newest first (title/updated only -- cheap)."""
        out = []
        for name in os.listdir(self.directory):
            if not name.endswith(".json"):
                continue
            session = self.load(name[:-5])
            if not session:
                continue
            out.append({k: session.get(k) for k in
                        ("id", "title", "project", "updated", "created")})
        out.sort(key=lambda s: s.get("updated") or 0, reverse=True)
        return out

    def delete(self, session_id: str) -> None:
        try:
            os.remove(self._path(session_id))
        except OSError:
            pass

    def rename(self, session_id: str, title: str) -> dict | None:
        session = self.load(session_id)
        if not session:
            return None
        session["title"] = (title or "").strip() or session["title"]
        self.save(session)
        return session

    # ---------------- prompt context ---------------- #

    @staticmethod
    def context_block(session: dict) -> str:
        """The task-scoped header every message carries, so the model knows
        which task/project it is working on and nothing from other chats."""
        lines = [f"Task: {session.get('title') or 'New task'}"]
        if session.get("project"):
            lines.append(f"Project: {session['project']}")
        recent = [m for m in session.get("messages", [])
                  if m.get("role") in ("user", "assistant")][-MAX_CONTEXT_MESSAGES:]
        if recent:
            lines.append("Conversation so far in THIS task only:")
            for m in recent:
                text = (m.get("text") or "").replace("\n", " ").strip()
                lines.append(f"{m.get('role', '?').capitalize()}: {text[:MAX_CONTEXT_CHARS]}")
        return "\n".join(lines)
