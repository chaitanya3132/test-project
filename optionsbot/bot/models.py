"""Shared domain models. Frozen dataclasses keep signal/decision objects immutable."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

OptionType = Literal["call", "put"]
Side = Literal["buy", "sell"]

OPTION_MULTIPLIER = 100


def occ_symbol(underlying: str, expiry: date, option_type: OptionType, strike: float) -> str:
    """Build an OCC option symbol, e.g. SPY260618P00540000."""
    cp = "C" if option_type == "call" else "P"
    return f"{underlying.upper()}{expiry:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    bid: float
    ask: float
    delta: float | None
    iv: float | None
    ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class OptionLeg:
    symbol: str
    side: Side
    ratio: int = 1


@dataclass(frozen=True)
class Signal:
    """A proposed trade emitted by a strategy. Sizing here is a *request*;
    the RiskManager has final say on quantity (including zero)."""

    strategy: str
    underlying: str
    legs: tuple[OptionLeg, ...]
    qty: int
    # Net price per spread, per share. Negative = credit (Alpaca convention).
    limit_price: float
    # Worst-case loss for ONE spread, in dollars (multiplier included).
    max_loss_per_contract: float
    expiry: date
    rationale: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class RiskDecision:
    signal_id: str
    approved: bool
    qty: int
    reasons: tuple[str, ...]
    token: str = ""  # HMAC issued by RiskManager; ExecutionEngine verifies it


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    buying_power: float


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float  # negative = short
    avg_entry_price: float
    market_value: float
    underlying: str


@dataclass(frozen=True)
class OrderState:
    order_id: str
    signal_id: str
    strategy: str
    underlying: str
    status: str  # accepted | filled | canceled | rejected | expired
    qty: int
    filled_qty: int
    limit_price: float
    max_loss_per_contract: float
    created_at: datetime


@dataclass(frozen=True)
class NewsItem:
    id: str
    headline: str
    summary: str
    symbols: tuple[str, ...]
    source: str
    ts: datetime


@dataclass(frozen=True)
class ResearchView:
    """Structured output of the pre-market LLM analyst. Informational only —
    nothing in this object can place or size an order."""

    regime: Literal["bull", "bear", "chop"]
    flagged_events: tuple[str, ...]
    confidence: float  # 0..1
    generated_at: datetime
