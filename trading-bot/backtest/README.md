# Python Backtester for HatchFibBreakout

Pure-Python backtest harness that mirrors the MT5 EA's logic, so you can validate the strategy on historical data **without** opening MT5.

---

## Quick Start

```bash
# 1) Install dependencies (one-time)
cd trading-bot/backtest
pip install -r requirements.txt

# 2a) Backtest using yfinance (easiest, no manual download)
python backtest.py --yfinance GC=F --start 2023-06-01 --interval 1h

# 2b) Or backtest using your own CSV (most accurate)
python backtest.py --csv data/xauusd_h1.csv

# 3) Save a trade log + equity curve
python backtest.py --csv data/xauusd_h1.csv --export-trades trades.csv --plot equity.png
```

---

## What it does

For each trading day in your data, the backtester:

1. Identifies the **previous trading day's High & Low** (range from setup time → setup time)
2. Computes the Fib levels: Buy Stop @ High (SL 0.9 / TP 1.2), Sell Stop @ Low (SL 0.1 / TP -0.2)
3. Walks bar-by-bar through the current trading day:
   - When a bar's H/L crosses a pending order's price → fill it
   - When both could fill in the same bar → tiebreaker = whichever level is closer to the bar's open
   - On fill, the opposing pending is cancelled (OCO)
   - Tracks the open trade until SL or TP hit
   - If both SL and TP could be hit in the same bar → assumes SL hit first (pessimistic)
4. Closes positions still open at end-of-day at the closing price
5. Computes lot size for each trade as `(balance * risk%) / SL_distance`

The balance compounds as the backtest progresses.

---

## Where to get OHLC data

The backtester needs **intraday** OHLC bars (H1 or finer) for accurate fills. Options:

### Option 1: yfinance (easiest, free, but limited)

`yfinance` is built-in via `--yfinance` flag. Caveats:
- **`1h` interval**: ~730 days of history max
- **`15m`/`5m` intervals**: ~60 days of history max
- Symbols to try: `GC=F` (Gold futures, recommended), `GLD` (Gold ETF), `XAUUSD=X`
- Prices may differ slightly from your MT5 broker's XAUUSD feed

```bash
python backtest.py --yfinance GC=F --start 2023-06-01 --interval 1h
```

### Option 2: MetaTrader 5 (most accurate)

Export your broker's exact tick data. On Windows with MT5 + Python:

```bash
pip install MetaTrader5
```

```python
# export_mt5.py - Run once to dump MT5 history to CSV
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

mt5.initialize()
rates = mt5.copy_rates_range(
    "XAUUSD", mt5.TIMEFRAME_H1,
    datetime(2022, 1, 1), datetime(2024, 12, 31),
)
mt5.shutdown()

df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
df = df[["time", "open", "high", "low", "close"]]
df.to_csv("xauusd_h1.csv", index=False)
print(f"Saved {len(df)} bars")
```

Then:
```bash
python backtest.py --csv xauusd_h1.csv
```

### Option 3: TradingView export

On a TradingView chart: right-click chart → **Export chart data** → CSV.
Edit the file so columns are: `time, open, high, low, close` (case-insensitive).

### Option 4: Dukascopy / HistData / etc.

Free historical tick & bar data for FX/Gold:
- https://www.dukascopy.com/swiss/english/marketwatch/historical/
- http://www.histdata.com/

Both require minor CSV reformatting to match the expected columns.

---

## CSV format

The script auto-detects a timestamp column named `timestamp`, `time`, `datetime`, or `date`. Other columns must be named `open`, `high`, `low`, `close` (case-insensitive). Timestamps should be **UTC**:

```csv
timestamp,open,high,low,close
2023-01-02 00:00:00,1820.5,1822.3,1819.8,1821.4
2023-01-02 01:00:00,1821.4,1823.1,1820.9,1822.7
2023-01-02 02:00:00,1822.7,1824.0,1822.3,1823.5
...
```

H1 bars are recommended (good speed/accuracy tradeoff). M15 gives more fill precision at the cost of more data.

---

## CLI parameters

