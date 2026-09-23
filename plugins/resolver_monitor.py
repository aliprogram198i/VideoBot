"""Small helpers for resolver-outcome monitoring used by Smart Operations."""
import html

from telemetry.resolver_outcomes import get_monitor_data


def render_resolver_monitor(get_db, days=1):
    data = get_monitor_data(get_db, days)
    lines = [
        "📡 <b>Platform / Resolver Monitor</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"محاولات: {data['total_attempts']} | نجاح: {data['successful']} | فشل نهائي: {data['terminal_failures']}",
        "",
    ]
    if not data["resolver_outcomes"]:
        lines.append("لا توجد بيانات Resolver بعد.")
    else:
        for row in data["resolver_outcomes"]:
            lines.append(
                f"• <b>{html.escape(row['resolver'])}</b> — "
                f"محاولات: {row['attempts']} | أخطاء: {row['errors']} | "
                f"استعادة: {row['recovered']} | فشل نهائي: {row['terminal_failures']}"
            )
    if data["recovery"]:
        lines.extend(["", "🔁 <b>Recovery paths</b>"])
        for item in data["recovery"][:10]:
            lines.append(
                f"• {html.escape(item['transition'])}: {item['count']}"
            )
    return "\n".join(lines)


def resolver_monitor_data(get_db, days=1):
    return get_monitor_data(get_db, days)
