"""
Prop-firm rule simulator for HatchFibBreakout.

Models common prop firm evaluation rules:
  - Profit target (% of initial balance)
  - Max daily drawdown (% of start-of-day balance)
  - Max overall drawdown (% of initial balance, static OR trailing)
  - Min trading days
  - Max evaluation days
  - Consistency rule (single day cannot be > X% of total profit)

Outputs verdict (PASS / FAIL: <reason>) and a day-by-day equity log.

Includes Monte Carlo mode: shuffles the trade order N times to estimate
the % chance of passing the evaluation - this catches "lucky sequence"
issues where a strategy only passes because trades happened in a
favorable order.

Usage:
    # Single deterministic simulation
    python propfirm.py --csv data.csv --balance 100000 --profit-target 8 \\
                       --max-daily-dd 5 --max-total-dd 10 --max-days 30

    # Monte Carlo (10k shuffles to estimate pass probability)
    python propfirm.py --csv data.csv --monte-carlo 10000

    # FTMO 2-step preset
    python propfirm.py --csv data.csv --preset ftmo
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

from backtest import Backtester, Config, Trade, load_csv, load_yfinance


# ============================================================
# Rule definitions
# ============================================================

@dataclass
class PropFirmRules:
    initial_balance: float = 100_000.0
    profit_target_pct: float = 8.0
    max_daily_dd_pct: float = 5.0
    max_total_dd_pct: float = 10.0
    trailing_dd: bool = False
    min_trading_days: int = 5
    max_trading_days: int = 30   # 0 = unlimited
    consistency_pct: float = 0.0  # 0 = disabled. else: max % of profit from a single day


PRESETS: Dict[str, Dict[str, Any]] = {
    "ftmo": dict(
        profit_target_pct=8.0, max_daily_dd_pct=5.0,
        max_total_dd_pct=10.0, trailing_dd=False,
        min_trading_days=4, max_trading_days=30,
    ),
    "ftmo_verification": dict(
        profit_target_pct=5.0, max_daily_dd_pct=5.0,
        max_total_dd_pct=10.0, trailing_dd=False,
        min_trading_days=4, max_trading_days=60,
    ),
    "myforexfunds": dict(
        profit_target_pct=8.0, max_daily_dd_pct=5.0,
        max_total_dd_pct=12.0, trailing_dd=False,
        min_trading_days=5, max_trading_days=0,
    ),
    "the5ers": dict(
        profit_target_pct=6.0, max_daily_dd_pct=4.0,
        max_total_dd_pct=4.0, trailing_dd=True,
        min_trading_days=0, max_trading_days=0,
    ),
    "topstep": dict(
        profit_target_pct=6.0, max_daily_dd_pct=3.0,
        max_total_dd_pct=4.0, trailing_dd=True,
        min_trading_days=5, max_trading_days=0,
    ),
}


# ============================================================
# Simulation
# ============================================================

def simulate_propfirm(
    trades: List[Trade],
    rules: PropFirmRules,
) -> Dict[str, Any]:
    """Simulate the day-by-day evaluation given trade results."""
    real_trades = [t for t in trades if t.direction != "none" and t.exit_time is not None]
    if not real_trades:
        return {
            "verdict": "FAIL: no completed trades",
            "passed": False,
            "final_balance": rules.initial_balance,
            "peak_balance": rules.initial_balance,
            "trading_days": 0,
            "daily_log": [],
        }

    # Group P&L by day (use exit_time for the booking day)
    df = pd.DataFrame([{
        "day": pd.Timestamp(t.exit_time).normalize().date(),
        "pnl": t.pnl,
    } for t in real_trades])
    daily_pnl = df.groupby("day")["pnl"].sum().sort_index()

    balance = rules.initial_balance
    peak_balance = rules.initial_balance
    trading_days = 0
    log: List[Dict[str, Any]] = []
    verdict = None
    passed = False

    for day, day_pnl in daily_pnl.items():
        start_balance = balance
        balance += day_pnl
        trading_days += 1

        # Daily DD: loss within the day vs start-of-day balance
        daily_dd_pct = max(0.0, (start_balance - balance) / start_balance * 100)

        # Total DD: drop from reference (initial or peak)
        ref = peak_balance if rules.trailing_dd else rules.initial_balance
        total_dd_pct = max(0.0, (ref - balance) / ref * 100)

        # Update peak after computing trailing-DD reference for the day
        if balance > peak_balance:
            peak_balance = balance

        return_pct = (balance - rules.initial_balance) / rules.initial_balance * 100

        entry = {
            "day": day,
            "start_balance": start_balance,
            "pnl": day_pnl,
            "balance": balance,
            "return_pct": return_pct,
            "daily_dd_pct": daily_dd_pct,
            "total_dd_pct": total_dd_pct,
            "violation": None,
        }

        # 1) Daily DD violation (hard fail)
        if daily_dd_pct > rules.max_daily_dd_pct:
            entry["violation"] = f"DAILY_DD ({daily_dd_pct:.2f}% > {rules.max_daily_dd_pct}%)"
            log.append(entry)
            verdict = (f"FAIL: Max Daily DD breached on {day} "
                       f"({daily_dd_pct:.2f}% > {rules.max_daily_dd_pct}%)")
            break

        # 2) Total DD violation (hard fail)
        if total_dd_pct > rules.max_total_dd_pct:
            entry["violation"] = f"TOTAL_DD ({total_dd_pct:.2f}% > {rules.max_total_dd_pct}%)"
            log.append(entry)
            verdict = (f"FAIL: Max Total DD breached on {day} "
                       f"({total_dd_pct:.2f}% > {rules.max_total_dd_pct}%)")
            break

        log.append(entry)

        # 3) Profit target reached?
        if return_pct >= rules.profit_target_pct and trading_days >= rules.min_trading_days:
            # Consistency check (if enabled)
            if rules.consistency_pct > 0:
                profit_so_far = balance - rules.initial_balance
                wins = [e["pnl"] for e in log if e["pnl"] > 0]
                best_day = max(wins) if wins else 0
                if profit_so_far > 0 and (best_day / profit_so_far * 100) > rules.consistency_pct:
                    pct = best_day / profit_so_far * 100
                    verdict = (f"FAIL: Consistency rule on {day} "
                               f"(best day {pct:.1f}% > {rules.consistency_pct}%)")
                    break
            verdict = f"PASS: target hit on day {trading_days} ({day}) at {return_pct:+.2f}%"
            passed = True
            break

        # 4) Max evaluation days exhausted
        if rules.max_trading_days > 0 and trading_days >= rules.max_trading_days:
            verdict = (f"FAIL: ran out of days. {return_pct:+.2f}% / "
                       f"{rules.profit_target_pct}% target after {trading_days} days")
            break

    if verdict is None:
        return_pct = (balance - rules.initial_balance) / rules.initial_balance * 100
        if return_pct >= rules.profit_target_pct and trading_days >= rules.min_trading_days:
            verdict = f"PASS: data ended at {return_pct:+.2f}% (>= target)"
            passed = True
        else:
            verdict = (f"INCOMPLETE: data ended. {return_pct:+.2f}% / "
                       f"{rules.profit_target_pct}% target after {trading_days} days")

    return {
        "verdict": verdict,
        "passed": passed,
        "final_balance": balance,
        "peak_balance": peak_balance,
        "trading_days": trading_days,
        "max_total_dd": max((e["total_dd_pct"] for e in log), default=0.0),
        "max_daily_dd": max((e["daily_dd_pct"] for e in log), default=0.0),
        "daily_log": log,
    }


def monte_carlo(
    trades: List[Trade],
    rules: PropFirmRules,
    n_runs: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Shuffle trade order N times and re-run the prop firm sim each time.
    Estimates the % chance of passing across plausible orderings.
    """
    real_trades = [t for t in trades if t.direction != "none" and t.exit_time is not None]
    if len(real_trades) < 5:
        return {"runs": 0, "pass_rate": 0.0, "fail_reasons": {}}

    rng = random.Random(seed)
    pass_count = 0
    fail_reasons: Dict[str, int] = {}
    final_returns: List[float] = []

    for _ in range(n_runs):
        # Shuffle a copy and rebuild balance_after sequence so day-by-day sim works
        shuffled = real_trades.copy()
        rng.shuffle(shuffled)
        bal = rules.initial_balance
        for t in shuffled:
            bal += t.pnl
            t.balance_after = bal

        result = simulate_propfirm(shuffled, rules)
        if result["passed"]:
            pass_count += 1
        else:
            reason = result["verdict"].split(":")[1].strip().split("(")[0].strip() \
                if ":" in result["verdict"] else result["verdict"]
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
        final_returns.append((result["final_balance"] - rules.initial_balance) / rules.initial_balance * 100)

    pass_rate = pass_count / n_runs * 100
    return {
        "runs": n_runs,
        "pass_count": pass_count,
        "pass_rate": pass_rate,
        "fail_reasons": fail_reasons,
        "median_return_pct": float(pd.Series(final_returns).median()),
        "p5_return_pct": float(pd.Series(final_returns).quantile(0.05)),
        "p95_return_pct": float(pd.Series(final_returns).quantile(0.95)),
    }


