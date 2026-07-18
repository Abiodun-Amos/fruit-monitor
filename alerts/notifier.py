"""
alerts/notifier.py
Outbound alert dispatcher — Telegram and Gmail SMTP.

Both channels are optional. If credentials are missing for a channel
it is silently skipped; the other channel still fires.

Usage:
    notifier = AlertNotifier(settings)
    await notifier.send(message, alert_level, fruit_name, days_remaining)
"""
import asyncio
import logging
import smtplib
import ssl
import httpx
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Alert level → colour used in the HTML email
_LEVEL_COLOUR = {
    "warning":  "#f59e0b",   # amber
    "critical": "#ef4444",   # red
    "spoiled":  "#7c3aed",   # purple
}
_LEVEL_ICON = {
    "warning":  "⚠️",
    "critical": "🚨",
    "spoiled":  "❌",
}


class AlertNotifier:

    def __init__(self, settings):
        self._s            = settings
        self._telegram_bot = None
        self._init_telegram()

    # ── Setup ─────────────────────────────────────────────────

    def _init_telegram(self):
        if self._s.TELEGRAM_BOT_TOKEN and self._s.TELEGRAM_CHAT_ID:
            try:
                from telegram import Bot
                self._telegram_bot = Bot(token=self._s.TELEGRAM_BOT_TOKEN)
                logger.info("Telegram alerts enabled")
            except Exception as exc:
                logger.warning("Telegram init failed (will skip): %s", exc)

    # ── Public entry point ────────────────────────────────────

    async def send(
        self,
        message:        str,
        alert_level:    str,
        fruit_name:     str   = "",
        days_remaining: float = 0.0,
    ):
        """
        Fire alerts on all configured channels simultaneously.
        Exceptions in one channel do not affect the other.
        """
        await asyncio.gather(
            self._telegram(message, alert_level),
            self._email(message, alert_level, fruit_name, days_remaining),
            return_exceptions=True,
        )

    # ── Telegram ──────────────────────────────────────────────

    async def _telegram(self, message: str, alert_level: str):
        if not self._telegram_bot:
            return
        chat_ids = [c.strip() for c in self._s.TELEGRAM_CHAT_IDS.split(",") if c.strip()]
        if not chat_ids:
            return
        icon = _LEVEL_ICON.get(alert_level, "⚠️")
        text = f"{icon} *Fruit Monitor Alert*\n━━━━━━━━━━━━━━━━━━━━\n{message}"
        for chat_id in chat_ids:
            try:
                await self._telegram_bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
                logger.info("Telegram alert sent to %s (%s)", chat_id, alert_level)
            except Exception as exc:
                logger.error("Telegram send failed for %s: %s", chat_id, exc)

    # ── Email ─────────────────────────────────────────────────

    async def _email(
        self,
        message:        str,
        alert_level:    str,
        fruit_name:     str,
        days_remaining: float,
    ):
        if not (self._s.RESEND_API_KEY and self._s.EMAIL_TO):
            return

        icon    = _LEVEL_ICON.get(alert_level, "⚠️")
        colour  = _LEVEL_COLOUR.get(alert_level, "#f59e0b")
        subject = (
            f"{icon} {fruit_name} Alert — {alert_level.capitalize()}"
            if fruit_name else
            f"{icon} Fruit Monitor Alert — {alert_level.capitalize()}"
        )
        days_line = (
            f"<p style='margin:8px 0;color:#374151;'>"
            f"⏱ Days remaining: <strong>{days_remaining:.1f} days</strong></p>"
            if days_remaining > 0 else ""
        )
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;">
          <div style="background:{colour};padding:20px 24px;border-radius:8px 8px 0 0;">
            <h2 style="color:#fff;margin:0;">{icon} Fruit Monitor Alert</h2>
            <p style="color:#fff;opacity:.9;margin:4px 0 0;">{alert_level.upper()}</p>
          </div>
          <div style="background:#f9fafb;padding:20px 24px;border:1px solid #e5e7eb;
                      border-top:none;border-radius:0 0 8px 8px;">
            <p style="font-size:15px;color:#111827;margin:0 0 12px;">{message}</p>
            {days_line}
          </div>
        </body></html>
        """

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {self._s.RESEND_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": "Fruit Monitor <onboarding@resend.dev>",
                        "to": [self._s.EMAIL_TO],
                        "subject": subject,
                        "html": html,
                        "text": message,
                    },
                )
                resp.raise_for_status()
            logger.info("Email alert sent → %s (%s)", self._s.EMAIL_TO, alert_level)
        except Exception as exc:
            logger.error("Email send failed: %s", exc)