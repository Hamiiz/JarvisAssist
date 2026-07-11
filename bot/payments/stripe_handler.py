import logging
import stripe
from aiohttp import web

from config import STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, PLANS

logger = logging.getLogger(__name__)

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


async def handle_stripe_webhook(request: web.Request) -> web.Response:
    """aiohttp handler for POST /stripe/webhook."""
    payload = await request.read()
    sig_header = request.headers.get("Stripe-Signature", "")

    # Verify webhook signature
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.errors.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        return web.Response(status=400, text="Invalid signature")
    except Exception as e:
        logger.error("Stripe webhook error: %s", e)
        return web.Response(status=400, text=str(e))

    tenant_mgr = request.app["tenant_mgr"]
    bot = request.app["bot"]

    event_type = event["type"]
    data = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            await _on_checkout_completed(data, tenant_mgr, bot)

        elif event_type == "invoice.paid":
            await _on_invoice_paid(data, tenant_mgr)

        elif event_type == "customer.subscription.deleted":
            await _on_subscription_deleted(data, tenant_mgr, bot)

        elif event_type == "customer.subscription.updated":
            await _on_subscription_updated(data, tenant_mgr, bot)

    except Exception as e:
        logger.error("Error handling Stripe event %s: %s", event_type, e)

    return web.Response(status=200, text="OK")


async def _on_checkout_completed(data: dict, tenant_mgr, bot):
    """Activate tenant after successful Stripe checkout."""
    tenant_id = data.get("client_reference_id")  # Set in Payment Link metadata
    customer_id = data.get("customer")
    sub_id = data.get("subscription")

    if not tenant_id:
        logger.warning("Stripe checkout.session.completed missing client_reference_id")
        return

    # Determine plan from subscription price
    plan = await _get_plan_from_subscription(sub_id)
    await tenant_mgr.activate(
        tenant_id=tenant_id,
        plan=plan,
        stripe_customer_id=customer_id,
        stripe_sub_id=sub_id,
    )

    # Notify the tenant owner
    tenant = await tenant_mgr.get_tenant(tenant_id)
    if tenant:
        plan_info = PLANS.get(plan, {})
        cap = plan_info.get("cap", 0)
        cap_str = "Unlimited" if cap == -1 else f"{cap:,}"
        try:
            await bot.send_message(
                chat_id=tenant["owner_tg_id"],
                text=(
                    f"🎉 *Payment confirmed!* You're now on the *{plan_info.get('label', plan)} Plan*.\n\n"
                    f"✅ {cap_str} AI replies/month activated.\n"
                    f"Use /setup to configure your assistant."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Failed to notify tenant owner %s: %s", tenant["owner_tg_id"], e)

    logger.info("Stripe checkout completed: tenant=%s plan=%s", tenant_id, plan)


async def _on_invoice_paid(data: dict, tenant_mgr):
    """Reset monthly usage on successful renewal."""
    customer_id = data.get("customer")
    if not customer_id:
        return

    # Find tenant by Stripe customer ID
    tenants = await tenant_mgr.get_all_tenants()
    for t in tenants:
        if t.get("stripe_customer_id") == customer_id:
            await tenant_mgr.reset_monthly_usage(t["tenant_id"])
            logger.info("Monthly usage reset for tenant %s", t["tenant_id"])
            break


async def _on_subscription_deleted(data: dict, tenant_mgr, bot):
    """Suspend tenant when subscription is cancelled."""
    customer_id = data.get("customer")
    if not customer_id:
        return

    tenants = await tenant_mgr.get_all_tenants()
    for t in tenants:
        if t.get("stripe_customer_id") == customer_id:
            await tenant_mgr.suspend(t["tenant_id"])
            try:
                await bot.send_message(
                    chat_id=t["owner_tg_id"],
                    text=(
                        "⚠️ *Your JarvisAssist subscription has ended.*\n\n"
                        "Your bot is now paused. Use /subscribe to reactivate "
                        "and restore your assistant."
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error("Failed to notify tenant %s of suspension: %s", t["tenant_id"], e)
            break


async def _on_subscription_updated(data: dict, tenant_mgr, bot):
    """Handle plan upgrades/downgrades."""
    customer_id = data.get("customer")
    sub_id = data.get("id")
    if not customer_id:
        return

    plan = await _get_plan_from_subscription(sub_id)
    tenants = await tenant_mgr.get_all_tenants()
    for t in tenants:
        if t.get("stripe_customer_id") == customer_id:
            await tenant_mgr.activate(t["tenant_id"], plan, stripe_sub_id=sub_id)
            logger.info("Plan updated for tenant %s → %s", t["tenant_id"], plan)
            break


async def _get_plan_from_subscription(sub_id: str) -> str:
    """Look up a Stripe subscription and map the price to a plan name."""
    if not sub_id or not STRIPE_SECRET_KEY:
        return "starter"
    try:
        sub = stripe.Subscription.retrieve(sub_id)
        amount = sub["items"]["data"][0]["price"]["unit_amount"]  # cents
        if amount >= 4000:
            return "business"
        elif amount >= 1500:
            return "pro"
        else:
            return "starter"
    except Exception as e:
        logger.error("Could not retrieve Stripe subscription %s: %s", sub_id, e)
        return "starter"
