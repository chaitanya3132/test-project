"""Send reports by email (SMTP) and always save a copy under reports/."""

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)

REPORTS_DIR = ROOT / "reports"


def save_report(name: str, markdown: str) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / name
    path.write_text(markdown)
    return path


def markdown_to_basic_html(md: str) -> str:
    """Minimal markdown -> HTML so email clients render tables readably.

    Pre-wrapped monospace keeps the markdown tables aligned without pulling
    in a full converter.
    """
    import html
    return (
        "<html><body><pre style=\"font-family: Menlo, Consolas, monospace; "
        f"font-size: 13px; white-space: pre-wrap;\">{html.escape(md)}</pre></body></html>"
    )


def send_email(subject: str, markdown: str, to_addr: str, from_name: str) -> bool:
    """Send via SMTP using env credentials. Returns False if not configured."""
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not user or not password:
        log.warning("SMTP_USER/SMTP_PASSWORD not set; skipping email send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(markdown, "plain"))
    msg.attach(MIMEText(markdown_to_basic_html(markdown), "html"))

    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
    log.info("emailed %r to %s", subject, to_addr)
    return True
