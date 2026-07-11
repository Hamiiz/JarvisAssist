import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils.helpers import is_admin

logger = logging.getLogger(__name__)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for the SaaS Platform Admin dashboard (you only)."""
    user = update.effective_user
    if not is_admin(user.id):
        return

    text, markup = await build_platform_menu(context)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any active input flow (works for both tenants and platform admin)."""
    user = update.effective_user
    admin_states = context.bot_data.get("admin_states", {})
    if user.id in admin_states:
        del admin_states[user.id]
        await update.message.reply_text("🚫 Action cancelled.")
    else:
        await update.message.reply_text("No active action to cancel.")


async def build_platform_menu(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """Build the platform owner dashboard menu."""
    db = context.bot_data["db"]
    stats = await db.get_platform_stats()

    # Calculate approximate MRR
    # Starter = $5, Pro = $15, Business = $40
    tenant_mgr = context.bot_data["tenant_mgr"]
    tenants = await tenant_mgr.get_all_tenants()
    
    mrr = 0
    active_count = 0
    for t in tenants:
        if t["status"] == "active":
            active_count += 1
            plan = t["plan"]
            if plan == "starter":
                mrr += 5
            elif plan == "pro":
                mrr += 15
            elif plan == "business":
                mrr += 40

    text = (
        "🏢 *JarvisAssist Platform Dashboard*\n\n"
        f"👥 Total Tenants: *{stats['total_tenants']}*\n"
        f"🟢 Active Tenants: *{active_count}* (out of {stats['active_tenants']} active/trial)\n"
        f"💰 Est. Card MRR: *${mrr}*\n"
        f"🤖 Total AI replies today: *{stats['today_ai']}*\n"
        f"📈 Total AI replies lifetime: *{stats['total_ai_used']:,}*"
    )

    keyboard = [
        [
            InlineKeyboardButton("👥 All Tenants", callback_data="platform_tenants_page_0"),
            InlineKeyboardButton("📊 System Stats", callback_data="platform_stats"),
        ],
        [
            InlineKeyboardButton("⚠️ Suspend Tenant", callback_data="platform_suspend_prompt"),
            InlineKeyboardButton("🎁 Gift Plan", callback_data="platform_gift_prompt"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast to Owners", callback_data="platform_broadcast"),
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)
