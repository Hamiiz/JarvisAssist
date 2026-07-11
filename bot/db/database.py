import os
import logging
import asyncpg
from datetime import date, datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Schema ───────────────────────────────────────────────────────────────────
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id          TEXT PRIMARY KEY,
    owner_tg_id        BIGINT NOT NULL,
    plan               TEXT DEFAULT 'trial',
    status             TEXT DEFAULT 'trial',
    trial_ends_at      TIMESTAMPTZ,
    ai_replies_used    INTEGER DEFAULT 0,
    ai_replies_cap     INTEGER DEFAULT 100,
    stripe_customer_id TEXT,
    stripe_sub_id      TEXT,
    stars_renewal_date DATE,
    stars_plan         TEXT,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    tenant_id TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE IF NOT EXISTS features (
    tenant_id TEXT    NOT NULL,
    key       TEXT    NOT NULL,
    enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE IF NOT EXISTS users (
    tenant_id  TEXT   NOT NULL,
    user_id    BIGINT NOT NULL,
    username   TEXT   DEFAULT '',
    first_name TEXT   DEFAULT '',
    last_name  TEXT   DEFAULT '',
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen  TIMESTAMPTZ DEFAULT NOW(),
    msg_count  INTEGER DEFAULT 0,
    is_blocked BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id         BIGSERIAL PRIMARY KEY,
    tenant_id  TEXT   NOT NULL,
    user_id    BIGINT NOT NULL,
    role       TEXT   NOT NULL,
    content    TEXT   NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS faq_entries (
    id         BIGSERIAL PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    keyword    TEXT NOT NULL,
    response   TEXT NOT NULL,
    hit_count  INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, keyword)
);

CREATE TABLE IF NOT EXISTS analytics (
    tenant_id     TEXT NOT NULL,
    date_key      DATE NOT NULL,
    msgs_received INTEGER DEFAULT 0,
    ai_responses  INTEGER DEFAULT 0,
    faq_hits      INTEGER DEFAULT 0,
    voice_msgs    INTEGER DEFAULT 0,
    image_msgs    INTEGER DEFAULT 0,
    PRIMARY KEY (tenant_id, date_key)
);

CREATE TABLE IF NOT EXISTS ai_cache (
    tenant_id  TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    response   TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, query_hash)
);

