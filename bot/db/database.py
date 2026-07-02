import os
import logging
import aiosqlite
from datetime import date

logger = logging.getLogger(__name__)

CREATE_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features (
    key     TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT    DEFAULT '',
    first_name TEXT    DEFAULT '',
    last_name  TEXT    DEFAULT '',
    first_seen TEXT    DEFAULT (datetime('now')),
    last_seen  TEXT    DEFAULT (datetime('now')),
    msg_count  INTEGER DEFAULT 0,
    is_blocked INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS faq_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    response   TEXT    NOT NULL,
    hit_count  INTEGER DEFAULT 0,
    created_at TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analytics (
    date_key      TEXT PRIMARY KEY,
    msgs_received INTEGER DEFAULT 0,
    ai_responses  INTEGER DEFAULT 0,
    faq_hits      INTEGER DEFAULT 0,
    voice_msgs    INTEGER DEFAULT 0,
    image_msgs    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_cache (
    query_hash TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conv_date ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(is_blocked);
"""


class DatabaseManager:
    """Async SQLite database manager for the bot."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    async def init(self, default_settings: dict, default_features: dict):
        """Initialize tables and seed default values."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(CREATE_SQL)
            for key, value in default_settings.items():
                await db.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, str(value))
                )
            for key, meta in default_features.items():
                await db.execute(
                    "INSERT OR IGNORE INTO features (key, enabled) VALUES (?, ?)",
                    (key, 1 if meta["default"] else 0)
                )
            await db.commit()
        logger.info("Database initialized at %s", self.db_path)

    # ── Settings ──────────────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else default

    async def set_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
            await db.commit()

    async def get_all_settings(self) -> dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT key, value FROM settings") as cur:
                rows = await cur.fetchall()
                return {r[0]: r[1] for r in rows}

    # ── Features ──────────────────────────────────────────────────────────────

    async def get_feature(self, key: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT enabled FROM features WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
                return bool(row[0]) if row else False

    async def toggle_feature(self, key: str) -> bool:
        """Toggle a feature and return the NEW state."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT enabled FROM features WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
                current = bool(row[0]) if row else False
            new_state = not current
            await db.execute(
                "UPDATE features SET enabled = ? WHERE key = ?",
                (1 if new_state else 0, key)
            )
            await db.commit()
        return new_state

    async def set_feature(self, key: str, enabled: bool):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO features (key, enabled) VALUES (?, ?)",
                (key, 1 if enabled else 0)
            )
            await db.commit()

    async def get_all_features(self) -> dict[str, bool]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT key, enabled FROM features") as cur:
                rows = await cur.fetchall()
                return {r[0]: bool(r[1]) for r in rows}

    # ── Users ─────────────────────────────────────────────────────────────────

    async def upsert_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO users (user_id, username, first_name, last_name)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       username   = excluded.username,
                       first_name = excluded.first_name,
                       last_name  = excluded.last_name,
                       last_seen  = datetime('now'),
                       msg_count  = msg_count + 1""",
                (user_id, username or "", first_name or "", last_name or "")
            )
            await db.commit()

    async def get_user(self, user_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def is_first_time_user(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT msg_count FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row is None or row[0] == 0

    async def get_all_users(self, limit: int = 500, offset: int = 0) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def count_users(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def is_blocked(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT is_blocked FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return bool(row[0]) if row else False

    async def set_blocked(self, user_id: int, blocked: bool):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET is_blocked = ? WHERE user_id = ?",
                (1 if blocked else 0, user_id)
            )
            await db.commit()

    async def get_all_user_ids(self) -> list[int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE is_blocked = 0"
            ) as cur:
                rows = await cur.fetchall()
                return [r[0] for r in rows]

    # ── Conversations ─────────────────────────────────────────────────────────

    async def save_message(self, user_id: int, role: str, content: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content)
            )
            await db.commit()

    async def get_history(self, user_id: int, limit: int = 20) -> list[dict]:
        """Return history in Gemini chat format (chronological order)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """SELECT role, content FROM conversations
                   WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit)
            ) as cur:
                rows = await cur.fetchall()
        return [
            {"role": r[0], "parts": [{"text": r[1]}]}
            for r in reversed(rows)
        ]

    async def clear_history(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM conversations WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    async def get_message_count(self, user_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    # ── FAQ ───────────────────────────────────────────────────────────────────

    async def add_faq(self, keyword: str, response: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO faq_entries (keyword, response) VALUES (?, ?)",
                (keyword.lower().strip(), response)
            )
            await db.commit()

    async def get_faqs(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM faq_entries ORDER BY hit_count DESC"
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def delete_faq(self, faq_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM faq_entries WHERE id = ?", (faq_id,))
            await db.commit()

    async def check_faq(self, message: str) -> str | None:
        """Return FAQ response if message contains a keyword, else None."""
        message_lower = message.lower()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, keyword, response FROM faq_entries"
            ) as cur:
                rows = await cur.fetchall()
        for faq_id, keyword, response in rows:
            if keyword in message_lower:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        "UPDATE faq_entries SET hit_count = hit_count + 1 WHERE id = ?",
                        (faq_id,)
                    )
                    await db.commit()
                return response
        return None

    # ── AI Cache ──────────────────────────────────────────────────────────────

    async def get_cached_response(self, query_hash: str) -> str | None:
        """Fetch a cached AI response. Returns None if not found or older than 1 day."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT response FROM ai_cache WHERE query_hash = ? AND datetime('now', '-1 day') <= created_at",
                (query_hash,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def set_cached_response(self, query_hash: str, response: str):
        """Save an AI response to the cache."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO ai_cache (query_hash, response, created_at) VALUES (?, ?, datetime('now'))",
                (query_hash, response)
            )
            await db.commit()


    async def increment_analytics(self, field: str):
        """Increment an analytics counter for today."""
        today = date.today().isoformat()
        safe_fields = {"msgs_received", "ai_responses", "faq_hits", "voice_msgs", "image_msgs"}
        if field not in safe_fields:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""INSERT INTO analytics (date_key, {field}) VALUES (?, 1)
                    ON CONFLICT(date_key) DO UPDATE SET {field} = {field} + 1""",
                (today,)
            )
            await db.commit()

    async def get_analytics(self, days: int = 7) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM analytics ORDER BY date_key DESC LIMIT ?", (days,)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_total_stats(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """SELECT
                   COALESCE(SUM(msgs_received), 0),
                   COALESCE(SUM(ai_responses),  0),
                   COALESCE(SUM(faq_hits),      0),
                   COALESCE(SUM(voice_msgs),    0),
                   COALESCE(SUM(image_msgs),    0)
                   FROM analytics"""
            ) as cur:
                row = await cur.fetchone()
                return {
                    "total_msgs":   row[0],
                    "total_ai":     row[1],
                    "total_faq":    row[2],
                    "total_voice":  row[3],
                    "total_images": row[4],
                }
