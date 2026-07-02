import asyncio
import hashlib
import logging
import re
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.ai.prompt_builder import build_system_prompt
from bot.utils.helpers import is_admin, is_within_schedule
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

# Anti-spam tracking
_last_msg_time: dict[int, float] = {}

# Queue management for dynamic delay & batching
# Key: chat_id, Value: asyncio.Task
_pending_tasks: dict[int, asyncio.Task] = {}
# Key: chat_id, Value: list of strings (messages to batch)
_pending_messages: dict[int, list[str]] = {}


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route incoming text messages through FAQ → Delay Queue → Groq."""
    msg = update.effective_message
    if not msg or not msg.text:
        return

    chat_id = msg.chat_id
    user = msg.from_user
    uid = user.id
    db = context.bot_data["db"]

    is_business = bool(update.business_message)
    # Check if the message is from the owner themselves or the bot
    is_outgoing = False
    if is_business:
        if user.id == context.bot.id or is_admin(user.id) or getattr(msg, "is_outgoing", False):
            is_outgoing = True
    else:
        if user.id == context.bot.id:
            is_outgoing = True

    # ── Owner Override (Cancel Queue) ─────────────────────────────────────────
    if is_outgoing:
        if chat_id in _pending_tasks:
            _pending_tasks[chat_id].cancel()
            del _pending_tasks[chat_id]
        if chat_id in _pending_messages:
            del _pending_messages[chat_id]
        return

    if await db.is_blocked(uid):
        return

    await db.upsert_user(uid, user.username or "", user.first_name or "", user.last_name or "")

    settings = await db.get_all_settings()
    features = await db.get_all_features()

    # ── Admin input state ─────────────────────────────────────────────────────
    if is_admin(uid):
        admin_states = context.bot_data.get("admin_states", {})
        if uid in admin_states:
            await _handle_admin_input(update, context, settings, features)
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
    if features.get("anti_spam", True) and not is_admin(uid):
        cooldown = float(settings.get("anti_spam_cooldown", "3"))
        now = time.time()
        if now - _last_msg_time.get(uid, 0) < cooldown:
            return
        _last_msg_time[uid] = now

    user_text = msg.text.strip()

    # ── FAQ check (Instant) ───────────────────────────────────────────────────
    if features.get("faq_engine", True):
        faq_reply = await db.check_faq(user_text)
        if faq_reply:
            await db.increment_analytics("faq_hits")
            await msg.reply_text(faq_reply)
            return

    # ── Batching & Delay Logic ────────────────────────────────────────────────
    if chat_id not in _pending_messages:
        _pending_messages[chat_id] = []
    _pending_messages[chat_id].append(user_text)

    # Reset the timer
    if chat_id in _pending_tasks:
        _pending_tasks[chat_id].cancel()

    delay = int(settings.get("reply_delay", "10"))
    task = asyncio.create_task(
        _process_delayed_messages(chat_id, update, context, settings, features, delay)
    )
    _pending_tasks[chat_id] = task


async def _process_delayed_messages(
    chat_id: int, 
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
        return  # Aborted because owner replied or user sent another message

    # Wake up & pop messages
    if chat_id not in _pending_messages:
        return
    messages_to_process = _pending_messages.pop(chat_id)
    if chat_id in _pending_tasks:
        del _pending_tasks[chat_id]

    combined_text = "\n".join(messages_to_process)

    msg = update.effective_message
    uid = update.effective_user.id
    db = context.bot_data["db"]
    groq_client = context.bot_data["gemini"]  # Keeping key as 'gemini' for backward compatibility
    b_id = update.business_message.business_connection_id if update.business_message else None

    await db.increment_analytics("msgs_received")

    if features.get("typing_sim", True):
        try:
            await context.bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.TYPING,
                business_connection_id=b_id
            )
        except Exception as e:
            logger.warning("Failed to send typing action: %s", e)

    system_prompt = build_system_prompt(settings, features)
    
    # Limit history to 5 (or user setting max 10) to heavily reduce Groq tokens
    history_limit = min(int(settings.get("history_limit", "5")), 10)
    history: list[dict] = []
    if features.get("history", True):
        history = await db.get_history(uid, history_limit)

    if features.get("history", True):
        await db.save_message(uid, "user", combined_text)

    # ── Groq API with Caching ─────────────────────────────────────────────────
    # We hash the exact conversational state to instantly fetch from SQLite if seen recently
    history_str = str([h["parts"][0]["text"] for h in history])
    hash_input = f"{system_prompt}|{history_str}|{combined_text}"
    query_hash = hashlib.sha256(hash_input.encode()).hexdigest()

    cached_reply = await db.get_cached_response(query_hash)
    
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
            rate_limit_msg=rate_limit_msg,
        )
        await db.set_cached_response(query_hash, ai_reply)

    if features.get("history", True):
        await db.save_message(uid, "model", ai_reply)

    await db.increment_analytics("ai_responses")

    # ── Notification Parsing ──────────────────────────────────────────────────
    notify_match = re.search(r'\[NOTIFY:\s*(.*?)\]', ai_reply, re.IGNORECASE | re.DOTALL)
    if notify_match:
        notification_text = notify_match.group(1).strip()
        # Remove tag from the reply shown to the user
        ai_reply = re.sub(r'\[NOTIFY:\s*(.*?)\]', '', ai_reply, flags=re.IGNORECASE | re.DOTALL).strip()
        
        # Relay to the bot owner (admin)
        if ADMIN_IDS:
            admin_id = ADMIN_IDS[0]
            alert_msg = f"🔔 *New Message Left by {msg.from_user.first_name}:*\n\n_{notification_text}_"
            try:
                await context.bot.send_message(chat_id=admin_id, text=alert_msg, parse_mode="Markdown")
            except Exception as e:
                logger.error("Failed to relay notification to admin: %s", e)

    # Fallback if the AI ONLY wrote the notify tag
    if not ai_reply:
        ai_reply = "I'll let them know!"

    # Append signature so people definitively know it's an AI
    ai_reply += "\n\n🤖 _Automated AI Reply_"

    # ── Typing simulation delay ───────────────────────────────────────────────
    if features.get("typing_sim", True):
        delay_min = float(settings.get("typing_delay_min", "1.0"))
        delay_max = float(settings.get("typing_delay_max", "3.0"))
        delay = min(delay_min + len(ai_reply) * 0.008, delay_max)
        await asyncio.sleep(delay)

    await msg.reply_text(ai_reply, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# Admin input state processor
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: dict,
    features: dict,
):
    """Process a text message from an admin who is in an active input state."""
    uid          = update.effective_user.id
    db           = context.bot_data["db"]
    admin_states = context.bot_data.setdefault("admin_states", {})
    state        = admin_states.get(uid, {})
    awaiting     = state.get("awaiting")
    text         = update.message.text.strip()

    def _done():
        """Clear the admin's input state."""
        admin_states.pop(uid, None)

    # ── System prompt / custom personality ───────────────────────────────────
    if awaiting == "custom_prompt":
        await db.set_setting("custom_prompt", text)
        await db.set_setting("personality",   "custom")
        _done()
        await update.message.reply_text(
            "✅ Custom personality saved!\n\n"
            "The AI will now follow your custom instructions."
        )

    # ── Welcome message ───────────────────────────────────────────────────────
    elif awaiting == "welcome_message":
        await db.set_setting("welcome_message", text)
        _done()
        await update.message.reply_text(
            f"✅ Welcome message updated!\n\n_{text}_",
            parse_mode="Markdown",
        )

    # ── Bot name ──────────────────────────────────────────────────────────────
    elif awaiting == "bot_name":
        await db.set_setting("bot_name", text[:50])
        _done()
        await update.message.reply_text(
            f"✅ Bot name set to: *{text[:50]}*", parse_mode="Markdown"
        )

    # ── Owner name ────────────────────────────────────────────────────────────
    elif awaiting == "owner_name":
        await db.set_setting("owner_name", text[:50])
        _done()
        await update.message.reply_text(
            f"✅ Owner name set to: *{text[:50]}*", parse_mode="Markdown"
        )

    # ── History limit ─────────────────────────────────────────────────────────
    elif awaiting == "history_limit":
        try:
            val = max(5, min(100, int(text)))
            await db.set_setting("history_limit", str(val))
            _done()
            await update.message.reply_text(
                f"✅ History limit set to *{val}* messages.", parse_mode="Markdown"
            )
        except ValueError:
            await update.message.reply_text("❌ Please enter a whole number (5–100).")

    # ── Anti-spam cooldown ────────────────────────────────────────────────────
    elif awaiting == "anti_spam_cooldown":
        try:
            val = max(0, min(60, int(text)))
            await db.set_setting("anti_spam_cooldown", str(val))
            _done()
            await update.message.reply_text(
                f"✅ Anti-spam cooldown set to *{val}s*.", parse_mode="Markdown"
            )
        except ValueError:
            await update.message.reply_text("❌ Please enter a whole number (0–60).")

    # ── AI temperature ────────────────────────────────────────────────────────
    elif awaiting == "ai_temperature":
        try:
            val = round(max(0.0, min(1.0, float(text))), 2)
            await db.set_setting("ai_temperature", str(val))
            _done()
            await update.message.reply_text(
                f"✅ AI creativity set to *{val}*\n_(0.0 = predictable, 1.0 = very creative)_",
                parse_mode="Markdown",
            )
        except ValueError:
            await update.message.reply_text("❌ Enter a decimal between 0.0 and 1.0.")

    # ── Typing delay ──────────────────────────────────────────────────────────
    elif awaiting == "typing_delay":
        try:
            parts = [p.strip() for p in text.split("-")]
            mn    = max(0.0, float(parts[0]))
            mx    = max(mn,  float(parts[1])) if len(parts) > 1 else mn + 2.0
            await db.set_setting("typing_delay_min", str(mn))
            await db.set_setting("typing_delay_max", str(mx))
            _done()
            await update.message.reply_text(
                f"✅ Typing delay set to *{mn}s – {mx}s*.", parse_mode="Markdown"
            )
        except (ValueError, IndexError):
            await update.message.reply_text(
                "❌ Format: `min - max`  e.g. `1.0 - 3.0`", parse_mode="Markdown"
            )

    # ── Reply delay ───────────────────────────────────────────────────────────
    elif awaiting == "reply_delay":
        try:
            val = max(1, min(300, int(text)))
            await db.set_setting("reply_delay", str(val))
            _done()
            await update.message.reply_text(
                f"✅ Reply delay (wait time) set to *{val}s*.", parse_mode="Markdown"
            )
        except ValueError:
            await update.message.reply_text("❌ Please enter a whole number (1–300).")

    # ── Rate limit msg ────────────────────────────────────────────────────────
    elif awaiting == "rate_limit_msg":
        await db.set_setting("rate_limit_msg", text)
        _done()
        await update.message.reply_text(
            f"✅ Rate limit message updated to:\n_{text}_", parse_mode="Markdown"
        )

    # ── AI Model ──────────────────────────────────────────────────────────────
    elif awaiting == "ai_model":
        await db.set_setting("ai_model", text.strip())
        # We must update the active client instance
        context.bot_data["gemini"].update_model(text.strip())
        _done()
        await update.message.reply_text(
            f"✅ AI Model updated to: *{text.strip()}*\n"
            "_(Make sure this is a valid Groq model name!)_", 
            parse_mode="Markdown"
        )

    # ── Schedule start ────────────────────────────────────────────────────────
    elif awaiting == "schedule_start":
        if re.match(r"^\d{1,2}:\d{2}$", text):
            await db.set_setting("schedule_start", text)
            _done()
            await update.message.reply_text(
                f"✅ Schedule start set to *{text}*.", parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ Use `HH:MM` format, e.g. `08:00`", parse_mode="Markdown"
            )

    # ── Schedule end ──────────────────────────────────────────────────────────
    elif awaiting == "schedule_end":
        if re.match(r"^\d{1,2}:\d{2}$", text):
            await db.set_setting("schedule_end", text)
            _done()
            await update.message.reply_text(
                f"✅ Schedule end set to *{text}*.", parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ Use `HH:MM` format, e.g. `22:00`", parse_mode="Markdown"
            )

    # ── FAQ keyword (step 1 of 2) ─────────────────────────────────────────────
    elif awaiting == "faq_keyword":
        admin_states[uid] = {"awaiting": "faq_response", "keyword": text}
        await update.message.reply_text(
            f"📝 Keyword: *{text}*\n\nNow send the *response* for this keyword:",
            parse_mode="Markdown",
        )

    # ── FAQ response (step 2 of 2) ────────────────────────────────────────────
    elif awaiting == "faq_response":
        keyword = state.get("keyword", "")
        await db.add_faq(keyword, text)
        _done()
        await update.message.reply_text(
            f"✅ FAQ entry added!\n\n🔑 Keyword: *{keyword}*\n💬 Response: _{text}_",
            parse_mode="Markdown",
        )

    # ── Broadcast (send to all users) ─────────────────────────────────────────
    elif awaiting == "broadcast":
        _done()
        user_ids = await db.get_all_user_ids()
        sent, failed = 0, 0
        notice = await update.message.reply_text(
            f"📡 Broadcasting to {len(user_ids):,} users…"
        )
        for target_uid in user_ids:
            try:
                await context.bot.send_message(chat_id=target_uid, text=text)
                sent += 1
                await asyncio.sleep(0.05)  # Stay under Telegram flood limits
            except Exception:
                failed += 1

        await notice.edit_text(
            f"📡 *Broadcast complete!*\n\n"
            f"✅ Delivered: {sent:,}\n"
            f"❌ Failed: {failed:,}",
            parse_mode="Markdown",
        )
