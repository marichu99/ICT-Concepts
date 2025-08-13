//+------------------------------------------------------------------+
//|         ICT-Inspired Lightweight FVG Bias EA (with Visuals)     |
//+------------------------------------------------------------------+
#include <Trade\Trade.mqh>
CTrade trade;

input double LotSize = 0.5;
input double RR = 4.0;
input int SlBufferPips = 3;
input ENUM_TIMEFRAMES SweepTF = PERIOD_M15;
input ENUM_TIMEFRAMES HourlyTF = PERIOD_H1;
input ENUM_TIMEFRAMES BiasTF = PERIOD_D1;
const ENUM_TIMEFRAMES FvgTFs[3] = { PERIOD_M5, PERIOD_M3, PERIOD_M2 };

//+------------------------------------------------------------------+
int OnInit() {
   Print("ICT-FVG EA Initialized");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick() {
   static datetime lastBarTime = 0;
   datetime current = iTime(_Symbol, SweepTF, 0);
   if (current == lastBarTime) return;
   lastBarTime = current;

   string bias = GetDailyBias();
   if (bias == "neutral") return;

   datetime nyOpen = GetNYOpenTime();
   double sessionHigh, sessionLow;
   if (!GetSessionHighLow(nyOpen, sessionHigh, sessionLow)) return;

   if (DetectSessionSweep(bias, sessionHigh, sessionLow)) {
      for (int i = 0; i < ArraySize(FvgTFs); i++) {
         if (DetectAndTradeFVG(bias, FvgTFs[i])) break;
      }
   }
   ShowRecentFVGs();
}

//+------------------------------------------------------------------+
string GetDailyBias() {
   int window = 30;
   if (Bars(_Symbol, BiasTF) < window + 3) return "neutral";

   double currentClose = iClose(_Symbol, BiasTF, 0);
   double maxHigh = iHigh(_Symbol, BiasTF, iHighest(_Symbol, BiasTF, MODE_HIGH, window, 1));
   double minLow  = iLow(_Symbol, BiasTF, iLowest(_Symbol, BiasTF, MODE_LOW, window, 1));

   bool seekingBuyStops  = currentClose < maxHigh && (maxHigh - currentClose) / currentClose > 0.01;
   bool seekingSellStops = currentClose > minLow  && (currentClose - minLow) / currentClose > 0.01;

   string liquidityBias = "neutral";
   if (seekingBuyStops) liquidityBias = "bullish";
   else if (seekingSellStops) liquidityBias = "bearish";

   string fvgBias = "neutral";
   for (int i = 30; i >= 2; i--) {
      double h1 = iHigh(_Symbol, BiasTF, i);
      double l1 = iLow(_Symbol, BiasTF, i);
      double h3 = iHigh(_Symbol, BiasTF, i - 2);
      double l3 = iLow(_Symbol, BiasTF, i - 2);
      if (l3 > h1 && currentClose < h1) { fvgBias = "bullish"; break; }
      if (h3 < l1 && currentClose > l1) { fvgBias = "bearish"; break; }
   }

   if (liquidityBias == fvgBias && liquidityBias != "neutral") return liquidityBias;
   if (liquidityBias != "neutral") return liquidityBias;
   if (fvgBias != "neutral") return fvgBias;
   return "neutral";
}

//+------------------------------------------------------------------+
datetime GetNYOpenTime() {
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   dt.hour = 16; dt.min = 30; dt.sec = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
bool GetSessionHighLow(datetime nyOpen, double &high, double &low) {
   high = -DBL_MAX; low = DBL_MAX;
   for (int i = 1; i < 12; i++) {
      datetime t = iTime(_Symbol, HourlyTF, i);
      if (t >= nyOpen) continue;
      high = MathMax(high, iHigh(_Symbol, HourlyTF, i));
      low  = MathMin(low, iLow(_Symbol, HourlyTF, i));
   }
   return true;
}

//+------------------------------------------------------------------+
bool DetectSessionSweep(string bias, double sessionHigh, double sessionLow) {
   for (int i = 2; i >= 0; i--) {
      double h = iHigh(_Symbol, SweepTF, i);
      double l = iLow(_Symbol, SweepTF, i);
      double o = iOpen(_Symbol, SweepTF, i);
      double c = iClose(_Symbol, SweepTF, i);
      if (bias == "bullish" && l < sessionLow && c > o) return true;
      if (bias == "bearish" && h > sessionHigh && c < o) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
bool DetectAndTradeFVG(string bias, ENUM_TIMEFRAMES tf) {
   for (int i = 3; i >= 1; i--) {
      double h1 = iHigh(_Symbol, tf, i + 2);
      double l1 = iLow(_Symbol, tf, i + 2);
      double h3 = iHigh(_Symbol, tf, i);
      double l3 = iLow(_Symbol, tf, i);

      if (bias == "bullish" && l3 > h1) {
         double entry = (l3 + h1) / 2;
         double sl = l1 - SlBufferPips * _Point;
         double tp = entry + RR * (entry - sl);
         return PlaceBuyLimit(entry, sl, tp);
      }
      if (bias == "bearish" && h3 < l1) {
         double entry = (h3 + l1) / 2;
         double sl = h1 + SlBufferPips * _Point;
         double tp = entry - RR * (sl - entry);
         return PlaceSellLimit(entry, sl, tp);
      }
   }
   return false;
}

//+------------------------------------------------------------------+
bool PlaceBuyLimit(double entry, double sl, double tp) {
   if (PositionSelect(_Symbol)) return false;
   datetime expiration = iTime(_Symbol, PERIOD_D1, 0) + 86340; // 23:55
   return trade.BuyLimit(LotSize, entry, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, expiration);
}

bool PlaceSellLimit(double entry, double sl, double tp) {
   if (PositionSelect(_Symbol)) return false;
   datetime expiration = iTime(_Symbol, PERIOD_D1, 0) + 86340; // 23:55
   return trade.SellLimit(LotSize, entry, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, expiration);
}

//+------------------------------------------------------------------+
bool FindFVG(ENUM_TIMEFRAMES tf, int barIndex, bool &isBullish, double &fvgLow, double &fvgHigh) {
   if (iBars(_Symbol, tf) <= barIndex + 2) return false;
   double h1 = iHigh(_Symbol, tf, barIndex + 2);
   double l1 = iLow(_Symbol, tf, barIndex + 2);
   double h3 = iHigh(_Symbol, tf, barIndex);
   double l3 = iLow(_Symbol, tf, barIndex);
   if (l3 > h1) { isBullish = true;  fvgLow = h1; fvgHigh = l3; return true; }
   if (h3 < l1) { isBullish = false; fvgHigh = l1; fvgLow = h3; return true; }
   return false;
}

void OnStart() {
   for (int i = 0; i < 100; i++) {
      Print(TimeToString(iTime(_Symbol, PERIOD_D1, i)));
   }
}

void DrawFVGBox(string namePrefix, int index, ENUM_TIMEFRAMES tf, bool isBullish, double fvgLow, double fvgHigh)
{
    datetime time1 = iTime(_Symbol, tf, index + 2);
    datetime time2 = iTime(_Symbol, tf, index);
    string objName = namePrefix + "_" + IntegerToString(index);

    ObjectDelete(0, objName);
    if (!ObjectCreate(0, objName, OBJ_RECTANGLE, 0, time1, fvgHigh, time2, fvgLow))
    {
        Print("Failed to create object: ", objName);
        return;
    }

    color fillColor = isBullish ? clrGreen : clrRed;

    ObjectSetInteger(0, objName, OBJPROP_COLOR, fillColor);
    ObjectSetInteger(0, objName, OBJPROP_STYLE, STYLE_SOLID);
    ObjectSetInteger(0, objName, OBJPROP_WIDTH, 1);
    ObjectSetInteger(0, objName, OBJPROP_BACK, true);
}


void ShowRecentFVGs() {
   for (int i = 0; i < 3; i++) {
      bool isBullish; double low, high;
      if (FindFVG(PERIOD_M15, i, isBullish, low, high))
         DrawFVGBox("FVG", i, PERIOD_M15, isBullish, low, high);
   }
}
