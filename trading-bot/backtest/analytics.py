"""
Shared analytics for HatchFibBreakout backtest results.

Functions here take a list of Trade objects (and a config) and return
DataFrames / dict-of-stats consumable by:
  - print_report   (CLI text summary)
  - visualize.py   (edge-finder dashboard)
  - optimizer.py   (parameter sweep scoring)
  - propfirm.py    (rule simulator)
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from backtest import Trade


def trades_to_df(trades: List[Trade], contract_size: float = 100.0) -> pd.DataFrame:
    """Convert list of Trade -> DataFrame, computing R-multiples and durations.

    Only includes trades that actually filled and exited (excludes 'none' and
    NotFilled rows).
    """
    rows = []
    for t in trades:
        if t.direction == "none" or t.exit_time is None:
            continue
        sl_dist = abs(t.entry_price - t.sl)
        risk_dollars = sl_dist * contract_size * t.lot_size
        r = (t.pnl / risk_dollars) if risk_dollars > 0 else 0.0
        try:
            duration = pd.Timestamp(t.exit_time) - pd.Timestamp(t.entry_time)
        except Exception:
            duration = pd.Timedelta(0)
        rows.append({
            "setup_date": pd.Timestamp(t.setup_date),
            "entry_time": pd.Timestamp(t.entry_time),
            "exit_time": pd.Timestamp(t.exit_time),
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "lot_size": t.lot_size,
            "pnl": t.pnl,
            "balance_after": t.balance_after,
            "r_multiple": r,
            "duration_h": duration.total_seconds() / 3600.0,
        })
    return pd.DataFrame(rows)


def compute_streaks(wins_mask):
    """Return (max_win_streak, max_loss_streak)."""
    max_w = max_l = cur = 0
    cur_sign = 0
    for w in wins_mask:
        sign = 1 if w else -1
        if sign == cur_sign:
            cur += 1
        else:
            cur_sign = sign
            cur = 1
        if sign > 0:
            max_w = max(max_w, cur)
        else:
            max_l = max(max_l, cur)
    return max_w, max_l


def compute_drawdown(balances):
    """Return (peaks, dd_pct) arrays where dd_pct is negative below peak."""
    balances = np.asarray(balances, dtype=float)
    peaks = np.maximum.accumulate(balances)
    dd_pct = np.where(peaks > 0, (balances - peaks) / peaks * 100, 0.0)
    return peaks, dd_pct


def compute_stats(
    trades: List[Trade],
    initial_balance: float,
    contract_size: float = 100.0,
) -> Dict[str, Any]:
    """Compute the full stats dict used by reports & dashboards."""
    df = trades_to_df(trades, contract_size=contract_size)
    no_trade_days = sum(1 for t in trades if t.direction == "none")

    if df.empty:
        return {
            "trades": 0,
            "no_trade_days": no_trade_days,
            "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "return_pct": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0,
            "largest_win": 0.0, "largest_loss": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0,
            "max_dd_pct": 0.0, "max_dd_dollars": 0.0,
            "sharpe": 0.0, "sortino": 0.0,
            "max_win_streak": 0, "max_loss_streak": 0,
            "avg_r": 0.0, "best_r": 0.0, "worst_r": 0.0,
            "avg_duration_h": 0.0,
            "final_balance": initial_balance,
        }

    pnl = df["pnl"].values
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    # Equity curve including starting balance
    equity = np.concatenate([[initial_balance], df["balance_after"].values])
    _, dd = compute_drawdown(equity)
    max_dd_pct = abs(dd.min()) if len(dd) else 0.0
    max_dd_dollars = float((np.maximum.accumulate(equity) - equity).max())

    # Daily aggregated returns -> Sharpe / Sortino (annualized)
    daily = (
        df.set_index(pd.to_datetime(df["exit_time"]))
          .resample("1D")["pnl"]
          .sum()
    )
    daily_ret = daily / initial_balance
    sharpe = (
        (daily_ret.mean() / daily_ret.std()) * np.sqrt(252)
        if daily_ret.std() > 0 else 0.0
    )
    downside = daily_ret[daily_ret < 0]
    sortino = (
        (daily_ret.mean() / downside.std()) * np.sqrt(252)
        if len(downside) > 1 and downside.std() > 0 else 0.0
    )

    max_win_streak, max_loss_streak = compute_streaks(pnl > 0)

    gross_win = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    return {
        "trades": int(len(df)),
        "no_trade_days": no_trade_days,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": float(len(wins) / len(df) * 100),
        "total_pnl": float(pnl.sum()),
        "return_pct": float((df["balance_after"].iloc[-1] - initial_balance) / initial_balance * 100),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "largest_win": float(wins.max()) if len(wins) else 0.0,
        "largest_loss": float(losses.min()) if len(losses) else 0.0,
        "profit_factor": profit_factor,
        "expectancy": float(pnl.mean()),
        "max_dd_pct": float(max_dd_pct),
        "max_dd_dollars": float(max_dd_dollars),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_win_streak": int(max_win_streak),
        "max_loss_streak": int(max_loss_streak),
        "avg_r": float(df["r_multiple"].mean()),
        "best_r": float(df["r_multiple"].max()),
        "worst_r": float(df["r_multiple"].min()),
        "avg_duration_h": float(df["duration_h"].mean()),
        "final_balance": float(df["balance_after"].iloc[-1]),
    }
