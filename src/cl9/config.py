"""Configuration and project registry management."""

import json
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import contextmanager
from platformdirs import user_config_dir, user_data_dir, user_cache_dir


class Config:
    """Manages cl9 global configuration and XDG directories."""

    def __init__(self):
        self.config_dir = Path(user_config_dir("cl9"))
        self.data_dir = Path(user_data_dir("cl9"))
        self.cache_dir = Path(user_cache_dir("cl9"))

        # Ensure directories exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.data_dir / "projects.db"
        self.global_config_file = self.config_dir / "config.json"
        self.plugins_dir = self.config_dir / "plugins"

        # Ensure plugin directory exists
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Initialize the database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    name TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    created TEXT NOT NULL,
                    last_accessed TEXT
                )
            """)
            conn.commit()

    def add_project(self, name: str, path: Path) -> None:
        """Add a project to the registry."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO projects (name, path, created, last_accessed)
                VALUES (?, ?, ?, ?)
                """,
                (name, str(path.resolve()), datetime.now().isoformat(), None)
            )
            conn.commit()

    def get_project(self, name: str) -> Optional[dict]:
        """Get a project by name."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name, path, created, last_accessed FROM projects WHERE name = ?",
                (name,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def list_projects(self) -> List[dict]:
        """List all registered projects."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name, path, created, last_accessed FROM projects ORDER BY name"
            )
            return [dict(row) for row in cursor.fetchall()]

    def project_exists(self, name: str) -> bool:
        """Check if a project exists in the registry."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM projects WHERE name = ?",
                (name,)
            )
            return cursor.fetchone() is not None

    def update_last_accessed(self, name: str) -> None:
        """Update the last accessed timestamp for a project."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE projects SET last_accessed = ? WHERE name = ?",
                (datetime.now().isoformat(), name)
            )
            conn.commit()

    def remove_project(self, name: str) -> bool:
        """Remove a project from the registry.

        Returns True if the project was removed, False if it didn't exist.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM projects WHERE name = ?",
                (name,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def load_global_config(self) -> Dict[str, Any]:
        """Load global configuration from config.json.

        Returns:
            Dict with global configuration, or default config if file doesn't exist
        """
        if not self.global_config_file.exists():
            return self._default_global_config()

        try:
            with open(self.global_config_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            # Log warning but return defaults
            print(f"Warning: Failed to load config from {self.global_config_file}: {e}")
            return self._default_global_config()

    def save_global_config(self, config_dict: Dict[str, Any]) -> None:
        """Save global configuration to config.json."""
        with open(self.global_config_file, 'w') as f:
            json.dump(config_dict, f, indent=2)

    def _default_global_config(self) -> Dict[str, Any]:
        """Return default global configuration."""
        return {
            "version": "1",
            "plugins": {},
            "hooks": {}
        }


# Global config instance
config = Config()
