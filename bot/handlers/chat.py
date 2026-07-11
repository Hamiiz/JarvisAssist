import asyncio
import hashlib
import logging
import re
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.ai.prompt_builder import build_system_prompt
from bot.utils.helpers import is_within_schedule, is_admin
from config import PLANS

logger = logging.getLogger(__name__)

# Anti-spam tracking: key is (tenant_id, user_id) -> last timestamp
_last_msg_time: dict[tuple[str, int], float] = {}

# Queue management for dynamic delay & batching
# Key: chat_id, Value: asyncio.Task
_pending_tasks: dict[int, asyncio.Task] = {}
# Key: chat_id, Value: list of strings (messages to batch)
_pending_messages: dict[int, list[str]] = {}


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route incoming text messages through FAQ → Delay Queue → Groq."""
    msg = update.effective_message
    if not msg:
        return

    user = msg.from_user
    if not user:
        return
    uid = user.id
    chat_id = msg.chat_id
    db = context.bot_data["db"]
    tenant_mgr = context.bot_data["tenant_mgr"]

    is_business = bool(update.business_message)

    # ── Non-Business Messages (Direct Messages to the Bot) ───────────────────
    if not is_business:
        # Check if they are in admin/tenant setup states
        admin_states = context.bot_data.get("admin_states", {})
        if uid in admin_states:
            await _handle_admin_input(update, context)
            return
        # Command handlers will deal with /start, /setup, /subscribe, etc.
        return

    # ── Business Messages ───────────────────────────────────────────────────
    tenant_id = update.business_message.business_connection_id
    tenant = await tenant_mgr.get_tenant(tenant_id)
    if not tenant or tenant["status"] == "cancelled":
        return  # Silently ignore if not registered or cancelled

    # Check if the message is outgoing (from the business owner themselves or the bot)
    is_outgoing = False
    if user.id == context.bot.id or user.id == tenant["owner_tg_id"] or getattr(msg, "is_outgoing", False):
        is_outgoing = True

    # ── Owner Override (Cancel Queue) ─────────────────────────────────────────
    if is_outgoing:
        if chat_id in _pending_tasks:
            _pending_tasks[chat_id].cancel()
            del _pending_tasks[chat_id]
        if chat_id in _pending_messages:
            del _pending_messages[chat_id]
        return

    # Check if this sender is blocked by the tenant owner
    if await db.is_blocked(tenant_id, uid):
        return

    # Upsert user inside the tenant's namespace
    await db.upsert_user(
        tenant_id=tenant_id,
        user_id=uid,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or ""
    )

    # Check activation & quota
    if not await tenant_mgr.is_active(tenant_id):
        return
    if not await tenant_mgr.check_quota(tenant_id):
        return

    settings = await db.get_all_settings(tenant_id)
    features = await db.get_all_features(tenant_id)

    # Away Mode is the master switch for automatic replies.
    if not features.get("away_mode", True):
        logger.info("Auto-reply skipped: Away Mode is disabled for tenant %s", tenant_id)
        return

    # ── Schedule gate ─────────────────────────────────────────────────────────
    if features.get("schedule", False):
        if not is_within_schedule(
            settings.get("schedule_start", "08:00"),
            settings.get("schedule_end",   "22:00"),
            settings.get("schedule_timezone", "Africa/Addis_Ababa"),
        ):
            return

    # ── Anti-spam gate ────────────────────────────────────────────────────────
    if features.get("anti_spam", True):
        cooldown = float(settings.get("anti_spam_cooldown", "3"))
        now = time.time()
        spam_key = (tenant_id, uid)
        if now - _last_msg_time.get(spam_key, 0) < cooldown:
            return
        _last_msg_time[spam_key] = now

    user_text = msg.text or ""

    # ── FAQ check (Instant) ───────────────────────────────────────────────────
    if features.get("faq_engine", True) and user_text:
        faq_reply = await db.check_faq(tenant_id, user_text)
        if faq_reply:
            await db.increment_analytics(tenant_id, "faq_hits")
            await context.bot.send_message(
                chat_id=chat_id,
                text=faq_reply,
                business_connection_id=tenant_id
            )
            return

    # ── Batching & Delay Logic ────────────────────────────────────────────────
    if chat_id not in _pending_messages:
        _pending_messages[chat_id] = []
    
    if user_text:
        _pending_messages[chat_id].append(user_text)

    # Reset the timer
    if chat_id in _pending_tasks:
        _pending_tasks[chat_id].cancel()

    delay = int(settings.get("reply_delay", "10"))
    task = asyncio.create_task(
        _process_delayed_messages(chat_id, tenant_id, update, context, settings, features, delay)
    )
    task.add_done_callback(_log_background_task_failure)
    _pending_tasks[chat_id] = task


def _log_background_task_failure(task: asyncio.Task) -> None:
    """Make delayed-reply failures visible instead of silently losing a reply."""
    if task.cancelled():
        return
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return
    if error:
        logger.exception("Auto-reply task failed", exc_info=error)


async def _process_delayed_messages(
    chat_id: int,
    tenant_id: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: dict,
    features: dict,
    delay: int
):
    """Executes after dynamic delay. Fetches batched messages, calls Groq, sends reply."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return

    # Wake up & pop messages
    if chat_id not in _pending_messages:
        return
    messages_to_process = _pending_messages.pop(chat_id)
    if chat_id in _pending_tasks:
        del _pending_tasks[chat_id]

    combined_text = "\n".join(messages_to_process)
    if not combined_text.strip():
        return

    msg = update.effective_message
    uid = update.effective_user.id
    db = context.bot_data["db"]
    tenant_mgr = context.bot_data["tenant_mgr"]
    groq_client = context.bot_data["gemini"]  # Backwards-compatible key name

    await db.increment_analytics(tenant_id, "msgs_received")

    if features.get("typing_sim", True):
        try:
            await context.bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.TYPING,
                business_connection_id=tenant_id
            )
        except Exception as e:
            logger.warning("Failed to send typing action: %s", e)

    system_prompt = build_system_prompt(settings, features)
    
    history_limit = min(int(settings.get("history_limit", "5")), 10)
    history: list[dict] = []
    if features.get("history", True):
        history = await db.get_history(tenant_id, uid, history_limit)

    if features.get("history", True):
        await db.save_message(tenant_id, uid, "user", combined_text)

    # ── Groq API with Caching ─────────────────────────────────────────────────
    history_str = str([h["parts"][0]["text"] for h in history])
    hash_input = f"{tenant_id}|{system_prompt}|{history_str}|{combined_text}"
    query_hash = hashlib.sha256(hash_input.encode()).hexdigest()

    cached_reply = await db.get_cached_response(tenant_id, query_hash)
    
    if cached_reply:
        ai_reply = cached_reply
    else:
        temperature = float(settings.get("ai_temperature", "0.75"))
        rate_limit_msg = settings.get("rate_limit_msg", "⚠️ I'm currently overwhelmed with messages. Please try again later!")
        ai_reply = await groq_client.chat(
            user_message=combined_text,
            history=history,
            system_prompt=system_prompt,
            temperature=temperature,
            model_name=settings.get("ai_model") or None,
            rate_limit_msg=rate_limit_msg,
        )
        await db.set_cached_response(tenant_id, query_hash, ai_reply)

    if features.get("history", True):
        await db.save_message(tenant_id, uid, "model", ai_reply)

    await tenant_mgr.increment_usage(tenant_id)
    await db.increment_analytics(tenant_id, "ai_responses")

    # ── Notification Parsing ──────────────────────────────────────────────────
    notify_match = re.search(r'\[NOTIFY:\s*(.*?)\]', ai_reply, re.IGNORECASE | re.DOTALL)
    if notify_match:
        notification_text = notify_match.group(1).strip()
        ai_reply = re.sub(r'\[NOTIFY:\s*(.*?)\]', '', ai_reply, flags=re.IGNORECASE | re.DOTALL).strip()
        
        # Relay to the bot owner (the paying tenant owner)
        tenant = await tenant_mgr.get_tenant(tenant_id)
        if tenant:
            alert_msg = f"🔔 *New Message Left by {msg.from_user.first_name}:*\n\n_{notification_text}_"
            try:
                await context.bot.send_message(chat_id=tenant["owner_tg_id"], text=alert_msg, parse_mode="Markdown")
            except Exception as e:
                logger.error("Failed to relay notification to tenant owner: %s", e)

    if not ai_reply:
        ai_reply = "I'll let them know!"

    # Send model output as plain text. Markdown generated by an AI can contain
    # unmatched formatting characters, which Telegram rejects and drops.
    ai_reply = f"{ai_reply.strip()}\n\n🤖 Automated AI Reply"[:4096]

    # ── Typing simulation delay ───────────────────────────────────────────────
    if features.get("typing_sim", True):
        delay_min = float(settings.get("typing_delay_min", "1.0"))
        delay_max = float(settings.get("typing_delay_max", "3.0"))
        delay_typing = min(delay_min + len(ai_reply) * 0.008, delay_max)
        await asyncio.sleep(delay_typing)

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=ai_reply,
            business_connection_id=tenant_id,
        )
    except Exception as e:
        logger.error("Failed to send AI reply to chat %s: %s", chat_id, e)


