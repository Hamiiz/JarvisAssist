import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Reply caps per plan (-1 = unlimited)
PLAN_CAPS = {
    "trial":    100,
    "starter":  500,
    "pro":      2000,
    "business": -1,
    "owner":    -1,
}


class TenantManager:
    """Manages tenant lifecycle: creation, quota, activation, suspension."""

    def __init__(self, db):
        self._db = db  # DatabaseManager instance

    @property
    def pool(self):
        return self._db._pool

    # ── Create / Get ──────────────────────────────────────────────────────────

    async def create_tenant(self, tenant_id: str, owner_tg_id: int) -> dict:
        """Create a new trial tenant. Seeds default settings/features."""
        from config import DEFAULT_SETTINGS, FEATURES
        trial_ends = datetime.now(timezone.utc) + timedelta(days=14)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO tenants
                   (tenant_id, owner_tg_id, plan, status, trial_ends_at, ai_replies_cap)
                   VALUES ($1, $2, 'trial', 'trial', $3, 100)
                   ON CONFLICT (tenant_id) DO UPDATE SET updated_at=NOW()
                   RETURNING *""",
                tenant_id, owner_tg_id, trial_ends,
            )
        await self._db.seed_tenant_defaults(tenant_id, DEFAULT_SETTINGS, FEATURES)
        return dict(row)

    async def get_tenant(self, tenant_id: str) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE tenant_id=$1", tenant_id,
            )
            return dict(row) if row else None

    async def get_tenant_by_owner(self, owner_tg_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE owner_tg_id=$1 ORDER BY created_at ASC LIMIT 1",
                owner_tg_id,
            )
            return dict(row) if row else None

    async def get_all_tenants(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tenants ORDER BY created_at DESC"
            )
            return [dict(r) for r in rows]

    # ── Status Checks ─────────────────────────────────────────────────────────

    async def is_active(self, tenant_id: str) -> bool:
        """Returns True if tenant can receive AI replies (active or within trial)."""
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return False
        if tenant["status"] == "active":
            return True
        if tenant["status"] == "trial":
            ends = tenant.get("trial_ends_at")
            if ends and datetime.now(timezone.utc) <= ends:
                return True
        return False

    async def check_quota(self, tenant_id: str) -> bool:
        """Returns True if tenant is under their monthly reply cap."""
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return False
        cap = tenant["ai_replies_cap"]
        if cap == -1:
            return True  # Unlimited
        return tenant["ai_replies_used"] < cap

    async def trial_days_remaining(self, tenant_id: str) -> int:
        tenant = await self.get_tenant(tenant_id)
        if not tenant or tenant["status"] != "trial":
            return 0
        ends = tenant.get("trial_ends_at")
        if not ends:
            return 0
        delta = ends - datetime.now(timezone.utc)
        return max(0, delta.days)

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def increment_usage(self, tenant_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET ai_replies_used=ai_replies_used+1, updated_at=NOW()"
                " WHERE tenant_id=$1",
                tenant_id,
            )

    async def activate(self, tenant_id: str, plan: str,
                       stripe_customer_id: str = None, stripe_sub_id: str = None,
                       stars_plan: str = None):
        """Activate a tenant on a paid plan (called by payment webhooks)."""
        cap = PLAN_CAPS.get(plan, 500)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE tenants SET
                   plan=$1, status='active', ai_replies_cap=$2,
                   stripe_customer_id=COALESCE($3, stripe_customer_id),
                   stripe_sub_id=COALESCE($4, stripe_sub_id),
                   stars_plan=COALESCE($5, stars_plan),
                   updated_at=NOW()
                   WHERE tenant_id=$6""",
                plan, cap, stripe_customer_id, stripe_sub_id, stars_plan, tenant_id,
            )
        logger.info("Tenant %s activated on plan %s (cap=%s)", tenant_id, plan, cap)

    async def suspend(self, tenant_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET status='suspended', updated_at=NOW() WHERE tenant_id=$1",
                tenant_id,
            )
        logger.info("Tenant %s suspended", tenant_id)

    async def cancel(self, tenant_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET status='cancelled', updated_at=NOW() WHERE tenant_id=$1",
                tenant_id,
            )

    async def unsuspend(self, tenant_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET status='active', updated_at=NOW() WHERE tenant_id=$1",
                tenant_id,
            )

    async def reset_monthly_usage(self, tenant_id: str):
        """Reset monthly reply counter — called on successful invoice.paid."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET ai_replies_used=0, updated_at=NOW() WHERE tenant_id=$1",
                tenant_id,
            )

    async def set_stars_renewal(self, tenant_id: str, renewal_date, plan: str):
        from datetime import date
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET stars_renewal_date=$1, stars_plan=$2, updated_at=NOW()"
                " WHERE tenant_id=$3",
                renewal_date, plan, tenant_id,
            )

    async def gift_plan(self, tenant_id: str, plan: str):
        """Give a tenant a complimentary plan upgrade (platform admin use)."""
        await self.activate(tenant_id, plan)

    # ── Owner Tenant Bootstrap ─────────────────────────────────────────────────

    async def ensure_owner_tenant(self, owner_tg_id: int):
        """Ensure the platform owner has a permanent unlimited tenant."""
        from config import DEFAULT_SETTINGS, FEATURES
        tenant_id = f"__owner_{owner_tg_id}__"
        async with self.pool.acquire() as conn:
            exists = await conn.fetchrow(
                "SELECT 1 FROM tenants WHERE tenant_id=$1", tenant_id,
            )
            if not exists:
                await conn.execute(
                    """INSERT INTO tenants
                       (tenant_id, owner_tg_id, plan, status, ai_replies_cap)
                       VALUES ($1, $2, 'owner', 'active', -1)""",
                    tenant_id, owner_tg_id,
                )
                await self._db.seed_tenant_defaults(tenant_id, DEFAULT_SETTINGS, FEATURES)
                # Set owner's real name in their settings
                logger.info("Owner tenant created for tg_id=%s", owner_tg_id)
        return tenant_id
