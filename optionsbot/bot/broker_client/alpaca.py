"""Typed wrapper over Alpaca's trading + market-data REST APIs.

Paper vs live is a single flag; credentials come from the environment only.
The HTTP session is injectable so unit tests can run fully offline.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Protocol

import requests

from ..config import alpaca_credentials
from ..models import (
    Account,
    OptionContract,
    OptionLeg,
    OptionQuote,
    Position,
    NewsItem,
)

log = logging.getLogger(__name__)

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
LIVE_TRADING_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class BrokerError(Exception):
    pass


class HttpSession(Protocol):
    """The subset of requests.Session we use; tests substitute a fake."""

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response: ...


def _parse_occ(symbol: str) -> OptionContract:
    """Parse an OCC symbol like SPY260618P00540000."""
    body = symbol.strip().upper()
    strike = int(body[-8:]) / 1000.0
    option_type = "call" if body[-9] == "C" else "put"
    expiry = datetime.strptime(body[-15:-9], "%y%m%d").date()
    underlying = body[:-15]
    return OptionContract(
        symbol=body, underlying=underlying, expiry=expiry,
        strike=strike, option_type=option_type,
    )


class AlpacaClient:
    def __init__(
        self,
        paper: bool = True,
        session: HttpSession | None = None,
        options_feed: str = "indicative",
        max_retries: int = 3,
        timeout: float = 15.0,
        sleep: Any = time.sleep,
    ) -> None:
        key_id, secret = alpaca_credentials()
        self.paper = paper
        self.trading_url = PAPER_TRADING_URL if paper else LIVE_TRADING_URL
        self.data_url = DATA_URL
        self.options_feed = options_feed
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep
        self._session: HttpSession = session or requests.Session()
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        }

    # ----- low-level -----

    def _request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                 json: dict[str, Any] | None = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.request(
                    method, url, params=params, json=json,
                    headers=self._headers, timeout=self.timeout,
                )
            except requests.ConnectionError as exc:
                last_exc = exc
            else:
                if resp.status_code in RETRYABLE_STATUS:
                    last_exc = BrokerError(f"{method} {url} -> {resp.status_code}")
                elif resp.status_code >= 400:
                    raise BrokerError(f"{method} {url} -> {resp.status_code}: {resp.text}")
                else:
                    return resp.json() if resp.text else None
            if attempt < self.max_retries:
                delay = 2 ** attempt
                log.warning("retrying %s %s in %ss (%s)", method, url, delay, last_exc)
                self._sleep(delay)
        raise BrokerError(f"request failed after {self.max_retries + 1} attempts: {last_exc}")

    def _trading(self, method: str, path: str, **kw: Any) -> Any:
        return self._request(method, f"{self.trading_url}{path}", **kw)

    def _data(self, method: str, path: str, **kw: Any) -> Any:
        return self._request(method, f"{self.data_url}{path}", **kw)

    # ----- account / clock -----

    def get_account(self) -> Account:
        d = self._trading("GET", "/v2/account")
        return Account(
            equity=float(d["equity"]),
            cash=float(d["cash"]),
            buying_power=float(d["buying_power"]),
        )

    def market_is_open(self) -> bool:
        return bool(self._trading("GET", "/v2/clock")["is_open"])

    # ----- market data -----

    def get_stock_price(self, symbol: str) -> tuple[float, datetime]:
        d = self._data("GET", f"/v2/stocks/{symbol}/trades/latest")
        trade = d["trade"]
        ts = datetime.fromisoformat(trade["t"].replace("Z", "+00:00"))
        return float(trade["p"]), ts

    def get_option_chain(
        self, underlying: str,
        expiry_gte: date | None = None,
        expiry_lte: date | None = None,
        option_type: str | None = None,
    ) -> list[OptionQuote]:
        """Fetch option snapshots (quote + greeks + IV) for an underlying."""
        params: dict[str, Any] = {"feed": self.options_feed, "limit": 1000}
        if expiry_gte:
            params["expiration_date_gte"] = expiry_gte.isoformat()
        if expiry_lte:
            params["expiration_date_lte"] = expiry_lte.isoformat()
        if option_type:
            params["type"] = option_type

        quotes: list[OptionQuote] = []
        page_token: str | None = None
        while True:
            if page_token:
                params["page_token"] = page_token
            d = self._data("GET", f"/v1beta1/options/snapshots/{underlying}", params=params)
            for sym, snap in (d.get("snapshots") or {}).items():
                q = snap.get("latestQuote") or {}
                greeks = snap.get("greeks") or {}
                if "bp" not in q or "ap" not in q:
                    continue
                ts_raw = q.get("t", "")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    ts = datetime.now(timezone.utc)
                quotes.append(OptionQuote(
                    contract=_parse_occ(sym),
                    bid=float(q["bp"]),
                    ask=float(q["ap"]),
                    delta=greeks.get("delta"),
                    iv=snap.get("impliedVolatility"),
                    ts=ts,
                ))
            page_token = d.get("next_page_token")
            if not page_token:
                break
        return quotes

    def get_news(self, symbols: list[str], limit: int = 50) -> list[NewsItem]:
        d = self._data("GET", "/v1beta1/news",
                       params={"symbols": ",".join(symbols), "limit": limit})
        items = []
        for n in d.get("news", []):
            items.append(NewsItem(
                id=str(n["id"]),
                headline=n.get("headline", ""),
                summary=n.get("summary", ""),
                symbols=tuple(n.get("symbols", [])),
                source=n.get("source", ""),
                ts=datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")),
            ))
        return items

    # ----- orders -----

    def submit_mleg_order(self, legs: tuple[OptionLeg, ...], qty: int,
                          limit_price: float) -> dict[str, Any]:
        """Place a multi-leg options order (limit, day). Negative price = credit."""
        payload = {
            "order_class": "mleg",
            "qty": str(qty),
            "type": "limit",
            "limit_price": str(round(limit_price, 2)),
            "time_in_force": "day",
            "legs": [
                {
                    "symbol": leg.symbol,
                    "ratio_qty": str(leg.ratio),
                    "side": leg.side,
                    "position_intent": "buy_to_open" if leg.side == "buy" else "sell_to_open",
                }
                for leg in legs
            ],
        }
        return self._trading("POST", "/v2/orders", json=payload)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._trading("GET", f"/v2/orders/{order_id}")

    def list_open_orders(self) -> list[dict[str, Any]]:
        return self._trading("GET", "/v2/orders", params={"status": "open", "limit": 500}) or []

    def cancel_order(self, order_id: str) -> None:
        self._trading("DELETE", f"/v2/orders/{order_id}")

    def cancel_all_orders(self) -> None:
        self._trading("DELETE", "/v2/orders")

    # ----- positions -----

    def list_positions(self) -> list[Position]:
        rows = self._trading("GET", "/v2/positions") or []
        out = []
        for r in rows:
            sym = r["symbol"]
            underlying = _parse_occ(sym).underlying if len(sym) > 12 else sym
            out.append(Position(
                symbol=sym,
                qty=float(r["qty"]),
                avg_entry_price=float(r["avg_entry_price"]),
                market_value=float(r.get("market_value") or 0),
                underlying=underlying,
            ))
        return out

    def close_all_positions(self) -> None:
        """Liquidate everything (also cancels open orders)."""
        self._trading("DELETE", "/v2/positions", params={"cancel_orders": "true"})
