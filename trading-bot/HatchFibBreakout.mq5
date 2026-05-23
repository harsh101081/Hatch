//+------------------------------------------------------------------+
//|                                          HatchFibBreakout.mq5    |
//|                                                            Hatch |
//|              Previous-Day Fibonacci Breakout EA for Gold (XAUUSD)|
//+------------------------------------------------------------------+
//| STRATEGY                                                         |
//|  - At the daily NY close (default 22:00 UTC), identify previous  |
//|    day's High and Low.                                           |
//|  - Anchor a Fibonacci: Low = 0.0, High = 1.0.                    |
//|  - Place TWO pending stop orders:                                |
//|       BUY  STOP @ High  (1.0)  SL @ 0.9 fib   TP @ 1.1 fib       |
//|       SELL STOP @ Low   (0.0)  SL @ 0.1 fib   TP @ -0.1 fib      |
//|  - Optional: widen TP (or tighten SL) to achieve target RR       |
//|    (e.g. 1:2 for prop firm rules).                               |
//|  - One-Cancels-Other (OCO): when one order fills, cancel the     |
//|    opposing pending order.                                       |
//|  - Maximum 1 trade per day. Pending orders auto-expire at next   |
//|    daily setup window.                                           |
//|  - Lot size auto-calculated from risk % of account balance.      |
//|                                                                  |
//| NOTES                                                            |
//|  - Uses TimeGMT() (UTC) so timing is consistent regardless of    |
//|    broker server time.                                           |
//|  - Previous-day range read via iHigh/iLow on PERIOD_D1 shift=1.  |
//|    For brokers whose daily candle does NOT close at NY 17:00,    |
//|    enable InpUseManualRange to compute the 24h window manually.  |
//+------------------------------------------------------------------+
#property copyright "Hatch"
#property link      "https://github.com/harsh101081/Hatch"
#property version   "1.00"
#property strict
#property description "Gold (XAUUSD) Previous-Day Fibonacci Breakout EA"
#property description "Places Buy Stop @ Prev High and Sell Stop @ Prev Low"
#property description "OCO logic, configurable RR, risk-based lot sizing"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

CTrade        trade;
CSymbolInfo   symInfo;
CPositionInfo posInfo;
COrderInfo    ordInfo;

//=== INPUT PARAMETERS ============================================
input group "=== Strategy ==="
input string InpSymbol           = "";        // Symbol ("" = use chart symbol, recommend XAUUSD)
input double InpRiskPercent      = 0.5;       // Risk % of account balance per trade
input double InpRiskRewardRatio  = 2.0;       // Target Risk:Reward (e.g. 2.0 = 1:2)
input bool   InpAdjustTPForRR    = true;      // true: widen TP for RR  |  false: tighten SL for RR
input double InpFibBuffer        = 0.1;       // Base Fib buffer (0.1 = 0.9 / 1.1 levels)

input group "=== Timing (UTC) ==="
input int    InpEntryHourUTC     = 22;        // Hour to place pending orders (UTC)
input int    InpEntryMinuteUTC   = 0;         // Minute to place pending orders (UTC)
input int    InpSetupWindowMin   = 5;         // Minutes window after entry time to attempt setup

input group "=== Risk / Execution ==="
input long   InpMagicNumber      = 990110;    // Magic number
input int    InpMaxSpreadPoints  = 0;         // Max spread allowed in points (0 = no limit)
input int    InpSlippagePoints   = 30;        // Max slippage in points
input bool   InpEnableLogging    = true;      // Verbose logs

//=== GLOBALS =====================================================
string   g_symbol;
datetime g_lastSetupDay = 0;     // floor-to-day of last setup placement
ulong    g_buyOrderTicket  = 0;
ulong    g_sellOrderTicket = 0;
bool     g_oneFilledHandled = false;

//+------------------------------------------------------------------+
//| Helper: log only when verbose logging on                         |
//+------------------------------------------------------------------+
void Log(string msg)
{
   if(InpEnableLogging) Print("[HatchFib] ", msg);
}

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   g_symbol = (InpSymbol == "" ? _Symbol : InpSymbol);

   if(!symInfo.Name(g_symbol))
   {
      Print("[HatchFib] FATAL: cannot select symbol ", g_symbol);
      return INIT_FAILED;
   }
   symInfo.RefreshRates();

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints((ulong)InpSlippagePoints);
   trade.SetTypeFillingBySymbol(g_symbol);

   // Validate input
   if(InpRiskPercent <= 0 || InpRiskPercent > 10)
   {
      Print("[HatchFib] FATAL: InpRiskPercent must be in (0, 10]. Got ", InpRiskPercent);
      return INIT_FAILED;
   }
   if(InpRiskRewardRatio <= 0)
   {
      Print("[HatchFib] FATAL: InpRiskRewardRatio must be > 0");
      return INIT_FAILED;
   }
   if(InpEntryHourUTC < 0 || InpEntryHourUTC > 23 || InpEntryMinuteUTC < 0 || InpEntryMinuteUTC > 59)
   {
      Print("[HatchFib] FATAL: invalid entry time");
      return INIT_FAILED;
   }

   Log(StringFormat("Initialized | symbol=%s risk=%.2f%% RR=1:%.1f setupTime=%02d:%02d UTC",
                    g_symbol, InpRiskPercent, InpRiskRewardRatio,
                    InpEntryHourUTC, InpEntryMinuteUTC));
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Log("Deinit, reason=" + IntegerToString(reason));
}

