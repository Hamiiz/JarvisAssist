import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot.utils.helpers import is_admin, format_uptime

logger = logging.getLogger(__name__)

# Track when the bot started for uptime display
BOT_START_TIME = datetime.now()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — greet the user and send welcome message if first-timer."""
    user = update.effective_user
    db   = context.bot_data["db"]

    first_time = await db.is_first_time_user(user.id)
    await db.upsert_user(
        user.id,
        user.username or "",
        user.first_name or "",
        user.last_name  or "",
    )

    settings = await db.get_all_settings()
    features = await db.get_all_features()
    bot_name = settings.get("bot_name", "HmassAssistant")

    if first_time and features.get("welcome_msg", True):
        welcome = settings.get(
            "welcome_message",
            "Hi {name}! 👋 I'm {bot_name}. How can I help you today?"
        )
        welcome = welcome.replace("{name}", user.first_name or "there")
        welcome = welcome.replace("{bot_name}", bot_name)
        await update.message.reply_text(welcome)
    else:
        await update.message.reply_text(
            f"👋 Welcome back! I'm {bot_name}.\n"
            f"Send me any message and I'll do my best to help!\n\n"
            f"Use /help for available commands."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help — display all available commands."""
    user = update.effective_user

    admin_section = ""
    if is_admin(user.id):
        admin_section = (
            "\n\n*🔧 Admin Commands:*\n"
            "/admin — Open the management panel\n"
            "/status — Bot status & full stats\n"
            "/cancel — Cancel any pending input\n"
        )

    help_text = (
        "🤖 *Available Commands*\n\n"
        "/start — Start or restart the bot\n"
        "/help — Show this help message\n"
        "/clear — Clear your conversation history\n"
        "/status — Check bot status"
        f"{admin_section}"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear — wipe this user's conversation history."""
    user = update.effective_user
    db   = context.bot_data["db"]

    await db.clear_history(user.id)
    await update.message.reply_text(
        "🗑 Conversation history cleared!\n"
        "Starting fresh — what can I help you with? 😊"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status — show bot health and stats."""
    db       = context.bot_data["db"]
    settings = await db.get_all_settings()
    features = await db.get_all_features()
    stats    = await db.get_total_stats()
    users    = await db.count_users()
    uptime   = format_uptime(BOT_START_TIME)

    away_state   = "🟢 ON"  if features.get("away_mode", True)  else "🔴 OFF"
    typing_state = "✅ Yes" if features.get("typing_sim", True)  else "❌ No"
    bot_name     = settings.get("bot_name",    "HmassAssistant")
    model        = settings.get("ai_model",    "gemini-2.5-flash")
    personality  = settings.get("personality", "friendly").capitalize()

    text = (
        f"📊 *{bot_name} — Status*\n\n"
        f"⏱ Uptime: `{uptime}`\n"
        f"🌙 Away Mode: {away_state}\n"
        f"⌨️ Typing Sim: {typing_state}\n"
        f"🎭 Personality: `{personality}`\n"
        f"🤖 AI Model: `{model}`\n\n"
        f"*📈 All-Time Statistics:*\n"
        f"👥 Registered Users: `{users:,}`\n"
        f"💬 Messages: `{stats['total_msgs']:,}`\n"
        f"🤖 AI Responses: `{stats['total_ai']:,}`\n"
        f"📖 FAQ Hits: `{stats['total_faq']:,}`\n"
        f"🎤 Voice Messages: `{stats['total_voice']:,}`\n"
        f"🖼️ Images Analyzed: `{stats['total_images']:,}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
