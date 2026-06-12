"""Render markdown reports for the digest and news alerts."""

from datetime import datetime, timezone

import pandas as pd

DISCLAIMER = (
    "\n---\n*This is automated research, not financial advice. Scores are "
    "simple momentum/trend rankings, not predictions. Do your own due "
    "diligence before trading; never invest money you can't afford to lose.*\n"
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_pct(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.1f}%"


def render_screen(scored: pd.DataFrame, details: dict[str, dict], top_n: int) -> str:
    lines = [f"## Top {top_n} screen results", ""]
    if scored.empty:
        lines.append("_Screen produced no results (data fetch may have failed)._")
        return "\n".join(lines)
    lines += [
        "| Ticker | Name | Score | Price | 3m | 6m | Off 52w high | P/E |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t, r in scored.head(top_n).iterrows():
        d = details.get(t, {})
        pe = d.get("trailing_pe")
        pe_s = f"{pe:.1f}" if isinstance(pe, (int, float)) else "—"
        lines.append(
            f"| {t} | {d.get('name', t)} | {r['score']:.0f} | ${r['price']:.2f} "
            f"| {fmt_pct(r['ret_3m'])} | {fmt_pct(r['ret_6m'])} "
            f"| {fmt_pct(r['pct_off_high'])} | {pe_s} |"
        )
    return "\n".join(lines)


def render_portfolio(result: dict, benchmark: str) -> str:
    df = result["positions"]
    lines = ["## Portfolio", ""]
    if df.empty:
        lines.append("_No positions found in portfolio.csv._")
        return "\n".join(lines)
    lines.append(
        f"**Total value: ${result['total_value']:,.2f}** "
        f"(P/L {result['total_pl']:+,.2f})"
    )
    lines += [
        "",
        f"| Ticker | Shares | Cost | Price | Value | P/L | vs {benchmark} | Alloc |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        vs = f"{r['vs_bench_pct']:+.1f}%" if r["vs_bench_pct"] is not None and not pd.isna(r["vs_bench_pct"]) else "—"
        lines.append(
            f"| {r['ticker']} | {r['shares']:g} | ${r['cost_basis']:.2f} | ${r['price']:.2f} "
            f"| ${r['value']:,.2f} | {r['pl_pct']:+.1f}% | {vs} | {r['alloc_pct']:.1f}% |"
        )
    if result["flags"]:
        lines += ["", "### Flags", ""]
        lines += [f"- ⚠️ {f}" for f in result["flags"]]
    return "\n".join(lines)


def render_digest(screen_md: str, portfolio_md: str) -> str:
    return (
        f"# Daily Investment Digest — {_now()}\n\n"
        f"{portfolio_md}\n\n{screen_md}\n{DISCLAIMER}"
    )


def render_alerts(alerts: list[dict]) -> str:
    lines = [f"# News Alerts — {_now()}", ""]
    for a in alerts:
        emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}[a["tone"]]
        tag = " · material" if a["material"] else ""
        lines.append(
            f"- {emoji} **{a['ticker']}** — [{a['title']}]({a['link']}) "
            f"({a['publisher']}, sentiment {a['sentiment']:+.2f}{tag})"
        )
    return "\n".join(lines) + DISCLAIMER
