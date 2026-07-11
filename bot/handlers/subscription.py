import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.payments.stars import send_stars_invoice
from config import PLANS, STRIPE_LINKS

logger = logging.getLogger(__name__)


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show subscription plans and payment options."""
    user = update.effective_user
    tenant_mgr = context.bot_data["tenant_mgr"]

    tenant = await tenant_mgr.get_tenant_by_owner(user.id)
    if not tenant:
        await update.message.reply_text(
            "⚠️ You need to connect your Telegram Business account first.\n\n"
            "Go to *Settings → Telegram Business → Chatbots* and add this bot.",
            parse_mode="Markdown",
        )
        return

    plan_info = PLANS.get(tenant["plan"], {})
    cap = tenant["ai_replies_cap"]
    used = tenant["ai_replies_used"]
    cap_str = "Unlimited" if cap == -1 else f"{cap:,}"
    days_left = await tenant_mgr.trial_days_remaining(tenant["tenant_id"])

    status_line = (
        f"📊 Current: *{plan_info.get('label', tenant['plan'])}*"
        + (f" | {used}/{cap_str} replies used" if cap != -1 else "")
        + (f"\n⏳ Trial expires in *{days_left} days*" if tenant["status"] == "trial" else "")
    )

    keyboard = [
        [InlineKeyboardButton("🥉 Starter — 500 replies/mo", callback_data="sub_plan_starter")],
        [InlineKeyboardButton("🥈 Pro — 2,000 replies/mo",   callback_data="sub_plan_pro")],
        [InlineKeyboardButton("🥇 Business — Unlimited",     callback_data="sub_plan_business")],
    ]
    if tenant["status"] in ("active",):
        keyboard.append([InlineKeyboardButton("❌ Cancel Subscription", callback_data="sub_cancel")])

    await update.message.reply_text(
        f"{status_line}\n\n"
        "Choose a plan to activate or upgrade:\n\n"
        "🥉 *Starter* — 500 replies/mo — ⭐500 or $5\n"
        "🥈 *Pro* — 2,000 replies/mo — ⭐1,500 or $15\n"
        "🥇 *Business* — Unlimited — ⭐4,000 or $40\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def handle_subscribe_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses from the /subscribe menu."""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    tenant_mgr = context.bot_data["tenant_mgr"]

    tenant = await tenant_mgr.get_tenant_by_owner(user_id)
    if not tenant:
        await query.answer("No tenant found.", show_alert=True)
        return

    tenant_id = tenant["tenant_id"]

    if data.startswith("sub_plan_"):
        plan = data.replace("sub_plan_", "")
        plan_info = PLANS.get(plan, {})
        stars = plan_info.get("stars", 0)
        usd = plan_info.get("usd_cents", 0) / 100
        cap = plan_info.get("cap", 0)
        cap_str = "Unlimited" if cap == -1 else f"{cap:,}"
        label = plan_info.get("label", plan)

        # Build payment method choice
        keyboard = [
            [InlineKeyboardButton(f"⭐ Pay {stars:,} Stars/month", callback_data=f"sub_pay_stars_{plan}")],
        ]
        stripe_link = STRIPE_LINKS.get(plan, "")
        if stripe_link:
            # Append tenant_id as query param so Stripe can identify the tenant
            link_with_ref = f"{stripe_link}?client_reference_id={tenant_id}"
            keyboard.append([InlineKeyboardButton(f"💳 Pay ${usd:.0f}/month (Card)", url=link_with_ref)])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="sub_back")])

        await query.edit_message_text(
            f"*{label} Plan*\n"
            f"✅ {cap_str} AI replies/month\n\n"
            "Choose payment method:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    elif data.startswith("sub_pay_stars_"):
        plan = data.replace("sub_pay_stars_", "")
        await query.message.delete()
        await send_stars_invoice(
            bot=context.bot,
            chat_id=query.message.chat_id,
            tenant_id=tenant_id,
            plan=plan,
        )

    elif data == "sub_cancel":
        # For Stripe: redirect to billing portal or just inform
        # For Stars: manual cancel — suspend and inform
        await tenant_mgr.suspend(tenant_id)
        await query.edit_message_text(
            "✅ *Subscription cancelled.*\n\n"
            "Your assistant will remain active until the end of your billing period.\n"
            "Use /subscribe to reactivate at any time.",
            parse_mode="Markdown",
        )

    elif data == "sub_back":
        # Re-show the plan selection menu
        plan_info = PLANS.get(tenant["plan"], {})
        cap = tenant["ai_replies_cap"]
        used = tenant["ai_replies_used"]
        cap_str = "Unlimited" if cap == -1 else f"{cap:,}"
        keyboard = [
            [InlineKeyboardButton("🥉 Starter — 500 replies/mo", callback_data="sub_plan_starter")],
            [InlineKeyboardButton("🥈 Pro — 2,000 replies/mo",   callback_data="sub_plan_pro")],
            [InlineKeyboardButton("🥇 Business — Unlimited",     callback_data="sub_plan_business")],
        ]
        await query.edit_message_text(
            f"📊 Current: *{plan_info.get('label', tenant['plan'])}* | {used}/{cap_str} replies\n\n"
            "Choose a plan:\n\n"
            "🥉 *Starter* — 500 replies/mo — ⭐500 or $5\n"
            "🥈 *Pro* — 2,000 replies/mo — ⭐1,500 or $15\n"
            "🥇 *Business* — Unlimited — ⭐4,000 or $40\n",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    try:
        await query.answer()
    except Exception:
        pass
