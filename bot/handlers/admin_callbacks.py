import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils.helpers import is_admin, paginate

logger = logging.getLogger(__name__)

async def platform_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route platform admin dashboard inline keyboard callbacks (owner only)."""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if not is_admin(user_id):
        await query.answer("Unauthorized", show_alert=True)
        return

    tenant_mgr = context.bot_data["tenant_mgr"]
    db = context.bot_data["db"]

    try:
        if data == "platform_main":
            from .admin import build_platform_menu
            text, markup = await build_platform_menu(context)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")

        elif data.startswith("platform_tenants_page_"):
            page = int(data.replace("platform_tenants_page_", ""))
            await _show_tenants_list(query, context, page)

        elif data == "platform_stats":
            await _show_system_details(query, context)

        elif data == "platform_suspend_prompt":
            await _prompt_admin_input(query, context, "suspend_tenant_id")

        elif data == "platform_gift_prompt":
            await _prompt_admin_input(query, context, "gift_tenant_id")

        elif data == "platform_broadcast":
            await _prompt_admin_input(query, context, "platform_broadcast_msg")

        elif data.startswith("platform_toggle_suspend_"):
            tenant_id = data.replace("platform_toggle_suspend_", "")
            tenant = await tenant_mgr.get_tenant(tenant_id)
            if tenant:
                if tenant["status"] == "suspended":
                    await tenant_mgr.unsuspend(tenant_id)
                    await query.answer("Tenant unsuspended")
                else:
                    await tenant_mgr.suspend(tenant_id)
                    await query.answer("Tenant suspended")
                await _show_tenants_list(query, context, page=0)
            else:
                await query.answer("Tenant not found", show_alert=True)

        else:
            await query.answer()

    except Exception as e:
        logger.error("Platform admin callback error [%s]: %s", data, e)
        await query.answer("⚠️ An error occurred.", show_alert=True)
    finally:
        try:
            await query.answer()
        except Exception:
            pass

async def _show_tenants_list(query, context, page: int = 0):
    tenant_mgr = context.bot_data["tenant_mgr"]
    tenants = await tenant_mgr.get_all_tenants()
    page_items, total_pages = paginate(tenants, page, per_page=5)

    text = f"👥 *JarvisAssist Tenants* (Page {page+1}/{total_pages})\n\n"
    keyboard = []

    for t in page_items:
        tid = t["tenant_id"]
        status = "🟢" if t["status"] in ("active", "owner") else "🟡" if t["status"] == "trial" else "🔴"
        cap = t["ai_replies_cap"]
        cap_str = "Unlimited" if cap == -1 else f"{cap:,}"
        
        # Try to get bot name
        db = context.bot_data["db"]
        bot_name = await db.get_setting(tid, "bot_name", "Jarvis")

        text += (
            f"• *{bot_name}* (`{tid}`)\n"
            f"  Plan: *{t['plan'].upper()}* | Status: {status} `{t['status'].upper()}`\n"
            f"  Usage: `{t['ai_replies_used']}` / `{cap_str}` replies\n"
            f"  Owner: TG ID `{t['owner_tg_id']}`\n\n"
        )

        susp_label = "Unsuspend" if t["status"] == "suspended" else "Suspend"
        keyboard.append([
            InlineKeyboardButton(f"🚫 {susp_label}", callback_data=f"platform_toggle_suspend_{tid}"),
            InlineKeyboardButton("🎁 Gift", callback_data=f"platform_gift_tenant_{tid}")
        ])

    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"platform_tenants_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"platform_tenants_page_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="platform_main")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def _show_system_details(query, context):
    db = context.bot_data["db"]
    stats = await db.get_platform_stats()
    
    text = (
        "📊 *Detailed System Analytics*\n\n"
        f"• Total registered tenants: `{stats['total_tenants']}`\n"
        f"• Active/Trial tenants: `{stats['active_tenants']}`\n"
        f"• Total lifetime replies: `{stats['total_ai_used']:,}`\n"
        f"• Today's AI replies: `{stats['today_ai']}`\n"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="platform_main")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def _prompt_admin_input(query, context, awaiting_key: str):
    user_id = query.from_user.id
    admin_states = context.bot_data.setdefault("admin_states", {})
    admin_states[user_id] = {"awaiting": awaiting_key, "scope": "platform"}

    prompts = {
        "suspend_tenant_id":     "Send the `tenant_id` (business connection ID) to suspend/unsuspend:",
        "gift_tenant_id":        "Send the `tenant_id` to upgrade/gift a plan to:",
        "platform_broadcast_msg":"Send the message to broadcast to **ALL active tenant owners**:",
    }
    
    text = prompts.get(awaiting_key, "Send input:") + "\n\n_(Send /cancel to abort)_"
    await query.message.delete()
    await context.bot.send_message(chat_id=query.message.chat_id, text=text, parse_mode="Markdown")