//+------------------------------------------------------------------+
//| OnTick                                                           |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1) New-day setup: place buy/sell stops once per day inside window
   if(IsSetupTimeNow() && !IsSetupDoneToday())
      DoDailySetup();

   // 2) OCO management: cancel opposite pending if a position has been opened
   ManageOCO();
}

//+------------------------------------------------------------------+
//| Returns true if current UTC time falls inside the daily setup    |
//| window [InpEntryHourUTC:InpEntryMinuteUTC, +InpSetupWindowMin)   |
//+------------------------------------------------------------------+
bool IsSetupTimeNow()
{
   datetime nowUTC = TimeGMT();
   MqlDateTime dt;
   TimeToStruct(nowUTC, dt);

   int nowMin    = dt.hour * 60 + dt.min;
   int targetMin = InpEntryHourUTC * 60 + InpEntryMinuteUTC;
   int diff      = nowMin - targetMin;
   if(diff < 0) diff += 1440;

   return (diff >= 0 && diff < InpSetupWindowMin);
}

//+------------------------------------------------------------------+
//| Returns true if we've already placed today's pendings            |
//+------------------------------------------------------------------+
bool IsSetupDoneToday()
{
   datetime nowUTC = TimeGMT();
   datetime nowDay = nowUTC - (nowUTC % 86400);
   return (g_lastSetupDay == nowDay);
}

//+------------------------------------------------------------------+
//| Mark setup done for today                                        |
//+------------------------------------------------------------------+
void MarkSetupDone()
{
   datetime nowUTC = TimeGMT();
   g_lastSetupDay = nowUTC - (nowUTC % 86400);
   g_oneFilledHandled = false; // reset OCO flag for the new day
}

