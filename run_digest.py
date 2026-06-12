"""Daily digest: run the screener + portfolio tracker, email + save the report.

Usage: python run_digest.py
"""

import logging
import sys

from investor import data, emailer, portfolio, report, screener, universe
from investor.config import load_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("digest")


def main() -> int:
    cfg = load_config()

    stocks, etfs = universe.build_universe(cfg)
    tickers = sorted(set(stocks + etfs))
    log.info("screening %d tickers", len(tickers))

    hist = data.download_history(tickers, period="1y")
    closes, volumes = data.closes_and_volumes(hist, tickers)
    scored = screener.run_screen(closes, volumes, cfg)

    top_n = int(cfg["screener"]["top_n"])
    details = {t: data.quote_details(t) for t in scored.head(top_n).index}
    screen_md = report.render_screen(scored, details, top_n)

    try:
        port_md = report.render_portfolio(portfolio.run(cfg), cfg["portfolio"]["benchmark"])
    except Exception as exc:  # noqa: BLE001 - portfolio issues shouldn't kill the digest
        log.exception("portfolio section failed")
        port_md = f"## Portfolio\n\n_Error building portfolio section: {exc}_"

    digest = report.render_digest(screen_md, port_md)
    path = emailer.save_report("latest_digest.md", digest)
    log.info("report saved to %s", path)

    sent = emailer.send_email(
        subject="📈 Daily Investment Digest",
        markdown=digest,
        to_addr=cfg["email"]["to"],
        from_name=cfg["email"]["from_name"],
    )
    if not sent:
        log.info("email not configured; report available in repo only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
