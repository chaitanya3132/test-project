"""Crash-safe persistent order/position state (SQLite, transactional)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import OrderState
from ..risk.manager import OpenRisk

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    underlying TEXT NOT NULL,
    status TEXT NOT NULL,
    qty INTEGER NOT NULL,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    limit_price REAL NOT NULL,
    max_loss_per_contract REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""

OPEN_STATUSES = ("accepted", "new", "partially_filled", "pending_new")
LIVE_STATUSES = OPEN_STATUSES + ("filled",)


class PositionStateStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record_order(self, order: OrderState) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                (order.order_id, order.signal_id, order.strategy, order.underlying,
                 order.status, order.qty, order.filled_qty, order.limit_price,
                 order.max_loss_per_contract, order.created_at.isoformat()),
            )

    def update_status(self, order_id: str, status: str, filled_qty: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE orders SET status=?, filled_qty=? WHERE order_id=?",
                (status, filled_qty, order_id),
            )

    def _rows(self, where: str, args: tuple) -> list[OrderState]:
        rows = self._conn.execute(
            "SELECT order_id, signal_id, strategy, underlying, status, qty, "
            "filled_qty, limit_price, max_loss_per_contract, created_at "
            f"FROM orders {where}", args,
        )
        return [
            OrderState(
                order_id=r[0], signal_id=r[1], strategy=r[2], underlying=r[3],
                status=r[4], qty=r[5], filled_qty=r[6], limit_price=r[7],
                max_loss_per_contract=r[8],
                created_at=datetime.fromisoformat(r[9]),
            )
            for r in rows
        ]

    def open_orders(self) -> list[OrderState]:
        marks = ",".join("?" * len(OPEN_STATUSES))
        return self._rows(f"WHERE status IN ({marks})", OPEN_STATUSES)

    def live_orders(self) -> list[OrderState]:
        """Orders that are open or filled (i.e. carry risk)."""
        marks = ",".join("?" * len(LIVE_STATUSES))
        return self._rows(f"WHERE status IN ({marks})", LIVE_STATUSES)

    def all_orders(self) -> list[OrderState]:
        return self._rows("", ())

    def open_risk(self) -> list[OpenRisk]:
        """Risk currently on the books, for the RiskManager."""
        return [
            OpenRisk(underlying=o.underlying,
                     max_loss_dollars=max(o.filled_qty, o.qty if o.status in OPEN_STATUSES else 0)
                     * o.max_loss_per_contract)
            for o in self.live_orders()
        ]

    def mark_all_closed(self, reason: str) -> None:
        """Used after flatten-all: everything live becomes 'closed'."""
        marks = ",".join("?" * len(LIVE_STATUSES))
        with self._conn:
            self._conn.execute(
                f"UPDATE orders SET status='closed' WHERE status IN ({marks})",
                LIVE_STATUSES,
            )

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
