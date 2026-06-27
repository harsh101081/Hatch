#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Opening-Range-Breakout (ORB) intraday backtester for gold (XAU/USD CFD).

What it does
------------
For each trading day:
  1. Builds the "opening range" (OR) from the first `or_minutes` of the session.
  2. After the OR window, watches for a breakout beyond OR-high (long) or OR-low (short),
     with an optional buffer to filter false breaks.
  3. Enters on the breakout, sets a stop on the opposite OR edge (or ATR-based),
     and a take-profit at a configurable reward multiple (R).
  4. Exits on stop, target, or session close (whichever comes first).
  5. Sizes the position so each trade risks a fixed % of current equity.

It works out-of-the-box on synthetic data (no internet/data files needed) and
can also load your own intraday CSV.

DISCLAIMER: This is an educational research tool, NOT financial advice. CFDs are
high-risk leveraged products; most retail accounts lose money. Backtest results
do not guarantee future performance. Test ideas on a demo account first.
"""

from __future__ import annotations
import argparse
from dataclasses import dataclass, field
from datetime import time
import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    # Session window (exchange/broker local time, must match your data's tz)
    session_start: str = "13:30"   # e.g. NY open ~13:30 UTC
    session_end: str = "20:00"     # flat-by time; open trades closed here
    or_minutes: int = 30           # opening-range duration

    # Strategy
    direction: str = "both"        # "both" | "long" | "short"
    buffer_points: float = 0.30    # price buffer beyond OR edge to confirm break
    stop_type: str = "or"          # "or" (opposite edge) | "atr"
    atr_period: int = 14
    atr_mult: float = 1.5          # used when stop_type == "atr"
    target_r: float | None = 2.0   # reward multiple of risk; None => exit at EOD
    max_trades_per_day: int = 1    # set >1 to allow re-entries after a stop

    # Money management
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.01   # 1% of equity risked per trade
    contract_multiplier: float = 1.0   # P&L per 1.0 price move per unit (XAU ~ $1/oz)

    # Costs (in price points, one-way unless noted)
    spread_points: float = 0.30    # paid on entry and exit
    commission_per_trade: float = 0.0  # currency, round-turn

    # Fill assumption when a single bar touches both stop & target
    pessimistic_fills: bool = True  # True => assume stop hit first


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #
def load_csv(path: str) -> pd.DataFrame:
    """Load intraday OHLC CSV. Expected columns (case-insensitive):
    datetime, open, high, low, close  (volume optional)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "datetime" not in df.columns:
        # try common alternatives
        for alt in ("date", "timestamp", "time"):
            if alt in df.columns:
                df = df.rename(columns={alt: "datetime"})
                break
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    needed = {"open", "high", "low", "close"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    return df[["datetime", "open", "high", "low", "close"]]


def make_synthetic(days: int = 120, bar_minutes: int = 5, seed: int = 42,
                   cfg: Config | None = None) -> pd.DataFrame:
    """Generate reproducible synthetic intraday gold bars for a demo run.
    Produces a mild intraday momentum/trend behaviour so the ORB logic is exercised."""
    cfg = cfg or Config()
    rng = np.random.default_rng(seed)
    start_t = pd.to_datetime(cfg.session_start).time()
    end_t = pd.to_datetime(cfg.session_end).time()

    rows = []
    price = 2000.0  # starting gold price
    day0 = pd.Timestamp("2024-01-01")
    d = 0
    made = 0
    while made < days:
        day = day0 + pd.Timedelta(days=d)
        d += 1
        if day.weekday() >= 5:   # skip weekends
            continue
        made += 1
        # build the session bar timestamps
        t = pd.Timestamp.combine(day.date(), start_t)
        t_end = pd.Timestamp.combine(day.date(), end_t)
        # each day gets a random drift "regime" -> some days trend, some chop
        daily_drift = rng.normal(0, 0.06)        # points per bar
        vol = abs(rng.normal(0.9, 0.3))          # per-bar volatility (points)
        gap = rng.normal(0, 3.0)                 # overnight gap
        price += gap
        while t <= t_end:
            o = price
            step = rng.normal(daily_drift, vol)
            c = o + step
            hi = max(o, c) + abs(rng.normal(0, vol * 0.6))
            lo = min(o, c) - abs(rng.normal(0, vol * 0.6))
            rows.append((t, round(o, 2), round(hi, 2), round(lo, 2), round(c, 2)))
            price = c
            t += pd.Timedelta(minutes=bar_minutes)
    df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close"])
    return df


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def add_atr(df: pd.DataFrame, period: int) -> pd.DataFrame:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df = df.copy()
    df["atr"] = tr.rolling(period, min_periods=1).mean()
    return df


# --------------------------------------------------------------------------- #
# Backtest engine
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    day: object
    side: str
    entry_time: object
    entry: float
    stop: float
    target: float | None
    exit_time: object
    exit: float
    units: float
    pnl: float
    r_multiple: float
    reason: str


def _within(t: time, start: time, end: time) -> bool:
    return start <= t <= end


def backtest(df: pd.DataFrame, cfg: Config) -> tuple[list[Trade], pd.DataFrame]:
    df = add_atr(df, cfg.atr_period)
    df["date"] = df["datetime"].dt.date
    df["t"] = df["datetime"].dt.time
    s_start = pd.to_datetime(cfg.session_start).time()
    s_end = pd.to_datetime(cfg.session_end).time()
    or_end_dt = (pd.to_datetime(cfg.session_start) + pd.Timedelta(minutes=cfg.or_minutes)).time()

    equity = cfg.initial_equity
    trades: list[Trade] = []
    equity_curve = []

    for day, g in df.groupby("date", sort=True):
        g = g[g["t"].apply(lambda x: _within(x, s_start, s_end))]
        if len(g) < 3:
            continue
        or_bars = g[g["t"].apply(lambda x: s_start <= x < or_end_dt)]
        if or_bars.empty:
            continue
        or_high = or_bars["high"].max()
        or_low = or_bars["low"].min()
        or_range = or_high - or_low
        if or_range <= 0:
            continue

        post = g[g["t"].apply(lambda x: x >= or_end_dt)].reset_index(drop=True)
        if post.empty:
            continue

        trades_today = 0
        in_pos = False
        side = None
        entry = stop = target = units = np.nan
        entry_time = None

        for i in range(len(post)):
            bar = post.iloc[i]

            if not in_pos:
                if trades_today >= cfg.max_trades_per_day:
                    break
                long_trig = or_high + cfg.buffer_points
                short_trig = or_low - cfg.buffer_points
                go_long = cfg.direction in ("both", "long") and bar["high"] >= long_trig
                go_short = cfg.direction in ("both", "short") and bar["low"] <= short_trig

                # if both trigger in the same bar, take the one nearer the open
                if go_long and go_short:
                    if abs(bar["open"] - long_trig) <= abs(bar["open"] - short_trig):
                        go_short = False
                    else:
                        go_long = False

                if go_long or go_short:
                    side = "long" if go_long else "short"
                    raw_entry = long_trig if go_long else short_trig
                    # entry fill incl. spread (buy higher / sell lower)
                    entry = raw_entry + cfg.spread_points if go_long else raw_entry - cfg.spread_points
                    if cfg.stop_type == "atr":
                        atr = bar["atr"] if not np.isnan(bar["atr"]) else or_range
                        stop = entry - cfg.atr_mult * atr if go_long else entry + cfg.atr_mult * atr
                    else:  # opposite OR edge
                        stop = or_low if go_long else or_high
                    risk_per_unit = abs(entry - stop)
                    if risk_per_unit <= 0:
                        continue
                    if cfg.target_r is not None:
                        target = entry + cfg.target_r * risk_per_unit if go_long \
                            else entry - cfg.target_r * risk_per_unit
                    else:
                        target = None
                    cash_risk = equity * cfg.risk_per_trade
                    units = cash_risk / (risk_per_unit * cfg.contract_multiplier)
                    entry_time = bar["datetime"]
                    in_pos = True
                continue

            # ---- manage open position ----
            hit_stop = bar["low"] <= stop if side == "long" else bar["high"] >= stop
            hit_tgt = (target is not None) and (
                bar["high"] >= target if side == "long" else bar["low"] <= target)

            exit_price = None
            reason = None
            if hit_stop and hit_tgt:
                if cfg.pessimistic_fills:
                    exit_price, reason = stop, "stop"
                else:
                    exit_price, reason = target, "target"
            elif hit_stop:
                exit_price, reason = stop, "stop"
            elif hit_tgt:
                exit_price, reason = target, "target"
            elif i == len(post) - 1:
                exit_price, reason = bar["close"], "eod"

            if exit_price is not None:
                # spread on exit (sell lower / buy higher)
                fill = exit_price - cfg.spread_points if side == "long" else exit_price + cfg.spread_points
                gross = (fill - entry) * units * cfg.contract_multiplier if side == "long" \
                    else (entry - fill) * units * cfg.contract_multiplier
                pnl = gross - cfg.commission_per_trade
                risk_per_unit = abs(entry - stop)
                r_mult = pnl / (equity * cfg.risk_per_trade) if equity > 0 else 0.0
                equity += pnl
                trades.append(Trade(day, side, entry_time, round(entry, 2), round(stop, 2),
                                    None if target is None else round(target, 2),
                                    bar["datetime"], round(fill, 2), round(units, 4),
                                    round(pnl, 2), round(r_mult, 3), reason))
                equity_curve.append((bar["datetime"], equity))
                trades_today += 1
                in_pos = False

    ec = pd.DataFrame(equity_curve, columns=["datetime", "equity"])
    return trades, ec


# --------------------------------------------------------------------------- #
# Performance stats
# --------------------------------------------------------------------------- #
def performance(trades: list[Trade], ec: pd.DataFrame, cfg: Config) -> dict:
    if not trades:
        return {"trades": 0, "note": "No trades generated."}
    pnls = np.array([t.pnl for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_profit = wins.sum()
    gross_loss = -losses.sum()
    end_eq = cfg.initial_equity + pnls.sum()

    eq = ec["equity"].values
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = dd.min() if len(dd) else 0.0

    rmults = np.array([t.r_multiple for t in trades])
    return {
        "trades": len(trades),
        "wins": int((pnls > 0).sum()),
        "losses": int((pnls < 0).sum()),
        "win_rate_%": round(100 * (pnls > 0).mean(), 2),
        "net_pnl": round(pnls.sum(), 2),
        "return_%": round(100 * (end_eq / cfg.initial_equity - 1), 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "avg_win": round(wins.mean(), 2) if len(wins) else 0.0,
        "avg_loss": round(losses.mean(), 2) if len(losses) else 0.0,
        "expectancy_R": round(rmults.mean(), 3),
        "best_trade": round(pnls.max(), 2),
        "worst_trade": round(pnls.min(), 2),
        "max_drawdown_%": round(100 * max_dd, 2),
        "final_equity": round(end_eq, 2),
    }


def trades_to_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([t.__dict__ for t in trades])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Opening-Range-Breakout gold backtester")
    p.add_argument("--csv", help="Path to intraday OHLC CSV (datetime,open,high,low,close)")
    p.add_argument("--days", type=int, default=120, help="Synthetic days if no CSV")
    p.add_argument("--bar", type=int, default=5, help="Synthetic bar minutes")
    p.add_argument("--or-min", type=int, default=30, help="Opening-range minutes")
    p.add_argument("--target-r", type=float, default=2.0, help="Reward multiple; -1 = exit at EOD")
    p.add_argument("--direction", default="both", choices=["both", "long", "short"])
    p.add_argument("--risk", type=float, default=0.01, help="Risk per trade (fraction)")
    p.add_argument("--stop", default="or", choices=["or", "atr"])
    p.add_argument("--save-trades", help="Optional path to write trade log CSV")
    args = p.parse_args()

    cfg = Config(
        or_minutes=args.or_min,
        direction=args.direction,
        risk_per_trade=args.risk,
        stop_type=args.stop,
        target_r=None if args.target_r is not None and args.target_r < 0 else args.target_r,
    )

    if args.csv:
        df = load_csv(args.csv)
        src = f"CSV: {args.csv}"
    else:
        df = make_synthetic(days=args.days, bar_minutes=args.bar, cfg=cfg)
        src = f"SYNTHETIC ({args.days} days, {args.bar}m bars, seed=42)"

    trades, ec = backtest(df, cfg)
    stats = performance(trades, ec, cfg)

    print("=" * 64)
    print("OPENING-RANGE-BREAKOUT BACKTEST  -  GOLD (XAU/USD)")
    print("=" * 64)
    print(f"Data source     : {src}")
    print(f"Session         : {cfg.session_start}-{cfg.session_end} | OR={cfg.or_minutes}m")
    print(f"Direction       : {cfg.direction} | stop={cfg.stop_type} | target_R={cfg.target_r}")
    print(f"Risk/trade      : {cfg.risk_per_trade:.1%} | start equity={cfg.initial_equity:,.0f}")
    print("-" * 64)
    for k, v in stats.items():
        print(f"  {k:<18}: {v}")
    print("-" * 64)
    if trades:
        tf = trades_to_frame(trades)
        print("Last 5 trades:")
        cols = ["day", "side", "entry", "stop", "target", "exit", "pnl", "r_multiple", "reason"]
        print(tf[cols].tail(5).to_string(index=False))
        if args.save_trades:
            tf.to_csv(args.save_trades, index=False)
            print(f"\nTrade log written to {args.save_trades}")
    print("=" * 64)
    print("NOTE: Educational tool only. Not financial advice. Past performance "
          "does not guarantee future results.")


if __name__ == "__main__":
    main()