CREATE INDEX IF NOT EXISTS idx_conv_tenant_user ON conversations(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_conv_created     ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_users_blocked    ON users(tenant_id, is_blocked);
CREATE INDEX IF NOT EXISTS idx_tenants_owner    ON tenants(owner_tg_id);
CREATE INDEX IF NOT EXISTS idx_tenants_status   ON tenants(status);
CREATE INDEX IF NOT EXISTS idx_cache_created    ON ai_cache(created_at);
"""


class DatabaseManager:
    """Async PostgreSQL database manager — multi-tenant, pool-based."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None

    async def init(self, default_settings: dict, default_features: dict):
        """Create the connection pool and initialise schema."""
        dsn = os.environ["DATABASE_URL"]
        self._pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_SQL)
        logger.info("PostgreSQL database pool initialised")

    async def close(self):
        if self._pool:
            await self._pool.close()

    # ── Tenant bootstrap ──────────────────────────────────────────────────────

    async def seed_tenant_defaults(self, tenant_id: str, default_settings: dict, default_features: dict):
        """Seed per-tenant default settings and features (INSERT IGNORE)."""
        async with self._pool.acquire() as conn:
            for key, value in default_settings.items():
                await conn.execute(
                    "INSERT INTO settings (tenant_id, key, value) VALUES ($1, $2, $3)"
                    " ON CONFLICT DO NOTHING",
                    tenant_id, key, str(value),
                )
            for key, meta in default_features.items():
                await conn.execute(
                    "INSERT INTO features (tenant_id, key, enabled) VALUES ($1, $2, $3)"
                    " ON CONFLICT DO NOTHING",
                    tenant_id, key, bool(meta["default"]),
                )

    # ── Settings ──────────────────────────────────────────────────────────────

    async def get_setting(self, tenant_id: str, key: str, default: str = "") -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM settings WHERE tenant_id=$1 AND key=$2",
                tenant_id, key,
            )
            return row["value"] if row else default

    async def set_setting(self, tenant_id: str, key: str, value: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO settings (tenant_id, key, value) VALUES ($1, $2, $3)"
                " ON CONFLICT (tenant_id, key) DO UPDATE SET value=EXCLUDED.value",
                tenant_id, key, value,
            )

    async def get_all_settings(self, tenant_id: str) -> dict[str, str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, value FROM settings WHERE tenant_id=$1", tenant_id,
            )
            return {r["key"]: r["value"] for r in rows}

    # ── Features ──────────────────────────────────────────────────────────────

    async def get_feature(self, tenant_id: str, key: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled FROM features WHERE tenant_id=$1 AND key=$2",
                tenant_id, key,
            )
            return bool(row["enabled"]) if row else False

    async def toggle_feature(self, tenant_id: str, key: str) -> bool:
        """Toggle a feature and return the NEW state."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled FROM features WHERE tenant_id=$1 AND key=$2",
                tenant_id, key,
            )
            current = bool(row["enabled"]) if row else False
            new_state = not current
            await conn.execute(
                "UPDATE features SET enabled=$1 WHERE tenant_id=$2 AND key=$3",
                new_state, tenant_id, key,
            )
            return new_state

    async def set_feature(self, tenant_id: str, key: str, enabled: bool):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO features (tenant_id, key, enabled) VALUES ($1, $2, $3)"
                " ON CONFLICT (tenant_id, key) DO UPDATE SET enabled=EXCLUDED.enabled",
                tenant_id, key, enabled,
            )

    async def get_all_features(self, tenant_id: str) -> dict[str, bool]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, enabled FROM features WHERE tenant_id=$1", tenant_id,
            )
            return {r["key"]: bool(r["enabled"]) for r in rows}

    # ── Users ─────────────────────────────────────────────────────────────────

    async def upsert_user(self, tenant_id: str, user_id: int, username: str,
                          first_name: str, last_name: str = ""):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO users (tenant_id, user_id, username, first_name, last_name)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                       username   = EXCLUDED.username,
                       first_name = EXCLUDED.first_name,
                       last_name  = EXCLUDED.last_name,
                       last_seen  = NOW(),
                       msg_count  = users.msg_count + 1""",
                tenant_id, user_id, username or "", first_name or "", last_name or "",
            )

    async def get_user(self, tenant_id: str, user_id: int) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE tenant_id=$1 AND user_id=$2",
                tenant_id, user_id,
            )
            return dict(row) if row else None

    async def is_first_time_user(self, tenant_id: str, user_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT msg_count FROM users WHERE tenant_id=$1 AND user_id=$2",
                tenant_id, user_id,
            )
            return row is None or row["msg_count"] == 0

    async def get_all_users(self, tenant_id: str, limit: int = 500, offset: int = 0) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM users WHERE tenant_id=$1 ORDER BY last_seen DESC LIMIT $2 OFFSET $3",
                tenant_id, limit, offset,
            )
            return [dict(r) for r in rows]

    async def count_users(self, tenant_id: str) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM users WHERE tenant_id=$1", tenant_id,
            )
            return row["cnt"] if row else 0

    async def is_blocked(self, tenant_id: str, user_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_blocked FROM users WHERE tenant_id=$1 AND user_id=$2",
                tenant_id, user_id,
            )
            return bool(row["is_blocked"]) if row else False

    async def set_blocked(self, tenant_id: str, user_id: int, blocked: bool):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_blocked=$1 WHERE tenant_id=$2 AND user_id=$3",
                blocked, tenant_id, user_id,
            )

    async def get_all_user_ids(self, tenant_id: str) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM users WHERE tenant_id=$1 AND is_blocked=FALSE",
                tenant_id,
            )
            return [r["user_id"] for r in rows]

    # ── Conversations ─────────────────────────────────────────────────────────

    async def save_message(self, tenant_id: str, user_id: int, role: str, content: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO conversations (tenant_id, user_id, role, content) VALUES ($1, $2, $3, $4)",
                tenant_id, user_id, role, content,
            )

    async def get_history(self, tenant_id: str, user_id: int, limit: int = 20) -> list[dict]:
        """Return history in Groq chat format (chronological order)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT role, content FROM conversations
                   WHERE tenant_id=$1 AND user_id=$2
                   ORDER BY created_at DESC LIMIT $3""",
                tenant_id, user_id, limit,
            )
        return [
            {"role": r["role"], "parts": [{"text": r["content"]}]}
            for r in reversed(rows)
        ]

    async def clear_history(self, tenant_id: str, user_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM conversations WHERE tenant_id=$1 AND user_id=$2",
                tenant_id, user_id,
            )

    # ── FAQ ───────────────────────────────────────────────────────────────────

    async def add_faq(self, tenant_id: str, keyword: str, response: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO faq_entries (tenant_id, keyword, response) VALUES ($1, $2, $3)"
                " ON CONFLICT (tenant_id, keyword) DO UPDATE SET response=EXCLUDED.response",
                tenant_id, keyword.lower().strip(), response,
            )

    async def get_faqs(self, tenant_id: str) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM faq_entries WHERE tenant_id=$1 ORDER BY hit_count DESC",
                tenant_id,
            )
            return [dict(r) for r in rows]

    async def delete_faq(self, tenant_id: str, faq_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM faq_entries WHERE tenant_id=$1 AND id=$2",
                tenant_id, faq_id,
            )

    async def check_faq(self, tenant_id: str, message: str) -> str | None:
        """Return FAQ response if message contains a keyword, else None."""
        message_lower = message.lower()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, keyword, response FROM faq_entries WHERE tenant_id=$1",
                tenant_id,
            )
            for row in rows:
                if row["keyword"] in message_lower:
                    await conn.execute(
                        "UPDATE faq_entries SET hit_count=hit_count+1 WHERE id=$1", row["id"],
                    )
                    return row["response"]
        return None

    # ── AI Cache ──────────────────────────────────────────────────────────────

    async def get_cached_response(self, tenant_id: str, query_hash: str) -> str | None:
        """Fetch a cached AI response (valid for 24h)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT response FROM ai_cache"
                " WHERE tenant_id=$1 AND query_hash=$2"
                " AND created_at >= NOW() - INTERVAL '1 day'",
                tenant_id, query_hash,
            )
            return row["response"] if row else None

    async def set_cached_response(self, tenant_id: str, query_hash: str, response: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ai_cache (tenant_id, query_hash, response)"
                " VALUES ($1, $2, $3)"
                " ON CONFLICT (tenant_id, query_hash) DO UPDATE SET response=EXCLUDED.response, created_at=NOW()",
                tenant_id, query_hash, response,
            )

    # ── Analytics ─────────────────────────────────────────────────────────────

    async def increment_analytics(self, tenant_id: str, field: str):
        safe_fields = {"msgs_received", "ai_responses", "faq_hits", "voice_msgs", "image_msgs"}
        if field not in safe_fields:
            return
        today = date.today()
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO analytics (tenant_id, date_key, {field}) VALUES ($1, $2, 1)
                    ON CONFLICT (tenant_id, date_key) DO UPDATE SET {field} = analytics.{field} + 1""",
                tenant_id, today,
            )

    async def get_analytics(self, tenant_id: str, days: int = 7) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM analytics WHERE tenant_id=$1 ORDER BY date_key DESC LIMIT $2",
                tenant_id, days,
            )
            return [dict(r) for r in rows]

    async def get_total_stats(self, tenant_id: str) -> dict:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT
                   COALESCE(SUM(msgs_received), 0) AS total_msgs,
                   COALESCE(SUM(ai_responses),  0) AS total_ai,
                   COALESCE(SUM(faq_hits),      0) AS total_faq,
                   COALESCE(SUM(voice_msgs),    0) AS total_voice,
                   COALESCE(SUM(image_msgs),    0) AS total_images
                   FROM analytics WHERE tenant_id=$1""",
                tenant_id,
            )
            return dict(row) if row else {
                "total_msgs": 0, "total_ai": 0, "total_faq": 0,
                "total_voice": 0, "total_images": 0,
            }

    # ── Platform-wide queries (admin only) ────────────────────────────────────

    async def get_platform_stats(self) -> dict:
        """Aggregate stats across ALL tenants for the platform admin."""
        async with self._pool.acquire() as conn:
            tenants_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM tenants")
            active_row  = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM tenants WHERE status IN ('active', 'trial')"
            )
            usage_row   = await conn.fetchrow(
                "SELECT COALESCE(SUM(ai_replies_used), 0) AS total FROM tenants"
            )
            today_row   = await conn.fetchrow(
                """SELECT COALESCE(SUM(ai_responses), 0) AS today_ai
                   FROM analytics WHERE date_key = CURRENT_DATE"""
            )
        return {
            "total_tenants":  tenants_row["cnt"],
            "active_tenants": active_row["cnt"],
            "total_ai_used":  usage_row["total"],
            "today_ai":       today_row["today_ai"],
        }
