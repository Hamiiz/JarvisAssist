import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
ADMIN_IDS_RAW: str  = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [
    int(uid.strip())
    for uid in ADMIN_IDS_RAW.split(",")
    if uid.strip().isdigit()
]

# ─── Webhook ──────────────────────────────────────────────────────────────────
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
PORT: int = int(os.getenv("PORT", "8080"))

# ─── Groq AI ──────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# ─── Database (Postgres) ──────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# ─── Stripe ───────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY: str    = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Stripe Payment Links — create these once in the Stripe Dashboard
# Each link should pass client_reference_id = tenant_id (business_connection_id)
STRIPE_LINKS: dict[str, str] = {
    "starter":  os.getenv("STRIPE_LINK_STARTER",  ""),
    "pro":      os.getenv("STRIPE_LINK_PRO",       ""),
    "business": os.getenv("STRIPE_LINK_BUSINESS",  ""),
}

# ─── Subscription Plans ───────────────────────────────────────────────────────
PLANS: dict = {
    "trial": {
        "label":      "Free Trial",
        "cap":        100,
        "stars":      0,
        "usd_cents":  0,
        "duration_days": 14,
    },
    "starter": {
        "label":      "Starter",
        "cap":        500,
        "stars":      500,
        "usd_cents":  500,   # $5.00
    },
    "pro": {
        "label":      "Pro",
        "cap":        2000,
        "stars":      1500,
        "usd_cents":  1500,  # $15.00
    },
    "business": {
        "label":      "Business",
        "cap":        -1,    # Unlimited (-1 = no cap)
        "stars":      4000,
        "usd_cents":  4000,  # $40.00
    },
    "owner": {
        "label":      "Owner",
        "cap":        -1,
        "stars":      0,
        "usd_cents":  0,
    },
}

# ─── Feature Definitions ──────────────────────────────────────────────────────
FEATURES: dict = {
    "away_mode":   {"label": "Away Mode",           "emoji": "🌙", "default": True},
    "typing_sim":  {"label": "Typing Simulation",   "emoji": "⌨️", "default": True},
    "voice_msgs":  {"label": "Voice Messages",      "emoji": "🎤", "default": True},
    "image_msgs":  {"label": "Image Analysis",      "emoji": "🖼️", "default": True},
    "anti_spam":   {"label": "Anti-Spam",           "emoji": "🛡️", "default": True},
    "welcome_msg": {"label": "Welcome Message",     "emoji": "👋", "default": True},
    "faq_engine":  {"label": "FAQ Engine",          "emoji": "📖", "default": True},
    "analytics":   {"label": "Analytics Tracking",  "emoji": "📊", "default": True},
    "history":     {"label": "Conversation Memory", "emoji": "🧠", "default": True},
    "schedule":    {"label": "Active Schedule",     "emoji": "🕐", "default": False},
    "mood_detect": {"label": "Mood Detection",      "emoji": "🎭", "default": True},
}

# ─── Personality Presets ──────────────────────────────────────────────────────
PERSONALITY_PRESETS: dict = {
    "friendly": {
        "name": "Friendly & Warm",
        "emoji": "😊",
        "instruction": (
            "Be warm, friendly, and approachable. Use casual but clear language. "
            "Occasionally use appropriate emojis to add warmth. "
            "Make every user feel welcome, heard, and valued."
        ),
    },
    "professional": {
        "name": "Professional",
        "emoji": "💼",
        "instruction": (
            "Be professional, formal, and precise. Use proper grammar and "
            "well-structured responses. Maintain a business-like tone at all times. "
            "Avoid slang, contractions where possible, and emojis."
        ),
    },
    "concise": {
        "name": "Concise & Direct",
        "emoji": "⚡",
        "instruction": (
            "Be extremely concise and direct. Give short, clear, actionable answers. "
            "Remove all unnecessary words. If it can be said in 5 words, never use 10. "
            "Bullet points are your best friend."
        ),
    },
    "witty": {
        "name": "Witty & Playful",
        "emoji": "😄",
        "instruction": (
            "Be witty, clever, and playful. Use light humor where appropriate. "
            "Make conversations engaging and fun. Be creative and interesting. "
            "Keep it tasteful — never offensive."
        ),
    },
    "empathetic": {
        "name": "Empathetic",
        "emoji": "💙",
        "instruction": (
            "Be deeply empathetic and emotionally aware. Always acknowledge how "
            "the user feels before offering solutions. Be patient, supportive, and "
            "understanding. Use gentle, validating language."
        ),
    },
    "custom": {
        "name": "Custom",
        "emoji": "✏️",
        "instruction": "",  # Filled from DB custom_prompt setting
    },
}

# ─── Response Length Instructions ─────────────────────────────────────────────
RESPONSE_LENGTH_INSTRUCTIONS: dict[str, str] = {
    "brief":    "Keep your responses very short — 1 to 3 sentences maximum. Be punchy.",
    "normal":   "Keep responses clear and well-balanced. Not too short, not too long.",
    "detailed": "Provide thorough, detailed responses with context, examples, and explanations.",
}

# ─── Default Bot Settings ─────────────────────────────────────────────────────
DEFAULT_SETTINGS: dict[str, str] = {
    "bot_name":           "HM Jarvis",
    "owner_name":         "the owner",
    "personality":        "friendly",
    "custom_prompt":      "",
    "history_limit":      "20",
    "typing_delay_min":   "1.0",
    "typing_delay_max":   "3.0",
    "anti_spam_cooldown": "3",
    "response_length":    "normal",
    "ai_temperature":     "0.75",
    "ai_model":           "llama-3.1-8b-instant",
    "reply_delay":        "10",
    "rate_limit_msg":     "⚠️ I'm currently overwhelmed with messages. Please try again later!",
    "welcome_message":    "Hi {name}! 👋 I'm {bot_name}, an AI assistant. How can I help you today?",
    "schedule_start":     "08:00",
    "schedule_end":       "22:00",
    "schedule_timezone":  "Africa/Addis_Ababa",
}
