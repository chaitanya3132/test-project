"""Supervisor: keeps the bot process alive, restarting on crash with backoff.

Run as `python -m bot.scheduler.supervisor` (what `make paper` does). A clean
exit (code 0) or a persistent halt file stops the supervision loop.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Callable, Sequence

log = logging.getLogger("supervisor")

DEFAULT_CMD = (sys.executable, "-m", "bot.cli", "run")


def compute_backoff(consecutive_failures: int, base: float = 5.0,
                    cap: float = 300.0) -> float:
    """Exponential backoff: 5s, 10s, 20s ... capped at 5 minutes."""
    return min(base * (2 ** max(0, consecutive_failures - 1)), cap)


def supervise(cmd: Sequence[str] = DEFAULT_CMD,
              run: Callable[[Sequence[str]], int] | None = None,
              sleep: Callable[[float], None] = time.sleep,
              max_restarts: int | None = None) -> int:
    """Restart `cmd` whenever it exits non-zero. Returns last exit code."""
    run = run or (lambda c: subprocess.call(list(c)))
    failures = 0
    restarts = 0
    while True:
        log.info("starting: %s", " ".join(cmd))
        code = run(cmd)
        if code == 0:
            log.info("process exited cleanly; supervisor done")
            return 0
        failures += 1
        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            log.error("max restarts (%d) exceeded; giving up", max_restarts)
            return code
        delay = compute_backoff(failures)
        log.error("process crashed with code %d; restarting in %.0fs", code, delay)
        sleep(delay)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(supervise())
