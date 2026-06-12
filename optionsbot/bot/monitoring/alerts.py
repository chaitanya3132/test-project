"""Real-time alerts via a Slack-compatible JSON webhook (MONITOR_WEBHOOK_URL).

Falls back to logging when no webhook is configured, so the bot never
depends on alerting to function. Alert failures are swallowed (logged) —
monitoring must never take down trading or, worse, the kill switch.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger("alerts")

LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
EMOJI = {"info": "ℹ️", "warning": "⚠️", "error": "🛑", "critical": "🚨", "debug": "·"}


class AlertManager:
    def __init__(self, webhook_url: str | None = None, min_level: str = "info",
                 session: Any = None) -> None:
        self.webhook_url = webhook_url or os.environ.get("MONITOR_WEBHOOK_URL", "")
        self.min_level = LEVELS.get(min_level, 1)
        self._session = session or requests
        self.sent: list[dict[str, str]] = []  # in-memory ring for status/CLI

    def notify(self, event: str, message: str, level: str = "info") -> None:
        record = {"event": event, "message": message, "level": level}
        self.sent.append(record)
        if len(self.sent) > 200:
            del self.sent[: len(self.sent) - 200]

        log_fn = getattr(log, level if level in LEVELS else "info", log.info)
        log_fn("[%s] %s", event, message)

        if not self.webhook_url or LEVELS.get(level, 1) < self.min_level:
            return
        payload = {"text": f"{EMOJI.get(level, '')} *{event}* — {message}"}
        try:
            self._session.post(self.webhook_url, json=payload, timeout=10)
        except Exception as exc:  # noqa: BLE001 - alerting must never crash the bot
            log.warning("webhook alert failed: %s", exc)
