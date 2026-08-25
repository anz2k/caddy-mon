"""Incident alerting via Telegram and generic Webhooks."""

import time
import httpx
from typing import List, Dict, Any, Optional

from .config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    WEBHOOK_URL,
    ALERT_COOLDOWN_MINUTES,
)
from .db import get_connection, record_incident


async def send_telegram(text: str):
    """Send a Markdown-formatted message to Telegram."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
    except Exception:
        pass


async def send_webhook(title: str, text: str, is_error: bool = False):
    """Send alert JSON to a generic Webhook (Discord / Slack / custom)."""
    if not WEBHOOK_URL:
        return
    # Discord / Slack compatible payload
    color = 0xDC2626 if is_error else 0x16A34A
    payload = {
        "content": f"*{title}*\n{text}",
        "embeds": [
            {
                "title": title,
                "description": text,
                "color": color,
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(WEBHOOK_URL, json=payload)
    except Exception:
        pass


async def dispatch_alert(title: str, message: str, is_error: bool = False):
    """Broadcast alert to all configured notification channels."""
    tg_text = f"*{title}*\n{message}"
    await send_telegram(tg_text)
    await send_webhook(title, message, is_error=is_error)


async def process_site_alerts(sites: List[Dict[str, Any]], now: Optional[float] = None):
    """Check for state transitions (ALIVE <-> DEAD) and dispatch alerts."""
    if not (TELEGRAM_BOT_TOKEN or WEBHOOK_URL):
        return

    ts = now or time.time()
    cooldown_secs = ALERT_COOLDOWN_MINUTES * 60.0

    try:
        with get_connection() as conn:
            cur = conn.execute("SELECT host, last_state, last_alert_ts FROM alert_state")
            state_map = {
                r["host"]: {"last_state": r["last_state"], "last_alert_ts": r["last_alert_ts"]}
                for r in cur.fetchall()
            }
    except Exception:
        state_map = {}

    for s in sites:
        primary = s.get("primary_host") or (s.get("hosts") or [""])[0]
        if not primary:
            continue

        is_alive = 1 if s.get("alive") else 0
        prev = state_map.get(primary)

        # Initial state registration
        if prev is None:
            _save_alert_state(primary, is_alive, 0.0)
            continue

        prev_state = prev["last_state"]
        last_alert = prev["last_alert_ts"]

        # Transition: ALIVE -> DEAD
        if prev_state == 1 and is_alive == 0:
            from .db import get_maintenance_status
            maint = get_maintenance_status(primary)
            if maint and maint.get("enabled"):
                # Site is under planned maintenance; suppress alert
                _save_alert_state(primary, 0, ts)
                record_incident(primary, "MAINTENANCE", f"Down during planned maintenance: {maint.get('reason')}", ts=ts)
                continue

            if (ts - last_alert) >= cooldown_secs or last_alert == 0.0:
                err_details = []
                for u in s.get("upstreams", []):
                    if not u.get("probe_ok") or u.get("caddy_healthy") is False:
                        err_details.append(f"`{u.get('upstream')}`: {u.get('error') or 'unhealthy'}")
                detail_str = "\n".join(err_details) if err_details else "Probe failed or Caddy reported unhealthy"
                title = f"🔴 Caddy-Mon Alert: {primary} is DOWN"
                body = f"Site *{primary}* is unreachable.\nUpstreams:\n{detail_str}"

                record_incident(primary, "DOWN", detail_str, ts=ts)
                _save_alert_state(primary, 0, ts)
                await dispatch_alert(title, body, is_error=True)

        # Transition: DEAD -> ALIVE
        elif prev_state == 0 and is_alive == 1:
            latency = s.get("latency_ms", 0.0)
            title = f"🟢 Caddy-Mon Alert: {primary} RECOVERED"
            body = f"Site *{primary}* is back online (latency: {latency}ms)."

            record_incident(primary, "RECOVERED", f"Latency: {latency}ms", ts=ts)
            _save_alert_state(primary, 1, ts)
            await dispatch_alert(title, body, is_error=False)


def _save_alert_state(host: str, state: int, alert_ts: float):
    """Update or insert alert state for a host."""
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO alert_state (host, last_state, last_alert_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(host) DO UPDATE SET
                    last_state = excluded.last_state,
                    last_alert_ts = excluded.last_alert_ts
                """,
                (host, state, alert_ts),
            )
            conn.commit()
    except Exception:
        pass
