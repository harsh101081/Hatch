"""
Parameter optimizer for HatchFibBreakout.

Two modes:
  --mode grid           : Full grid search over (risk%, RR). Plots a heatmap.
  --mode walkforward    : Walk-forward analysis - optimize on each in-sample
                          window then test out-of-sample. Honest results.

Usage:
    python optimizer.py --csv data.csv --mode grid --heatmap heatmap.png
    python optimizer.py --csv data.csv --mode walkforward --windows 4
"""

from __future__ import annotations

import argparse
import itertools
import sys
from typing import Iterable

import numpy as np
import pandas as pd

from backtest import Backtester, Config, load_csv, load_yfinance
from analytics import compute_stats


# ============================================================
# Grid search
# ============================================================

def grid_search(
    df: pd.DataFrame,
    risk_grid: Iterable[float],
    rr_grid: Iterable[float],
    initial_balance: float = 10_000.0,
    setup_hour: int = 22,
) -> pd.DataFrame:
    """Run backtest on every (risk, RR) combination. Return a DataFrame."""
    rows = []
    combos = list(itertools.product(risk_grid, rr_grid))
    for i, (risk, rr) in enumerate(combos, start=1):
        cfg = Config(
            risk_pct=float(risk),
            rr=float(rr),
            initial_balance=initial_balance,
            setup_hour_utc=setup_hour,
        )
        try:
            bt = Backtester(df, cfg)
            bt.run()
            stats = compute_stats(bt.trades, cfg.initial_balance)
        except Exception as e:
            stats = {"trades": 0, "error": str(e)}
        row = {"risk": float(risk), "rr": float(rr), **stats}
        rows.append(row)
        print(f"  [{i}/{len(combos)}] risk={risk:.2f}%  RR=1:{rr:.2f}  "
              f"trades={stats.get('trades', 0)}  "
              f"PF={stats.get('profit_factor', 0):.2f}  "
              f"return={stats.get('return_pct', 0):+.2f}%  "
              f"DD={stats.get('max_dd_pct', 0):.2f}%")
    return pd.DataFrame(rows)


# ============================================================
# Walk-forward
# ============================================================

def walk_forward(
    df: pd.DataFrame,
    risk_grid: Iterable[float],
    rr_grid: Iterable[float],
    n_windows: int = 4,
    is_ratio: float = 0.7,
    initial_balance: float = 10_000.0,
    setup_hour: int = 22,
    score_metric: str = "profit_factor",
) -> pd.DataFrame:
    """
    Walk-forward optimization:

      |---------- window 1 ----------|---------- window 2 ----------|...
      |  IS (70%)        |  OOS (30%)|  IS (70%)        |  OOS (30%)|

    For each window:
      1. Find best (risk, RR) on the in-sample slice
      2. Apply those params to the out-of-sample slice
      3. Aggregate the out-of-sample results = honest performance estimate
    """
    total_seconds = (df.index[-1] - df.index[0]).total_seconds()
    win_seconds = total_seconds / n_windows
    rows = []

    for w in range(n_windows):
        win_start = df.index[0] + pd.Timedelta(seconds=w * win_seconds)
        win_end = win_start + pd.Timedelta(seconds=win_seconds)
        is_end = win_start + pd.Timedelta(seconds=win_seconds * is_ratio)

        is_data = df[(df.index >= win_start) & (df.index < is_end)]
        oos_data = df[(df.index >= is_end) & (df.index < win_end)]

        if len(is_data) < 100 or len(oos_data) < 50:
            print(f"Window {w+1}: skipping (insufficient data: IS={len(is_data)}, OOS={len(oos_data)})")
            continue

        # Optimize on IS
        best_score = -np.inf
        best_params = None
        for risk, rr in itertools.product(risk_grid, rr_grid):
            cfg = Config(
                risk_pct=float(risk),
                rr=float(rr),
                initial_balance=initial_balance,
                setup_hour_utc=setup_hour,
            )
            try:
                bt = Backtester(is_data, cfg)
                bt.run()
                s = compute_stats(bt.trades, cfg.initial_balance)
            except Exception:
                continue
            if s.get("trades", 0) < 5:
                continue
            score = s.get(score_metric, 0)
            if score > best_score:
                best_score = score
                best_params = (float(risk), float(rr), s)

        if best_params is None:
            print(f"Window {w+1}: no viable IS parameters")
            continue

        risk_b, rr_b, is_stats = best_params

        # Test on OOS
        cfg = Config(
            risk_pct=risk_b, rr=rr_b,
            initial_balance=initial_balance,
            setup_hour_utc=setup_hour,
        )
        bt_oos = Backtester(oos_data, cfg)
        bt_oos.run()
        oos = compute_stats(bt_oos.trades, cfg.initial_balance)

        rows.append({
            "window": w + 1,
            "is_period": f"{is_data.index[0].date()} -> {is_data.index[-1].date()}",
            "oos_period": f"{oos_data.index[0].date()} -> {oos_data.index[-1].date()}",
            "best_risk": risk_b,
            "best_rr": rr_b,
            "is_pf": is_stats.get("profit_factor", 0),
            "is_winrate": is_stats.get("win_rate", 0),
            "is_trades": is_stats.get("trades", 0),
            "oos_pf": oos.get("profit_factor", 0),
            "oos_winrate": oos.get("win_rate", 0),
            "oos_trades": oos.get("trades", 0),
            "oos_return_pct": oos.get("return_pct", 0),
            "oos_max_dd": oos.get("max_dd_pct", 0),
        })

    return pd.DataFrame(rows)


