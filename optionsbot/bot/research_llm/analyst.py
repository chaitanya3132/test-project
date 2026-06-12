"""Pre-market LLM analyst (Anthropic API, claude-fable-5).

Hard boundaries, by design:
  * Output is STRUCTURED JSON ONLY: {regime, flagged_events, confidence}.
  * The output is advisory. Strategies may use it to VETO entries; nothing in
    this module (or its output) can place, size, or modify an order.
  * Anything that *would* increase risk (e.g. "high-confidence bull" implying
    larger size) is appended to state/pending_approvals.jsonl for a human to
    review — never auto-applied.
  * On refusal, API error, or invalid output the bot proceeds with NO view,
    which is the most conservative configuration (no veto data ≠ no trading;
    the deterministic strategy and RiskManager still gate everything).

Claude Fable 5 notes (see Anthropic docs): thinking is always on — do not
send a `thinking` parameter; check stop_reason == "refusal" before reading
content; structured output via output_config.format.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from ..models import NewsItem, ResearchView

log = logging.getLogger(__name__)

VALID_REGIMES = ("bull", "bear", "chop")

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": list(VALID_REGIMES)},
        "flagged_events": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number"},
    },
    "required": ["regime", "flagged_events", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are a pre-market research analyst for a defined-risk options strategy on
US equity indexes. You will receive recent news headlines and summaries.

Classify the likely market regime for today's session and flag events a
trader should know about (earnings, Fed/CPI/FOMC, geopolitical shocks,
halts, anything that materially moves index volatility).

Definitions:
- bull: drift up likely, vol subdued
- bear: meaningful downside risk or risk-off tone
- chop: range-bound / mixed / unclear

confidence is 0 to 1 and should reflect how strongly the evidence supports
the regime call; use low confidence when evidence is thin or conflicting.
Respond with the JSON object only."""


class MorningAnalyst:
    def __init__(self, model: str = "claude-fable-5", state_dir: Path | str = "state",
                 client: anthropic.Anthropic | None = None,
                 max_news_items: int = 40) -> None:
        self.model = model
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.max_news_items = max_news_items
        # Anthropic() reads ANTHROPIC_API_KEY from the environment.
        self._client = client or anthropic.Anthropic()

    # ----- public API -----

    def analyze(self, news: list[NewsItem]) -> ResearchView | None:
        """Run the analysis; returns None (trade with no view) on any failure."""
        prompt = self._build_prompt(news)
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                },
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            log.error("research call failed: %s", exc)
            return None

        if response.stop_reason == "refusal":
            log.warning("research request was refused; proceeding without a view")
            return None

        text = next((b.text for b in response.content if b.type == "text"), "")
        view = self._parse_view(text)
        if view is None:
            return None

        self._persist(view)
        self._log_risk_increasing_implications(view)
        return view

    # ----- internals -----

    def _build_prompt(self, news: list[NewsItem]) -> str:
        lines = [f"Date: {datetime.now(timezone.utc):%Y-%m-%d} (pre-market, UTC)", "",
                 "Recent news:"]
        for n in news[: self.max_news_items]:
            lines.append(f"- [{n.ts:%m-%d %H:%M}] ({', '.join(n.symbols) or 'macro'}) "
                         f"{n.headline}")
            if n.summary:
                lines.append(f"  {n.summary[:300]}")
        if len(lines) == 3:
            lines.append("- (no news available)")
        return "\n".join(lines)

    def _parse_view(self, text: str) -> ResearchView | None:
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            log.error("research output was not valid JSON: %.200s", text)
            return None
        regime = d.get("regime")
        if regime not in VALID_REGIMES:
            log.error("invalid regime %r in research output", regime)
            return None
        try:
            confidence = max(0.0, min(1.0, float(d.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        events = tuple(str(e) for e in d.get("flagged_events", []))[:20]
        return ResearchView(
            regime=regime, flagged_events=events, confidence=confidence,
            generated_at=datetime.now(timezone.utc),
        )

    def _persist(self, view: ResearchView) -> None:
        out = asdict(view)
        out["generated_at"] = view.generated_at.isoformat()
        (self.state_dir / "research.json").write_text(json.dumps(out, indent=2))

    def _log_risk_increasing_implications(self, view: ResearchView) -> None:
        """A strongly bullish view might tempt someone to size up. We never do
        that automatically — we log it for a human instead."""
        if view.regime == "bull" and view.confidence >= 0.8:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "risk_increase_suggestion",
                "detail": (f"research is bull with confidence {view.confidence:.2f}; "
                           "any size/limit increase requires human approval"),
                "status": "pending_human_approval",
            }
            with open(self.state_dir / "pending_approvals.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")
            log.info("logged risk-increase suggestion for human approval (not executed)")


def load_research_view(state_dir: Path | str, max_age_hours: float = 20.0) -> ResearchView | None:
    """Load today's persisted view for the strategy layer; None if missing/stale."""
    path = Path(state_dir) / "research.json"
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
        generated_at = datetime.fromisoformat(d["generated_at"])
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None
    age_h = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
    if age_h > max_age_hours or d.get("regime") not in VALID_REGIMES:
        return None
    return ResearchView(
        regime=d["regime"],
        flagged_events=tuple(d.get("flagged_events", [])),
        confidence=float(d.get("confidence", 0.0)),
        generated_at=generated_at,
    )