# ============================================================
# Reporting
# ============================================================

def print_report(result: Dict[str, Any], rules: PropFirmRules) -> None:
    print("=" * 70)
    print("PROP FIRM EVALUATION")
    print("=" * 70)
    print(f"Rules:            target={rules.profit_target_pct}%  "
          f"daily_dd={rules.max_daily_dd_pct}%  "
          f"total_dd={rules.max_total_dd_pct}% ({'trailing' if rules.trailing_dd else 'static'})")
    print(f"                  min_days={rules.min_trading_days}  max_days={rules.max_trading_days}")
    if rules.consistency_pct > 0:
        print(f"                  consistency<={rules.consistency_pct}% of profit per day")
    print("-" * 70)
    print(f"Verdict:          {result['verdict']}")
    print(f"Final balance:    ${result['final_balance']:,.2f}")
    print(f"Peak balance:     ${result['peak_balance']:,.2f}")
    print(f"Trading days:     {result['trading_days']}")
    print(f"Max daily DD:     {result.get('max_daily_dd', 0):.2f}%")
    print(f"Max total DD:     {result.get('max_total_dd', 0):.2f}%")
    print("=" * 70)

    log = result.get("daily_log", [])
    if log:
        print("\nLast 10 trading days:")
        print(f"{'Day':<12} {'PnL':>10} {'Balance':>12} {'Return%':>9} {'DailyDD':>9} {'TotalDD':>9}")
        for e in log[-10:]:
            mark = " !!" if e.get("violation") else ""
            print(f"{str(e['day']):<12} ${e['pnl']:>9,.2f} ${e['balance']:>11,.2f} "
                  f"{e['return_pct']:>+8.2f}% {e['daily_dd_pct']:>8.2f}% {e['total_dd_pct']:>8.2f}%{mark}")


