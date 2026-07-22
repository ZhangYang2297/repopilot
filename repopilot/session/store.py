"""Session Store: JSONL event log with SQLite index for session metadata.

Each session is a JSONL file under ~/.repopilot/sessions/YYYY/MM/DD/.
Every line is an event: {"id": int, "ts": ISO8601, "type": str, "payload": dict}.
A SQLite database at ~/.repopilot/state.sqlite indexes sessions for fast listing.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

VALID_EVENT_TYPES = frozenset({
    "user_msg", "assistant_msg", "tool_call", "tool_result",
    "plan", "replan", "finish", "error", "slash", "system",
    "compact", "cost",
})


@dataclass
class Session:
    """An active or persisted session."""
    id: str
    title: str = ""
    cwd: str = ""
    model: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    rollout_path: str = ""
    tokens_used: int = 0
    last_event_id: int = 0


@dataclass
class SessionMeta:
    """Lightweight metadata for listing sessions."""
    id: str
    title: str
    cwd: str
    model: str
    created_at: float
    updated_at: float
    tokens_used: int
    event_count: int


class SessionStore:
    """Append-only JSONL session store with SQLite index.

    Thread-safe: a lock serializes writes to the same session file.
    """

    def __init__(self, sessions_dir: Path, db_path: Optional[Path] = None):
        self.sessions_dir = Path(sessions_dir).expanduser().resolve()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.sessions_dir.parent / "state.sqlite"
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._init_db()

    # ---------------------------------------------------------------- SQLite

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    rollout_path TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    cwd TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    tokens_used INTEGER DEFAULT 0,
                    last_event_id INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def _get_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    # ---------------------------------------------------------------- Session lifecycle

    def create(
        self,
        title: str = "",
        cwd: str = "",
        model: str = "",
    ) -> Session:
        """Create a new session and return it."""
        now = time.time()
        sid = uuid.uuid4().hex[:12]
        date_part = datetime.fromtimestamp(now, tz=timezone.utc)
        day_dir = self.sessions_dir / f"{date_part.year:04d}" / f"{date_part.month:02d}" / f"{date_part.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)
        rollout_path = day_dir / f"rollout-{int(now)}-{sid}.jsonl"
        rollout_path.touch()

        session = Session(
            id=sid,
            title=title or f"Session {sid[:8]}",
            cwd=cwd,
            model=model,
            created_at=now,
            updated_at=now,
            rollout_path=str(rollout_path),
            last_event_id=0,
        )
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO threads (id, rollout_path, title, cwd, model, created_at, updated_at, tokens_used, last_event_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session.id, session.rollout_path, session.title, session.cwd,
                 session.model, int(session.created_at * 1000), int(session.updated_at * 1000),
                 session.tokens_used, session.last_event_id),
            )
            conn.commit()
        return session

    def get(self, session_id: str) -> Optional[Session]:
        """Load session metadata from SQLite."""
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT id, rollout_path, title, cwd, model, created_at, updated_at, tokens_used, last_event_id "
                "FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return Session(
            id=row[0], rollout_path=row[1], title=row[2], cwd=row[3],
            model=row[4], created_at=row[5] / 1000, updated_at=row[6] / 1000,
            tokens_used=row[7] or 0, last_event_id=row[8] or 0,
        )

    def list(self, limit: int = 50) -> list[SessionMeta]:
        """List recent sessions ordered by updated_at DESC."""
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id, title, cwd, model, created_at, updated_at, tokens_used, last_event_id "
                "FROM threads ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        results = []
        for r in rows:
            results.append(SessionMeta(
                id=r[0], title=r[1], cwd=r[2], model=r[3],
                created_at=r[4] / 1000, updated_at=r[5] / 1000,
                tokens_used=r[6] or 0, event_count=r[7] or 0,
            ))
        return results

    def delete(self, session_id: str) -> bool:
        """Delete a session (remove JSONL file and SQLite row)."""
        session = self.get(session_id)
        if session is None:
            return False
        lock = self._get_lock(session_id)
        with lock:
            rollout = Path(session.rollout_path)
            if rollout.exists():
                rollout.unlink()
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("DELETE FROM threads WHERE id = ?", (session_id,))
                conn.commit()
        return True

    # ---------------------------------------------------------------- Events

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> dict:
        """Atomically append an event to the session JSONL and update index.

        Returns the event dict (with assigned id and timestamp).
        """
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event type: {event_type!r}. Must be one of {sorted(VALID_EVENT_TYPES)}")

        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")

        now = ts if ts is not None else time.time()
        lock = self._get_lock(session_id)
        with lock:
            # Re-read last_event_id from file (authoritative) to avoid race
            next_id = self._count_lines(session.rollout_path) + 1
            event = {
                "id": next_id,
                "ts": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                "type": event_type,
                "payload": payload or {},
            }
            with open(session.rollout_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

            tokens_delta = 0
            if event_type in ("assistant_msg",) and payload:
                tokens_delta = payload.get("usage", {}).get("total_tokens", 0)
            if event_type == "cost" and payload:
                tokens_delta = payload.get("tokens", 0)

            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    "UPDATE threads SET updated_at = ?, last_event_id = ?, tokens_used = tokens_used + ? "
                    "WHERE id = ?",
                    (int(now * 1000), next_id, tokens_delta, session_id),
                )
                conn.commit()
        return event

    def read_events(self, session_id: str, after_id: int = 0) -> list[dict]:
        """Read events from a session, optionally starting after a given id."""
        session = self.get(session_id)
        if session is None:
            return []
        events: list[dict] = []
        with open(session.rollout_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("id", 0) > after_id:
                    events.append(evt)
        return events

    def rewind(self, session_id: str, step: int) -> list[dict]:
        """Truncate the session to keep only the first `step` events.

        Returns the remaining events (the kept prefix).
        """
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        if step < 0:
            raise ValueError("step must be >= 0")

        lock = self._get_lock(session_id)
        with lock:
            all_events = self.read_events(session_id, after_id=0)
            kept = all_events[:step]
            # Rewrite the file with only kept events
            with open(session.rollout_path, "w", encoding="utf-8") as f:
                for evt in kept:
                    f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            last_id = kept[-1]["id"] if kept else 0
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    "UPDATE threads SET last_event_id = ?, updated_at = ? WHERE id = ?",
                    (last_id, int(time.time() * 1000), session_id),
                )
                conn.commit()
        return kept

    # ---------------------------------------------------------------- Helpers

    @staticmethod
    def _count_lines(path: str) -> int:
        """Count lines in a file efficiently."""
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return 0
        count = 0
        with open(path, "rb") as f:
            for _ in f:
                count += 1
        return count

    def update_title(self, session_id: str, title: str) -> None:
        """Update session title."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("UPDATE threads SET title = ? WHERE id = ?", (title, session_id))
            conn.commit()