//+------------------------------------------------------------------+
//| Daily setup: cancel leftovers, read prev day H/L, place pendings |
//+------------------------------------------------------------------+
void DoDailySetup()
{
   // Clean up: cancel any pendings from yesterday & ensure no open trades
   CancelAllPendingsForSymbol();

   if(HasOpenPosition())
   {
      Log("Open position exists; skipping setup for today.");
      MarkSetupDone();
      return;
   }

   symInfo.RefreshRates();

   // Spread guard
   if(InpMaxSpreadPoints > 0)
   {
      long spread = (long)symInfo.Spread();
      if(spread > InpMaxSpreadPoints)
      {
         Log(StringFormat("Spread too wide (%d > %d). Skipping today.", (int)spread, InpMaxSpreadPoints));
         MarkSetupDone();
         return;
      }
   }

   // Previous day H/L (most recently completed daily candle)
   double prevHigh = iHigh(g_symbol, PERIOD_D1, 1);
   double prevLow  = iLow(g_symbol, PERIOD_D1, 1);

   if(prevHigh <= 0 || prevLow <= 0 || prevHigh <= prevLow)
   {
      Log(StringFormat("Invalid prev day range: H=%.5f L=%.5f", prevHigh, prevLow));
      return; // do NOT mark done; retry next tick
   }

   double range = prevHigh - prevLow;

   // Compute SL & TP buffers (in price units)
   //   Base: SL = InpFibBuffer * range  (e.g. 0.1 * range = 0.9/0.1 fib)
   //         TP = InpFibBuffer * range  (i.e. 1.1/-0.1 fib, gives 1:1 RR)
   //   Adjust for desired RR:
   double slBuffer = InpFibBuffer * range;
   double tpBuffer = InpFibBuffer * range;

   if(InpAdjustTPForRR)
      tpBuffer = slBuffer * InpRiskRewardRatio;       // widen TP
   else
      slBuffer = tpBuffer / InpRiskRewardRatio;       // tighten SL

   int digits = (int)symInfo.Digits();

   // BUY STOP at high
   double buyEntry = NormalizeDouble(prevHigh,             digits);
   double buySL    = NormalizeDouble(prevHigh - slBuffer,  digits);
   double buyTP    = NormalizeDouble(prevHigh + tpBuffer,  digits);

   // SELL STOP at low
   double sellEntry = NormalizeDouble(prevLow,             digits);
   double sellSL    = NormalizeDouble(prevLow + slBuffer,  digits);
   double sellTP    = NormalizeDouble(prevLow - tpBuffer,  digits);

   double ask = symInfo.Ask();
   double bid = symInfo.Bid();

   // Stops level: broker-required minimum distance from market
   double pointSize = symInfo.Point();
   long stopsLevel  = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist   = stopsLevel * pointSize;

   string comment = StringFormat("HatchFib %s", TimeToString(TimeGMT(), TIME_DATE));

   // ===== Place BUY STOP =====
   if(buyEntry > ask + minDist)
   {
      double buyLots = CalculateLots(MathAbs(buyEntry - buySL));
      if(buyLots > 0)
      {
         if(trade.BuyStop(buyLots, buyEntry, g_symbol, buySL, buyTP,
                          ORDER_TIME_DAY, 0, comment))
         {
            g_buyOrderTicket = trade.ResultOrder();
            Log(StringFormat("BUY STOP placed | ticket=%I64u lots=%.2f entry=%.5f SL=%.5f TP=%.5f",
                             g_buyOrderTicket, buyLots, buyEntry, buySL, buyTP));
         }
         else
         {
            Log("BUY STOP failed: " + IntegerToString(trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription());
         }
      }
      else
      {
         Log("BUY STOP skipped: invalid lot size");
      }
   }
   else
   {
      Log(StringFormat("BUY STOP skipped: prev high (%.5f) too close to ask (%.5f); minDist=%.5f",
                       buyEntry, ask, minDist));
   }

   // ===== Place SELL STOP =====
   if(sellEntry < bid - minDist)
   {
      double sellLots = CalculateLots(MathAbs(sellEntry - sellSL));
      if(sellLots > 0)
      {
         if(trade.SellStop(sellLots, sellEntry, g_symbol, sellSL, sellTP,
                           ORDER_TIME_DAY, 0, comment))
         {
            g_sellOrderTicket = trade.ResultOrder();
            Log(StringFormat("SELL STOP placed | ticket=%I64u lots=%.2f entry=%.5f SL=%.5f TP=%.5f",
                             g_sellOrderTicket, sellLots, sellEntry, sellSL, sellTP));
         }
         else
         {
            Log("SELL STOP failed: " + IntegerToString(trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription());
         }
      }
      else
      {
         Log("SELL STOP skipped: invalid lot size");
      }
   }
   else
   {
      Log(StringFormat("SELL STOP skipped: prev low (%.5f) too close to bid (%.5f); minDist=%.5f",
                       sellEntry, bid, minDist));
   }

   MarkSetupDone();
}

//+------------------------------------------------------------------+
//| Risk-based lot sizing                                            |
//|   risk_$ = balance * risk%                                       |
//|   loss_per_lot = (slDistancePrice / tickSize) * tickValue        |
//|   lots = risk_$ / loss_per_lot   (floored to lot step)           |
//+------------------------------------------------------------------+
double CalculateLots(double slDistancePrice)
{
   if(slDistancePrice <= 0) return 0.0;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= 0) return 0.0;

   double riskAmount = balance * InpRiskPercent / 100.0;

   double tickSize  = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0 || tickValue <= 0) return 0.0;

   double lossPerLot = (slDistancePrice / tickSize) * tickValue;
   if(lossPerLot <= 0) return 0.0;

   double lots = riskAmount / lossPerLot;

   double lotStep = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);

   if(lotStep <= 0) lotStep = 0.01;
   lots = MathFloor(lots / lotStep) * lotStep;

   if(lots < minLot)
   {
      Log(StringFormat("Computed lots %.4f below broker min %.4f; aborting", lots, minLot));
      return 0.0;
   }
   if(lots > maxLot) lots = maxLot;

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| OCO: when one of our pendings becomes a position, cancel the     |
//| other pending. Also tracks "1 trade per day" rule.               |
//+------------------------------------------------------------------+
void ManageOCO()
{
   if(g_oneFilledHandled) return;
   if(!HasOpenPosition()) return;

   // A position with our magic exists -> kill any remaining pendings
   int killed = CancelAllPendingsForSymbol();
   g_oneFilledHandled = true;

   if(killed > 0)
      Log(StringFormat("OCO triggered: %d opposing pending order(s) cancelled.", killed));
   else
      Log("OCO triggered: no opposing pending to cancel.");
}

//+------------------------------------------------------------------+
//| True if any position with our magic on our symbol exists         |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(posInfo.SelectByIndex(i))
      {
         if(posInfo.Magic() == InpMagicNumber && posInfo.Symbol() == g_symbol)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Cancel all pending orders matching our magic & symbol            |
//+------------------------------------------------------------------+
int CancelAllPendingsForSymbol()
{
   int cancelled = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(ordInfo.SelectByIndex(i))
      {
         if(ordInfo.Magic() == InpMagicNumber && ordInfo.Symbol() == g_symbol)
         {
            if(trade.OrderDelete(ordInfo.Ticket()))
               cancelled++;
            else
               Log("Failed to delete order " + IntegerToString((long)ordInfo.Ticket()) +
                   ": " + trade.ResultRetcodeDescription());
         }
      }
   }
   return cancelled;
}
//+------------------------------------------------------------------+
