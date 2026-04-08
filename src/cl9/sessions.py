"""Project-local agent session and process state."""

from __future__ import annotations

import json
import os
import sqlite3

from cl9.runtime import remove_runtime
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


def _now() -> str:
    """Return the current timestamp."""
    return datetime.now().isoformat()


@dataclass(frozen=True)
class SessionTarget:
    """Resolved session target."""

    session_id: str
    name: Optional[str]
    profile: str
    metadata: Optional[dict] = None


class ProjectState:
    """Manage project-local SQLite state under .cl9/state.db."""

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.db_path = self.project_root / ".cl9" / "state.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    name TEXT,
                    profile TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    source_cwd TEXT NOT NULL,
                    forked_from_session_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_processes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    pid INTEGER,
                    status TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    ended_at TEXT,
                    exit_code INTEGER,
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_processes_one_running
                ON agent_processes(session_id)
                WHERE status IN ('starting', 'running')
                """
            )
            conn.commit()

    def reconcile_processes(self) -> None:
        """Mark dead running processes as stale."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, pid, status
                FROM agent_processes
                WHERE status IN ('starting', 'running')
                """
            ).fetchall()

            for row in rows:
                pid = row["pid"]
                if pid is None or self._pid_exists(pid):
                    continue

                now = _now()
                conn.execute(
                    """
                    UPDATE agent_processes
                    SET status = 'stale',
                        last_seen_at = ?,
                        ended_at = COALESCE(ended_at, ?)
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
                conn.execute(
                    """
                    UPDATE agent_sessions
                    SET status = 'idle',
                        last_used_at = ?
                    WHERE session_id = ?
                    """,
                    (now, row["session_id"]),
                )

            conn.commit()

    def create_session(
        self,
        session_id: str,
        name: Optional[str],
        profile: str,
        tool: str,
        source_cwd: Path,
        forked_from_session_id: Optional[str] = None,
    ) -> None:
        """Create a new agent session."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    session_id, name, profile, tool, status,
                    created_at, last_used_at, source_cwd, forked_from_session_id, metadata_json
                ) VALUES (?, ?, ?, ?, 'idle', ?, ?, ?, ?, '{}')
                """,
                (
                    session_id,
                    name,
                    profile,
                    tool,
                    now,
                    now,
                    str(source_cwd.resolve()),
                    forked_from_session_id,
                ),
            )
            conn.commit()

    def start_process(self, session_id: str, cwd: Path, command: List[str]) -> int:
        """Insert a starting process row and return its ID."""
        now = _now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_processes (
                    session_id, pid, status, cwd, command_json,
                    started_at, last_seen_at, ended_at, exit_code
                ) VALUES (?, NULL, 'starting', ?, ?, ?, ?, NULL, NULL)
                """,
                (session_id, str(cwd.resolve()), json.dumps(command), now, now),
            )
            conn.execute(
                """
                UPDATE agent_sessions
                SET status = 'active',
                    last_used_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def mark_process_running(self, process_id: int, pid: int) -> None:
        """Attach a PID to a process row."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_processes
                SET pid = ?, status = 'running', last_seen_at = ?
                WHERE id = ?
                """,
                (pid, now, process_id),
            )
            conn.commit()

    def finish_process(self, process_id: int, session_id: str, exit_code: int) -> None:
        """Mark a process as exited and release the session."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_processes
                SET status = 'exited',
                    last_seen_at = ?,
                    ended_at = ?,
                    exit_code = ?
                WHERE id = ?
                """,
                (now, now, exit_code, process_id),
            )
            conn.execute(
                """
                UPDATE agent_sessions
                SET status = 'idle',
                    last_used_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )
            conn.commit()

    def fail_process_start(self, process_id: int, session_id: str) -> None:
        """Mark a process launch as failed."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_processes
                SET status = 'failed_to_start',
                    last_seen_at = ?,
                    ended_at = ?
                WHERE id = ?
                """,
                (now, now, process_id),
            )
            conn.execute(
                """
                UPDATE agent_sessions
                SET status = 'idle',
                    last_used_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )
            conn.commit()

    def list_sessions(self) -> List[dict]:
        """List sessions for the current project."""
        self.reconcile_processes()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.session_id,
                    s.name,
                    s.profile,
                    s.tool,
                    s.status,
                    s.created_at,
                    s.last_used_at,
                    s.forked_from_session_id,
                    EXISTS(
                        SELECT 1
                        FROM agent_processes p
                        WHERE p.session_id = s.session_id
                          AND p.status IN ('starting', 'running')
                    ) AS has_running_process
                FROM agent_sessions s
                ORDER BY s.last_used_at DESC, s.created_at DESC
                """
            )
            return [dict(row) for row in rows.fetchall()]

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get a session row."""
        self.reconcile_processes()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM agent_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def _parse_metadata(self, metadata_json: Optional[str]) -> Optional[dict]:
        """Parse metadata JSON string, returning None on failure."""
        if not metadata_json:
            return None
        try:
            return json.loads(metadata_json)
        except (json.JSONDecodeError, TypeError):
            return None

    def resolve_session_target(self, target: Optional[str]) -> SessionTarget:
        """Resolve a session target pragmatically."""
        self.reconcile_processes()
        value = target or "latest"
        if value == "latest":
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT session_id, name, profile, metadata_json
                    FROM agent_sessions
                    ORDER BY last_used_at DESC, created_at DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    return SessionTarget(
                        session_id=row["session_id"],
                        name=row["name"],
                        profile=row["profile"],
                        metadata=self._parse_metadata(row["metadata_json"]),
                    )
            raise ValueError("No sessions found.")

        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, name, profile, metadata_json FROM agent_sessions WHERE session_id = ?",
                (value,),
            ).fetchone()
            if row:
                return SessionTarget(
                    session_id=row["session_id"],
                    name=row["name"],
                    profile=row["profile"],
                    metadata=self._parse_metadata(row["metadata_json"]),
                )

            rows = conn.execute(
                "SELECT session_id, name, profile, metadata_json FROM agent_sessions WHERE name = ?",
                (value,),
            ).fetchall()
            if len(rows) == 1:
                row = rows[0]
                return SessionTarget(
                    session_id=row["session_id"],
                    name=row["name"],
                    profile=row["profile"],
                    metadata=self._parse_metadata(row["metadata_json"]),
                )
            if len(rows) > 1:
                raise ValueError(f"Session name '{value}' is ambiguous.")

        raise ValueError(f"Session '{value}' not found.")

    def session_has_running_process(self, session_id: str) -> bool:
        """Return whether a session is currently owned by a running process."""
        self.reconcile_processes()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM agent_processes
                WHERE session_id = ?
                  AND status IN ('starting', 'running')
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            return row is not None

    def delete_session(self, session_id: str, force: bool = False) -> None:
        """Delete a session, its local process history, and its runtime directory."""
        self.reconcile_processes()
        if self.session_has_running_process(session_id) and not force:
            raise ValueError("Session currently has a running process. Use --force to delete local tracking.")

        with self._connect() as conn:
            conn.execute("DELETE FROM agent_processes WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"Session '{session_id}' not found.")

        remove_runtime(self.project_root, session_id)

    def prune_sessions(self, older_than_days: int = 30) -> int:
        """Delete old idle sessions from local tracking."""
        self.reconcile_processes()
        cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id
                FROM agent_sessions
                WHERE status = 'idle'
                  AND last_used_at < ?
                """,
                (cutoff,),
            ).fetchall()
            session_ids = [row["session_id"] for row in rows]
            for session_id in session_ids:
                conn.execute("DELETE FROM agent_processes WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
            conn.commit()

        for session_id in session_ids:
            remove_runtime(self.project_root, session_id)

        return len(session_ids)

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        """Return True if a PID appears alive."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
