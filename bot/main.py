import asyncio
import logging
import os
import sys
from aiohttp import web

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    TypeHandler,
    filters,
    ContextTypes,
)

# AIORateLimiter is imported from telegram.ext if installed via [rate-limiter]
try:
    from telegram.ext import AIORateLimiter
except ImportError:
    AIORateLimiter = None

from config import TELEGRAM_TOKEN, GROQ_API_KEY, DEFAULT_SETTINGS, FEATURES, PORT, WEBHOOK_URL
from bot.db import DatabaseManager, TenantManager
from bot.ai import GroqClient
from bot.handlers import (
    cmd_start,
    cmd_help,
    cmd_clear,
    cmd_status,
    cmd_admin,
    cmd_cancel,
    cmd_setup,
    cmd_subscribe,
    handle_subscribe_callbacks,
    handle_text_message,
    handle_business_connection,
    setup_callback_router,
    platform_callback_router,
)
from bot.payments.stars import handle_pre_checkout, handle_successful_payment
from bot.payments.stripe_handler import handle_stripe_webhook

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
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Sorry, an unexpected error occurred."
            )
        except Exception:
            pass


# ─── Webhook HTTP Server Handlers ─────────────────────────────────────────────
async def telegram_webhook_handler(request: web.Request) -> web.Response:
    """Handle incoming updates pushed from Telegram webhook."""
    app = request.app["telegram_app"]
    try:
        data = await request.json()
        update = Update.de_json(data, app.bot)
        # Process the update directly since we are using a custom aiohttp server
        # (avoiding queue-based routing which is only active during polling/run_webhook)
        await app.process_update(update)
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error("Error processing telegram update: %s", e)
        return web.Response(status=500, text="Internal error")


async def health_check_handler(request: web.Request) -> web.Response:
    """Returns 200 OK to satisfy Fly.io HTTP health checks."""
    return web.Response(status=200, text="OK")


# ─── Daily Stars Renewal Task ──────────────────────────────────────────────────
async def run_stars_renewal_check(app: Application):
    """Background task to check for Telegram Stars subscriptions due for renewal."""
    from datetime import date
    from bot.payments.stars import send_renewal_invoice
    
    tenant_mgr = app.bot_data["tenant_mgr"]
    while True:
        try:
            logger.info("Running daily Stars subscription renewal checks...")
            tenants = await tenant_mgr.get_all_tenants()
            today = date.today()
            
            for t in tenants:
                if t.get("status") == "active" and t.get("stars_plan") and t.get("stars_renewal_date"):
                    renewal_date = t["stars_renewal_date"]
                    # If renewal is due or past due
                    if today >= renewal_date:
                        logger.info("Renewing Stars subscription for tenant: %s", t["tenant_id"])
                        await send_renewal_invoice(app.bot, t)
                        # We suspend them temporarily until they pay the new invoice.
                        # Wait, we can give a 3-day grace period. For simplicity, we send invoice and
                        # set next renewal/grace date. If they fail to pay after 3 days, they suspend.
                        # For this version, let's keep it simple: suspend them, send invoice, they pay to reactivate.
                        await tenant_mgr.suspend(t["tenant_id"])
                        
            # Run once every 24 hours
            await asyncio.sleep(86400)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in Stars renewal task: %s", e)
            await asyncio.sleep(3600)  # Retry in an hour on failure


