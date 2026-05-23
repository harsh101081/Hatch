"""
Edge-Finder style dashboard for HatchFibBreakout backtest results.

Produces a single PNG with:
  - Header: 8 key stats (win rate, PF, total return, max DD, Sharpe, Avg R, expectancy, trades)
  - Equity curve with high-watermark and underwater fill
  - Drawdown-over-time area chart
  - R-multiple distribution histogram (wins vs losses)
  - P&L by day-of-week bars
  - Monthly returns bars

Usage:
    python visualize.py --csv data.csv --save dashboard.png
    python visualize.py --yfinance GC=F --start 2023-06-01 --save dashboard.png
"""

from __future__ import annotations

import argparse
import sys

import matplotlib
if "--no-show" in sys.argv or "--save" in sys.argv:
    matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec

from analytics import compute_drawdown, compute_stats, trades_to_df
from backtest import Backtester, Config, load_csv, load_yfinance


# ============================================================
# Theme
# ============================================================
THEME = {
    "bg":         "#0e1117",
    "panel":      "#1a1f2e",
    "text":       "#e6edf3",
    "text_dim":   "#8b949e",
    "grid":       "#30363d",
    "green":      "#3fb950",
    "red":        "#f85149",
    "blue":       "#58a6ff",
    "yellow":     "#f7b955",
    "purple":     "#a371f7",
}


def _style_axis(ax, title=None):
    ax.set_facecolor(THEME["panel"])
    if title:
        ax.set_title(title, color=THEME["text"], fontsize=11,
                     fontweight="bold", loc="left", pad=8)
    ax.tick_params(colors=THEME["text_dim"], labelsize=9)
    ax.grid(True, alpha=0.2, color=THEME["grid"])
    for spine in ax.spines.values():
        spine.set_color(THEME["grid"])


# ============================================================
# Dashboard builder
# ============================================================