# ─────────────────────────────────────────────────────────────────────────────
# Input state processor
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process text input from a user in an active configuration state."""
    uid = update.effective_user.id
    db = context.bot_data["db"]
    tenant_mgr = context.bot_data["tenant_mgr"]
    admin_states = context.bot_data.setdefault("admin_states", {})
    state = admin_states.get(uid, {})
    awaiting = state.get("awaiting")
    scope = state.get("scope", "setup")
    tenant_id = state.get("tenant_id")
    text = update.message.text.strip()

    def _done():
        admin_states.pop(uid, None)

    # If it is tenant setup scope
    if scope == "setup":
        if not tenant_id:
            _done()
            return

        if awaiting == "custom_prompt":
            await db.set_setting(tenant_id, "custom_prompt", text)
            await db.set_setting(tenant_id, "personality", "custom")
            _done()
            await update.message.reply_text("✅ Custom personality saved!\n\nAI will follow these instructions.")

        elif awaiting == "welcome_message":
            await db.set_setting(tenant_id, "welcome_message", text)
            _done()
            await update.message.reply_text(f"✅ Welcome message updated!\n\n_{text}_", parse_mode="Markdown")

        elif awaiting == "bot_name":
            await db.set_setting(tenant_id, "bot_name", text[:50])
            _done()
            await update.message.reply_text(f"✅ Bot name set to: *{text[:50]}*", parse_mode="Markdown")

        elif awaiting == "owner_name":
            await db.set_setting(tenant_id, "owner_name", text[:50])
            _done()
            await update.message.reply_text(f"✅ Owner name set to: *{text[:50]}*", parse_mode="Markdown")

        elif awaiting == "history_limit":
            try:
                val = max(5, min(100, int(text)))
                await db.set_setting(tenant_id, "history_limit", str(val))
                _done()
                await update.message.reply_text(f"✅ History limit set to *{val}*.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Please enter a whole number (5-100).")

        elif awaiting == "anti_spam_cooldown":
            try:
                val = max(0, min(60, int(text)))
                await db.set_setting(tenant_id, "anti_spam_cooldown", str(val))
                _done()
                await update.message.reply_text(f"✅ Anti-spam cooldown set to *{val}s*.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Please enter a whole number (0-60).")

        elif awaiting == "ai_temperature":
            try:
                val = round(max(0.0, min(1.0, float(text))), 2)
                await db.set_setting(tenant_id, "ai_temperature", str(val))
                _done()
                await update.message.reply_text(f"✅ AI creativity set to *{val}*.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Enter a decimal between 0.0 and 1.0.")

        elif awaiting == "typing_delay":
            try:
                parts = [p.strip() for p in text.split("-")]
                mn = max(0.0, float(parts[0]))
                mx = max(mn, float(parts[1])) if len(parts) > 1 else mn + 2.0
                await db.set_setting(tenant_id, "typing_delay_min", str(mn))
                await db.set_setting(tenant_id, "typing_delay_max", str(mx))
                _done()
                await update.message.reply_text(f"✅ Typing delay set to *{mn}s – {mx}s*.", parse_mode="Markdown")
            except (ValueError, IndexError):
                await update.message.reply_text("❌ Format: `min - max`  e.g. `1.0 - 3.0`", parse_mode="Markdown")

        elif awaiting == "reply_delay":
            try:
                val = max(1, min(300, int(text)))
                await db.set_setting(tenant_id, "reply_delay", str(val))
                _done()
                await update.message.reply_text(f"✅ Reply delay set to *{val}s*.", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Please enter a whole number (1-300).")

        elif awaiting == "rate_limit_msg":
            await db.set_setting(tenant_id, "rate_limit_msg", text)
            _done()
            await update.message.reply_text(f"✅ Rate limit message updated to:\n_{text}_", parse_mode="Markdown")

        elif awaiting == "ai_model":
            await db.set_setting(tenant_id, "ai_model", text.strip())
            _done()
            await update.message.reply_text(f"✅ AI Model updated to: *{text.strip()}*", parse_mode="Markdown")

        elif awaiting == "schedule_start":
            if re.match(r"^\d{1,2}:\d{2}$", text):
                await db.set_setting(tenant_id, "schedule_start", text)
                _done()
                await update.message.reply_text(f"✅ Schedule start set to *{text}*.", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Use `HH:MM` format, e.g. `08:00`", parse_mode="Markdown")

        elif awaiting == "schedule_end":
            if re.match(r"^\d{1,2}:\d{2}$", text):
                await db.set_setting(tenant_id, "schedule_end", text)
                _done()
                await update.message.reply_text(f"✅ Schedule end set to *{text}*.", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Use `HH:MM` format, e.g. `22:00`", parse_mode="Markdown")

        elif awaiting == "faq_keyword":
            admin_states[uid] = {"awaiting": "faq_response", "keyword": text, "tenant_id": tenant_id, "scope": "setup"}
            await update.message.reply_text(f"📝 Keyword: *{text}*\n\nNow send the *response*:")

        elif awaiting == "faq_response":
            keyword = state.get("keyword", "")
            await db.add_faq(tenant_id, keyword, text)
            _done()
            await update.message.reply_text(
                f"✅ FAQ entry added!\n\n🔑 Keyword: *{keyword}*\n💬 Response: _{text}_",
                parse_mode="Markdown",
            )

    # Platform Owner Admin scope
    elif scope == "platform":
        if awaiting == "suspend_tenant_id":
            target = await tenant_mgr.get_tenant(text)
            if target:
                if target["status"] == "suspended":
                    await tenant_mgr.unsuspend(text)
                    await update.message.reply_text(f"✅ Tenant `{text}` unsuspended.")
                else:
                    await tenant_mgr.suspend(text)
                    await update.message.reply_text(f"✅ Tenant `{text}` suspended.")
            else:
                await update.message.reply_text(f"❌ Tenant `{text}` not found.")
            _done()

        elif awaiting == "gift_tenant_id":
            target = await tenant_mgr.get_tenant(text)
            if target:
                admin_states[uid] = {"awaiting": "gift_plan_tier", "target_tenant_id": text, "scope": "platform"}
                await update.message.reply_text(
                    f"🎁 Tenant: `{text}`\n\nSend the plan tier to gift (`starter`, `pro`, `business`):"
                )
            else:
                await update.message.reply_text(f"❌ Tenant `{text}` not found.")
                _done()

        elif awaiting == "gift_plan_tier":
            target_id = state.get("target_tenant_id")
            tier = text.lower().strip()
            if tier in PLANS:
                await tenant_mgr.gift_plan(target_id, tier)
                await update.message.reply_text(f"✅ Tenant `{target_id}` upgraded to *{tier.upper()}*.", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Invalid plan. Use starter, pro, or business.")
            _done()

        elif awaiting == "platform_broadcast_msg":
            _done()
            tenants = await tenant_mgr.get_all_tenants()
            sent, failed = 0, 0
            notice = await update.message.reply_text(f"📡 Broadcasting to {len(tenants)} active tenant owners...")
            
            # Send message to all unique tenant owners
            seen_owners = set()
            for t in tenants:
                owner_id = t["owner_tg_id"]
                if owner_id in seen_owners:
                    continue
                seen_owners.add(owner_id)
                try:
                    await context.bot.send_message(
                        chat_id=owner_id,
                        text=f"📡 *Broadcast Message from Platform Admin:*\n\n{text}",
                        parse_mode="Markdown"
                    )
                    sent += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    failed += 1

            await notice.edit_text(
                f"📡 *Broadcast complete!*\n\n"
                f"✅ Delivered: {sent}\n"
                f"❌ Failed: {failed}",
                parse_mode="Markdown"
            )