| Flag | Default | Description |
|------|---------|-------------|
| `--csv PATH` | — | Load OHLC from a CSV file |
| `--yfinance SYM` | — | Download from yfinance (e.g. `GC=F`) |
| `--start YYYY-MM-DD` | `2023-01-01` | yfinance: start date |
| `--end YYYY-MM-DD` | today | yfinance: end date |
| `--interval` | `1h` | yfinance: bar interval (`1h`, `30m`, `15m`, `5m`) |
| `--risk PCT` | `0.5` | Risk % per trade |
| `--rr X` | `2.0` | Risk:Reward (e.g. `2` = 1:2) |
| `--balance $` | `10000` | Initial balance |
| `--setup-hour H` | `22` | Setup hour, UTC (matches EA default) |
| `--no-adjust-tp` | off | Tighten SL instead of widening TP |
| `--contract-size N` | `100` | Lot contract size (Gold = 100 oz) |
| `--commission $/lot` | `0` | Commission per lot per round-trip |
| `--export-trades PATH` | — | Save trade log to CSV |
| `--plot PATH` | — | Save equity curve to PNG |

---

## Sample output

```
============================================================
BACKTEST REPORT - HatchFibBreakout
============================================================
Config:        risk=0.5%  RR=1:2.0  fib=0.1
Period:        2023-06-01 to 2024-12-30
Bars analyzed: 9,432
------------------------------------------------------------
Initial balance: $   10,000.00
Final balance:   $   12,847.30
Total P&L:       $    2,847.30  (+28.47%)
Max Drawdown:           4.12%
------------------------------------------------------------
Total trades:             287
  Wins:                   183  (63.8%)
  Losses:                 104
  No-trade days:           42
Avg win:         $       29.50
Avg loss:        $      -24.80
Profit factor:           2.10
============================================================
```

---

## Limitations & caveats

- **Bar-level fills, not tick-level**: an H1 bar with high=$2050 and low=$2010 may have hit your SL and TP in any order. The backtester assumes SL-first (worst case) when both are hit in the same bar.
- **No spread modelling by default**: Set `Config.spread_price` in code or add a `--spread` flag to model bid/ask spread cost.
- **No slippage modelling**: live fills can be a few cents off your stop price.
- **No swap/overnight financing**: trades closing same-day so this rarely matters, but EOD closures will skip swap fees.
- **yfinance feed differs from your broker**: use MT5/CSV for production-grade results.
- **Single instrument**: only one symbol per run (XAUUSD).

For prop-firm validation, **use real broker data** (Option 2 above) and verify the results match an MT5 Strategy Tester run (`Every tick based on real ticks`) on the same period.

---

## Comparing Python vs. MT5 Strategy Tester

After running both:

| Metric | Python | MT5 Strategy Tester |
|--------|--------|---------------------|
| Total trades | should match within ±5% |
| Win rate | should match within ±3% |
| P&L | should match within ±10% |
| Max DD | should match within ±10% |

Discrepancies usually come from: different data feed (yfinance vs broker), spread/slippage model differences, or same-bar SL/TP resolution. Use the same CSV in MT5 (via `History Center → Import`) to eliminate the data variable.

---

## Tweaking the strategy

The `Config` dataclass at the top of `backtest.py` exposes all knobs. Edit it directly or pass CLI flags. Try:

- `--rr 1.5` vs. `--rr 2.0` vs. `--rr 3.0` to find the sweet spot
- `--no-adjust-tp` to use the original strategy's exact TP (1.1 fib) with tighter SL
- `--setup-hour 21` if your broker's daily candle closes at 21:00 UTC instead of 22:00 UTC
- Edit `cfg.fib_buffer = 0.05` for tighter SL (0.95/0.05 levels) — riskier, larger lots

---

## Going further

For a more rigorous statistical edge check, run **Monte Carlo** on the trade log:

```python
import pandas as pd, numpy as np
trades = pd.read_csv("trades.csv")
returns = trades["pnl"].dropna().values
results = []
for _ in range(10_000):
    sample = np.random.choice(returns, size=len(returns), replace=True)
    results.append(np.cumsum(sample)[-1])
results = np.array(results)
print(f"Median: ${np.median(results):.2f}")
print(f"5th pct: ${np.percentile(results, 5):.2f}")
print(f"95th pct: ${np.percentile(results, 95):.2f}")
```

