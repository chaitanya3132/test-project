"""News & sentiment monitor for the watchlist.

Each headline gets a VADER sentiment score plus a materiality check against
keywords that usually move a stock (earnings, guidance, downgrades, M&A,
FDA decisions, lawsuits, ...). An item becomes an alert when it is material
or strongly positive/negative, and it hasn't been alerted before
(state/seen_news.json tracks history).
"""

import json
import re
from pathlib import Path

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .config import ROOT
from . import data

STATE_FILE = ROOT / "state" / "seen_news.json"
MAX_SEEN_IDS = 5000

MATERIAL_PATTERNS = [
    r"\bearnings\b", r"\bguidance\b", r"\bforecast\b", r"\boutlook\b",
    r"\bupgrade[ds]?\b", r"\bdowngrade[ds]?\b", r"\bprice target\b",
    r"\bmerger\b", r"\bacqui(re|sition)", r"\bbuyout\b", r"\btakeover\b",
    r"\bSEC\b", r"\bDOJ\b", r"\bFTC\b", r"\blawsuit\b", r"\bprobe\b", r"\binvestigation\b",
    r"\bFDA\b", r"\brecall\b", r"\bbankrupt", r"\bdefault\b",
    r"\bdividend\b", r"\bsplit\b", r"\bbuyback\b",
    r"\bCEO\b", r"\bCFO\b", r"\bresign", r"\bsteps down\b",
    r"\blayoffs?\b", r"\bmisses\b", r"\bbeats\b", r"\bplunge", r"\bsurge", r"\bsoar",
]
_MATERIAL_RE = re.compile("|".join(MATERIAL_PATTERNS), re.IGNORECASE)

_analyzer = SentimentIntensityAnalyzer()


def sentiment(text: str) -> float:
    """VADER compound score in [-1, 1]."""
    return _analyzer.polarity_scores(text)["compound"]


def is_material(title: str) -> bool:
    return bool(_MATERIAL_RE.search(title))


def classify(title: str, threshold: float) -> dict | None:
    """Return alert details for a headline, or None if not alert-worthy."""
    s = sentiment(title)
    material = is_material(title)
    if not material and abs(s) < threshold:
        return None
    if s >= 0.2:
        tone = "positive"
    elif s <= -0.2:
        tone = "negative"
    else:
        tone = "neutral"
    return {"sentiment": s, "tone": tone, "material": material}


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # keep the file bounded; ordering doesn't matter for dedupe
    STATE_FILE.write_text(json.dumps(sorted(seen)[-MAX_SEEN_IDS:], indent=0))


def scan_watchlist(cfg: dict, state_path: Path | None = None) -> list[dict]:
    """Fetch news for every watchlist ticker and return new alerts."""
    threshold = float(cfg["news"]["sentiment_alert_threshold"])
    seen = load_seen()
    alerts = []
    for ticker in cfg["news"]["watchlist"]:
        for item in data.fetch_news(str(ticker)):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            verdict = classify(item["title"], threshold)
            if verdict:
                alerts.append({"ticker": ticker, **item, **verdict})
    save_seen(seen)
    return alerts
