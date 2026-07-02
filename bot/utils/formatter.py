import html
import re


def escape_html(text: str) -> str:
    return html.escape(text)


def clean_ai_response(text: str, max_len: int = 4000) -> str:
    """Trim and cap AI response for Telegram's 4096-char limit."""
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len - 30] + "\n\n_[Response truncated]_"
    return text


def format_analytics_table(rows: list[dict], total: dict) -> str:
    """Format analytics data as a Markdown-safe string."""
    if not rows:
        return "📊 No analytics data yet."

    lines = ["📊 *Analytics Overview*\n"]
    lines.append("*Recent Activity (last 7 days):*")
    for row in rows[:7]:
        date_key = row.get("date_key", "?")
        msgs   = row.get("msgs_received", 0)
        ai     = row.get("ai_responses",  0)
        faq    = row.get("faq_hits",      0)
        voice  = row.get("voice_msgs",    0)
        images = row.get("image_msgs",    0)
        lines.append(
            f"  `{date_key}` — 💬{msgs} 🤖{ai} 📖{faq} 🎤{voice} 🖼️{images}"
        )

    lines.append("\n*All-Time Totals:*")
    lines.append(f"  💬 Messages received: `{total.get('total_msgs', 0):,}`")
    lines.append(f"  🤖 AI responses sent: `{total.get('total_ai', 0):,}`")
    lines.append(f"  📖 FAQ hits: `{total.get('total_faq', 0):,}`")
    lines.append(f"  🎤 Voice messages: `{total.get('total_voice', 0):,}`")
    lines.append(f"  🖼️ Images analyzed: `{total.get('total_images', 0):,}`")
    return "\n".join(lines)
