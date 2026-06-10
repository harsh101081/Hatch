"""
HatchFibBreakout - Python Backtester
=====================================

Backtests the Previous-Day Fibonacci Breakout strategy on Gold (XAUUSD)
using historical OHLC data. Mirrors the logic of HatchFibBreakout.mq5.

Strategy:
    At each trading day's setup time (default 22:00 UTC):
      1. Read previous trading day's H/L
      2. Place Buy Stop @ Prev High (SL @ 0.9 fib, TP @ 1.2 fib for 1:2 RR)
      3. Place Sell Stop @ Prev Low (SL @ 0.1 fib, TP @ -0.2 fib for 1:2 RR)
      4. OCO: when one fills, cancel the other
      5. Position runs to TP or SL (or EOD)
      6. Lot size = (balance * risk%) / SL_distance_in_$

Usage:
    # Backtest using a CSV file (recommended):
    python backtest.py --csv data/xauusd_h1.csv

    # Or download via yfinance (~1-2yr H1 max):
    python backtest.py --yfinance GC=F --start 2023-01-01 --interval 1h

    # Override strategy params:
    python backtest.py --csv data/xauusd_h1.csv --risk 0.5 --rr 2 --balance 10000

    # Export trades & plot equity curve:
    python backtest.py --csv data/xauusd_h1.csv --export-trades trades.csv --plot equity.png

CSV format (header row required, timestamps in UTC):
    timestamp,open,high,low,close
    2023-01-02 00:00:00,1820.5,1822.3,1819.8,1821.4
    2023-01-02 01:00:00,1821.4,1823.1,1820.9,1822.7
    ...
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pandas as pd


# ============================================================
# Configuration
# ============================================================

@dataclass
class Config:
    risk_pct: float = 0.5             # % of balance risked per trade
    rr: float = 2.5                   # Risk:Reward (e.g. 2.5 = 1:2.5, TP at 1.25 fib)
    adjust_tp_for_rr: bool = True     # True: widen TP; False: tighten SL
    fib_buffer: float = 0.1           # Base fib buffer (0.1 -> 0.9/0.1 levels)
    setup_hour_utc: int = 22          # Hour to "place" pendings (UTC)
    initial_balance: float = 10_000.0
    contract_size: float = 100.0      # 100 oz/lot for XAUUSD
    min_lot: float = 0.01
    lot_step: float = 0.01
    commission_per_lot: float = 0.0   # $ per lot (round-trip)
    spread_price: float = 0.0         # extra price slippage per fill ($)

    # No-trade window (low-liquidity NY hours, default 12pm-6pm NY)
    use_no_trade_window: bool = True
    no_trade_start_ny_hour: int = 12  # NY local hour, inclusive
    no_trade_end_ny_hour: int = 18    # NY local hour, exclusive
    ny_timezone: str = "America/New_York"


# ============================================================
# Trade record
# ============================================================

@dataclass
class Trade:
    setup_date: pd.Timestamp
    direction: str = "none"  # 'buy' | 'sell' | 'none'
    entry_time: Optional[pd.Timestamp] = None
    entry_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    exit_price: float = 0.0
    exit_reason: str = "NotFilled"  # TP | SL | EOD | NotFilled | SL (same-bar)
    lot_size: float = 0.0
    pnl: float = 0.0
    balance_after: float = 0.0


# ============================================================
# Backtester
# ============================================================

class Backtester:
    def __init__(self, df: pd.DataFrame, cfg: Config):
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex (UTC).")
        for col in ("open", "high", "low", "close"):
            if col not in df.columns:
                raise ValueError(f"DataFrame missing required column: {col}")

        self.df = df.copy().sort_index()
        if self.df.index.tz is None:
            self.df.index = self.df.index.tz_localize("UTC")
        else:
            self.df.index = self.df.index.tz_convert("UTC")

        self.cfg = cfg
        self.balance = cfg.initial_balance
        self.peak_balance = cfg.initial_balance
        self.trades: List[Trade] = []

    # ---------- helpers ----------

    def _calc_lots(self, sl_distance: float) -> float:
        cfg = self.cfg
        if sl_distance <= 0:
            return 0.0
        risk_amount = self.balance * cfg.risk_pct / 100.0
        loss_per_lot = sl_distance * cfg.contract_size
        if loss_per_lot <= 0:
            return 0.0
        lots = risk_amount / loss_per_lot
        lots = math.floor(lots / cfg.lot_step) * cfg.lot_step
        if lots < cfg.min_lot:
            return 0.0
        return round(lots, 2)

    # ---------- main loop ----------

    def run(self) -> None:
        cfg = self.cfg
        df = self.df

        # Group bars into "trading days" starting at setup_hour_utc
        td = (df.index - pd.Timedelta(hours=cfg.setup_hour_utc)).floor("D")
        df = df.assign(trading_day=td)

        daily_hl = df.groupby("trading_day").agg(
            high=("high", "max"),
            low=("low", "min"),
        )
        days = sorted(daily_hl.index.unique())
        if len(days) < 2:
            raise ValueError("Need at least 2 trading days of data.")

        for i in range(1, len(days)):
            prev_day = days[i - 1]
            today = days[i]
            prev_high = float(daily_hl.loc[prev_day, "high"])
            prev_low = float(daily_hl.loc[prev_day, "low"])
            today_bars = df[df["trading_day"] == today].sort_index()
            if today_bars.empty:
                continue
            self._simulate_day(today, prev_high, prev_low, today_bars)

    def _simulate_day(
        self,
        day: pd.Timestamp,
        prev_high: float,
        prev_low: float,
        bars: pd.DataFrame,
    ) -> None:
        cfg = self.cfg
        rng = prev_high - prev_low
        if rng <= 0:
            return

        # Compute SL/TP buffers (in price units)
        sl_buf = cfg.fib_buffer * rng
        tp_buf = cfg.fib_buffer * rng
        if cfg.adjust_tp_for_rr:
            tp_buf = sl_buf * cfg.rr
        else:
            sl_buf = tp_buf / cfg.rr

        buy_entry = prev_high
        buy_sl = prev_high - sl_buf
        buy_tp = prev_high + tp_buf

        sell_entry = prev_low
        sell_sl = prev_low + sl_buf
        sell_tp = prev_low - tp_buf

        buy_lots = self._calc_lots(abs(buy_entry - buy_sl))
        sell_lots = self._calc_lots(abs(sell_entry - sell_sl))

        if buy_lots <= 0 and sell_lots <= 0:
            return

        buy_pending = buy_lots > 0
        sell_pending = sell_lots > 0
        open_trade: Optional[Trade] = None

        for ts, bar in bars.iterrows():
            h = float(bar["high"])
            l = float(bar["low"])
            o = float(bar["open"])

            # No-trade window check (NY local time)
            in_no_trade = False
            if cfg.use_no_trade_window:
                ny_hour = ts.tz_convert(cfg.ny_timezone).hour
                start = cfg.no_trade_start_ny_hour
                end = cfg.no_trade_end_ny_hour
                if start < end:
                    in_no_trade = start <= ny_hour < end
                else:
                    in_no_trade = ny_hour >= start or ny_hour < end

            # --- 1) Order fills ---
            if open_trade is None and (buy_pending or sell_pending):
                buy_hit = buy_pending and h >= buy_entry
                sell_hit = sell_pending and l <= sell_entry

                # Skip the trade if fill happens during no-trade window
                if (buy_hit or sell_hit) and in_no_trade:
                    # Cancel both pendings to enforce 1-trade-per-day
                    buy_pending = sell_pending = False
                    self.trades.append(
                        Trade(
                            setup_date=day,
                            direction="none",
                            exit_reason="Skipped: no-trade window",
                            balance_after=self.balance,
                        )
                    )
                    return

                if buy_hit and sell_hit:
                    # Same-bar both hits: tiebreaker = whichever level is closer to bar open
                    if abs(o - buy_entry) <= abs(o - sell_entry):
                        sell_hit = False
                    else:
                        buy_hit = False

                if buy_hit:
                    open_trade = Trade(
                        setup_date=day,
                        direction="buy",
                        entry_time=ts,
                        entry_price=buy_entry + cfg.spread_price,
                        sl=buy_sl,
                        tp=buy_tp,
                        lot_size=buy_lots,
                    )
                elif sell_hit:
                    open_trade = Trade(
                        setup_date=day,
                        direction="sell",
                        entry_time=ts,
                        entry_price=sell_entry - cfg.spread_price,
                        sl=sell_sl,
                        tp=sell_tp,
                        lot_size=sell_lots,
                    )

                if open_trade:
                    buy_pending = sell_pending = False  # OCO

            # --- 2) Exit checks (may happen on same bar as fill) ---
            if open_trade and open_trade.exit_time is None:
                if open_trade.direction == "buy":
                    hit_sl = l <= open_trade.sl
                    hit_tp = h >= open_trade.tp
                else:
                    hit_sl = h >= open_trade.sl
                    hit_tp = l <= open_trade.tp

                exit_price = None
                exit_reason = None
                if hit_sl and hit_tp:
                    # Pessimistic: assume SL hit first (worst-case for backtest)
                    exit_price = open_trade.sl
                    exit_reason = "SL (same-bar)"
                elif hit_sl:
                    exit_price = open_trade.sl
                    exit_reason = "SL"
                elif hit_tp:
                    exit_price = open_trade.tp
                    exit_reason = "TP"

                if exit_price is not None:
                    open_trade.exit_time = ts
                    open_trade.exit_price = exit_price
                    open_trade.exit_reason = exit_reason
                    self._close_trade(open_trade)
                    return  # 1 trade/day max

        # End of day handling
        if open_trade is None:
            self.trades.append(
                Trade(
                    setup_date=day,
                    direction="none",
                    exit_reason="NotFilled",
                    balance_after=self.balance,
                )
            )
        elif open_trade.exit_time is None:
            last_ts = bars.index[-1]
            open_trade.exit_time = last_ts
            open_trade.exit_price = float(bars.iloc[-1]["close"])
            open_trade.exit_reason = "EOD"
            self._close_trade(open_trade)

    def _close_trade(self, t: Trade) -> None:
        cfg = self.cfg
        sign = 1 if t.direction == "buy" else -1
        price_diff = (t.exit_price - t.entry_price) * sign
        pnl = price_diff * cfg.contract_size * t.lot_size
        pnl -= cfg.commission_per_lot * t.lot_size
        t.pnl = pnl
        self.balance += pnl
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        t.balance_after = self.balance
        self.trades.append(t)


# ============================================================
# Reporting
# ============================================================

def print_report(bt: Backtester) -> None:
    cfg = bt.cfg
    real = [t for t in bt.trades if t.direction != "none"]
    no_trade = sum(1 for t in bt.trades if t.direction == "none")

    if not real:
        print("No trades executed.")
        return

    wins = [t for t in real if t.pnl > 0]
    losses = [t for t in real if t.pnl < 0]
    breakeven = [t for t in real if t.pnl == 0]

    win_rate = (len(wins) / len(real) * 100) if real else 0.0
    total_pnl = sum(t.pnl for t in real)
    avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(t.pnl for t in losses) / len(losses)) if losses else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return_pct = (bt.balance - cfg.initial_balance) / cfg.initial_balance * 100

    # Max drawdown over trade-by-trade equity
    balances = [cfg.initial_balance]
    for t in bt.trades:
        if t.direction != "none":
            balances.append(t.balance_after)
    peak = balances[0]
    max_dd_pct = 0.0
    for b in balances:
        if b > peak:
            peak = b
        dd = (peak - b) / peak * 100 if peak > 0 else 0
        if dd > max_dd_pct:
            max_dd_pct = dd

    print("=" * 60)
    print("BACKTEST REPORT - HatchFibBreakout")
    print("=" * 60)
    print(f"Config:        risk={cfg.risk_pct}%  RR=1:{cfg.rr}  fib={cfg.fib_buffer}")
    print(f"Period:        {bt.df.index[0].date()} to {bt.df.index[-1].date()}")
    print(f"Bars analyzed: {len(bt.df):,}")
    print("-" * 60)
    print(f"Initial balance: ${cfg.initial_balance:>12,.2f}")
    print(f"Final balance:   ${bt.balance:>12,.2f}")
    print(f"Total P&L:       ${total_pnl:>12,.2f}  ({return_pct:+.2f}%)")
    print(f"Max Drawdown:    {max_dd_pct:>12.2f}%")
    print("-" * 60)
    print(f"Total trades:    {len(real):>12d}")
    print(f"  Wins:          {len(wins):>12d}  ({win_rate:.1f}%)")
    print(f"  Losses:        {len(losses):>12d}")
    if breakeven:
        print(f"  Breakeven:     {len(breakeven):>12d}")
    print(f"  No-trade days: {no_trade:>12d}")
    print(f"Avg win:         ${avg_win:>12,.2f}")
    print(f"Avg loss:        ${avg_loss:>12,.2f}")
    print(f"Profit factor:   {profit_factor:>12.2f}")
    print("=" * 60)


def export_trades_csv(bt: Backtester, path: str) -> None:
    rows = []
    for t in bt.trades:
        rows.append({
            "setup_date": t.setup_date,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "sl": t.sl,
            "tp": t.tp,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "lot_size": t.lot_size,
            "pnl": t.pnl,
            "balance_after": t.balance_after,
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"Trades exported to: {path}")


def plot_equity_curve(bt: Backtester, path: Optional[str] = None) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg" if path else matplotlib.get_backend())
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping plot.  pip install matplotlib")
        return

    cfg = bt.cfg
    times: List[pd.Timestamp] = []
    balances: List[float] = []
    running = cfg.initial_balance
    for t in bt.trades:
        if t.direction == "none":
            continue
        if t.exit_time is None:
            continue
        running = t.balance_after
        times.append(pd.Timestamp(t.exit_time))
        balances.append(running)

    if not times:
        print("No trades to plot.")
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(times, balances, linewidth=1.5)
    ax.axhline(cfg.initial_balance, color="gray", linestyle="--", alpha=0.5,
               label=f"Initial ${cfg.initial_balance:,.0f}")
    ax.set_title("Equity Curve - HatchFibBreakout")
    ax.set_xlabel("Date")
    ax.set_ylabel("Balance ($)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    plt.tight_layout()
    if path:
        plt.savefig(path, dpi=120)
        print(f"Equity curve saved to: {path}")
    else:
        plt.show()


# ============================================================
# Data loaders
# ============================================================

def load_csv(path: str, tz: str = "UTC") -> pd.DataFrame:
    df = pd.read_csv(path)
    ts_col = None
    for cand in ("timestamp", "time", "datetime", "date"):
        if cand in (c.lower() for c in df.columns):
            ts_col = next(c for c in df.columns if c.lower() == cand)
            break
    if ts_col is None:
        ts_col = df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.set_index(ts_col)
    if df.index.tz is None:
        df.index = df.index.tz_localize(tz)
    df.columns = [c.lower() for c in df.columns]
    needed = ["open", "high", "low", "close"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    return df[needed]


def load_yfinance(symbol: str, start: str, end: str, interval: str = "1h") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("yfinance not installed.  pip install yfinance")
    print(f"Downloading {symbol} {interval} from {start} to {end}...")
    df = yf.download(
        symbol, start=start, end=end, interval=interval,
        progress=False, auto_adjust=False,
    )
    if df.empty:
        raise SystemExit("yfinance returned empty data. "
                         "(Note: 1h interval is limited to ~730d, 5m/15m to ~60d.)")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    needed = ["open", "high", "low", "close"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise SystemExit(f"yfinance data missing columns: {missing}")
    return df[needed]


# ============================================================
# CLI
# ============================================================

def main() -> int:
    p = argparse.ArgumentParser(
        description="HatchFibBreakout Python backtester",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="Path to OHLC CSV file (UTC timestamps)")
    src.add_argument("--yfinance", metavar="SYMBOL",
                     help="yfinance symbol (e.g. GC=F, GLD, XAUUSD=X)")

    p.add_argument("--start", default="2023-01-01",
                   help="Start date YYYY-MM-DD (yfinance only)")
    p.add_argument("--end", default=None,
                   help="End date YYYY-MM-DD (yfinance only; default = today)")
    p.add_argument("--interval", default="1h",
                   help="Bar interval for yfinance (1h, 30m, 15m, 5m)")

    p.add_argument("--risk", type=float, default=0.5,
                   help="Risk %% per trade")
    p.add_argument("--rr", type=float, default=2.5,
                   help="Risk:Reward ratio (default 2.5 -> TP at 1.25 fib)")
    p.add_argument("--balance", type=float, default=10_000.0,
                   help="Initial balance $")
    p.add_argument("--setup-hour", type=int, default=22,
                   help="Setup hour (UTC)")
    p.add_argument("--no-adjust-tp", action="store_true",
                   help="Tighten SL instead of widening TP for RR")
    p.add_argument("--contract-size", type=float, default=100.0,
                   help="Contract size (XAUUSD=100, EURUSD=100000)")
    p.add_argument("--commission", type=float, default=0.0,
                   help="Commission $/lot per round-trip")

    p.add_argument("--no-no-trade-window", action="store_true",
                   help="Disable the no-trade window filter (allow trades any hour)")
    p.add_argument("--no-trade-start-ny", type=int, default=12,
                   help="No-trade window start (NY local hour, inclusive)")
    p.add_argument("--no-trade-end-ny", type=int, default=18,
                   help="No-trade window end (NY local hour, exclusive)")

    p.add_argument("--export-trades", help="Export trade log to CSV path")
    p.add_argument("--plot", help="Save equity curve PNG to this path")

    args = p.parse_args()

    if args.csv:
        df = load_csv(args.csv)
    else:
        end = args.end or datetime.utcnow().strftime("%Y-%m-%d")
        df = load_yfinance(args.yfinance, args.start, end, args.interval)

    print(f"Loaded {len(df):,} bars from {df.index[0]} to {df.index[-1]}")

    cfg = Config(
        risk_pct=args.risk,
        rr=args.rr,
        adjust_tp_for_rr=not args.no_adjust_tp,
        setup_hour_utc=args.setup_hour,
        initial_balance=args.balance,
        contract_size=args.contract_size,
        commission_per_lot=args.commission,
        use_no_trade_window=not args.no_no_trade_window,
        no_trade_start_ny_hour=args.no_trade_start_ny,
        no_trade_end_ny_hour=args.no_trade_end_ny,
    )

    bt = Backtester(df, cfg)
    bt.run()
    print_report(bt)

    if args.export_trades:
        export_trades_csv(bt, args.export_trades)
    if args.plot:
        plot_equity_curve(bt, args.plot)

    return 0


if __name__ == "__main__":
    sys.exit(main())
