# Hatch Fib Breakout EA (MT5)

A MetaTrader 5 Expert Advisor that automates the **Previous-Day Fibonacci Breakout** strategy on Gold (XAUUSD). Designed to be prop-firm friendly: fixed % risk per trade, max 1 trade per day, OCO logic, configurable RR.

---

## Strategy Summary

At the configured daily setup time (default **22:00 UTC** — NY 5pm close):

1. Read the **previous daily candle's High and Low**.
2. Anchor a Fibonacci: **Low = 0.0**, **High = 1.0**.
3. Place TWO pending **Stop** orders simultaneously:

   | Order        | Entry            | Stop Loss     | Take Profit (1:2 RR default) |
   |--------------|------------------|---------------|------------------------------|
   | Buy Stop     | Prev High (1.0)  | 0.9 fib level | 1.2 fib level                |
   | Sell Stop    | Prev Low (0.0)   | 0.1 fib level | -0.2 fib level               |

4. **OCO**: as soon as one order fills (becomes a position), the opposing pending order is cancelled.
5. **Max 1 trade per day**. Pending orders auto-expire end-of-day (`ORDER_TIME_DAY`).
6. **Lot size auto-calculated** from `RiskPercent * AccountBalance / SL_distance`.

> The default `InpRiskRewardRatio = 2.0` and `InpAdjustTPForRR = true` widen the TP to 1.2 / -0.2 fib levels for a 1:2 RR while keeping the strategy's signature SL at the 0.9 / 0.1 levels. Set `InpAdjustTPForRR = false` to instead tighten the SL and keep TP at the original 1.1 / -0.1 levels.

---

## Files

- `HatchFibBreakout.mq5` — the Expert Advisor source
- `README.md` — this file

---

## Installation

1. Open MetaTrader 5
2. `File` → `Open Data Folder`
3. Copy `HatchFibBreakout.mq5` into `MQL5/Experts/`
4. Back in MT5, press `F4` to open MetaEditor
5. Open `HatchFibBreakout.mq5` and click **Compile** (or press F7). You should see `0 errors, 0 warnings`.
6. Back in MT5, refresh the Navigator panel (Ctrl+M) — the EA should appear under `Expert Advisors`.
7. Drag it onto an **XAUUSD** chart (any timeframe).
8. In the dialog:
   - **Common tab**: enable `Allow Algo Trading`
   - **Inputs tab**: review/adjust parameters (see below)
9. Click OK. Make sure the global `Algo Trading` button (top toolbar) is green.

---

## Input Parameters

### Strategy
| Param | Default | Description |
|-------|---------|-------------|
| `InpSymbol` | `""` | Trading symbol. Leave empty to use the chart's symbol (recommended: XAUUSD). |
| `InpRiskPercent` | `0.5` | % of account balance risked per trade. |
| `InpRiskRewardRatio` | `2.0` | Target Risk:Reward (e.g. `2.0` = 1:2). |
| `InpAdjustTPForRR` | `true` | `true` = widen TP for RR. `false` = tighten SL for RR. |
| `InpFibBuffer` | `0.1` | Base fib buffer. `0.1` means SL at 0.9/0.1 fib level. |

### Timing (UTC)
| Param | Default | Description |
|-------|---------|-------------|
| `InpEntryHourUTC` | `22` | Hour (UTC) to place pending orders. Default 22:00 UTC = NY 5pm during EST winter, NY 6pm during EDT summer. |
| `InpEntryMinuteUTC` | `0` | Minute. |
| `InpSetupWindowMin` | `5` | Minutes window after entry time during which the EA will attempt setup if it hasn't already (gives MT5 time to fully load if started late). |

### Risk / Execution
| Param | Default | Description |
|-------|---------|-------------|
| `InpMagicNumber` | `990110` | Unique trade identifier. Change if running multiple instances. |
| `InpMaxSpreadPoints` | `0` | Skip setup if spread exceeds this many points. `0` = no check. Recommend `100` for prop firms. |
| `InpSlippagePoints` | `30` | Max allowed slippage on order placement. |
| `InpEnableLogging` | `true` | Verbose logging to the Experts tab. |

---

## Prop Firm Compliance Notes

- **Fixed risk %**: `0.5%` of balance per trade by default — well under the typical 1-2% cap.
- **One trade per day**: enforced by `g_lastSetupDay` flag + OCO cancel on fill.
- **No martingale, no grid, no hedging hacks**: standard pending-order placement only.
- **No news straddling**: place orders at NY close, far from major USD news typically.
- **Spread guard**: enable `InpMaxSpreadPoints` to skip setup during illiquid periods.
- **Magic number**: isolated, won't interfere with other EAs.

---

## How OCO Is Implemented

On every tick, `ManageOCO()`:
1. Checks if any position with our magic exists on our symbol.
2. If yes → cancels every pending order with our magic on our symbol.
3. Sets `g_oneFilledHandled = true` so the check doesn't repeat for the day.

This guarantees that the moment one Stop order fills, the opposing one is killed within ~1 tick.

---

## Timing Considerations

- The EA uses `TimeGMT()` (UTC) so timing is **broker-server-agnostic**.
- Daily candle range comes from `iHigh()` / `iLow()` on `PERIOD_D1` shift `1` (last fully closed daily candle on the broker's chart). Most MT5 brokers run on GMT+2/+3 such that the daily candle closes around 22:00–23:00 UTC, aligning naturally with our 22:00 UTC setup.
- If your broker uses an unusual daily reset (e.g. midnight UTC), the previous-day H/L may not match your visual fib drawing on TradingView. In that case, run the EA on a broker with NY-close daily candles, or fork the EA to compute H/L over a fixed 24h UTC window.

---

## Testing

Before going live, **strategy-test** the EA in MT5:

1. `View` → `Strategy Tester` (Ctrl+R)
2. Expert: `HatchFibBreakout`
3. Symbol: `XAUUSD`
4. Period: `M5` or `M15`
5. Date range: at least 6 months
6. Modeling: `Every tick based on real ticks` (most accurate)
7. Run, then check:
   - Trades happen ~once/day at the entry window
   - Both pendings appear, and only one fills per day
   - Lot sizes match `0.5%` risk

Per the source video, the expected long-run win rate is roughly 60-70% — but **always backtest on your specific broker's data before going live**.

---

## Live Run Checklist

- [ ] Compiled with 0 errors in MetaEditor
- [ ] Attached to an XAUUSD chart
- [ ] `Algo Trading` enabled (chart-level + global toolbar)
- [ ] `InpRiskPercent` matches your prop firm's max risk-per-trade rule
- [ ] `InpEntryHourUTC` matches your desired daily setup time
- [ ] `InpMaxSpreadPoints` set to a sane value for your broker (e.g. 50–150 for XAUUSD)
- [ ] Account has enough balance for at least the broker's min lot at the calculated SL distance

---

## License

MIT — use freely. No warranty; trade at your own risk. This is automation only, not financial advice.
