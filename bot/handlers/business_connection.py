import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_SETTINGS, FEATURES

logger = logging.getLogger(__name__)


async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fires when a Telegram Business account connects OR disconnects this bot.
    This is the automatic onboarding entry point for every new tenant.
    """
    bc = update.business_connection
    if bc is None:
        return

    tenant_mgr = context.bot_data["tenant_mgr"]
    owner_tg_id = bc.user.id
    tenant_id = bc.id  # Unique business_connection_id — this IS the tenant key

    if bc.is_enabled:
        # ── New connection → create tenant + send welcome ──────────────────
        tenant = await tenant_mgr.create_tenant(tenant_id, owner_tg_id)
        logger.info("New tenant connected: tenant_id=%s owner=%s", tenant_id, owner_tg_id)

        first_name = bc.user.first_name or "there"
        await context.bot.send_message(
            chat_id=owner_tg_id,
            text=(
                f"🎉 *Connected successfully, {first_name}!*\n\n"
                "Your 14-day free trial has started. You get *100 AI replies* to test the assistant.\n\n"
                "Get started:\n"
                "• /setup — Configure your assistant (name, personality, FAQs...)\n"
                "• /subscribe — Upgrade to a paid plan\n"
                "• /status — View your usage & plan info\n\n"
                "Your bot will now automatically reply to messages in your Telegram Business chats. 🤖"
            ),
            parse_mode="Markdown",
        )
    else:
        # ── Disconnection → mark as cancelled ─────────────────────────────
        await tenant_mgr.cancel(tenant_id)
        logger.info("Tenant disconnected: tenant_id=%s owner=%s", tenant_id, owner_tg_id)
        try:
            await context.bot.send_message(
                chat_id=owner_tg_id,
                text=(
                    "😢 *Bot disconnected.*\n\n"
                    "Your assistant has been paused. Reconnect the bot via your "
                    "Telegram Business settings anytime to reactivate your plan."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Could not send disconnect message to %s: %s", owner_tg_id, e)
