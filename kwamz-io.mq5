#include <Trade\Trade.mqh>

CTrade trade;

input double RR = 4.0; // Risk-to-Reward Ratio
input int SlBufferMultiplier = 2; // ATR multiplier for stop-loss buffer
input ENUM_TIMEFRAMES SweepTF = PERIOD_M15;
input ENUM_TIMEFRAMES HourlyTF = PERIOD_H1;
const ENUM_TIMEFRAMES FvgTFs[3] = {PERIOD_M5, PERIOD_M3, PERIOD_M2};

// New York Session times (hardcoded for UTC+3 server)
datetime NYSessionOpenTime, NYSessionCloseTime;

//+------------------------------------------------------------------+
int OnInit()
{
    Print("SessionSweepFVG EA initialized.");
    UpdateNYSessionTimes();
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
    static datetime lastBarTime = 0;
    datetime current = iTime(_Symbol, SweepTF, 0);
    if (current == lastBarTime) return;
    lastBarTime = current;

    datetime now = TimeCurrent();
    if (now < NYSessionOpenTime || now > NYSessionCloseTime) return; // Restrict to NY session in server time (UTC+3)
    Print("Trading session active. Current time: ", TimeToString(now));

    string bias = GetDailyBias();
    if (bias == "neutral" || !IsTrendAligned(bias)) 
    {
        Print("No valid bias or trend alignment. Bias: ", bias);
        return;
    }
    Print("Valid bias detected: ", bias);

    datetime nyOpen = GetNYOpenTime();
    double sessionHigh, sessionLow;
    if (!GetSessionHighLow(nyOpen, sessionHigh, sessionLow)) 
    {
        Print("Failed to get session high/low.");
        return;
    }
    Print("Session High: ", DoubleToString(sessionHigh, _Digits), " Session Low: ", DoubleToString(sessionLow, _Digits));

    if (DetectSessionSweep(bias, sessionHigh, sessionLow))
    {
        for (int i = 0; i < ArraySize(FvgTFs); i++)
        {
            if (DetectAndTradeFVG(bias, FvgTFs[i])) break;
        }
    }

    ManageOpenPositions();
}

//+------------------------------------------------------------------+
void UpdateNYSessionTimes()
{
    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);

    // Hardcode NY session for UTC+3 server (EAT)
    // DST in US (March 9 - November 2, 2025): NY session is 8:30 AM - 1:00 PM ET (UTC-4), so 3:30 PM - 8:00 PM EAT
    dt.hour = 15; // 3:30 PM EAT
    dt.min = 30;
    dt.sec = 0;
    NYSessionOpenTime = StructToTime(dt);

    dt.hour = 20; // 8:00 PM EAT
    dt.min = 0;
    dt.sec = 0;
    NYSessionCloseTime = StructToTime(dt);
}

//+------------------------------------------------------------------+
string GetDailyBias()
{
    ENUM_TIMEFRAMES tf = PERIOD_D1;
    int window = 30;

    if (Bars(_Symbol, tf) < window + 1)
        return "neutral";

    double maxHigh = iHigh(_Symbol, tf, iHighest(_Symbol, tf, MODE_HIGH, window, 1));
    double minLow  = iLow(_Symbol, tf, iLowest(_Symbol, tf, MODE_LOW, window, 1));
    double close   = iClose(_Symbol, tf, 0);

    double diffHigh = (maxHigh - close) / close;
    double diffLow  = (close - minLow) / close;

    if (diffHigh > 0.01) return "bullish";
    if (diffLow > 0.01) return "bearish";
    return "neutral";
}

//+------------------------------------------------------------------+
datetime GetNYOpenTime()
{
    datetime now = TimeCurrent();
    MqlDateTime dt;
    TimeToStruct(now, dt);
    dt.hour = 16; // Base UTC time (adjusted by hardcoded session times)
    dt.min = 30;
    dt.sec = 0;
    return StructToTime(dt);
}

