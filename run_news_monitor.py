"""News monitor: scan the watchlist, email only when there are new alerts.

Usage: python run_news_monitor.py
"""

import logging
import sys

from investor import emailer, news, report
from investor.config import load_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("news-monitor")


def main() -> int:
    cfg = load_config()
    alerts = news.scan_watchlist(cfg)
    if not alerts:
        log.info("no new alerts")
        return 0

    log.info("%d new alert(s)", len(alerts))
    md = report.render_alerts(alerts)
    emailer.save_report("latest_alerts.md", md)
    emailer.send_email(
        subject=f"🚨 {len(alerts)} stock news alert(s)",
        markdown=md,
        to_addr=cfg["email"]["to"],
        from_name=cfg["email"]["from_name"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
