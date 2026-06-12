"""Command-line entrypoints.

    python -m bot.cli run        # trading loop (paper by default)
    python -m bot.cli halt       # EMERGENCY: flatten all positions and halt
    python -m bot.cli backtest   # run the backtest on stored data
    python -m bot.cli seed-data  # load deterministic sample data for backtests
    python -m bot.cli status     # show halt/order state
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from .backtest import Backtester
from .backtest.sample_data import seed_store
from .broker_client import AlpacaClient
from .config import Config, load_config
from .data import Store
from .execution import ExecutionEngine, PositionStateStore
from .monitoring import AlertManager
from .risk import RiskManager, RiskState
from .scheduler.jobs import TradingDayScheduler
from .strategy import build_strategies
from .strategy.vertical_spread import BullPutSpread


def _build_engine(cfg: Config) -> tuple[ExecutionEngine, AlertManager]:
    alerts = AlertManager(min_level=str(cfg.monitoring.get("alert_min_level", "info")))
    broker = AlpacaClient(paper=cfg.is_paper, options_feed=cfg.options_feed)
    risk = RiskManager(cfg.risk, RiskState(cfg.state_dir))
    state = PositionStateStore(cfg.state_dir / "positions.db")
    return ExecutionEngine(broker, risk, state, alerts), alerts


def cmd_run(cfg: Config) -> int:
    engine, alerts = _build_engine(cfg)
    store = Store(cfg.db_path)
    strategies = build_strategies(cfg.strategy)
    alerts.notify("startup", f"bot starting in {cfg.mode.upper()} mode with "
                             f"{len(strategies)} strategies")
    scheduler = TradingDayScheduler(cfg, engine.broker, store, engine, strategies, alerts)
    scheduler.run_forever()
    return 0


def cmd_halt(cfg: Config) -> int:
    """Flatten everything and halt the bot. The single big red button."""
    engine, _ = _build_engine(cfg)
    engine.flatten_all("manual halt via CLI")
    engine.risk.state.set_halt(datetime.now(timezone.utc).date(),
                               "manual halt via CLI", permanent=True)
    print("All positions flattened. Bot halted (delete state/halt.json to resume).")
    return 0


def cmd_backtest(cfg: Config) -> int:
    store = Store(cfg.db_path)
    params = dict(cfg.strategy.get("bull_put_spread", {}))
    strategy = BullPutSpread(**params)
    if not store.get_bars(strategy.underlying):
        print(f"No bars stored for {strategy.underlying}; seeding sample data...")
        seed_store(store, strategy.underlying)
    report = Backtester(store, strategy, cfg.risk).run()
    print(report)
    return 0


def cmd_seed_data(cfg: Config) -> int:
    store = Store(cfg.db_path)
    n = seed_store(store)
    print(f"Seeded {n} sample daily bars into {cfg.db_path}")
    return 0


def cmd_status(cfg: Config) -> int:
    state = RiskState(cfg.state_dir)
    halt = state.halted_reason(datetime.now(timezone.utc).date())
    orders = PositionStateStore(cfg.state_dir / "positions.db").all_orders()
    print(json.dumps({
        "mode": cfg.mode,
        "halted": halt or False,
        "orders_recorded": len(orders),
        "open_orders": len([o for o in orders if o.status in ("accepted", "new")]),
        "filled": len([o for o in orders if o.status == "filled"]),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="bot")
    parser.add_argument("command",
                        choices=["run", "halt", "backtest", "seed-data", "status"])
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    handlers = {
        "run": cmd_run,
        "halt": cmd_halt,
        "backtest": cmd_backtest,
        "seed-data": cmd_seed_data,
        "status": cmd_status,
    }
    return handlers[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
