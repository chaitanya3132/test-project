"""Strategy interface.

Strategies are pure decision functions: MarketSnapshot in, list[Signal] out.
They are deterministic — no ML, no LLM calls, no I/O. The pre-market
ResearchView may be present on the snapshot, but strategies may only use it
to *veto* entries (risk-reducing), never to size up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models import OptionQuote, ResearchView, Signal


@dataclass(frozen=True)
class MarketSnapshot:
    ts: datetime
    underlying_prices: dict[str, float]
    chains: dict[str, tuple[OptionQuote, ...]]
    research: ResearchView | None = None
    open_position_underlyings: tuple[str, ...] = field(default_factory=tuple)


class Strategy(ABC):
    name: str

    @abstractmethod
    def generate(self, snapshot: MarketSnapshot) -> list[Signal]:
        """Return zero or more proposed trades for this snapshot."""


def build_strategies(strategy_cfg: dict[str, Any]) -> list[Strategy]:
    """Instantiate every configured strategy. New strategies register here."""
    from .vertical_spread import BullPutSpread

    registry = {"bull_put_spread": BullPutSpread}
    strategies: list[Strategy] = []
    for key, params in (strategy_cfg or {}).items():
        cls = registry.get(key)
        if cls is None:
            raise ValueError(f"unknown strategy {key!r}; known: {sorted(registry)}")
        strategies.append(cls(**params))
    return strategies
