import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
# AIORateLimiter is imported from telegram.ext if installed via [rate-limiter]
try:
    from telegram.ext import AIORateLimiter
except ImportError:
    AIORateLimiter = None

from config import TELEGRAM_TOKEN, GROQ_API_KEY, DB_PATH, DEFAULT_SETTINGS, FEATURES
from bot.db import DatabaseManager
from bot.ai import GroqClient
from bot.handlers import (
    cmd_start,
    cmd_help,
    cmd_clear,
    cmd_status,
    cmd_admin,
    cmd_cancel,
    handle_text_message,
    callback_router,
)

# ─── Setup Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Suppress noisy httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)


# ─── Error Handler ────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a message to notify the developer."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    # If it's a message update, we can reply
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Sorry, an unexpected error occurred."
            )
        except Exception:
            pass


# ─── Main Startup ─────────────────────────────────────────────────────────────
async def post_init(app: Application) -> None:
    """Run after the app is initialized but before polling starts."""
    logger.info("Initializing database...")
    db = DatabaseManager(DB_PATH)
    await db.init(DEFAULT_SETTINGS, FEATURES)
    
    logger.info("Initializing Groq AI...")
    model_name = await db.get_setting("ai_model", "llama-3.1-8b-instant")
    # Auto-migrate legacy database settings if user previously ran with Gemini or old Llama
    if "gemini" in model_name.lower() or "llama3-8b-8192" in model_name:
        model_name = "llama-3.1-8b-instant"
        await db.set_setting("ai_model", model_name)
        
    gemini = GroqClient(api_key=GROQ_API_KEY, model_name=model_name)

    # Attach to bot_data so handlers can access them
    app.bot_data["db"] = db
    app.bot_data["gemini"] = gemini
    
    # Store dynamic states like admin setup flows
    app.bot_data["admin_states"] = {}

    logger.info("HmassAssistant is ready!")


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error(
            "TELEGRAM_TOKEN is not set! "
            "Set it via: fly secrets set TELEGRAM_TOKEN=\"your_token\" "
            "or add it to your .env file."
        )
        sys.exit(1)
    if not GROQ_API_KEY:
        logger.error(
            "GROQ_API_KEY is not set! "
            "Set it via: fly secrets set GROQ_API_KEY=\"your_key\" "
            "or add it to your .env file."
        )
        sys.exit(1)

    # Build Application
    builder = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init)
    
    if AIORateLimiter:
        builder = builder.rate_limiter(AIORateLimiter())
    else:
        logger.warning(
            "AIORateLimiter not installed. Run: pip install python-telegram-bot[rate-limiter]"
        )

    app = builder.build()

    # ── Register Handlers ──
    # ── Register Handlers ──
    # Commands (Restricted to private chats)
    app.add_handler(CommandHandler("start", cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", cmd_help, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("clear", cmd_clear, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("status", cmd_status, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("admin", cmd_admin, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=filters.ChatType.PRIVATE))

    # Text messages — private chats AND Telegram Business messages
    app.add_handler(MessageHandler(
        (filters.ChatType.PRIVATE | filters.UpdateType.BUSINESS_MESSAGE) 
        & filters.TEXT & ~filters.COMMAND, 
        handle_text_message
    ))

    # Callbacks (Inline Keyboards)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Global Error Handler
    app.add_error_handler(error_handler)

    # Start the Bot (Webhook or Polling)
    from config import WEBHOOK_URL, PORT

    if WEBHOOK_URL:
        # Webhook mode — used in production (Fly.io, Render, VPS, etc.)
        # The bot spins up a real HTTP server that Telegram pushes updates to.
        # This also satisfies Fly.io's port 8080 health check.
        webhook_path = "telegram-webhook"
        full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{webhook_path}"
        logger.info("Starting Webhook mode on port %s → %s", PORT, full_webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=webhook_path,
            webhook_url=full_webhook_url,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        # Polling mode — used locally (no public URL needed)
        logger.info("No WEBHOOK_URL set — starting long polling mode...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