# ============================================================
# Plotting
# ============================================================

def plot_heatmap(results_df: pd.DataFrame, path: str, metric: str = "profit_factor"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pivot = results_df.pivot(index="rr", columns="risk", values=metric)

    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto",
                   origin="lower", interpolation="nearest")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f"{x:.2f}" for x in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"{x:.2f}" for x in pivot.index])
    ax.set_xlabel("Risk %")
    ax.set_ylabel("Risk:Reward (1:X)")
    ax.set_title(f"{metric.replace('_', ' ').title()} - parameter heatmap")

    # Cell annotations
    for i, row in enumerate(pivot.values):
        for j, val in enumerate(row):
            if pd.isna(val):
                continue
            ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                    color="black", fontsize=8, fontweight="bold")

    plt.colorbar(im, label=metric)
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    print(f"Heatmap saved to {path}")


# ============================================================
# CLI
# ============================================================

def main() -> int:
    p = argparse.ArgumentParser(
        description="HatchFibBreakout parameter optimizer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="Path to OHLC CSV")
    src.add_argument("--yfinance", metavar="SYMBOL", help="yfinance symbol")

    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--interval", default="1h")

    p.add_argument("--mode", choices=["grid", "walkforward"], default="grid")
    p.add_argument("--metric", default="profit_factor",
                   choices=["profit_factor", "return_pct", "sharpe", "expectancy"],
                   help="Metric to optimize / display in heatmap")

    # Sweep ranges
    p.add_argument("--risk-min", type=float, default=0.25)
    p.add_argument("--risk-max", type=float, default=2.0)
    p.add_argument("--risk-step", type=float, default=0.25)
    p.add_argument("--rr-min", type=float, default=1.0)
    p.add_argument("--rr-max", type=float, default=3.0)
    p.add_argument("--rr-step", type=float, default=0.5)

    p.add_argument("--balance", type=float, default=10_000)
    p.add_argument("--setup-hour", type=int, default=22)
    p.add_argument("--windows", type=int, default=4,
                   help="Walk-forward windows (mode=walkforward only)")
    p.add_argument("--is-ratio", type=float, default=0.7,
                   help="In-sample fraction per window (0-1)")

    p.add_argument("--export", help="Save full results to CSV")
    p.add_argument("--heatmap", help="Save heatmap PNG (grid mode only)")

    args = p.parse_args()

    if args.csv:
        df = load_csv(args.csv)
    else:
        end = args.end or pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        df = load_yfinance(args.yfinance, args.start, end, args.interval)

    print(f"Loaded {len(df):,} bars from {df.index[0]} to {df.index[-1]}")

    risk_grid = np.round(
        np.arange(args.risk_min, args.risk_max + args.risk_step / 2, args.risk_step),
        3,
    )
    rr_grid = np.round(
        np.arange(args.rr_min, args.rr_max + args.rr_step / 2, args.rr_step),
        3,
    )

    if args.mode == "grid":
        print(f"\nGrid search: {len(risk_grid)} risk x {len(rr_grid)} RR = {len(risk_grid) * len(rr_grid)} combos\n")
        results = grid_search(
            df, risk_grid, rr_grid,
            initial_balance=args.balance,
            setup_hour=args.setup_hour,
        )
        # Top 10
        print("\n" + "=" * 80)
        print(f"TOP 10 BY {args.metric.upper()}")
        print("=" * 80)
        cols = ["risk", "rr", "trades", "win_rate", "profit_factor",
                "return_pct", "max_dd_pct", "sharpe"]
        cols = [c for c in cols if c in results.columns]
        top = results.sort_values(args.metric, ascending=False).head(10)
        print(top[cols].to_string(index=False))

        if args.heatmap:
            plot_heatmap(results, args.heatmap, metric=args.metric)
        if args.export:
            results.to_csv(args.export, index=False)
            print(f"\nFull results saved to {args.export}")

    else:  # walkforward
        print(f"\nWalk-forward analysis: {args.windows} windows, IS ratio = {args.is_ratio}\n")
        results = walk_forward(
            df, risk_grid, rr_grid,
            n_windows=args.windows,
            is_ratio=args.is_ratio,
            initial_balance=args.balance,
            setup_hour=args.setup_hour,
            score_metric=args.metric,
        )
        if results.empty:
            print("No usable windows.")
            return 1

        print("\n" + "=" * 80)
        print("WALK-FORWARD RESULTS")
        print("=" * 80)
        print(results.to_string(index=False))

        # Aggregate OOS performance
        print("\n" + "-" * 80)
        print("OUT-OF-SAMPLE AGGREGATE")
        print("-" * 80)
        print(f"Mean OOS profit factor: {results['oos_pf'].mean():.2f}")
        print(f"Mean OOS win rate:      {results['oos_winrate'].mean():.1f}%")
        print(f"Mean OOS return:        {results['oos_return_pct'].mean():+.2f}%")
        print(f"Mean OOS max DD:        {results['oos_max_dd'].mean():.2f}%")
        print(f"Total OOS trades:       {results['oos_trades'].sum()}")

        if args.export:
            results.to_csv(args.export, index=False)
            print(f"\nResults saved to {args.export}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
