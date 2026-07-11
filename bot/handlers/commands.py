import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils.helpers import format_uptime
from config import PLANS

logger = logging.getLogger(__name__)

# Track when the bot started for uptime display
from datetime import datetime as _dt
BOT_START_TIME = _dt.now()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Public /start — explains the service to anyone who messages the bot."""
    await update.message.reply_text(
        "🤖 *Welcome to JarvisAssist!*\n\n"
        "I'm an AI assistant that replies to your Telegram messages automatically — "
        "so you never leave anyone waiting.\n\n"
        "*How to get started:*\n"
        "1. Go to your *Telegram Settings → Telegram Business*\n"
        "2. Under *Chatbots*, search for and connect this bot\n"
        "3. Come back here and use /subscribe to activate\n\n"
        "Already connected? Use /setup to configure your assistant.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all available commands."""
    from bot.utils.helpers import is_admin
    user = update.effective_user
    is_platform_admin = is_admin(user.id)

    text = (
        "🤖 *Available Commands*\n\n"
        "/start — About JarvisAssist\n"
        "/help — Show this message\n"
        "/setup — Configure your assistant\n"
        "/subscribe — Manage your subscription\n"
        "/status — Your plan & usage stats\n"
        "/clear — Clear your conversation history\n"
    )
    if is_platform_admin:
        text += (
            "\n*🔧 Platform Admin:*\n"
            "/admin — Platform dashboard\n"
            "/cancel — Cancel pending input\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the caller's conversation history."""
    user = update.effective_user
    db = context.bot_data["db"]
    tenant_mgr = context.bot_data["tenant_mgr"]

    tenant = await tenant_mgr.get_tenant_by_owner(user.id)
    if not tenant:
        await update.message.reply_text("⚠️ No active assistant found. Use /setup after connecting your bot.")
        return

    await db.clear_history(tenant["tenant_id"], user.id)
    await update.message.reply_text(
        "🗑 Conversation history cleared!\nStarting fresh — what can I help you with? 😊"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the caller's tenant plan and usage stats."""
    user = update.effective_user
    db = context.bot_data["db"]
    tenant_mgr = context.bot_data["tenant_mgr"]

    tenant = await tenant_mgr.get_tenant_by_owner(user.id)
    if not tenant:
        await update.message.reply_text(
            "⚠️ No assistant connected to your account.\n\n"
            "Connect via Telegram Business settings, then use /subscribe."
        )
        return

    plan_info = PLANS.get(tenant["plan"], {})
    cap = tenant["ai_replies_cap"]
    used = tenant["ai_replies_used"]
    cap_str = "Unlimited" if cap == -1 else f"{cap:,}"
    used_pct = 0 if cap == -1 else int((used / cap) * 100) if cap > 0 else 0

    status_emoji = {
        "active": "🟢", "trial": "🟡", "suspended": "🔴", "cancelled": "⚫", "owner": "👑",
    }.get(tenant["status"], "⚪")

    uptime = format_uptime(BOT_START_TIME)
    stats = await db.get_total_stats(tenant["tenant_id"])
    users = await db.count_users(tenant["tenant_id"])

    # Trial days remaining
    trial_note = ""
    if tenant["status"] == "trial":
        days_left = await tenant_mgr.trial_days_remaining(tenant["tenant_id"])
        trial_note = f"\n⏳ Trial expires in: *{days_left} days*"

    text = (
        f"📊 *Your JarvisAssist Status*\n\n"
        f"{status_emoji} Plan: *{plan_info.get('label', tenant['plan'].capitalize())}*{trial_note}\n"
        f"💬 Replies used: *{used:,} / {cap_str}*"
        + (f" ({used_pct}%)" if cap != -1 else "") + "\n"
        f"⏱ Bot uptime: `{uptime}`\n\n"
        f"*📈 All-Time Stats:*\n"
        f"👥 Users: `{users:,}`\n"
        f"💬 Messages: `{stats['total_msgs']:,}`\n"
        f"🤖 AI Replies: `{stats['total_ai']:,}`\n"
        f"📖 FAQ Hits: `{stats['total_faq']:,}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