//+------------------------------------------------------------------+
bool GetSessionHighLow(datetime nyOpen, double &high, double &low)
{
    int count = iBars(_Symbol, HourlyTF);
    if (count < 5) return false;

    high = -DBL_MAX;
    low = DBL_MAX;

    for (int i = 1; i < 12; i++)
    {
        datetime t = iTime(_Symbol, HourlyTF, i);
        if (t >= nyOpen) continue;

        high = MathMax(high, iHigh(_Symbol, HourlyTF, i));
        low = MathMin(low, iLow(_Symbol, HourlyTF, i));
    }

    return true;
}

//+------------------------------------------------------------------+
bool DetectSessionSweep(string bias, double sessionHigh, double sessionLow)
{
    for (int i = 15; i >= 0; i--) // Increased lookback to 15 bars
    {
        double h = iHigh(_Symbol, SweepTF, i);
        double l = iLow(_Symbol, SweepTF, i);
        double o = iOpen(_Symbol, SweepTF, i);
        double c = iClose(_Symbol, SweepTF, i);

        if (bias == "bullish" && l < sessionLow && c > o)
        {
            Print("Bullish sweep detected below session low. High: ", DoubleToString(sessionHigh, _Digits), 
                  " Low: ", DoubleToString(sessionLow, _Digits), " Candle Low: ", DoubleToString(l, _Digits));
            return true;
        }
        if (bias == "bearish" && h > sessionHigh && c < o)
        {
            Print("Bearish sweep detected above session high. High: ", DoubleToString(sessionHigh, _Digits), 
                  " Low: ", DoubleToString(sessionLow, _Digits), " Candle High: ", DoubleToString(h, _Digits));
            return true;
        }
    }
    return false;
}

//+------------------------------------------------------------------+
bool DetectAndTradeFVG(string bias, ENUM_TIMEFRAMES tf)
{
    if (iBars(_Symbol, tf) < 4) return false;

    double atr = iATR(_Symbol, tf, 14, 1);
    double buffer = atr * SlBufferMultiplier;

    for (int i = 3; i >= 1; i--)
    {
        double h1 = iHigh(_Symbol, tf, i + 2);
        double l1 = iLow(_Symbol, tf, i + 2);
        double h2 = iHigh(_Symbol, tf, i + 1);
        double l2 = iLow(_Symbol, tf, i + 1);
        double h3 = iHigh(_Symbol, tf, i);
        double l3 = iLow(_Symbol, tf, i);

        if (bias == "bullish" && l3 > h1 && (h1 - l3) / _Point > 10)
        {
            double entry = (l3 + h1) / 2;
            double sl = l1 - buffer;
            double tp = entry + RR * (entry - sl);
            Print("Bullish FVG detected. Entry: ", DoubleToString(entry, _Digits), " SL: ", DoubleToString(sl, _Digits), 
                  " TP: ", DoubleToString(tp, _Digits), " Timeframe: ", EnumToString(tf));
            return PlaceBuyLimit(entry, sl, tp);
        }
        if (bias == "bearish" && h3 < l1 && (h3 - l1) / _Point > 10)
        {
            double entry = (h3 + l1) / 2;
            double sl = h1 + buffer;
            double tp = entry - RR * (sl - entry);
            Print("Bearish FVG detected. Entry: ", DoubleToString(entry, _Digits), " SL: ", DoubleToString(sl, _Digits), 
                  " TP: ", DoubleToString(tp, _Digits), " Timeframe: ", EnumToString(tf));
            return PlaceSellLimit(entry, sl, tp);
        }
    }
    return false;
}

//+------------------------------------------------------------------+
double CalculateLotSize(double slPoints)
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double riskPercent = 1.0; // 1% risk per trade
    double riskAmount = balance * (riskPercent / 100.0);
    double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    double lot = (riskAmount / (slPoints * tickValue / tickSize));
    lot = MathFloor(lot / lotStep) * lotStep;
    double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    return MathMax(minLot, MathMin(maxLot, lot));
}

