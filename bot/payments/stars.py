import logging
from datetime import date, timedelta

from telegram import LabeledPrice, Update
from telegram.ext import ContextTypes

from config import PLANS

logger = logging.getLogger(__name__)

# Mapping plan name → Stars price in smallest unit (1 Star = 100 units in Telegram API)
STARS_PRICES = {
    "starter":  500,
    "pro":      1500,
    "business": 4000,
}


async def send_stars_invoice(
    bot,
    chat_id: int,
    tenant_id: str,
    plan: str,
):
    """Send a Telegram Stars invoice to the tenant owner."""
    plan_info = PLANS.get(plan, {})
    stars = STARS_PRICES.get(plan, 0)
    label = plan_info.get("label", plan.capitalize())
    cap = plan_info.get("cap", 0)
    cap_str = "Unlimited" if cap == -1 else f"{cap:,}"

    await bot.send_invoice(
        chat_id=chat_id,
        title=f"JarvisAssist {label} Plan",
        description=f"Monthly AI assistant subscription — {cap_str} AI replies/month",
        payload=f"stars:{tenant_id}:{plan}",   # Parsed in handle_successful_payment
        currency="XTR",                         # Telegram Stars currency code
        prices=[LabeledPrice(f"{label} — {cap_str} replies/mo", stars)],
    )
    logger.info("Stars invoice sent to chat %s for plan %s (%s stars)", chat_id, plan, stars)


async def handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Always approve pre-checkout queries (required by Telegram)."""
    await update.pre_checkout_query.answer(ok=True)


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate tenant after successful Stars payment."""
    payment = update.message.successful_payment
    payload = payment.invoice_payload  # "stars:{tenant_id}:{plan}"

    try:
        _, tenant_id, plan = payload.split(":", 2)
    except ValueError:
        logger.error("Unexpected Stars payload: %s", payload)
        return

    tenant_mgr = context.bot_data["tenant_mgr"]
    await tenant_mgr.activate(tenant_id, plan, stars_plan=plan)

    # Set renewal date to ~30 days from now
    renewal_date = date.today() + timedelta(days=30)
    await tenant_mgr.set_stars_renewal(tenant_id, renewal_date, plan)

    plan_info = PLANS.get(plan, {})
    cap = plan_info.get("cap", 0)
    cap_str = "Unlimited" if cap == -1 else f"{cap:,}"

    await update.message.reply_text(
        f"🎉 *Payment successful!* You're now on the *{plan_info.get('label', plan)} Plan*.\n\n"
        f"✅ {cap_str} AI replies/month activated.\n"
        f"🔄 Renews: {renewal_date.strftime('%b %d, %Y')}\n\n"
        f"Use /setup to configure your assistant.",
        parse_mode="Markdown",
    )
    logger.info("Stars payment successful: tenant=%s plan=%s", tenant_id, plan)


async def send_renewal_invoice(bot, tenant: dict):
    """Resend a Stars invoice for monthly renewal (called by scheduled task)."""
    plan = tenant.get("stars_plan") or tenant.get("plan")
    if plan not in STARS_PRICES:
        return
    await send_stars_invoice(
        bot=bot,
        chat_id=tenant["owner_tg_id"],
        tenant_id=tenant["tenant_id"],
        plan=plan,
    )
