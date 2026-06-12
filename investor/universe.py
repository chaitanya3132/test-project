"""Build the screening universe: S&P 500 constituents plus configured ETFs.

Everything here trades on major US exchanges (NYSE/Nasdaq/AMEX/Arca), which
is the same universe Robinhood supports for stocks and ETFs. The S&P 500
list is fetched from Wikipedia at runtime; if that fails (offline, layout
change) we fall back to an embedded list of large, liquid names.
"""

import logging

import pandas as pd

log = logging.getLogger(__name__)

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Fallback: ~100 of the largest, most liquid US-listed stocks.
FALLBACK_STOCKS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B", "AVGO", "TSLA",
    "LLY", "JPM", "V", "UNH", "XOM", "MA", "ORCL", "COST", "HD", "PG", "JNJ",
    "NFLX", "ABBV", "BAC", "CRM", "WMT", "KO", "CVX", "AMD", "MRK", "PEP",
    "ADBE", "TMO", "CSCO", "ACN", "LIN", "MCD", "ABT", "INTU", "WFC", "IBM",
    "GE", "DHR", "TXN", "QCOM", "VZ", "AXP", "CAT", "AMGN", "PFE", "NOW",
    "MS", "NEE", "PM", "UNP", "GS", "ISRG", "RTX", "SPGI", "CMCSA", "T",
    "UBER", "LOW", "HON", "BKNG", "ELV", "SYK", "TJX", "LMT", "BLK", "COP",
    "VRTX", "PLD", "MDT", "PANW", "C", "ADP", "SBUX", "AMAT", "BMY", "DE",
    "GILD", "ADI", "MMC", "BA", "MU", "LRCX", "ETN", "SCHW", "ANET", "BSX",
    "KLAC", "MDLZ", "REGN", "CB", "SO", "FI", "DUK", "MO", "EOG", "SHW",
    "CME", "ICE", "WM", "CL", "ITW", "EMR", "MCK", "NOC", "PGR", "APH",
]


def sp500_tickers() -> list[str]:
    """Return current S&P 500 tickers, falling back to the embedded list."""
    try:
        tables = pd.read_html(WIKI_SP500_URL)
        symbols = tables[0]["Symbol"].astype(str).str.strip().tolist()
        # Yahoo uses '-' where the index list uses '.' (e.g. BRK.B -> BRK-B)
        symbols = [s.replace(".", "-") for s in symbols if s and s != "nan"]
        if len(symbols) > 400:
            return symbols
        log.warning("S&P 500 fetch returned only %d symbols; using fallback", len(symbols))
    except Exception as exc:  # noqa: BLE001 - any fetch/parse failure means fallback
        log.warning("Could not fetch S&P 500 list (%s); using fallback", exc)
    return list(FALLBACK_STOCKS)


def build_universe(cfg: dict) -> tuple[list[str], list[str]]:
    """Return (stocks, etfs) for the screener."""
    stocks = sp500_tickers()
    etfs = [str(t) for t in cfg["universe"].get("extra_etfs", [])]
    return stocks, etfs
