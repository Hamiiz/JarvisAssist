import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from config import PLANS

logger = logging.getLogger(__name__)

async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The /setup command - shows the tenant owner's dashboard/control panel."""
    user = update.effective_user
    tenant_mgr = context.bot_data["tenant_mgr"]

    tenant = await tenant_mgr.get_tenant_by_owner(user.id)
    if not tenant:
        await update.message.reply_text(
            "⚠️ No assistant connected to your account.\n\n"
            "Go to your *Telegram Settings → Telegram Business → Chatbots* and connect this bot first.",
            parse_mode="Markdown"
        )
        return

    text, markup = await build_setup_menu(context, tenant["tenant_id"], tenant)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def build_setup_menu(context, tenant_id: str, tenant: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Helper to build the main setup menu markup."""
    db = context.bot_data["db"]
    settings = await db.get_all_settings(tenant_id)
    bot_name = settings.get("bot_name", "HM Jarvis")
    
    plan_info = PLANS.get(tenant["plan"], {})
    used = tenant["ai_replies_used"]
    cap = tenant["ai_replies_cap"]
    cap_str = "Unlimited" if cap == -1 else f"{cap:,}"
    
    status_emoji = {
        "active": "🟢",
        "trial": "🟡",
        "suspended": "🔴",
        "cancelled": "⚫",
        "owner": "👑",
    }.get(tenant["status"], "⚪")

    text = (
        f"🤖 *{bot_name} — Control Panel*\n\n"
        f"Status: {status_emoji} *{tenant['status'].capitalize()}*\n"
        f"Plan: *{plan_info.get('label', tenant['plan'].capitalize())}*\n"
        f"Replies: *{used:,} / {cap_str}*"
    )

    keyboard = [
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="setup_menu_settings"),
            InlineKeyboardButton("🧩 Features", callback_data="setup_menu_features"),
        ],
        [
            InlineKeyboardButton("🎭 Persona", callback_data="setup_menu_persona"),
            InlineKeyboardButton("📖 FAQ Data", callback_data="setup_menu_faq"),
        ],
        [
            InlineKeyboardButton("👥 My Users", callback_data="setup_menu_users"),
            InlineKeyboardButton("📊 Analytics", callback_data="setup_menu_analytics"),
        ],
        [
            InlineKeyboardButton("💳 Subscription", callback_data="sub_back"),
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)