def build_dashboard(bt: Backtester, save_path: str = None, show: bool = True) -> None:
    cfg = bt.cfg
    df = trades_to_df(bt.trades, contract_size=cfg.contract_size)
    stats = compute_stats(bt.trades, cfg.initial_balance, contract_size=cfg.contract_size)

    if df.empty:
        print("No trades to visualize.")
        return

    fig = plt.figure(figsize=(16, 12), facecolor=THEME["bg"])
    gs = GridSpec(
        4, 6, figure=fig,
        height_ratios=[0.45, 1.4, 0.7, 1.0],
        hspace=0.55, wspace=0.55,
        left=0.06, right=0.97, top=0.94, bottom=0.06,
    )

    # ============ Title ============
    fig.suptitle(
        "HatchFibBreakout - Edge Finder Report",
        color=THEME["text"], fontsize=18, fontweight="bold", y=0.985,
    )
    fig.text(
        0.5, 0.955,
        f"Risk={cfg.risk_pct}%   RR=1:{cfg.rr}   Balance=${cfg.initial_balance:,.0f}   "
        f"Period: {bt.df.index[0].date()} -> {bt.df.index[-1].date()}",
        color=THEME["text_dim"], fontsize=11, ha="center",
    )

    # ============ Header stat cards ============
    ax_stats = fig.add_subplot(gs[0, :])
    ax_stats.axis("off")
    ax_stats.set_facecolor(THEME["bg"])

    pf_str = (f"{stats['profit_factor']:.2f}"
              if stats['profit_factor'] != float('inf') else "INF")
    cards = [
        ("Trades",        f"{stats['trades']}",                        THEME["text"]),
        ("Win Rate",      f"{stats['win_rate']:.1f}%",
            THEME["green"] if stats["win_rate"] >= 50 else THEME["red"]),
        ("Profit Factor", pf_str,
            THEME["green"] if stats["profit_factor"] >= 1.5 else
            (THEME["yellow"] if stats["profit_factor"] >= 1.0 else THEME["red"])),
        ("Total Return",  f"{stats['return_pct']:+.2f}%",
            THEME["green"] if stats["return_pct"] > 0 else THEME["red"]),
        ("Max DD",        f"{stats['max_dd_pct']:.2f}%",
            THEME["red"] if stats["max_dd_pct"] > 10 else THEME["yellow"]),
        ("Sharpe",        f"{stats['sharpe']:.2f}",
            THEME["green"] if stats["sharpe"] >= 1 else THEME["text_dim"]),
        ("Avg R",         f"{stats['avg_r']:+.2f}R",
            THEME["green"] if stats["avg_r"] > 0 else THEME["red"]),
        ("Expectancy",    f"${stats['expectancy']:+,.2f}",
            THEME["green"] if stats["expectancy"] > 0 else THEME["red"]),
    ]
    n = len(cards)
    for i, (label, val, color) in enumerate(cards):
        x = (i + 0.5) / n
        ax_stats.text(x, 0.85, label.upper(), ha="center", va="top",
                      color=THEME["text_dim"], fontsize=10, fontweight="bold",
                      transform=ax_stats.transAxes)
        ax_stats.text(x, 0.45, val, ha="center", va="center",
                      color=color, fontsize=22, fontweight="bold",
                      transform=ax_stats.transAxes)

    # ============ Equity curve ============
    ax_eq = fig.add_subplot(gs[1, :])
    _style_axis(ax_eq, "Equity Curve")

    first_entry = pd.to_datetime(df["entry_time"]).iloc[0]
    exit_times = pd.to_datetime(df["exit_time"])
    eq_x = pd.DatetimeIndex([first_entry] + list(exit_times))
    eq_y = np.concatenate([[cfg.initial_balance], df["balance_after"].values])
    peaks, _dd = compute_drawdown(eq_y)

    ax_eq.fill_between(eq_x, eq_y, cfg.initial_balance,
                       where=eq_y >= cfg.initial_balance,
                       color=THEME["green"], alpha=0.15)
    ax_eq.fill_between(eq_x, eq_y, cfg.initial_balance,
                       where=eq_y < cfg.initial_balance,
                       color=THEME["red"], alpha=0.15)
    ax_eq.plot(eq_x, peaks, color=THEME["text_dim"], linewidth=0.8,
               linestyle="--", alpha=0.6, label="High Watermark")
    ax_eq.plot(eq_x, eq_y, color=THEME["green"], linewidth=2, label="Equity")
    ax_eq.axhline(cfg.initial_balance, color=THEME["text_dim"],
                  linestyle=":", linewidth=0.8, alpha=0.6)

    ax_eq.set_ylabel("Balance ($)", color=THEME["text"])
    ax_eq.legend(loc="upper left", facecolor=THEME["panel"],
                 edgecolor=THEME["grid"], labelcolor=THEME["text"], framealpha=0.8)

    # ============ Drawdown ============
    ax_dd = fig.add_subplot(gs[2, :])
    _style_axis(ax_dd, "Drawdown (%)")
    _, dd_pct = compute_drawdown(eq_y)
    ax_dd.fill_between(eq_x, dd_pct, 0, color=THEME["red"], alpha=0.5)
    ax_dd.plot(eq_x, dd_pct, color=THEME["red"], linewidth=1)
    ax_dd.axhline(-stats["max_dd_pct"], color=THEME["yellow"],
                  linestyle="--", linewidth=0.8, alpha=0.7,
                  label=f"Max DD = {stats['max_dd_pct']:.2f}%")
    ax_dd.set_ylabel("DD %", color=THEME["text"])
    ax_dd.legend(loc="lower left", facecolor=THEME["panel"],
                 edgecolor=THEME["grid"], labelcolor=THEME["text"], framealpha=0.8)

    # ============ Bottom row: 3 panels ============
    # Panel 1: R-multiple distribution (cols 0-1)
    ax_r = fig.add_subplot(gs[3, 0:2])
    _style_axis(ax_r, "R-Multiple Distribution")
    r_mults = df["r_multiple"].values
    if len(r_mults) > 0:
        lo, hi = float(r_mults.min()), float(r_mults.max())
        bins = np.linspace(min(lo, -1.5), max(hi, 2.5), 25)
        ax_r.hist(r_mults[r_mults >= 0], bins=bins,
                  color=THEME["green"], alpha=0.85, edgecolor=THEME["bg"],
                  label=f"Wins ({(r_mults >= 0).sum()})")
        ax_r.hist(r_mults[r_mults < 0], bins=bins,
                  color=THEME["red"], alpha=0.85, edgecolor=THEME["bg"],
                  label=f"Losses ({(r_mults < 0).sum()})")
        ax_r.axvline(0, color=THEME["text"], linewidth=0.6, alpha=0.6)
        ax_r.axvline(stats["avg_r"], color=THEME["yellow"],
                     linestyle="--", linewidth=1.2,
                     label=f"Avg = {stats['avg_r']:+.2f}R")
    ax_r.set_xlabel("R", color=THEME["text"])
    ax_r.set_ylabel("Frequency", color=THEME["text"])
    ax_r.legend(facecolor=THEME["panel"], edgecolor=THEME["grid"],
                labelcolor=THEME["text"], framealpha=0.8, fontsize=9)

    # Panel 2: Day of week (cols 2-3)
    ax_dow = fig.add_subplot(gs[3, 2:4])
    _style_axis(ax_dow, "P&L by Day of Week")
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]
    df = df.copy()
    df["dow"] = pd.to_datetime(df["entry_time"]).dt.day_name()
    dow_pnl = df.groupby("dow")["pnl"].sum().reindex(dow_order, fill_value=0.0)
    colors = [THEME["green"] if v > 0 else THEME["red"] for v in dow_pnl.values]
    bars = ax_dow.bar(range(7), dow_pnl.values, color=colors,
                      edgecolor=THEME["bg"], linewidth=0.5)
    ax_dow.set_xticks(range(7))
    ax_dow.set_xticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                           color=THEME["text"])
    ax_dow.set_ylabel("$", color=THEME["text"])
    ax_dow.axhline(0, color=THEME["text"], linewidth=0.6)
    for bar, val in zip(bars, dow_pnl.values):
        if val == 0:
            continue
        ax_dow.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"${val:,.0f}",
                    ha="center",
                    va="bottom" if val > 0 else "top",
                    color=THEME["text"], fontsize=8)

    # Panel 3: Monthly returns (cols 4-5)
    ax_mo = fig.add_subplot(gs[3, 4:6])
    _style_axis(ax_mo, "Monthly Returns (%)")
    df["month"] = pd.to_datetime(df["exit_time"]).dt.to_period("M")
    monthly = df.groupby("month")["pnl"].sum()
    monthly_pct = (monthly / cfg.initial_balance) * 100
    if len(monthly_pct) > 0:
        colors_mo = [THEME["green"] if v > 0 else THEME["red"]
                     for v in monthly_pct.values]
        bars_m = ax_mo.bar(range(len(monthly_pct)), monthly_pct.values,
                           color=colors_mo, edgecolor=THEME["bg"], linewidth=0.5)
        ax_mo.set_xticks(range(len(monthly_pct)))
        labels = [str(p)[2:] for p in monthly_pct.index]
        ax_mo.set_xticklabels(labels, rotation=45, ha="right",
                              color=THEME["text"], fontsize=8)
        ax_mo.axhline(0, color=THEME["text"], linewidth=0.6)
        ax_mo.set_ylabel("%", color=THEME["text"])
        for bar, val in zip(bars_m, monthly_pct.values):
            if abs(val) < 0.5:
                continue
            ax_mo.text(bar.get_x() + bar.get_width() / 2,
                       bar.get_height(),
                       f"{val:+.1f}",
                       ha="center",
                       va="bottom" if val > 0 else "top",
                       color=THEME["text"], fontsize=7)

    # ============ Footer note ============
    streak_str = (f"Win streak: {stats['max_win_streak']}    "
                  f"Loss streak: {stats['max_loss_streak']}    "
                  f"No-trade days: {stats['no_trade_days']}    "
                  f"Avg duration: {stats['avg_duration_h']:.1f}h    "
                  f"Avg win: ${stats['avg_win']:,.2f}    "
                  f"Avg loss: ${stats['avg_loss']:,.2f}")
    fig.text(0.5, 0.015, streak_str, color=THEME["text_dim"],
             fontsize=9, ha="center")

    if save_path:
        plt.savefig(save_path, dpi=130, bbox_inches="tight",
                    facecolor=THEME["bg"])
        print(f"Dashboard saved to {save_path}")
    if show:
        try:
            plt.show()
        except Exception as e:
            print(f"(plt.show() unavailable: {e})")


# ============================================================
# CLI
# ============================================================

def main() -> int:
    p = argparse.ArgumentParser(
        description="HatchFibBreakout edge-finder dashboard",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--yfinance")
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--interval", default="1h")

    p.add_argument("--risk", type=float, default=0.5)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--balance", type=float, default=10_000.0)
    p.add_argument("--setup-hour", type=int, default=22)

    p.add_argument("--save", help="Save dashboard PNG to this path")
    p.add_argument("--no-show", action="store_true",
                   help="Don't open interactive window")

    args = p.parse_args()

    if args.csv:
        df = load_csv(args.csv)
    else:
        end = args.end or pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        df = load_yfinance(args.yfinance, args.start, end, args.interval)

    cfg = Config(
        risk_pct=args.risk, rr=args.rr,
        initial_balance=args.balance,
        setup_hour_utc=args.setup_hour,
    )
    bt = Backtester(df, cfg)
    bt.run()

    build_dashboard(bt, save_path=args.save, show=not args.no_show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
