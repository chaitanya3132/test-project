import json
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from bot.models import NewsItem
from bot.research_llm import MorningAnalyst, load_research_view


def fake_response(text, stop_reason="end_turn"):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(stop_reason=stop_reason, content=[block])


class FakeClient:
    def __init__(self, response=None, exc=None):
        self.calls = []
        self._response = response
        self._exc = exc
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer._exc:
                    raise outer._exc
                return outer._response

        self.messages = Messages()


def news():
    return [NewsItem(id="1", headline="Fed holds rates", summary="dovish tone",
                     symbols=("SPY",), source="x",
                     ts=datetime.now(timezone.utc))]


def test_valid_structured_output_is_parsed_and_persisted(tmp_path):
    payload = {"regime": "bull", "flagged_events": ["FOMC minutes 2pm"],
               "confidence": 0.7}
    client = FakeClient(response=fake_response(json.dumps(payload)))
    analyst = MorningAnalyst(state_dir=tmp_path, client=client)
    view = analyst.analyze(news())
    assert view is not None
    assert view.regime == "bull" and view.confidence == 0.7
    assert view.flagged_events == ("FOMC minutes 2pm",)

    # request shape: claude-fable-5, no `thinking` param, structured output schema
    call = client.calls[0]
    assert call["model"] == "claude-fable-5"
    assert "thinking" not in call
    assert call["output_config"]["format"]["type"] == "json_schema"

    # persisted view loads back for the strategy layer
    loaded = load_research_view(tmp_path)
    assert loaded is not None and loaded.regime == "bull"


def test_refusal_returns_no_view(tmp_path):
    client = FakeClient(response=fake_response("", stop_reason="refusal"))
    view = MorningAnalyst(state_dir=tmp_path, client=client).analyze(news())
    assert view is None
    assert load_research_view(tmp_path) is None


def test_api_error_returns_no_view(tmp_path):
    exc = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api"))
    client = FakeClient(exc=exc)
    assert MorningAnalyst(state_dir=tmp_path, client=client).analyze(news()) is None


@pytest.mark.parametrize("bad", [
    "not json at all",
    json.dumps({"regime": "moon", "flagged_events": [], "confidence": 0.9}),
])
def test_invalid_output_is_rejected(tmp_path, bad):
    client = FakeClient(response=fake_response(bad))
    assert MorningAnalyst(state_dir=tmp_path, client=client).analyze(news()) is None


def test_confidence_is_clamped(tmp_path):
    payload = {"regime": "chop", "flagged_events": [], "confidence": 3.5}
    client = FakeClient(response=fake_response(json.dumps(payload)))
    view = MorningAnalyst(state_dir=tmp_path, client=client).analyze(news())
    assert view.confidence == 1.0


def test_risk_increasing_view_is_logged_for_human_approval_not_executed(tmp_path):
    payload = {"regime": "bull", "flagged_events": [], "confidence": 0.95}
    client = FakeClient(response=fake_response(json.dumps(payload)))
    MorningAnalyst(state_dir=tmp_path, client=client).analyze(news())
    log_file = tmp_path / "pending_approvals.jsonl"
    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["status"] == "pending_human_approval"


def test_stale_persisted_view_is_ignored(tmp_path):
    (tmp_path / "research.json").write_text(json.dumps({
        "regime": "bear", "flagged_events": [], "confidence": 0.9,
        "generated_at": "2026-06-10T08:00:00+00:00",
    }))
    assert load_research_view(tmp_path, max_age_hours=20) is None
