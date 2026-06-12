# optionsbot — automated defined-risk options trading (Alpaca, paper-first)

An automated options-trading bot with hard risk controls. It trades ONE
deterministic, defined-risk strategy (bull put credit spreads) on Alpaca's
**paper** trading API by default, with an LLM research layer that can only
*reduce* risk, never increase it or place orders.

> ⚠️ Options trading involves substantial risk of loss. This bot defaults to
> paper trading and live mode requires a deliberate double opt-in. Nothing
> here is financial advice; past backtest performance does not predict
> future results.

## Architecture

```
                       ┌────────────────┐
   Alpaca data ──────▶ │    data/       │  SQLite store (bars, chains, news)
                       └──────┬─────────┘
                              ▼
┌──────────────┐       ┌────────────────┐      ┌──────────────┐
│ research_llm │ ────▶ │   strategy/    │ ───▶ │    risk/     │  hard limits,
│ (advisory,   │ veto  │ (deterministic │ sig- │ RiskManager  │  kill switch,
│  JSON only)  │ only  │  no ML/LLM)    │ nals │ (HMAC-signed │  staleness
└──────────────┘       └────────────────┘      │  approvals)  │  breaker
                                               └──────┬───────┘
                                                      ▼
                       ┌────────────────┐      ┌──────────────┐
                       │  scheduler/    │ ───▶ │  execution/  │ ──▶ Alpaca
                       │ premarket/     │      │ orders, fills│     (paper/live)
                       │ intraday/EOD   │      │ crash-safe   │
                       │ + supervisor   │      │ SQLite state │
                       └────────────────┘      └──────┬───────┘
                                                      ▼
                                               ┌──────────────┐
                                               │ monitoring/  │  webhook alerts,
                                               │              │  `make halt`
                                               └──────────────┘
```

**Why the risk limits can't be bypassed:** `ExecutionEngine.submit()` is the
only code path that places orders, and it calls the `RiskManager` itself and
verifies the HMAC-signed `RiskDecision` before routing. No other module can
mint an approval token. The kill switch and halt flag persist to disk and are
re-checked before every order.

## Setup

```bash
cd optionsbot
make install          # pip install -r requirements.txt

# Credentials — environment variables ONLY, never in config:
export APCA_API_KEY_ID=...        # from alpaca.markets (paper keys)
export APCA_API_SECRET_KEY=...
export ANTHROPIC_API_KEY=...      # for the pre-market research job (optional)
export MONITOR_WEBHOOK_URL=...    # Slack-compatible webhook for alerts (optional)
```

Review `config.yaml` — every risk parameter lives there:

| Limit | Default |
|---|---|
| Max risk per trade | 1.5% of equity |
| Max daily loss (kill switch: flatten + halt) | 5% |
| Defined-risk positions only | enforced |
| Max concurrent positions | 5 |
| Max exposure per underlying | 3% of equity |
| Data staleness circuit breaker | 60s |

## Usage

```bash
make test       # run the unit test suite
make backtest   # backtest on stored data (auto-seeds sample data)
make paper      # run the bot in paper mode under the crash-restart supervisor
make halt       # EMERGENCY: flatten all positions and halt the bot
make status     # show halt state and recorded orders
```

### Paper vs live

`mode: paper` in `config.yaml` is the single flag. Switching to `live`
additionally requires `BOT_ALLOW_LIVE=yes` in the environment — without it the
bot refuses to start. **Run paper for a long time first.**

### The trading day

1. **Pre-market (08:30 ET)** — ingest news; the `research_llm` job sends it to
   the Anthropic API (`claude-fable-5`) and stores structured JSON:
   `{regime: bull|bear|chop, flagged_events: [...], confidence: 0-1}`.
   This output can only *veto* entries (e.g. no new put spreads in a
   high-confidence bear regime). It cannot place or size orders. Anything
   that would increase risk is appended to `state/pending_approvals.jsonl`
   for a human — never auto-executed. If the research job fails or is
   refused, trading proceeds with no view (the deterministic strategy and
   RiskManager still gate everything).
2. **Intraday (10:00–15:30 ET, every 15 min)** — kill-switch check, fill
   polling, data refresh, strategy pass, risk review, order routing.
   US market hours only; confirmed against the broker clock.
3. **EOD (16:15 ET)** — review summary alert.

The supervisor (`make paper`) restarts the process with exponential backoff
if it crashes; persistent state (orders, halt flag, day-start equity) lives
in `state/` and survives restarts. On startup the engine reconciles its local
order state against the broker.

### Strategy

`strategy/vertical_spread.py` — bull put credit spread: sell a put near 0.30
delta, buy one $5 lower, 25–45 DTE, require credit ≥ 25% of width, take
profit at 50% of credit, stop at 2× credit, never stack positions on one
underlying. All parameters in `config.yaml`. New strategies implement
`strategy.base.Strategy` and register in `build_strategies`.

### Backtesting

`make backtest` replays stored daily bars (sample data is deterministic
synthetic GBM with bull/bear/chop regimes), synthesizes chains with
Black-Scholes from trailing realized vol, and runs the **same RiskManager**
as live trading. Reports win rate, avg win/loss, max drawdown, Sharpe,
kill-switch activations, and risk rejections.

To backtest on real data, ingest bars into the SQLite store
(`data/market.db`, table `bars`) and rerun.

## Module map

| Module | Role |
|---|---|
| `bot/broker_client/` | Typed Alpaca REST wrapper (auth, chains, multi-leg orders, positions, retries) |
| `bot/data/` | SQLite store + ingest jobs (quotes, chains, news) |
| `bot/strategy/` | Deterministic strategies behind a common interface |
| `bot/risk/` | RiskManager: hard limits, kill switch, HMAC-signed approvals |
| `bot/execution/` | Order routing, fills, crash-safe state, reconcile |
| `bot/scheduler/` | Market-hours jobs + crash-restart supervisor |
| `bot/research_llm/` | Pre-market Claude analyst (advisory JSON only) |
| `bot/monitoring/` | Webhook alerts + `make halt` |
| `bot/backtest/` | Backtest harness with the same risk rules |