//+------------------------------------------------------------------+
bool PlaceBuyLimit(double entry, double sl, double tp)
{
    if (PositionSelect(_Symbol)) return false;

    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);
    dt.hour = 20; // NY session close (8:00 PM EAT)
    dt.min = 0;
    dt.sec = 0;
    datetime expiration = StructToTime(dt);

    double lotSize = CalculateLotSize(MathAbs(entry - sl) / _Point);
    bool placed = trade.BuyLimit(lotSize, entry, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, expiration);
    if (placed) Print("Buy Limit placed successfully. Entry: ", DoubleToString(entry, _Digits), " SL: ", DoubleToString(sl, _Digits), " TP: ", DoubleToString(tp, _Digits));
    else Print("Failed to place Buy Limit. Error: ", trade.ResultRetcodeDescription());
    return placed;
}

//+------------------------------------------------------------------+
bool PlaceSellLimit(double entry, double sl, double tp)
{
    if (PositionSelect(_Symbol)) return false;

    MqlDateTime dt;
    TimeToStruct(TimeCurrent(), dt);
    dt.hour = 20; // NY session close (8:00 PM EAT)
    dt.min = 0;
    dt.sec = 0;
    datetime expiration = StructToTime(dt);

    double lotSize = CalculateLotSize(MathAbs(entry - sl) / _Point);
    bool placed = trade.SellLimit(lotSize, entry, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, expiration);
    if (placed) Print("Sell Limit placed successfully. Entry: ", DoubleToString(entry, _Digits), " SL: ", DoubleToString(sl, _Digits), " TP: ", DoubleToString(tp, _Digits));
    else Print("Failed to place Sell Limit. Error: ", trade.ResultRetcodeDescription());
    return placed;
}

//+------------------------------------------------------------------+
bool IsTrendAligned(string bias)
{
    double sma = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_SMA, PRICE_CLOSE, 1);
    double price = iClose(_Symbol, PERIOD_H1, 1);
    return (bias == "bullish" && price > sma) || (bias == "bearish" && price < sma);
}

//+------------------------------------------------------------------+
void ManageOpenPositions()
{
    if (!PositionSelect(_Symbol)) return;

    ulong ticket = PositionGetInteger(POSITION_TICKET);
    double entry = PositionGetDouble(POSITION_PRICE_OPEN);
    double currentPrice = SymbolInfoDouble(_Symbol, PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? SYMBOL_ASK : SYMBOL_BID);
    double tp = PositionGetDouble(POSITION_TP);

    ManageTrailingStop(ticket, entry, currentPrice);
    ManageBreakeven(ticket, entry, currentPrice, tp);
}

//+------------------------------------------------------------------+
void ManageTrailingStop(ulong ticket, double entry, double currentPrice)
{
    double sl = PositionGetDouble(POSITION_SL);
    double trailPips = 10 * _Point;
    if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY && currentPrice - entry > trailPips)
    {
        if (sl < currentPrice - trailPips) trade.PositionModify(ticket, currentPrice - trailPips, PositionGetDouble(POSITION_TP));
    }
    else if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL && entry - currentPrice > trailPips)
    {
        if (sl > currentPrice + trailPips) trade.PositionModify(ticket, currentPrice + trailPips, PositionGetDouble(POSITION_TP));
    }
}

//+------------------------------------------------------------------+
void ManageBreakeven(ulong ticket, double entry, double currentPrice, double tp)
{
    double profit = currentPrice - entry;
    if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY && profit > (tp - entry) * 0.5)
    {
        trade.PositionModify(ticket, entry, tp);
    }
    else if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL && profit < (entry - tp) * 0.5)
    {
        trade.PositionModify(ticket, entry, tp);
    }
}