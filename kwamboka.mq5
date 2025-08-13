#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
input double    LotSize            = 0.5;
input double    RiskPercent        = 2.0; 
input int       StopLossPoints     = 200;
input int       TakeProfitPoints   = 400;
input int       ATRPeriod          = 14;
input int       GroupSize          = 12;
input double    RRRatio            = 3.0;
input double    PartialTPPercent = 0.5; 
input double    PartialCloseRatio = 0.8; 
input double BreakevenBufferPips = 3;
input ENUM_TIMEFRAMES FvgTF        = PERIOD_M5;
input ENUM_TIMEFRAMES SweepTF      = PERIOD_M15;

//+------------------------------------------------------------------+
//| Expert Initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
    Print("SilverBullet EA initialized.");
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert Tick Handler                                              |
//+------------------------------------------------------------------+
void OnTick()
{
    static datetime lastBarTime = 0;
    datetime currentBarTime = iTime(_Symbol, FvgTF, 0);
    if (currentBarTime == lastBarTime)
        return;
    lastBarTime = currentBarTime;

    string bias = GetDailyBias();
    if (bias == "neutral")
    {
        Print("Neutral bias — skipping this candle.");
        return;
    }

    DetectFVGSignals(bias);
    DetectSweepSignals(bias);
    ManageOpenPositions();
}

//+------------------------------------------------------------------+
//| Get Daily Bias (based on 30-day high/low)                        |
//+------------------------------------------------------------------+
string GetDailyBias()
{
    int window = 30;
    ENUM_TIMEFRAMES tf = PERIOD_D1;

    if (Bars(_Symbol, tf) < window + 1)
        return "neutral";

    double maxHigh = iHigh(_Symbol, tf, iHighest(_Symbol, tf, MODE_HIGH, window, 1));
    double minLow  = iLow(_Symbol, tf, iLowest(_Symbol, tf, MODE_LOW, window, 1));
    double close   = iClose(_Symbol, tf, 0);

    double diffHigh = (maxHigh - close) / close;
    double diffLow  = (close - minLow) / close;

    if (diffHigh > 0.01)
        return "bullish";
    else if (diffLow > 0.01)
        return "bearish";

    return "neutral";
}

//+------------------------------------------------------------------+
//| Detect FVG + MSS Setup                                           |
//+------------------------------------------------------------------+
void DetectFVGSignals(string bias)
{
    double prevHigh = -DBL_MAX;
    double prevLow  = DBL_MAX;
    int group = GroupSize;

    for (int i = group; i < group * 2; i++)
    {
        prevHigh = MathMax(prevHigh, iHigh(_Symbol, SweepTF, i));
        prevLow  = MathMin(prevLow, iLow(_Symbol, SweepTF, i));
    }

    for (int j = 2; j >= 0; j--)
    {
        double high = iHigh(_Symbol, SweepTF, j);
        double low = iLow(_Symbol, SweepTF, j);
        double open = iOpen(_Symbol, SweepTF, j);
        double close = iClose(_Symbol, SweepTF, j);

        if (high > prevHigh && close < open && bias == "bearish")
        {
            CheckFVGSetupWithLimitOrder("bearish");
            return;
        }
        else if (low < prevLow && close > open && bias == "bullish")
        {
            CheckFVGSetupWithLimitOrder("bullish");
            return;
        }
    }
}

