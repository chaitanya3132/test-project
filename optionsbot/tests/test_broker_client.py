import json

import pytest

from bot.broker_client import AlpacaClient, BrokerError
from bot.broker_client.alpaca import LIVE_TRADING_URL, PAPER_TRADING_URL
from bot.models import OptionLeg


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    """Scripted HTTP session: pops responses in order, records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def make_client(responses, paper=True):
    session = FakeSession(responses)
    client = AlpacaClient(paper=paper, session=session, sleep=lambda s: None)
    return client, session


def test_paper_flag_selects_endpoint_and_sends_auth_headers():
    client, session = make_client([FakeResponse(payload={
        "equity": "100000", "cash": "50000", "buying_power": "200000"})])
    account = client.get_account()
    req = session.requests[0]
    assert req["url"].startswith(PAPER_TRADING_URL)
    assert req["headers"]["APCA-API-KEY-ID"] == "test-key"
    assert account.equity == 100000.0

    client, session = make_client([FakeResponse(payload={
        "equity": "1", "cash": "1", "buying_power": "1"})], paper=False)
    client.get_account()
    assert session.requests[0]["url"].startswith(LIVE_TRADING_URL)


def test_mleg_order_payload_shape():
    client, session = make_client([FakeResponse(payload={"id": "o1", "status": "accepted"})])
    legs = (
        OptionLeg(symbol="SPY260717P00540000", side="sell"),
        OptionLeg(symbol="SPY260717P00535000", side="buy"),
    )
    resp = client.submit_mleg_order(legs, qty=2, limit_price=-1.25)
    assert resp["id"] == "o1"
    body = session.requests[0]["json"]
    assert body["order_class"] == "mleg"
    assert body["qty"] == "2"
    assert body["limit_price"] == "-1.25"
    assert body["legs"][0]["side"] == "sell"
    assert body["legs"][0]["position_intent"] == "sell_to_open"
    assert body["legs"][1]["position_intent"] == "buy_to_open"


def test_retry_on_server_error_then_success():
    client, session = make_client([
        FakeResponse(status_code=503),
        FakeResponse(status_code=503),
        FakeResponse(payload={"is_open": True}),
    ])
    assert client.market_is_open() is True
    assert len(session.requests) == 3


def test_client_error_raises_without_retry():
    client, session = make_client([FakeResponse(status_code=403, payload={"msg": "no"})])
    with pytest.raises(BrokerError, match="403"):
        client.market_is_open()
    assert len(session.requests) == 1


def test_option_chain_parses_occ_symbols_and_paginates():
    page1 = FakeResponse(payload={
        "snapshots": {
            "SPY260717P00540000": {
                "latestQuote": {"bp": 5.0, "ap": 5.2, "t": "2026-06-12T15:00:00Z"},
                "greeks": {"delta": -0.31},
                "impliedVolatility": 0.18,
            }
        },
        "next_page_token": "tok",
    })
    page2 = FakeResponse(payload={
        "snapshots": {
            "SPY260717P00535000": {
                "latestQuote": {"bp": 4.0, "ap": 4.2, "t": "2026-06-12T15:00:00Z"},
                "greeks": {"delta": -0.27},
            }
        },
    })
    client, session = make_client([page1, page2])
    quotes = client.get_option_chain("SPY")
    assert len(quotes) == 2
    q = next(x for x in quotes if x.contract.strike == 540.0)
    assert q.contract.option_type == "put"
    assert q.contract.underlying == "SPY"
    assert q.contract.expiry.isoformat() == "2026-07-17"
    assert q.delta == -0.31
    assert session.requests[1]["params"]["page_token"] == "tok"
