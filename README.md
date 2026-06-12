# Investment Research Assistant

An automated research assistant for the US stock market (Robinhood-tradeable
stocks and ETFs). It runs day and night on GitHub Actions and emails you:

1. **Daily digest** (weekday pre-market) — a momentum/trend screen over the
   S&P 500 + major ETFs, ranked 0–100, plus your portfolio's P/L,
   benchmark comparison, and risk flags.
2. **News & sentiment alerts** (every 2 hours during market hours) — material
   or strongly positive/negative headlines for your watchlist, scored with
   VADER sentiment. Only *new* items alert; history is deduplicated.
3. **Portfolio tracker** — included in the daily digest: per-position P/L,
   performance vs SPY since each purchase, allocation percentages, and flags
   for concentration (>25% in one name), drawdowns (>20% below cost), and
   benchmark laggards.

> ⚠️ **What this is not:** a money-printing machine. No tool can reliably
> turn $10k into $100k in a year — that's a 900% return, far beyond what even
> elite professional investors achieve. This assistant surfaces research and
> watches your holdings so you make better-informed decisions. It never
> trades for you, and its scores are rankings, not predictions.

## One-time setup

### 1. Email delivery (Gmail)

The assistant emails reports via SMTP. For Gmail:

1. Enable 2-step verification on your Google account.
2. Create an **App Password**: <https://myaccount.google.com/apppasswords>
3. In this repo on GitHub: **Settings → Secrets and variables → Actions →
   New repository secret**, add:

   | Secret | Value |
   |---|---|
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASSWORD` | the 16-character app password |

   (`SMTP_HOST`/`SMTP_PORT` default to `smtp.gmail.com:465`; only set them
   for a non-Gmail provider.)

Without these secrets everything still runs — reports are committed to
`reports/` in the repo instead of emailed.

### 2. Your data

- **`portfolio.csv`** — replace the sample rows with your real Robinhood
  positions (`ticker, shares, cost_basis, buy_date`).
- **`config.yaml`** — edit the news `watchlist`, screener thresholds, ETF
  list, and risk-flag limits to taste. The recipient address is under
  `email.to`.

### 3. First run

Trigger either workflow manually to verify everything works:
**Actions → Daily Investment Digest → Run workflow** (and the same for
**News & Sentiment Monitor**). After it finishes, check your inbox and the
`reports/` folder.

> Note: GitHub disables cron schedules in repos with no activity for 60
> days; any commit re-enables them.

## How it works

| Piece | What it does |
|---|---|
| `investor/universe.py` | S&P 500 list (live fetch + fallback) + ETFs, filtered to major US exchanges — the Robinhood universe |
| `investor/screener.py` | Ranks on 3m/6m momentum, 50/200-day trend, distance from 52-week high, volatility penalty; liquidity & price floors |
| `investor/news.py` | VADER sentiment + materiality keywords (earnings, M&A, FDA, downgrades, …); dedupe state in `state/seen_news.json` |
| `investor/portfolio.py` | P/L, vs-SPY comparison since each buy date, allocation and drawdown flags |
| `run_digest.py` / `run_news_monitor.py` | Entrypoints used by the workflows |
| `.github/workflows/` | Cron schedules: digest 12:30 UTC weekdays, news scan every 2h 13:00–23:00 UTC weekdays |

## Running locally

```bash
pip install -r requirements.txt
python run_digest.py        # full digest -> reports/latest_digest.md
python run_news_monitor.py  # watchlist scan -> reports/latest_alerts.md
pytest                      # offline unit tests
```

Set `SMTP_USER`/`SMTP_PASSWORD` in your environment to also send the email.

## Disclaimer

Automated output, not financial advice. Markets involve risk of loss. Do
your own due diligence; never invest money you can't afford to lose.
