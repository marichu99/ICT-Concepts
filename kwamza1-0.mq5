//+------------------------------------------------------------------+
//|                                      ICT_2022_Trading_Strategy.mq5 |
//|                        Expert Advisor for ICT 2022 Trading Strategy |
//| Based on Michael Huddleston's ICT 2022 Model (Liquidity & Time)    |
//+------------------------------------------------------------------+
#include <Trade\Trade.mqh>
CTrade trade;
#property copyright "Grok 3, xAI"
#property link      "https://x.ai"
#property version   "1.00"

//--- Input Parameters
input double RiskPercent = 1.0; // Risk per trade (% of account balance)
input double RiskRewardRatio = 3.0; // Risk:Reward Ratio (1:3)
input int DailyBiasPeriod = 14; // Period for daily bias calculation (e.g., RSI)
input int FibLevel = 618; // Fibonacci OTE level (618 = 61.8%)
input ENUM_TIMEFRAMES TF_Daily = PERIOD_D1; // Daily timeframe for bias
input ENUM_TIMEFRAMES TF_High = PERIOD_H1; // 1-hour timeframe for perspective
input ENUM_TIMEFRAMES TF_Liquidity = PERIOD_M15; // 15-minute timeframe for liquidity
input ENUM_TIMEFRAMES TF_Execution = PERIOD_M5; // 5-minute timeframe for trade execution
input ENUM_TIMEFRAMES HourlyTF = PERIOD_H1
input int LondonOpenHour = 3; // London session open (NY time, 24-hour format)
input int NewYorkOpenHour = 8; // New York session open (NY time, 24-hour format)
input int RangeStartHour = 0; // New York midnight open for range

//--- Global Variables
datetime lastBarTime;
bool isLondonSession = false;
bool isNewYorkSession = false;
double rangeHigh, rangeLow;
bool liquiditySwept = false;
bool marketStructureShift = false;
double fvgHigh, fvgLow; // Fair Value Gap levels
double orderBlockHigh, orderBlockLow; // Order Block levels
enum TRADE_DIRECTION { NEUTRAL, BULLISH, BEARISH };
TRADE_DIRECTION dailyBias = NEUTRAL;

//--- Include Trade Library
#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   lastBarTime = 0;
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check for new bar on execution timeframe
   datetime currentTime = TimeCurrent();
   if(lastBarTime != iTime(_Symbol, TF_Execution, 0))
   {
      lastBarTime = iTime(_Symbol, TF_Execution, 0);
      
      // Get current time components
      MqlDateTime dt;
      TimeToStruct(currentTime, dt);
      
      // Check session times
      isLondonSession = (dt.hour == LondonOpenHour && dt.min >= 0 && dt.min < 60);
      isNewYorkSession = (dt.hour == NewYorkOpenHour && dt.min >= 0 && dt.min < 60);
      
      // Determine daily bias
      dailyBias = GetDailyBias();
      
      // Mark range high/low from NY midnight to session open
      if(dt.hour == RangeStartHour && dt.min == 0)
      {
         MarkDailyRange();
      }
      
      // London Session Strategy
      if(isLondonSession)
      {
         LondonSessionStrategy();
      }
      
      // New York Session Strategy
      if(isNewYorkSession)
      {
         NewYorkSessionStrategy();
      }
   }
}