def print_mc_report(mc: Dict[str, Any], rules: PropFirmRules) -> None:
    print("=" * 70)
    print(f"MONTE CARLO PROP FIRM EVALUATION ({mc['runs']:,} runs)")
    print("=" * 70)
    print(f"Pass rate:        {mc['pass_count']:,}/{mc['runs']:,}  ({mc['pass_rate']:.1f}%)")
    print(f"Median return:    {mc['median_return_pct']:+.2f}%")
    print(f"5th percentile:   {mc['p5_return_pct']:+.2f}%   (worst-case)")
    print(f"95th percentile:  {mc['p95_return_pct']:+.2f}%")
    if mc["fail_reasons"]:
        print("\nFail reasons:")
        for reason, count in sorted(mc["fail_reasons"].items(), key=lambda x: -x[1]):
            pct = count / mc["runs"] * 100
            print(f"  {count:>5,} ({pct:>5.1f}%) {reason}")
    print("=" * 70)


# ============================================================
# CLI
# ============================================================

def main() -> int:
    p = argparse.ArgumentParser(
        description="HatchFibBreakout prop-firm rule simulator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--yfinance")
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--interval", default="1h")

    p.add_argument("--balance", type=float, default=100_000)
    p.add_argument("--risk", type=float, default=0.5)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--setup-hour", type=int, default=22)

    p.add_argument("--preset", choices=list(PRESETS.keys()),
                   help="Use a preset rule config (overrides individual flags)")
    p.add_argument("--profit-target", type=float, default=8.0,
                   help="%% of initial balance")
    p.add_argument("--max-daily-dd", type=float, default=5.0,
                   help="%% of start-of-day balance")
    p.add_argument("--max-total-dd", type=float, default=10.0,
                   help="%% of initial (or trailing peak) balance")
    p.add_argument("--trailing-dd", action="store_true",
                   help="Use trailing peak balance for total DD")
    p.add_argument("--min-days", type=int, default=5)
    p.add_argument("--max-days", type=int, default=30,
                   help="0 = unlimited")
    p.add_argument("--consistency", type=float, default=0.0,
                   help="Max %% of total profit allowed from a single day (0 = disabled)")

    p.add_argument("--monte-carlo", type=int, default=0,
                   help="Run N shuffled iterations to estimate pass probability")
    p.add_argument("--export-log", help="Save day-by-day log to CSV")

    args = p.parse_args()

    rule_kwargs: Dict[str, Any] = dict(
        initial_balance=args.balance,
        profit_target_pct=args.profit_target,
        max_daily_dd_pct=args.max_daily_dd,
        max_total_dd_pct=args.max_total_dd,
        trailing_dd=args.trailing_dd,
        min_trading_days=args.min_days,
        max_trading_days=args.max_days,
        consistency_pct=args.consistency,
    )
    if args.preset:
        rule_kwargs.update(PRESETS[args.preset])
        rule_kwargs["initial_balance"] = args.balance
        print(f"Using preset: {args.preset}")
    rules = PropFirmRules(**rule_kwargs)

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

    if args.monte_carlo > 0:
        mc = monte_carlo(bt.trades, rules, n_runs=args.monte_carlo)
        print_mc_report(mc, rules)
    else:
        result = simulate_propfirm(bt.trades, rules)
        print_report(result, rules)
        if args.export_log:
            pd.DataFrame(result["daily_log"]).to_csv(args.export_log, index=False)
            print(f"\nLog saved to {args.export_log}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
