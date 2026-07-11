import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils.helpers import is_admin, paginate
from bot.utils.formatter import format_analytics_table
from config import FEATURES, PERSONALITY_PRESETS

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tenant /setup callbacks  (prefix: setup_)
# ─────────────────────────────────────────────────────────────────────────────

async def setup_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route /setup inline keyboard callbacks. Only for the tenant owner."""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    # Answer immediately to stop the client loading spinner and make buttons responsive
    try:
        await query.answer()
    except Exception:
        pass

    tenant_mgr = context.bot_data["tenant_mgr"]
    tenant = await tenant_mgr.get_tenant_by_owner(user_id)
    if not tenant:
        return

    tenant_id = tenant["tenant_id"]
    db = context.bot_data["db"]

    try:
        if data == "setup_main":
            from .setup import build_setup_menu
            text, markup = await build_setup_menu(context, tenant_id, tenant)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")

        elif data == "setup_menu_settings":
            await _show_settings_menu(query, context, tenant_id)

        elif data == "setup_menu_features":
            await _show_features_menu(query, context, tenant_id)

        elif data == "setup_menu_persona":
            await _show_persona_menu(query, context, tenant_id)

        elif data == "setup_menu_faq":
            await _show_faq_menu(query, context, tenant_id)

        elif data == "setup_menu_users":
            await _show_users_menu(query, context, tenant_id, page=0)

        elif data == "setup_menu_analytics":
            await _show_analytics(query, context, tenant_id)

        elif data.startswith("setup_toggle_"):
            feature_key = data.replace("setup_toggle_", "")
            await db.toggle_feature(tenant_id, feature_key)
            await _show_features_menu(query, context, tenant_id)

        elif data.startswith("setup_set_"):
            setting_key = data.replace("setup_set_", "")
            await _prompt_setting_input(query, context, tenant_id, setting_key, scope="setup")

        elif data.startswith("setup_persona_"):
            preset_key = data.replace("setup_persona_", "")
            if preset_key == "custom":
                await _prompt_setting_input(query, context, tenant_id, "custom_prompt", scope="setup")
            else:
                await db.set_setting(tenant_id, "personality", preset_key)
                await _show_persona_menu(query, context, tenant_id)

        elif data == "setup_faq_add":
            await _prompt_setting_input(query, context, tenant_id, "faq_keyword", scope="setup")

        elif data.startswith("setup_faq_del_"):
            faq_id = int(data.replace("setup_faq_del_", ""))
            await db.delete_faq(tenant_id, faq_id)
            await _show_faq_menu(query, context, tenant_id)

        elif data.startswith("setup_users_page_"):
            page = int(data.replace("setup_users_page_", ""))
            await _show_users_menu(query, context, tenant_id, page=page)

        elif data.startswith("setup_user_block_"):
            uid = int(data.replace("setup_user_block_", ""))
            await db.set_blocked(tenant_id, uid, True)
            await _show_users_menu(query, context, tenant_id, page=0)

        elif data.startswith("setup_user_unblock_"):
            uid = int(data.replace("setup_user_unblock_", ""))
            await db.set_blocked(tenant_id, uid, False)
            await _show_users_menu(query, context, tenant_id, page=0)

        elif data.startswith("setup_user_clear_"):
            uid = int(data.replace("setup_user_clear_", ""))
            await db.clear_history(tenant_id, uid)

        else:
            pass

    except Exception as e:
        logger.error("Setup callback error [%s]: %s", data, e)
        try:
            await query.answer("⚠️ An error occurred.", show_alert=True)
        except Exception:
            pass



# ─────────────────────────────────────────────────────────────────────────────
# Setup sub-menu renderers
# ─────────────────────────────────────────────────────────────────────────────

async def _show_settings_menu(query, context, tenant_id: str):
    db = context.bot_data["db"]
    settings = await db.get_all_settings(tenant_id)
    text = "⚙️ *Bot Configuration*\nTap a setting to change it:"
    keyboard = [
        [InlineKeyboardButton(f"Bot Name: {settings.get('bot_name', 'HM Jarvis')}", callback_data="setup_set_bot_name")],
        [InlineKeyboardButton(f"Owner Name: {settings.get('owner_name', 'the owner')}", callback_data="setup_set_owner_name")],
        [InlineKeyboardButton("Welcome Message (Edit)", callback_data="setup_set_welcome_message")],
        [InlineKeyboardButton(f"History Limit: {settings.get('history_limit', '20')}", callback_data="setup_set_history_limit")],
        [InlineKeyboardButton(f"Anti-Spam Cooldown: {settings.get('anti_spam_cooldown', '3')}s", callback_data="setup_set_anti_spam_cooldown")],
        [InlineKeyboardButton(f"AI Creativity: {settings.get('ai_temperature', '0.75')}", callback_data="setup_set_ai_temperature")],
        [InlineKeyboardButton(f"Typing Delay: {settings.get('typing_delay_min', '1.0')}–{settings.get('typing_delay_max', '3.0')}s", callback_data="setup_set_typing_delay")],
        [InlineKeyboardButton(f"Reply Delay: {settings.get('reply_delay', '10')}s", callback_data="setup_set_reply_delay")],
        [InlineKeyboardButton(f"AI Model: {settings.get('ai_model', 'llama-3.1-8b-instant')}", callback_data="setup_set_ai_model")],
        [InlineKeyboardButton("Rate Limit Message (Edit)", callback_data="setup_set_rate_limit_msg")],
        [
            InlineKeyboardButton(f"Start: {settings.get('schedule_start', '08:00')}", callback_data="setup_set_schedule_start"),
            InlineKeyboardButton(f"End: {settings.get('schedule_end', '22:00')}", callback_data="setup_set_schedule_end"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="setup_main")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_features_menu(query, context, tenant_id: str):
    db = context.bot_data["db"]
    db_features = await db.get_all_features(tenant_id)
    text = "🧩 *Feature Toggles*\nEnable or disable capabilities:"
    keyboard = []
    row = []
    for key, meta in FEATURES.items():
        is_on = db_features.get(key, meta["default"])
        status = "🟢" if is_on else "🔴"
        label = f"{status} {meta['emoji']} {meta['label']}"
        row.append(InlineKeyboardButton(label, callback_data=f"setup_toggle_{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="setup_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_persona_menu(query, context, tenant_id: str):
    db = context.bot_data["db"]
    current = await db.get_setting(tenant_id, "personality", "friendly")
    text = (
        "🎭 *AI Personality*\n"
        f"Current: `{current.capitalize()}`\n\n"
        "Choose how your AI should talk:"
    )
    keyboard = []
    for key, data in PERSONALITY_PRESETS.items():
        marker = "✅ " if key == current else ""
        keyboard.append([InlineKeyboardButton(f"{marker}{data['emoji']} {data['name']}", callback_data=f"setup_persona_{key}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="setup_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_faq_menu(query, context, tenant_id: str):
    db = context.bot_data["db"]
    faqs = await db.get_faqs(tenant_id)
    text = "📖 *Auto-FAQ Engine*\n"
    if not faqs:
        text += "_No FAQs added yet._"
    else:
        text += "Keyword → (Hits)\n\n"
        for faq in faqs:
            text += f"• `{faq['keyword']}` ({faq['hit_count']})\n"
    keyboard = [[InlineKeyboardButton("➕ Add New FAQ", callback_data="setup_faq_add")]]
    for faq in faqs:
        keyboard.append([InlineKeyboardButton(f"🗑 Delete '{faq['keyword']}'", callback_data=f"setup_faq_del_{faq['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="setup_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_users_menu(query, context, tenant_id: str, page: int = 0):
    db = context.bot_data["db"]
    users = await db.get_all_users(tenant_id)
    page_items, total_pages = paginate(users, page, per_page=5)
    text = f"👥 *User Management* (Page {page+1}/{total_pages})\n\n"
    keyboard = []
    for u in page_items:
        uid = u["user_id"]
        name = u["first_name"] or "Unknown"
        if u["username"]:
            name += f" (@{u['username']})"
        status = "🔴 BLOCKED" if u["is_blocked"] else "🟢 Active"
        text += f"• {name} [{uid}]\n  Msgs: {u['msg_count']} | {status}\n\n"
        row = [InlineKeyboardButton(f"🗑 Clear [{name[:10]}]", callback_data=f"setup_user_clear_{uid}")]
        if u["is_blocked"]:
            row.append(InlineKeyboardButton("✅ Unblock", callback_data=f"setup_user_unblock_{uid}"))
        else:
            row.append(InlineKeyboardButton("🚫 Block", callback_data=f"setup_user_block_{uid}"))
        keyboard.append(row)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"setup_users_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"setup_users_page_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="setup_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_analytics(query, context, tenant_id: str):
    db = context.bot_data["db"]
    rows = await db.get_analytics(tenant_id, 7)
    total = await db.get_total_stats(tenant_id)
    text = format_analytics_table(rows, total)
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="setup_main")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Input state setter
# ─────────────────────────────────────────────────────────────────────────────

async def _prompt_setting_input(query, context, tenant_id: str, awaiting_key: str, scope: str = "setup"):
    user_id = query.from_user.id
    admin_states = context.bot_data.setdefault("admin_states", {})
    admin_states[user_id] = {"awaiting": awaiting_key, "tenant_id": tenant_id, "scope": scope}

    prompts = {
        "bot_name":           "Send the new **bot name**:",
        "owner_name":         "Send the **owner name** (e.g. 'John'):",
        "welcome_message":    "Send the new **welcome message**.\nVariables: `{name}`, `{bot_name}`",
        "history_limit":      "Send number of messages to remember (e.g., `20`):",
        "anti_spam_cooldown": "Send anti-spam cooldown in seconds (e.g., `3`):",
        "ai_temperature":     "Send AI creativity 0.0 to 1.0 (e.g., `0.75`):",
        "typing_delay":       "Send typing delay range (e.g., `1.0 - 3.0`):",
        "reply_delay":        "Send reply wait time in seconds (e.g., `10`):",
        "ai_model":           "Send the Groq model name (e.g., `llama-3.1-8b-instant`):",
        "rate_limit_msg":     "Send the rate-limit message shown to users:",
        "schedule_start":     "Send active schedule start time (`HH:MM`):",
        "schedule_end":       "Send active schedule end time (`HH:MM`):",
        "custom_prompt":      "Send your **custom AI instructions/system prompt**:",
        "faq_keyword":        "Send the **keyword** or trigger phrase for the FAQ:",
    }
    text = prompts.get(awaiting_key, "Send the new value:") + "\n\n_(Send /cancel to abort)_"
    await query.message.delete()
    await context.bot.send_message(chat_id=query.message.chat_id, text=text, parse_mode="Markdown")