//+------------------------------------------------------------------+
//| FVG Detection with Limit Orders                                  |
//+------------------------------------------------------------------+
void CheckFVGSetupWithLimitOrder(string direction)
{
    int lookahead = 12;
    double slOffset = 3 * _Point;

    for (int i = lookahead + 2; i >= 2; i--)
    {
        double h1 = iHigh(_Symbol, FvgTF, i - 2);
        double h2 = iHigh(_Symbol, FvgTF, i - 1);
        double h3 = iHigh(_Symbol, FvgTF, i);
        double l1 = iLow(_Symbol, FvgTF, i - 2);
        double l2 = iLow(_Symbol, FvgTF, i - 1);
        double l3 = iLow(_Symbol, FvgTF, i);
        double o3 = iOpen(_Symbol, FvgTF, i);
        double c3 = iClose(_Symbol, FvgTF, i);

        if (direction == "bearish" && c3 < o3 && h3 < h2 && c3 < iClose(_Symbol, FvgTF, i - 1) && l3 > h1)
        {
            double entry = (h1 + l3) / 2;
            double sl = entry + slOffset;
            double nextLow = iLow(_Symbol, FvgTF, iLowest(_Symbol, FvgTF, MODE_LOW, 20, 1));
            double tp = nextLow + 3 * _Point;

            Alert("Bearish FVG detected — placing Sell Limit.");
            double slPoints = MathAbs(entry - sl) / _Point;
            double lotSize = CalculateLotSize(slPoints);
            trade.SellLimit(lotSize, _Symbol, entry, sl, tp, 0);
            return;
        }

        if (direction == "bullish" && c3 > o3 && l3 > l2 && c3 > iClose(_Symbol, FvgTF, i - 1) && h3 < l1)
        {
            double entry = (l1 + h3) / 2;
            double sl = entry - slOffset;
            double nextHigh = iHigh(_Symbol, FvgTF, iHighest(_Symbol, FvgTF, MODE_HIGH, 20, 1));
            double tp = nextHigh - 3 * _Point;

            Alert("Bullish FVG detected — placing Buy Limit.");
            trade.BuyLimit(LotSize, _Symbol, entry, sl, tp, NULL);
            return;
        }
    }
}

//+------------------------------------------------------------------+
//| Detect Breaker + FVG Combo                                       |
//+------------------------------------------------------------------+
void DetectBreakerSignals(string bias)
{
    double prevHigh = -DBL_MAX;
    double prevLow  = DBL_MAX;
    int group = GroupSize;

    for (int i = group; i < group * 2; i++)
    {
        prevHigh = MathMax(prevHigh, iHigh(_Symbol, SweepTF, i));
        prevLow  = MathMin(prevLow, iLow(_Symbol, SweepTF, i));
    }

    double h = iHigh(_Symbol, SweepTF, 0);
    double l = iLow(_Symbol, SweepTF, 0);
    double o = iOpen(_Symbol, SweepTF, 0);
    double c = iClose(_Symbol, SweepTF, 0);

    bool bearishSweep = (h > prevHigh && c < o && bias == "bearish");
    bool bullishSweep = (l < prevLow && c > o && bias == "bullish");

    if (!bearishSweep && !bullishSweep) return;

    for (int i = 10; i >= 3; i--)
    {
        double o3 = iOpen(_Symbol, FvgTF, i);
        double c3 = iClose(_Symbol, FvgTF, i);
        double h3 = iHigh(_Symbol, FvgTF, i);
        double l3 = iLow(_Symbol, FvgTF, i);
        double h1 = iHigh(_Symbol, FvgTF, i - 2);
        double l1 = iLow(_Symbol, FvgTF, i - 2);
        double h2 = iHigh(_Symbol, FvgTF, i - 1);
        double l2 = iLow(_Symbol, FvgTF, i - 1);

        if (bearishSweep && c3 > o3 && c3 > h1 && l3 > l2)
        {
            double entry = (iHigh(_Symbol, FvgTF, i + 1) + iLow(_Symbol, FvgTF, i + 3)) / 2;
            double sl = entry + 3 * _Point;
            double tp = entry - RRRatio * (sl - entry);
            Alert("Breaker-FVG bearish setup — placing Sell Limit.");
            double slPoints = MathAbs(entry - sl) / _Point;
            double lotSize = CalculateLotSize(slPoints);
            trade.SellLimit(lotSize, _Symbol, entry, sl, tp, 0);
            return;
        }

        if (bullishSweep && c3 < o3 && c3 < l1 && h3 < h2)
        {
            double entry = (iLow(_Symbol, FvgTF, i + 1) + iHigh(_Symbol, FvgTF, i + 3)) / 2;
            double sl = entry - 3 * _Point;
            double tp = entry + RRRatio * (entry - sl);
            Alert("Breaker-FVG bullish setup — placing Buy Limit.");
            trade.BuyLimit(LotSize, _Symbol, entry, sl, tp, NULL);
            return;
        }
    }
}

