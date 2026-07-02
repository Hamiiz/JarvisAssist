import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils.helpers import is_admin

logger = logging.getLogger(__name__)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for the admin dashboard."""
    user = update.effective_user
    if not is_admin(user.id):
        return

    text, markup = await build_main_menu(context)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any active admin input flow."""
    user = update.effective_user
    if not is_admin(user.id):
        return

    admin_states = context.bot_data.get("admin_states", {})
    if user.id in admin_states:
        del admin_states[user.id]
        await update.message.reply_text("🚫 Action cancelled.")
    else:
        await update.message.reply_text("No active action to cancel.")


# ─────────────────────────────────────────────────────────────────────────────
# Menu Builders
# ─────────────────────────────────────────────────────────────────────────────

async def build_main_menu(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """Build the main dashboard view."""
    settings = await context.bot_data["db"].get_all_settings()
    bot_name = settings.get("bot_name", "HmassAssistant")

    text = (
        f"🤖 *{bot_name} Control Panel*\n\n"
        "Welcome to the admin dashboard. What would you like to manage?"
    )
    keyboard = [
        [
            InlineKeyboardButton("⚙️ Settings",   callback_data="menu_settings"),
            InlineKeyboardButton("🧩 Features",   callback_data="menu_features"),
        ],
        [
            InlineKeyboardButton("🎭 Persona",    callback_data="menu_persona"),
            InlineKeyboardButton("📖 FAQ Data",   callback_data="menu_faq"),
        ],
        [
            InlineKeyboardButton("👥 Users",      callback_data="menu_users"),
            InlineKeyboardButton("📊 Analytics",  callback_data="menu_analytics"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast",  callback_data="action_broadcast"),
        ],
    ]
    return text, InlineKeyboardMarkup(keyboard)
