# HatchFib for TradingView

Two Pine Script v5 files that bring the **HatchFib Breakout** strategy to TradingView:

| File | What it does | Use it if |
|------|--------------|-----------|
| `HatchFibBreakout.pine` | Full **strategy** with auto-execution + backtester | You want to backtest in TradingView and/or use TV alerts to trade |
| `HatchFibLevels.pine`   | **Indicator** that just plots the levels | You want to manually place orders on a prop firm that bans EAs |

Both follow the exact same logic as the MT5 EA and the Python backtester.

## v2.0 Strategy Rules

At each daily setup time (NY 5pm - 6pm = 22:00 UTC during EST winter):

| Side | Entry | Stop Loss | Take Profit | RR |
|------|-------|-----------|-------------|----|
| Buy Stop | Prev Day High (1.0 fib) | 0.9 fib | **1.25 fib** | 2.5R |
| Sell Stop | Prev Day Low (0.0 fib) | 0.1 fib | **-0.25 fib** | 2.5R |

**Filters:**
- **OCO**: when one stop fills, the opposite is auto-cancelled
- **No-trade window**: 12pm-6pm NY local time (red shaded on chart). If a stop triggers during this low-liquidity window, the trade is skipped.
- **Gap protection**: gaps that fill far from entry are caught by the no-trade window since they typically happen near 5pm NY (boundary).
- **Day-of-week filter** (optional): skip Mondays, Fridays, etc.

**Sizing:**
- Risk-based position sizing (default 0.5% of equity)
- Max 1 trade per day

---

## Quick Start

### Strategy version (with backtesting)

1. Open Gold on TradingView (recommend **OANDA:XAUUSD** or your broker's symbol)
2. Switch to **15-minute** or **1-hour** timeframe
3. Pine Editor (bottom of chart) → New → Strategy
4. Paste contents of `HatchFibBreakout.pine`
5. Click **Save** → name it "HatchFib Breakout"
6. Click **Add to chart**
7. Open **Strategy Tester** panel (bottom)
8. Right-click chart → **Settings** → **Properties** tab:
   - Initial capital: `10000` (or your prop firm balance)
   - Order size: leave default (script overrides anyway)
   - Slippage: `2` ticks
   - Commission: set to your broker's commission
9. Click **Performance Summary** to see backtest results

### Indicator version (manual trading)

1. Open Gold on TradingView
2. Pine Editor → New → Indicator
3. Paste contents of `HatchFibLevels.pine`
4. Click **Save** → name it "HatchFib Levels"
5. Click **Add to chart**
6. The chart now shows entry/SL/TP lines with a stats table top-right
7. At setup time (default 22:00 UTC) the chart highlights yellow + a "PLACE ORDERS NOW" label appears
8. Manually place a Buy Stop and a Sell Stop in your broker's terminal at the displayed prices
9. When one fills, cancel the other (most brokers have an OCO option)

---

## What you'll see on the chart

```
═══════════════════ Buy TP   (1.2 fib) ──────  (light green)
═══════════════════ Buy Stop (1.0 / Prev High) ──── (bold green)
═══════════════════ Buy SL   (0.9 fib) ──────  (light red)

       … current price action …

═══════════════════ Sell SL  (0.1 fib) ──────  (light red)
═══════════════════ Sell Stop(0.0 / Prev Low) ──── (bold red)
═══════════════════ Sell TP  (-0.2 fib) ──────  (light green)
```

Plus a yellow vertical band on the bar where setup happens, and a stats panel in the top-right corner.

---

## Inputs reference

### Strategy

| Input | Default | Description |
|-------|---------|-------------|
| Risk per Trade (%) | `0.5` | % of equity risked per trade |
| Risk:Reward Ratio | `2.0` | Target RR (1:X) |
| Widen TP for RR | `true` | If true, keeps SL at 0.9/0.1 and widens TP. If false, keeps TP and tightens SL. |
| Fib Buffer | `0.1` | Base fib distance |
| Setup Hour (UTC) | `22` | Hour to place orders (NY 5pm during EST) |
| Setup Minute | `0` | Minute |
| Filter by Day of Week | `false` | Optional: skip Mondays, Fridays, etc. |
| Show Fib Levels | `true` | Plot levels on chart |
| Show Level Labels | `true` | Show price labels next to lines |

### Indicator (Levels)

Same set, minus risk management (no orders placed).

---

## How TradingView's Strategy Tester compares to the Python backtester

| Metric | TradingView | Python |
|--------|-------------|--------|
| Bar-by-bar simulation | ✅ | ✅ |
| Tick-by-tick simulation | ❌ (Pro+ only) | ❌ (need MT5) |
| OCO logic | ✅ | ✅ |
| 1 trade/day max | ✅ | ✅ |
| Risk-based sizing | ✅ | ✅ |
| Spread modeling | Set in script properties | Manual in code |
| Walk-forward / parameter sweep | ❌ | ✅ |
| Monte Carlo | ❌ | ✅ |
| Prop firm rule simulation | ❌ | ✅ |
| Edge Finder dashboard | Built-in TV reports | Custom PNG |

**Bottom line:** Use TradingView for quick visual verification and live alerts. Use the Python backtester for deep statistical analysis (walk-forward, Monte Carlo, prop firm validation).

---

## Live alerts (auto-trade via webhook)

The strategy script defines three alert conditions:

1. **HatchFib: Setup Time** — fires when orders should be placed (use this for webhook automation)
2. **HatchFib: Long Filled** — fires when a buy stop is hit
3. **HatchFib: Short Filled** — fires when a sell stop is hit

To enable:

1. Right-click chart → **Add Alert**
2. Condition: `HatchFib Breakout` → choose condition (e.g. "Setup Time")
3. **Frequency:** Once Per Bar Close
4. **Webhook URL:** point this at your broker bridge (3Commas, Pickmybrain, custom server) if you want auto-execution

---

## Symbol & timeframe recommendations

- **Symbol:** `OANDA:XAUUSD` is the cleanest (NY-aligned daily candles, low spread). Your broker's `XAUUSD` will also work.
- **Timeframe for trading:** Run the script on the **1-hour** chart for clean visual + setup detection. Use **15-minute** if you want more granular fill simulation in the strategy tester.
- ⚠️ **Avoid:** the strategy logic depends on the daily candle close aligning with NY 5pm. Some symbols (e.g. crypto, equities) have different daily resets — verify on your symbol that the daily candle closes at 22:00 UTC (or adjust `setupHour` to match).

---

## Limitations

- **No fractional positions in some markets** — TradingView's strategy tester rounds positions. The Python backtester handles fractional lots correctly.
- **No tick-level fills (free tier)** — same caveat as the Python tool. Use 15m bars or smaller for more accuracy.
- **Symbol-specific daily candle closes** — verify the daily reset matches your `setupHour`.
- **Built-in optimizer** — TradingView Pro+ has a basic parameter optimizer, but for serious analysis use the Python `optimizer.py` walk-forward mode.

---

## Recommended workflow

1. **Visualize** with `HatchFibLevels.pine` (indicator) to confirm levels look right on your symbol
2. **Backtest** with `HatchFibBreakout.pine` (strategy) over 1+ year of data → quick sanity check
3. **Deep analysis** with the Python tools → walk-forward, Monte Carlo, prop firm sims
4. **Live trade** either:
   - Manually using the indicator (any prop firm, including ones banning EAs)
   - Auto via TradingView alert webhooks (some brokers support this)
   - Auto via the MT5 EA (if your broker is MT5-based)