//+------------------------------------------------------------------+
//| Simple Sweep-Based Reversals                                     |
//+------------------------------------------------------------------+
void DetectSweepSignals(string bias)
{
    int lookback = 30;
    double buffer = 20 * _Point;

    double high = iHigh(_Symbol, FvgTF, 2);
    double low = iLow(_Symbol, FvgTF, 2);
    double open = iOpen(_Symbol, FvgTF, 2);
    double close = iClose(_Symbol, FvgTF, 2);

    double recentHigh = -DBL_MAX;
    double recentLow = DBL_MAX;

    for (int j = 3; j < 3 + lookback; j++)
    {
        recentHigh = MathMax(recentHigh, iHigh(_Symbol, FvgTF, j));
        recentLow  = MathMin(recentLow, iLow(_Symbol, FvgTF, j));
    }

    if (low < recentLow && close > open && bias == "bullish")
    {
        double sl = low - buffer;
        double tp = close + RRRatio * (close - sl);
        Alert("Simple Bullish Sweep — Buying now.");
        trade.Buy(LotSize, _Symbol, SymbolInfoDouble(_Symbol, SYMBOL_ASK), sl, tp);
    }

    if (high > recentHigh && close < open && bias == "bearish")
    {
        double sl = high + buffer;
        double tp = close - RRRatio * (sl - close);
        Alert("Simple Bearish Sweep — Selling now.");
        trade.Sell(LotSize, _Symbol, SymbolInfoDouble(_Symbol, SYMBOL_BID), sl, tp);
    }
}

//+------------------------------------------------------------------+
//| Calculating Lot Size                                     |
//+------------------------------------------------------------------+

double CalculateLotSize(double slPoints)
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double riskAmount = balance * (RiskPercent / 100.0);
    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    double lotStep   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

    double lot = (riskAmount / (slPoints * tickValue / tickSize));
    lot = MathFloor(lot / lotStep) * lotStep; // Round down to valid step

    double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

    return MathMax(minLot, MathMin(maxLot, lot));
}


//+------------------------------------------------------------------+
//| Managing open positions                                          |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double partialTrigger = balance * (PartialTPPercent / 100.0);
    double buffer = BreakevenBufferPips * _Point;

    for (int i = PositionsTotal() - 1; i >= 0; i--)
    {
        if (!PositionGetTicket(i)) continue;
        string symbol = PositionGetString(POSITION_SYMBOL);
        if (symbol != _Symbol) continue;

        ulong ticket = PositionGetInteger(POSITION_TICKET);
        double volume = PositionGetDouble(POSITION_VOLUME);
        double priceOpen = PositionGetDouble(POSITION_PRICE_OPEN);
        double currentProfit = PositionGetDouble(POSITION_PROFIT);
        double sl = PositionGetDouble(POSITION_SL);
        double tp = PositionGetDouble(POSITION_TP);
        int type = (int)PositionGetInteger(POSITION_TYPE);

        if (currentProfit >= partialTrigger)
        {
            double closeVolume = volume * PartialCloseRatio;
            closeVolume = NormalizeDouble(closeVolume, (int)SymbolInfoInteger(_Symbol, SYMBOL_VOLUME_DIGITS));

            if (type == POSITION_TYPE_BUY)
            {
                trade.PositionClosePartial(ticket, closeVolume);
                double newSL = priceOpen + buffer;
                trade.PositionModify(ticket, newSL, tp);
                Alert("Partial TP hit (BUY): closed 80% and moved SL to breakeven + buffer");
            }
            else if (type == POSITION_TYPE_SELL)
            {
                trade.PositionClosePartial(ticket, closeVolume);
                double newSL = priceOpen - buffer;
                trade.PositionModify(ticket, newSL, tp);
                Alert("Partial TP hit (SELL): closed 80% and moved SL to breakeven + buffer");
            }
        }
    }
}