# ─── Post Initialize ─────────────────────────────────────────────────────────
async def post_init(app: Application) -> None:
    """Run after the app is initialized but before webhook/polling starts."""
    logger.info("Initializing database...")
    db = DatabaseManager()
    await db.init(DEFAULT_SETTINGS, FEATURES)
    
    tenant_mgr = TenantManager(db)
    
    logger.info("Initializing Groq AI...")
    # Seed platform owner's tenant space at startup
    from config import ADMIN_IDS
    if ADMIN_IDS:
        await tenant_mgr.ensure_owner_tenant(ADMIN_IDS[0])
        owner_tenant_id = f"__owner_{ADMIN_IDS[0]}__"
        model_name = await db.get_setting(owner_tenant_id, "ai_model", "llama-3.1-8b-instant")
    else:
        model_name = "llama-3.1-8b-instant"

    gemini = GroqClient(api_key=GROQ_API_KEY, model_name=model_name)

    # Attach to bot_data so handlers can access them
    app.bot_data["db"] = db
    app.bot_data["tenant_mgr"] = tenant_mgr
    app.bot_data["gemini"] = gemini
    
    # Store dynamic states like admin setup flows
    app.bot_data["admin_states"] = {}

    # Start the background renewal check task
    asyncio.create_task(run_stars_renewal_check(app))
    
    logger.info("JarvisAssist Platform is ready!")


# ─── Business Connection Update Router ────────────────────────────────────────
async def handle_update_generic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercept all updates to check for business_connection events."""
    if update.business_connection:
        await handle_business_connection(update, context)


# ─── Main Program ─────────────────────────────────────────────────────────────
async def main_async():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set!")
        sys.exit(1)
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY is not set!")
        sys.exit(1)

    # Build Application
    builder = ApplicationBuilder().token(TELEGRAM_TOKEN)
    if AIORateLimiter:
        builder = builder.rate_limiter(AIORateLimiter())
    app = builder.build()
    
    # Manually run initialization of database & resources
    await post_init(app)
    await app.initialize()
    await app.start()

    # ── Register Handlers ──
    # Commands
    app.add_handler(CommandHandler("start", cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", cmd_help, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("clear", cmd_clear, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("status", cmd_status, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("admin", cmd_admin, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("setup", cmd_setup, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=filters.ChatType.PRIVATE))

    # Business connection lifecycle updates (intercepted globally).
    #
    # TypeHandler(Update, ...) matches every update, including callback
    # queries. In the default group it was selected before the callback
    # handlers below, swallowing button presses and leaving Telegram's
    # loading indicator active. Use a separate group so both can run.
    app.add_handler(TypeHandler(Update, handle_update_generic), group=-1)

    # Stars In-App Payments
    app.add_handler(PreCheckoutQueryHandler(handle_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))

    # Inline Keyboard Callbacks
    app.add_handler(CallbackQueryHandler(setup_callback_router, pattern="^setup_"))
    app.add_handler(CallbackQueryHandler(platform_callback_router, pattern="^platform_"))
    app.add_handler(CallbackQueryHandler(handle_subscribe_callbacks, pattern="^sub_"))

    # Chat Messages (DMs to bot + Telegram Business messages)
    app.add_handler(MessageHandler(
        (filters.ChatType.PRIVATE | filters.UpdateType.BUSINESS_MESSAGE) 
        & filters.TEXT & ~filters.COMMAND, 
        handle_text_message
    ))

    app.add_error_handler(error_handler)

    # Create aiohttp web server for Telegram Webhook, Stripe Webhook, and Fly Health Checks
    web_app = web.Application()
    web_app["telegram_app"] = app
    web_app["tenant_mgr"] = app.bot_data["tenant_mgr"]
    web_app["bot"] = app.bot

    web_app.router.add_post("/telegram-webhook", telegram_webhook_handler)
    web_app.router.add_post("/stripe/webhook", handle_stripe_webhook)
    web_app.router.add_get("/", health_check_handler)
    web_app.router.add_get("/healthz", health_check_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Web server started on port %s", PORT)

    if WEBHOOK_URL:
        full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}/telegram-webhook"
        logger.info("Setting Telegram Webhook to: %s", full_webhook_url)
        await app.bot.set_webhook(url=full_webhook_url, allowed_updates=Update.ALL_TYPES)
    else:
        logger.info("No WEBHOOK_URL set — running updater polling...")
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # Keep program running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Shutdown signal received")
    finally:
        logger.info("Shutting down bot application...")
        if WEBHOOK_URL:
            await app.bot.delete_webhook()
        else:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await runner.cleanup()
        await app.bot_data["db"].close()
        logger.info("Shutdown complete.")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