If the 5th percentile is still positive, the edge is robust.



---

## Advanced Tools

In addition to the basic backtester (`backtest.py`), this folder includes three advanced analysis tools:

### 1. `optimizer.py` — Parameter sweep & walk-forward analysis

Find the best risk/RR combination, or run honest out-of-sample validation.

```bash
# Grid search: try every combination of risk and RR, plot heatmap
python optimizer.py --csv data.csv --mode grid \
  --risk-min 0.25 --risk-max 2.0 --risk-step 0.25 \
  --rr-min 1.0 --rr-max 3.0 --rr-step 0.5 \
  --metric profit_factor \
  --heatmap heatmap.png --export grid_results.csv

# Walk-forward: optimize on in-sample, validate on out-of-sample (rolling windows)
python optimizer.py --csv data.csv --mode walkforward --windows 4 --is-ratio 0.7
```

The walk-forward mode is the **gold standard** for parameter selection — it tells you whether your "optimal" parameters actually generalize to unseen data, or whether you've just curve-fit your backtest.

### 2. `propfirm.py` — Prop firm rule simulator

Simulate FTMO/MFF/5ers/Topstep evaluation rules day-by-day. Includes Monte Carlo mode that shuffles trade order to estimate the true pass probability.

```bash
# Single deterministic run with FTMO Phase 1 rules
python propfirm.py --csv data.csv --preset ftmo --balance 100000

# Custom rules
python propfirm.py --csv data.csv \
  --balance 50000 \
  --profit-target 8 \
  --max-daily-dd 5 \
  --max-total-dd 10 \
  --max-days 30

# Monte Carlo: 10,000 shuffled orderings to estimate pass %
python propfirm.py --csv data.csv --preset ftmo --monte-carlo 10000
```

**Available presets:** `ftmo`, `ftmo_verification`, `myforexfunds`, `the5ers`, `topstep`

Output includes verdict (PASS/FAIL with reason), final balance, peak balance, trading days used, max daily DD, max total DD, and the last 10 days of equity log. Monte Carlo output adds pass rate, median return, and percentile bounds.

### 3. `visualize.py` — Edge Finder dashboard

Generates a single-page PNG dashboard with all the key stats — same idea as `Edge Finder` / `FX Replay` analytics.

```bash
python visualize.py --csv data.csv --risk 0.5 --rr 2 --save dashboard.png --no-show
```

The dashboard includes:
- **Header stats:** Trades, Win Rate, Profit Factor, Total Return, Max DD, Sharpe, Avg R, Expectancy
- **Equity Curve** with high-watermark line and underwater fill
- **Drawdown plot** showing % drawdown over time
- **R-Multiple distribution** histogram (wins green, losses red)
- **P&L by Day of Week** bars
- **Monthly Returns** bars
- **Footer:** win/loss streaks, no-trade days, avg duration

Color-coded based on quality (green = good, yellow = borderline, red = bad).

---

## Recommended Workflow

```bash
# Step 1: Backtest with default parameters to baseline
python backtest.py --csv data/xauusd_h1.csv --export-trades trades.csv

# Step 2: Find optimal parameters via walk-forward (avoids overfitting)
python optimizer.py --csv data/xauusd_h1.csv --mode walkforward --windows 5

# Step 3: Backtest with the optimized parameters
python backtest.py --csv data/xauusd_h1.csv --risk 0.75 --rr 2.5

# Step 4: Build edge finder report
python visualize.py --csv data/xauusd_h1.csv --risk 0.75 --rr 2.5 --save report.png

# Step 5: Validate against prop firm rules with Monte Carlo
python propfirm.py --csv data/xauusd_h1.csv --risk 0.75 --rr 2.5 \
  --preset ftmo --balance 100000 --monte-carlo 10000
```

If walk-forward and Monte Carlo both look good (OOS profit factor > 1.5, MC pass rate > 60%), the strategy has a real edge worth deploying. If MC pass rate is < 30% but the deterministic single-run passes, the strategy is fragile and depends on lucky trade ordering.
