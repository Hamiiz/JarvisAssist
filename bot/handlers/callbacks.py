import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils.helpers import is_admin, paginate
from bot.utils.formatter import format_analytics_table
from config import FEATURES, PERSONALITY_PRESETS

logger = logging.getLogger(__name__)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route all callback queries to the right handler based on prefix."""
    query = update.callback_query
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.answer("⛔ Admin access required.", show_alert=True)
        return

    data = query.data

    try:
        if data == "main_menu":
            from .admin import build_main_menu
            text, markup = await build_main_menu(context)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")

        # ── Menus ─────────────────────────────────────────────────────────────
        elif data == "menu_settings":
            await _show_settings_menu(query, context)
        elif data == "menu_features":
            await _show_features_menu(query, context)
        elif data == "menu_persona":
            await _show_persona_menu(query, context)
        elif data == "menu_faq":
            await _show_faq_menu(query, context)
        elif data == "menu_users":
            await _show_users_menu(query, context, page=0)
        elif data == "menu_analytics":
            await _show_analytics(query, context)

        # ── Toggles ───────────────────────────────────────────────────────────
        elif data.startswith("toggle_"):
            feature_key = data.replace("toggle_", "")
            db = context.bot_data["db"]
            new_state = await db.toggle_feature(feature_key)
            state_str = "ON" if new_state else "OFF"
            await query.answer(f"Feature '{feature_key}' turned {state_str}")
            await _show_features_menu(query, context)  # Refresh menu

        # ── Settings Input Prompts ────────────────────────────────────────────
        elif data.startswith("set_"):
            setting_key = data.replace("set_", "")
            await _prompt_setting_input(query, context, setting_key)

        # ── Persona Selection ─────────────────────────────────────────────────
        elif data.startswith("persona_"):
            preset_key = data.replace("persona_", "")
            db = context.bot_data["db"]
            if preset_key == "custom":
                await _prompt_setting_input(query, context, "custom_prompt")
            else:
                await db.set_setting("personality", preset_key)
                await query.answer(f"Personality set to: {preset_key.capitalize()}")
                await _show_persona_menu(query, context)

        # ── FAQ Actions ───────────────────────────────────────────────────────
        elif data == "faq_add":
            await _prompt_setting_input(query, context, "faq_keyword")
        elif data.startswith("faq_del_"):
            faq_id = int(data.replace("faq_del_", ""))
            await context.bot_data["db"].delete_faq(faq_id)
            await query.answer("FAQ deleted")
            await _show_faq_menu(query, context)

        # ── User Pagination & Actions ─────────────────────────────────────────
        elif data.startswith("users_page_"):
            page = int(data.replace("users_page_", ""))
            await _show_users_menu(query, context, page=page)
        elif data.startswith("user_block_"):
            uid = int(data.replace("user_block_", ""))
            await context.bot_data["db"].set_blocked(uid, True)
            await query.answer("User blocked")
            await _show_users_menu(query, context, page=0)
        elif data.startswith("user_unblock_"):
            uid = int(data.replace("user_unblock_", ""))
            await context.bot_data["db"].set_blocked(uid, False)
            await query.answer("User unblocked")
            await _show_users_menu(query, context, page=0)
        elif data.startswith("user_clear_"):
            uid = int(data.replace("user_clear_", ""))
            await context.bot_data["db"].clear_history(uid)
            await query.answer("History cleared for user", show_alert=True)

        # ── Broadcast ─────────────────────────────────────────────────────────
        elif data == "action_broadcast":
            await _prompt_setting_input(query, context, "broadcast")

        else:
            await query.answer()

    except Exception as e:
        logger.error("Error in callback query %s: %s", data, e)
        await query.answer("⚠️ An error occurred.", show_alert=True)
        # Still acknowledge the query so the button un-freezes
    finally:
        # Failsafe answer (if not already answered)
        try:
            await query.answer()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Sub-Menu Renderers
# ─────────────────────────────────────────────────────────────────────────────

async def _show_settings_menu(query, context):
    db = context.bot_data["db"]
    settings = await db.get_all_settings()

    text = "⚙️ *Bot Configuration*\nSelect a setting to change it:"
    keyboard = [
        [InlineKeyboardButton(f"Bot Name: {settings.get('bot_name', 'HmassAssistant')}", callback_data="set_bot_name")],
        [InlineKeyboardButton(f"Owner Name: {settings.get('owner_name', 'the owner')}", callback_data="set_owner_name")],
        [InlineKeyboardButton(f"Welcome Message (Edit)", callback_data="set_welcome_message")],
        [InlineKeyboardButton(f"History Limit: {settings.get('history_limit', '20')}", callback_data="set_history_limit")],
        [InlineKeyboardButton(f"Anti-Spam Cooldown: {settings.get('anti_spam_cooldown', '3')}s", callback_data="set_anti_spam_cooldown")],
        [InlineKeyboardButton(f"AI Creativity: {settings.get('ai_temperature', '0.75')}", callback_data="set_ai_temperature")],
        [InlineKeyboardButton(f"Typing Delay: {settings.get('typing_delay_min', '1.0')} - {settings.get('typing_delay_max', '3.0')}s", callback_data="set_typing_delay")],
        [InlineKeyboardButton(f"Reply Delay: {settings.get('reply_delay', '10')}s", callback_data="set_reply_delay")],
        [InlineKeyboardButton(f"AI Model: {settings.get('ai_model', 'llama-3.1-8b-instant')}", callback_data="set_ai_model")],
        [InlineKeyboardButton(f"Rate Limit Msg (Edit)", callback_data="set_rate_limit_msg")],
        [
            InlineKeyboardButton(f"Start: {settings.get('schedule_start', '08:00')}", callback_data="set_schedule_start"),
            InlineKeyboardButton(f"End: {settings.get('schedule_end', '22:00')}", callback_data="set_schedule_end"),
        ],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_features_menu(query, context):
    db = context.bot_data["db"]
    db_features = await db.get_all_features()

    text = "🧩 *Feature Toggles*\nEnable or disable bot capabilities:"
    keyboard = []
    
    # Render two columns
    row = []
    for key, meta in FEATURES.items():
        is_on = db_features.get(key, meta["default"])
        status = "🟢" if is_on else "🔴"
        label = f"{status} {meta['emoji']} {meta['label']}"
        row.append(InlineKeyboardButton(label, callback_data=f"toggle_{key}"))
        
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_persona_menu(query, context):
    db = context.bot_data["db"]
    current = await db.get_setting("personality", "friendly")

    text = (
        "🎭 *AI Personality*\n"
        f"Current persona: `{current.capitalize()}`\n\n"
        "Choose how the AI should talk:"
    )
    keyboard = []
    
    for key, data in PERSONALITY_PRESETS.items():
        marker = "✅ " if key == current else ""
        label = f"{marker}{data['emoji']} {data['name']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"persona_{key}")])

    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_faq_menu(query, context):
    db = context.bot_data["db"]
    faqs = await db.get_faqs()

    text = "📖 *Auto-FAQ Engine*\n"
    if not faqs:
        text += "_No FAQs added yet._"
    else:
        text += "Keyword → (Hits)\n\n"
        for faq in faqs:
            text += f"• `{faq['keyword']}` ({faq['hit_count']})\n"

    keyboard = [[InlineKeyboardButton("➕ Add New FAQ", callback_data="faq_add")]]
    
    # Add delete buttons for existing FAQs
    for faq in faqs:
        keyboard.append([
            InlineKeyboardButton(f"🗑 Delete '{faq['keyword']}'", callback_data=f"faq_del_{faq['id']}")
        ])

    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_users_menu(query, context, page=0):
    db = context.bot_data["db"]
    users = await db.get_all_users()
    
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
        
        # Action row for this user
        row = [InlineKeyboardButton(f"🗑 Clear His. [{name[:10]}]", callback_data=f"user_clear_{uid}")]
        if u["is_blocked"]:
            row.append(InlineKeyboardButton("✅ Unblock", callback_data=f"user_unblock_{uid}"))
        else:
            row.append(InlineKeyboardButton("🚫 Block", callback_data=f"user_block_{uid}"))
        keyboard.append(row)

    # Pagination controls
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"users_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"users_page_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_analytics(query, context):
    db = context.bot_data["db"]
    rows = await db.get_analytics(7)
    total = await db.get_total_stats()

    text = format_analytics_table(rows, total)
    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Input State Setter
# ─────────────────────────────────────────────────────────────────────────────

async def _prompt_setting_input(query, context, awaiting_key: str):
    """Put the admin in a state where their next text message updates a setting."""
    user_id = query.from_user.id
    admin_states = context.bot_data.setdefault("admin_states", {})
    admin_states[user_id] = {"awaiting": awaiting_key}

    prompts = {
        "bot_name": "Send the new **bot name**:",
        "owner_name": "Send the new **owner name** (e.g. 'John'):",
        "welcome_message": "Send the new **welcome message**.\nVariables: `{name}`, `{bot_name}`",
        "history_limit": "Send the number of messages to remember (e.g., `20`):",
        "anti_spam_cooldown": "Send anti-spam cooldown in seconds (e.g., `3`):",
        "ai_temperature": "Send AI creativity/temperature (0.0 to 1.0):",
        "typing_delay": "Send typing delay range (e.g., `1.0 - 3.0`):",
        "schedule_start": "Send active schedule start time (`HH:MM` format):",
        "schedule_end": "Send active schedule end time (`HH:MM` format):",
        "custom_prompt": "Send the **custom system prompt/instructions** for the AI:",
        "faq_keyword": "Send the **keyword** or trigger phrase for the new FAQ:",
        "broadcast": "Send the message you want to **broadcast** to ALL active users:\n_(⚠️ Cannot be undone)_",
    }

    text = prompts.get(awaiting_key, "Send the new value:")
    text += "\n\n_(Send /cancel to abort)_"

    # We delete the menu message and send a fresh prompt to keep chat clean
    await query.message.delete()
    await context.bot.send_message(chat_id=query.message.chat_id, text=text, parse_mode="Markdown")