//+------------------------------------------------------------------+
//| Get Daily Bias (Bullish/Bearish)                                  |
//+------------------------------------------------------------------+
TRADE_DIRECTION GetDailyBias()
{
    ENUM_TIMEFRAMES tf = PERIOD_D1;
    int window = 30;

    if (Bars(_Symbol, tf) < window + 1)
        return NEUTRAL;

    // (I) Daily Timeframe Order Flow
    double highestHigh = iHigh(_Symbol, tf, iHighest(_Symbol, tf, MODE_HIGH, window, 1));
    double lowestLow = iLow(_Symbol, tf, iLowest(_Symbol, tf, MODE_LOW, window, 1));
    double currentHigh = iHigh(_Symbol, tf, 1);
    double currentLow = iLow(_Symbol, tf, 1);
    double close = iClose(_Symbol, tf, 0);

    bool bullishOrderFlow = (currentHigh > highestHigh && currentLow > lowestLow);
    bool bearishOrderFlow = (currentHigh < highestHigh && currentLow < lowestLow);

    // (II) Imbalance to Rebalance
    double avgRange = 0;
    for (int i = 1; i <= window; i++) avgRange += iHigh(_Symbol, tf, i) - iLow(_Symbol, tf, i);
    avgRange /= window;
    double lastRange = iHigh(_Symbol, tf, 1) - iLow(_Symbol, tf, 1);
    bool imbalanceUp = (lastRange > avgRange * 1.5 && close > iClose(_Symbol, tf, 1)); // Large upward move
    bool imbalanceDown = (lastRange > avgRange * 1.5 && close < iClose(_Symbol, tf, 1)); // Large downward move

    // (III) Liquidity Sweep Confirmation (simplified with session data)
    datetime nyOpen = GetNYOpenTime();
    double sessionHigh, sessionLow;
    GetSessionHighLow(nyOpen, sessionHigh, sessionLow);
    bool liquiditySweepUp = (iLow(_Symbol, tf, 1) < sessionLow);
    bool liquiditySweepDown = (iHigh(_Symbol, tf, 1) > sessionHigh);

    // Combine conditions
    if ((bullishOrderFlow || imbalanceUp || liquiditySweepUp) && close > iMA(_Symbol, tf, 50, 0, MODE_SMA, PRICE_CLOSE))
        return BULLISH;
    if ((bearishOrderFlow || imbalanceDown || liquiditySweepDown) && close < iMA(_Symbol, tf, 50, 0, MODE_SMA, PRICE_CLOSE))
        return BEARISH;
    return NEUTRAL;
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
//| Mark Daily Range (High/Low from NY Midnight to Session Open)      |
//+------------------------------------------------------------------+
void MarkDailyRange()
{
   datetime startTime = iTime(_Symbol, TF_Liquidity, 0) - (iTime(_Symbol, TF_Liquidity, 0) % 86400);
   datetime endTime = TimeCurrent();
   int startBar = iBarShift(_Symbol, TF_Liquidity, startTime);
   int endBar = iBarShift(_Symbol, TF_Liquidity, endTime);
   
   rangeHigh = iHigh(_Symbol, TF_Liquidity, iHighest(_Symbol, TF_Liquidity, MODE_HIGH, startBar - endBar + 1, endBar));
   rangeLow = iLow(_Symbol, TF_Liquidity, iLowest(_Symbol, TF_Liquidity, MODE_LOW, startBar - endBar + 1, endBar));
   liquiditySwept = false;
}

//+------------------------------------------------------------------+
//| London Session Strategy                                           |
//+------------------------------------------------------------------+
void LondonSessionStrategy()
{
   // Check for liquidity sweep
   double currentHigh = iHigh(_Symbol, TF_Liquidity, 1);
   double currentLow = iLow(_Symbol, TF_Liquidity, 1);
   
   if(dailyBias == BULLISH && currentHigh > rangeHigh && !liquiditySwept)
   {
      liquiditySwept = true;
      CheckMarketStructureShift(true);
   }
   else if(dailyBias == BEARISH && currentLow < rangeLow && !liquiditySwept)
   {
      liquiditySwept = true;
      CheckMarketStructureShift(false);
   }
}

//+------------------------------------------------------------------+
//| New York Session Strategy                                         |
//+------------------------------------------------------------------+
void NewYorkSessionStrategy()
{
   // Scenario 1: Liquidity taken in London, look for retracement
   if(liquiditySwept)
   {
      double fibLevel = GetFibonacciOTE();
      if(fibLevel > 0)
      {
         ExecuteTradeAtOTE(fibLevel);
      }
   }
   // Scenario 2: Range-bound in London, check for liquidity sweep
   else
   {
      double currentHigh = iHigh(_Symbol, TF_Liquidity, 1);
      double currentLow = iLow(_Symbol, TF_Liquidity, 1);
      
      if(dailyBias == BULLISH && currentHigh > rangeHigh)
      {
         liquiditySwept = true;
         CheckMarketStructureShift(true);
      }
      else if(dailyBias == BEARISH && currentLow < rangeLow)
      {
         liquiditySwept = true;
         CheckMarketStructureShift(false);
      }
   }
}

//+------------------------------------------------------------------+
//| Check Market Structure Shift (MSS) with Displacement              |
//+------------------------------------------------------------------+
void CheckMarketStructureShift(bool isBullish)
{
   // Check MSS on lower timeframe (5-minute)
   double prevHigh = iHigh(_Symbol, TF_Execution, 2);
   double prevLow = iLow(_Symbol, TF_Execution, 2);
   double currentHigh = iHigh(_Symbol, TF_Execution, 1);
   double currentLow = iLow(_Symbol, TF_Execution, 1);
   
   if(isBullish && currentLow < prevLow)
   {
      marketStructureShift = true;
      IdentifyPDArray(true);
   }
   else if(!isBullish && currentHigh > prevHigh)
   {
      marketStructureShift = true;
      IdentifyPDArray(false);
   }
}

//+------------------------------------------------------------------+
//| Identify PD-Array (Fair Value Gap, Order Block)                   |
//+------------------------------------------------------------------+
void IdentifyPDArray(bool isBullish)
{
   // Simplified FVG detection (gap between candles)
   double prevClose = iClose(_Symbol, TF_Execution, 2);
   double prevOpen = iOpen(_Symbol, TF_Execution, 2);
   double currentOpen = iOpen(_Symbol, TF_Execution, 1);
   
   if(isBullish && currentOpen > prevClose)
   {
      fvgHigh = currentOpen;
      fvgLow = prevClose;
   }
   else if(!isBullish && currentOpen < prevClose)
   {
      fvgHigh = prevClose;
      fvgLow = currentOpen;
   }
   
   // Check for trade entry at PD-Array
   double currentPrice = iClose(_Symbol, TF_Execution, 0);
   if(isBullish && currentPrice >= fvgLow && currentPrice <= fvgHigh)
   {
      ExecuteTrade(true);
   }
   else if(!isBullish && currentPrice <= fvgHigh && currentPrice >= fvgLow)
   {
      ExecuteTrade(false);
   }
}

//+------------------------------------------------------------------+
//| Get Fibonacci Optimal Trade Entry (OTE) Level                     |
//+------------------------------------------------------------------+
double GetFibonacciOTE()
{
   double high = iHigh(_Symbol, TF_High, iHighest(_Symbol, TF_High, MODE_HIGH, 10, 1));
   double low = iLow(_Symbol, TF_High, iLowest(_Symbol, TF_High, MODE_LOW, 10, 1));
   double fib = (high - low) * (FibLevel / 1000.0) + low;
   double currentPrice = iClose(_Symbol, TF_Execution, 0);
   
   if(MathAbs(currentPrice - fib) <= _Point * 10) // Within 10 pips
      return fib;
   return 0;
}

//+------------------------------------------------------------------+
//| Execute Trade at OTE Level                                        |
//+------------------------------------------------------------------+
void ExecuteTradeAtOTE(double fibLevel)
{
   double lotSize = CalculateLotSize();
   double sl, tp, entryPrice = fibLevel;
   datetime currentTime = TimeCurrent();
   
   if(dailyBias == BULLISH)
   {
      sl = fibLevel - (rangeHigh - rangeLow);
      tp = fibLevel + (rangeHigh - rangeLow) * RiskRewardRatio;
      if(trade.BuyLimit(lotSize, entryPrice, _Symbol, sl, tp, 0, 0, "ICT Buy Limit OTE"))
      {
         Print("Buy Limit Order Placed: Symbol=", _Symbol, ", Lot=", lotSize, 
               ", Entry=", entryPrice, ", SL=", sl, ", TP=", tp, ", Time=", TimeToString(currentTime));
      }
      else
      {
         Print("Buy Limit Order Failed: Error=", GetLastError(), ", Time=", TimeToString(currentTime));
      }
   }
   else if(dailyBias == BEARISH)
   {
      sl = fibLevel + (rangeHigh - rangeLow);
      tp = fibLevel - (rangeHigh - rangeLow) * RiskRewardRatio;
      if(trade.SellLimit(lotSize, entryPrice, _Symbol, sl, tp, 0, 0, "ICT Sell Limit OTE"))
      {
         Print("Sell Limit Order Placed: Symbol=", _Symbol, ", Lot=", lotSize, 
               ", Entry=", entryPrice, ", SL=", sl, ", TP=", tp, ", Time=", TimeToString(currentTime));
      }
      else
      {
         Print("Sell Limit Order Failed: Error=", GetLastError(), ", Time=", TimeToString(currentTime));
      }
   }
}

//+------------------------------------------------------------------+
//| Execute Trade at PD-Array                                         |
//+------------------------------------------------------------------+
void ExecuteTrade(bool isBullish)
{
   double lotSize = CalculateLotSize();
   double sl, tp, entryPrice;
   datetime currentTime = TimeCurrent();
   
   if(isBullish)
   {
      entryPrice = fvgLow; // Enter at lower bound of FVG
      sl = rangeLow;
      tp = rangeHigh;
      if(trade.BuyLimit(lotSize, entryPrice, _Symbol, sl, tp, 0, 0, "ICT Buy Limit PD-Array"))
      {
         Print("Buy Limit Order Placed: Symbol=", _Symbol, ", Lot=", lotSize, 
               ", Entry=", entryPrice, ", SL=", sl, ", TP=", tp, ", Time=", TimeToString(currentTime));
      }
      else
      {
         Print("Buy Limit Order Failed: Error=", GetLastError(), ", Time=", TimeToString(currentTime));
      }
   }
   else
   {
      entryPrice = fvgHigh; // Enter at upper bound of FVG
      sl = rangeHigh;
      tp = rangeLow;
      if(trade.SellLimit(lotSize, entryPrice, _Symbol, sl, tp, 0, 0, "ICT Sell Limit PD-Array"))
      {
         Print("Sell Limit Order Placed: Symbol=", _Symbol, ", Lot=", lotSize, 
               ", Entry=", entryPrice, ", SL=", sl, ", TP=", tp, ", Time=", TimeToString(currentTime));
      }
      else
      {
         Print("Sell Limit Order Failed: Error=", GetLastError(), ", Time=", TimeToString(currentTime));
      }
   }
}

//+------------------------------------------------------------------+
//| Calculate Lot Size based on Risk Percentage                       |
//+------------------------------------------------------------------+
double CalculateLotSize()
{
   double accountBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = accountBalance * (RiskPercent / 100.0);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double stopLossPips = (rangeHigh - rangeLow) / tickSize;
   double lotSize = NormalizeDouble(riskAmount / (stopLossPips * tickValue), 2);
   return lotSize;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Cleanup if needed
}