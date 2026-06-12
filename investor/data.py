"""Thin wrappers around yfinance for bulk price history and quote details."""

import logging

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


def download_history(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    """Bulk-download daily OHLCV. Returns yfinance's multi-column frame."""
    data = yf.download(
        tickers,
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    return data


def closes_and_volumes(data: pd.DataFrame, tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract per-ticker Close and Volume frames from a bulk download."""
    closes, volumes = {}, {}
    for t in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                closes[t] = data[t]["Close"]
                volumes[t] = data[t]["Volume"]
            else:  # single-ticker download has flat columns
                closes[t] = data["Close"]
                volumes[t] = data["Volume"]
        except KeyError:
            continue
    return pd.DataFrame(closes).dropna(how="all"), pd.DataFrame(volumes).dropna(how="all")


def quote_details(ticker: str) -> dict:
    """Fetch name/PE/market cap for a single ticker; empty dict on failure."""
    try:
        info = yf.Ticker(ticker).info or {}
        return {
            "name": info.get("shortName") or info.get("longName") or ticker,
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector"),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("quote_details(%s) failed: %s", ticker, exc)
        return {"name": ticker}


def fetch_news(ticker: str) -> list[dict]:
    """Return normalized news items: {id, title, publisher, link, ts}."""
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch_news(%s) failed: %s", ticker, exc)
        return []
    items = []
    for n in raw:
        # yfinance >=0.2.50 nests fields under 'content'; older versions are flat
        c = n.get("content", n)
        title = c.get("title")
        if not title:
            continue
        link = c.get("canonicalUrl", {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link")
        items.append({
            "id": n.get("id") or c.get("id") or f"{ticker}:{title[:80]}",
            "title": title,
            "publisher": (c.get("provider") or {}).get("displayName", "") if isinstance(c.get("provider"), dict) else c.get("publisher", ""),
            "link": link or "",
            "ts": c.get("pubDate") or c.get("providerPublishTime") or "",
        })
    return items
