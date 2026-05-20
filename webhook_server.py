#property strict
#property version "5.14"

//+------------------------------------------------------------------+
//  Original: Webhook Trader v5.10
//  Added: Prop-firm risk-based lot sizing for FundedNext
//+------------------------------------------------------------------+

//--- ENUM must be declared before input variables
enum ENUM_FNEXT_PLAN
{
   PLAN_2STEP = 0,  // Stellar 2-Step  (Daily 5% / Max 10%)
   PLAN_1STEP = 1,  // Stellar 1-Step  (Daily 3% / Max 6%)
   PLAN_LITE  = 2   // Stellar Lite    (Daily 4% / Max 8%)
};

//--- Original inputs (LotSize replaced by risk-based calc)
input string ServerURL        = "https://mtrade-webhook.onrender.com/signals";
input int    CheckSeconds     = 5;
input int    MagicNumber      = 123456;
input int    SL_Buffer_Points = 30;

//--- New prop risk inputs
input ENUM_FNEXT_PLAN PlanType         = PLAN_2STEP; // FundedNext Plan Type
input double          RiskPercent      = 0.25;       // Risk % per trade (0.25 = 0.25%)
input double          InitialBalance   = 15000.0;    // Your starting account balance
input bool            EnableDailyGuard = true;       // Block trades near daily loss limit
input bool            EnableTotalGuard = true;       // Block trades near max drawdown limit
input double          GuardBuffer      = 0.5;        // Stop trading X% before the firm's limit

//--- Manual trade confirmation panel
input bool            EnableTradePanel = true;       // Show accept/decline panel before opening new trades
input double          FixedLotSize     = 0.01;       // Lot size used when Risk % is OFF
input double          MaxLotSize       = 2.00;       // Prop-style hard cap. 0 = no cap
input int             PanelX           = 10;         // Panel X position
input int             PanelY           = 20;         // Panel Y position

//--- Globals (original)
datetime lastCheck  = 0;
bool     firstFetch = true;

string processedSignals[];
int    processedCount = 0;

//--- Globals (new)
double   g_DayStartBalance = 0;
datetime g_LastDayTime     = 0;

//--- Manual panel globals
string          PANEL_PREFIX       = "WT_PANEL_";
bool            g_HasPendingTrade  = false;
string          g_PendingSignalKey = "";
string          g_PendingSymbol    = "";
string          g_PendingTF        = "";
ENUM_ORDER_TYPE g_PendingType      = ORDER_TYPE_BUY;
double          g_PendingSL        = 0.0;
double          g_PendingTP3       = 0.0;
bool            g_PanelUseSL       = true;
bool            g_PanelUseTP       = true;
bool            g_PanelUseRisk     = true;
double          g_PanelRiskPercent = 0.0;

//+------------------------------------------------------------------+
int OnInit()
{
   ArrayResize(processedSignals, 1000);

   g_DayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_LastDayTime     = TimeCurrent();

   double dailyUSD = InitialBalance * GetDailyLimitPct() / 100.0;
   double maxUSD   = InitialBalance * GetMaxLimitPct()   / 100.0;

   Print("========================================");
   Print("Webhook Trader v5.13 started");
   Print("Plan         : ", GetPlanName());
   Print("Daily Limit  : ", GetDailyLimitPct(), "% = $", DoubleToString(dailyUSD, 2));
   Print("Max Drawdown : ", GetMaxLimitPct(),   "% = $", DoubleToString(maxUSD,   2));
   Print("Risk/Trade   : ", RiskPercent, "%");
   Print("Balance      : $", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2));
   Print("========================================");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   DeleteTradePanel();
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id,
                  const long &lparam,
                  const double &dparam,
                  const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_ENDEDIT && sparam == PANEL_PREFIX + "RISK_EDIT")
   {
      GetPanelRiskPercent();
      DrawTradePanel();
      return;
   }

   if(id != CHARTEVENT_OBJECT_CLICK)
      return;

   if(StringFind(sparam, PANEL_PREFIX) != 0)
      return;

   ObjectSetInteger(0, sparam, OBJPROP_STATE, false);

   if(sparam == PANEL_PREFIX + "BTN_SL")
   {
      g_PanelUseSL = !g_PanelUseSL;
      DrawTradePanel();
   }
   else if(sparam == PANEL_PREFIX + "BTN_TP")
   {
      g_PanelUseTP = !g_PanelUseTP;
      DrawTradePanel();
   }
   else if(sparam == PANEL_PREFIX + "BTN_RISK")
   {
      g_PanelUseRisk = !g_PanelUseRisk;
      DrawTradePanel();
   }
   else if(sparam == PANEL_PREFIX + "BTN_ACCEPT")
   {
      AcceptPanelTrade();
   }
   else if(sparam == PANEL_PREFIX + "BTN_DECLINE")
   {
      DeclinePanelTrade();
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   CheckDayReset();

   if(TimeCurrent() - lastCheck < CheckSeconds)
      return;

   lastCheck = TimeCurrent();
   FetchSignals();
}

//+------------------------------------------------------------------+
//  PLAN HELPERS
//+------------------------------------------------------------------+
string GetPlanName()
{
   if(PlanType == PLAN_1STEP) return "Stellar 1-Step";
   if(PlanType == PLAN_LITE)  return "Stellar Lite";
   return "Stellar 2-Step";
}

double GetDailyLimitPct()
{
   if(PlanType == PLAN_1STEP) return 3.0;
   if(PlanType == PLAN_LITE)  return 4.0;
   return 5.0;
}

double GetMaxLimitPct()
{
   if(PlanType == PLAN_1STEP) return 6.0;
   if(PlanType == PLAN_LITE)  return 8.0;
   return 10.0;
}

//+------------------------------------------------------------------+
//  DAILY RESET
//+------------------------------------------------------------------+
void CheckDayReset()
{
   MqlDateTime now, last;
   TimeToStruct(TimeCurrent(), now);
   TimeToStruct(g_LastDayTime, last);

   if(now.day != last.day)
   {
      g_DayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      g_LastDayTime     = TimeCurrent();
      Print("New day — balance reset to $", DoubleToString(g_DayStartBalance, 2));
   }
}

//+------------------------------------------------------------------+
//  ACCOUNT GUARD
//+------------------------------------------------------------------+
bool AccountGuardCheck()
{
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   double checkValue = MathMin(balance, equity);

   if(EnableDailyGuard)
   {
      double dailyFloor = g_DayStartBalance * (1.0 - (GetDailyLimitPct() - GuardBuffer) / 100.0);
      if(checkValue <= dailyFloor)
      {
         Print("DAILY GUARD — Equity $", DoubleToString(checkValue, 2),
               " at floor $", DoubleToString(dailyFloor, 2), " — no new trades.");
         return false;
      }
   }

   if(EnableTotalGuard)
   {
      double totalFloor = InitialBalance * (1.0 - (GetMaxLimitPct() - GuardBuffer) / 100.0);
      if(checkValue <= totalFloor)
      {
         Print("TOTAL GUARD — Equity $", DoubleToString(checkValue, 2),
               " at floor $", DoubleToString(totalFloor, 2), " — no new trades.");
         return false;
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//  LOT SIZE CALCULATOR
//  Lots = (Balance * Risk%) / (SL_distance_points * ValuePerPoint)
//+------------------------------------------------------------------+
double CalcLotSizeByRisk(string symbol, double entryPrice, double slPrice, double riskPercent, bool logCalc = true)
{
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);

   if(slPrice <= 0 || entryPrice <= 0)
   {
      if(logCalc)
         Print("CalcLotSize: Invalid entry/SL — using min lot.");
      return minLot;
   }

   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney  = balance * riskPercent / 100.0;
   double point      = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double tickSize   = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);

   if(point <= 0 || tickSize <= 0 || tickValue <= 0)
   {
      if(logCalc)
         Print("CalcLotSize: Bad tick info for ", symbol, " — using min lot.");
      return minLot;
   }

   double slPoints      = MathAbs(entryPrice - slPrice) / point;
   double valuePerPoint = (tickValue / tickSize) * point;

   if(slPoints <= 0 || valuePerPoint <= 0)
   {
      if(logCalc)
         Print("CalcLotSize: Zero division guard — using min lot.");
      return minLot;
   }

   double lots = ApplyLotCap(symbol, riskMoney / (slPoints * valuePerPoint));

   if(logCalc)
   {
      Print("--- LOT CALC [", symbol, "] ---");
      Print("Balance     : $", DoubleToString(balance, 2));
      Print("Risk Money  : $", DoubleToString(riskMoney, 2), " (", riskPercent, "%)");
      Print("SL Distance : ", DoubleToString(slPoints, 1), " pts");
      Print("Value/Point : $", DoubleToString(valuePerPoint, 6));
      Print("Final Lots  : ", DoubleToString(lots, 2));
      if(MaxLotSize > 0.0)
         Print("Max Lot Cap : ", DoubleToString(MaxLotSize, 2));
      Print("-----------------------------");
   }

   return lots;
}

//+------------------------------------------------------------------+
double CalcLotSize(string symbol, double entryPrice, double slPrice)
{
   return CalcLotSizeByRisk(symbol, entryPrice, slPrice, RiskPercent, true);
}

//+------------------------------------------------------------------+
double RiskMoneyForPercent(double riskPercent)
{
   return AccountInfoDouble(ACCOUNT_BALANCE) * riskPercent / 100.0;
}

//+------------------------------------------------------------------+
double SLPoints(string symbol, double entryPrice, double slPrice)
{
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);

   if(point <= 0.0 || entryPrice <= 0.0 || slPrice <= 0.0)
      return 0.0;

   return MathAbs(entryPrice - slPrice) / point;
}

//+------------------------------------------------------------------+
double ApplyLotCap(string symbol, double volume)
{
   double lots = NormalizeVolume(symbol, volume);

   if(MaxLotSize > 0.0 && lots > MaxLotSize)
      lots = NormalizeVolume(symbol, MaxLotSize);

   return lots;
}

//+------------------------------------------------------------------+
void DeleteTradePanel()
{
   int total = ObjectsTotal(0, 0, -1);

   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, 0, -1);

      if(StringFind(name, PANEL_PREFIX) == 0)
         ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
void PanelLabel(string suffix, int x, int y, string text, color textColor = clrWhite, int fontSize = 9)
{
   string name = PANEL_PREFIX + suffix;

   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, textColor);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
void PanelButton(string suffix, int x, int y, int w, int h, string text, color bgColor, color textColor = clrWhite)
{
   string name = PANEL_PREFIX + suffix;

   ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bgColor);
   ObjectSetInteger(0, name, OBJPROP_COLOR, textColor);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, clrDimGray);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
void PanelEdit(string suffix, int x, int y, int w, int h, string text)
{
   string name = PANEL_PREFIX + suffix;

   ObjectCreate(0, name, OBJ_EDIT, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, clrBlack);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, clrDimGray);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, name, OBJPROP_READONLY, false);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
double GetPanelRiskPercent()
{
   string editName = PANEL_PREFIX + "RISK_EDIT";

   if(ObjectFind(0, editName) >= 0)
   {
      double editedRisk = StringToDouble(ObjectGetString(0, editName, OBJPROP_TEXT));

      if(editedRisk > 0.0)
         g_PanelRiskPercent = editedRisk;
   }

   if(g_PanelRiskPercent <= 0.0)
      g_PanelRiskPercent = RiskPercent;

   if(g_PanelRiskPercent > 100.0)
      g_PanelRiskPercent = 100.0;

   return g_PanelRiskPercent;
}

//+------------------------------------------------------------------+
double PendingEntryPrice()
{
   if(g_PendingSymbol == "")
      return 0.0;

   if(!SymbolSelect(g_PendingSymbol, true))
      return 0.0;

   if(g_PendingType == ORDER_TYPE_BUY)
      return SymbolInfoDouble(g_PendingSymbol, SYMBOL_ASK);

   return SymbolInfoDouble(g_PendingSymbol, SYMBOL_BID);
}

//+------------------------------------------------------------------+
double PreviewLotAndRR(double &rrOut)
{
   rrOut = 0.0;

   if(!g_HasPendingTrade || g_PendingSymbol == "")
      return 0.0;

   double entry = PendingEntryPrice();

   if(entry <= 0.0)
      return ApplyLotCap(g_PendingSymbol, FixedLotSize);

   double slAdjusted = g_PanelUseSL ? ApplySLBuffer(g_PendingSymbol, g_PendingType, g_PendingSL) : 0.0;

   if(g_PanelUseSL && g_PanelUseTP && slAdjusted > 0.0 && g_PendingTP3 > 0.0)
   {
      double riskDistance   = MathAbs(entry - slAdjusted);
      double rewardDistance = MathAbs(g_PendingTP3 - entry);

      if(riskDistance > 0.0)
         rrOut = rewardDistance / riskDistance;
   }

   if(g_PanelUseRisk && g_PanelUseSL && slAdjusted > 0.0)
      return CalcLotSizeByRisk(g_PendingSymbol, entry, slAdjusted, GetPanelRiskPercent(), false);

   return ApplyLotCap(g_PendingSymbol, FixedLotSize);
}

//+------------------------------------------------------------------+
string ToggleText(string label, bool enabled)
{
   return label + ": " + (enabled ? "ON" : "OFF");
}

//+------------------------------------------------------------------+
color ToggleColor(bool enabled)
{
   return enabled ? clrSeaGreen : clrFireBrick;
}

//+------------------------------------------------------------------+
void DrawTradePanel()
{
   DeleteTradePanel();

   if(!g_HasPendingTrade)
      return;

   int x = PanelX;
   int y = PanelY;
   int w = 285;
   int h = 238;

   string bgName = PANEL_PREFIX + "BG";
   ObjectCreate(0, bgName, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, bgName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, bgName, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, bgName, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, bgName, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, bgName, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, bgName, OBJPROP_BGCOLOR, clrBlack);
   ObjectSetInteger(0, bgName, OBJPROP_COLOR, clrDimGray);
   ObjectSetInteger(0, bgName, OBJPROP_BORDER_TYPE, BORDER_FLAT);

   string side = (g_PendingType == ORDER_TYPE_BUY ? "BUY" : "SELL");

   double rr = 0.0;
   double previewLot = PreviewLotAndRR(rr);
   string rrText = rr > 0.0 ? DoubleToString(rr, 2) : "N/A";
   double previewEntry = PendingEntryPrice();
   double previewSL = g_PanelUseSL ? ApplySLBuffer(g_PendingSymbol, g_PendingType, g_PendingSL) : 0.0;
   double previewSLPoints = SLPoints(g_PendingSymbol, previewEntry, previewSL);
   string riskInfo = "Risk $" + DoubleToString(RiskMoneyForPercent(GetPanelRiskPercent()), 2) +
                     " | SL pts " + (previewSLPoints > 0.0 ? DoubleToString(previewSLPoints, 1) : "N/A");

   PanelLabel("TITLE", x + 12, y + 10, "NEW TRADE", clrWhite, 11);
   PanelLabel("PAIR",  x + 12, y + 34, "Pair: " + g_PendingSymbol, clrWhite, 9);
   PanelLabel("SIDE",  x + 12, y + 54, "Side: " + side + " | TF: " + g_PendingTF, clrSilver, 9);

   PanelButton("BTN_SL",   x + 12,  y + 78, 82, 22, ToggleText("SL",   g_PanelUseSL),   ToggleColor(g_PanelUseSL));
   PanelButton("BTN_TP",   x + 102, y + 78, 82, 22, ToggleText("TP",   g_PanelUseTP),   ToggleColor(g_PanelUseTP));
   PanelButton("BTN_RISK", x + 192, y + 78, 82, 22, ToggleText("RISK", g_PanelUseRisk), ToggleColor(g_PanelUseRisk));

   PanelLabel("RISK_LABEL", x + 12, y + 112, "Risk %", clrSilver, 9);
   PanelEdit("RISK_EDIT", x + 70, y + 108, 58, 22, DoubleToString(GetPanelRiskPercent(), 2));
   PanelLabel("RISK_BAL", x + 136, y + 112, "% of balance", clrSilver, 9);

   PanelLabel("LOT", x + 12, y + 142, "Lot size: " + DoubleToString(previewLot, 2) + " | RR: " + rrText, clrWhite, 9);
   PanelLabel("RISK_INFO", x + 12, y + 162, riskInfo, clrSilver, 8);
   PanelLabel("NOTE", x + 12, y + 180, "Max lot cap: " + (MaxLotSize > 0.0 ? DoubleToString(MaxLotSize, 2) : "OFF"), clrGray, 8);

   PanelButton("BTN_ACCEPT",  x + 12,  y + 205, 124, 24, "ACCEPT",  clrSeaGreen);
   PanelButton("BTN_DECLINE", x + 148, y + 205, 124, 24, "DECLINE", clrFireBrick);

   ChartRedraw(0);
}

//+------------------------------------------------------------------+
void ShowTradePanel(string signalKey, string symbol, ENUM_ORDER_TYPE type, double sl, double tp3, string tf)
{
   if(g_HasPendingTrade)
   {
      Print("Panel already has a pending trade. New signal ignored until Accept/Decline: ", signalKey);
      return;
   }

   g_HasPendingTrade  = true;
   g_PendingSignalKey = signalKey;
   g_PendingSymbol    = symbol;
   g_PendingTF        = tf;
   g_PendingType      = type;
   g_PendingSL        = sl;
   g_PendingTP3       = tp3;
   g_PanelUseSL       = true;
   g_PanelUseTP       = true;
   g_PanelUseRisk     = true;
   g_PanelRiskPercent = RiskPercent;

   SymbolSelect(symbol, true);
   DrawTradePanel();
}

//+------------------------------------------------------------------+
void ClearPendingPanelTrade()
{
   g_HasPendingTrade  = false;
   g_PendingSignalKey = "";
   g_PendingSymbol    = "";
   g_PendingTF        = "";
   g_PendingSL        = 0.0;
   g_PendingTP3       = 0.0;

   DeleteTradePanel();
}

//+------------------------------------------------------------------+
void AcceptPanelTrade()
{
   if(!g_HasPendingTrade)
      return;

   double panelRisk = GetPanelRiskPercent();

   Print("Panel ACCEPT: ", g_PendingSignalKey,
         " SL=", g_PanelUseSL,
         " TP=", g_PanelUseTP,
         " Risk=", g_PanelUseRisk,
         " RiskPct=", panelRisk);

   ExecuteTradeEx(g_PendingSymbol,
                  g_PendingType,
                  g_PendingSL,
                  g_PendingTP3,
                  g_PendingTF,
                  g_PanelUseSL,
                  g_PanelUseTP,
                  g_PanelUseRisk,
                  panelRisk,
                  FixedLotSize);

   ClearPendingPanelTrade();
}

//+------------------------------------------------------------------+
void DeclinePanelTrade()
{
   if(!g_HasPendingTrade)
      return;

   Print("Panel DECLINE: ", g_PendingSignalKey);
   ClearPendingPanelTrade();
}

//+------------------------------------------------------------------+
void FetchSignals()
{
   char post[];
   char result[];
   string headers = "";
   string resultHeaders;

   ResetLastError();

   int res = WebRequest("GET", ServerURL, headers, 5000, post, result, resultHeaders);

   if(res == -1)
   {
      Print("WebRequest failed. Error: ", GetLastError());
      return;
   }

   string response = CharArrayToString(result);

   if(firstFetch)
   {
      Print("Startup protection: first fetch ignored.");
      firstFetch = false;
      return;
   }

   if(res == 200)
   {
      if(StringLen(response) <= 2)
      {
         Print("No new signals.");
         return;
      }
      ParseSignals(response);
   }
   else
   {
      Print("Bad HTTP response: ", res);
   }
}

//+------------------------------------------------------------------+
void ParseSignals(string json)
{
   int pos = 0;

   while(pos < StringLen(json))
   {
      int actionKey = StringFind(json, "\"action\"", pos);
      if(actionKey < 0) break;

      string id     = JsonString(json, "id",        actionKey);
      string action = JsonString(json, "action",    actionKey);
      string ticker = JsonString(json, "ticker",    actionKey);
      string tf     = JsonString(json, "timeframe", actionKey);

      double sl  = JsonNumber(json, "sl",  actionKey);
      double tp3 = JsonNumber(json, "tp3", actionKey);

      StringToLower(action);

      string symbol    = ConvertSymbol(ticker);
      string signalKey = id;

      if(signalKey == "")
         signalKey = action + "|" + ticker + "|" + tf + "|" +
                     DoubleToString(sl, 5) + "|" + DoubleToString(tp3, 5);

      if(IsProcessed(signalKey))
      {
         Print("Duplicate ignored: ", signalKey);
      }
      else
      {
         MarkProcessed(signalKey);
         Print("NEW SIGNAL: ", signalKey, " SYMBOL=", symbol);

         if(action == "buy")
            HandleNewSignal(signalKey, symbol, ORDER_TYPE_BUY, sl, tp3, tf);
         else if(action == "sell")
            HandleNewSignal(signalKey, symbol, ORDER_TYPE_SELL, sl, tp3, tf);
         else if(action == "breakeven")
            MoveSLToBreakeven(symbol, tf);
         else
            Print("Unknown action: ", action);
      }

      pos = actionKey + 10;
   }
}

//+------------------------------------------------------------------+
void HandleNewSignal(string signalKey, string symbol, ENUM_ORDER_TYPE type, double sl, double tp3, string tf)
{
   if(EnableTradePanel)
   {
      ShowTradePanel(signalKey, symbol, type, sl, tp3, tf);
      return;
   }

   ExecuteTrade(symbol, type, sl, tp3, tf);
}

//+------------------------------------------------------------------+
void ExecuteTrade(string symbol, ENUM_ORDER_TYPE type, double sl, double tp3, string tf)
{
   ExecuteTradeEx(symbol, type, sl, tp3, tf, true, true, true, RiskPercent, FixedLotSize);
}

//+------------------------------------------------------------------+
void ExecuteTradeEx(string symbol,
                    ENUM_ORDER_TYPE type,
                    double sl,
                    double tp3,
                    string tf,
                    bool useSL,
                    bool useTP,
                    bool useRisk,
                    double riskPercent,
                    double fixedLot)
{
   ResetLastError();

   // Prop guard check — blocks trade if near daily/total limit
   if(!AccountGuardCheck())
   {
      Print("Trade BLOCKED by prop guard. Symbol=", symbol);
      return;
   }

   if(!SymbolSelect(symbol, true))
   {
      Print("SymbolSelect failed: ", symbol, " Error: ", GetLastError());
      return;
   }

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      Print("Terminal trading not allowed.");
      return;
   }

   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      Print("EA trading not allowed.");
      return;
   }

   double price = (type == ORDER_TYPE_BUY)
                  ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                  : SymbolInfoDouble(symbol, SYMBOL_BID);

   if(price <= 0)
   {
      Print("Invalid price for ", symbol);
      return;
   }

   // Apply SL buffer first when SL is enabled.
   double slAdjusted = useSL ? ApplySLBuffer(symbol, type, sl) : 0.0;

   double volume = ApplyLotCap(symbol, fixedLot > 0.0 ? fixedLot : FixedLotSize);

   if(useRisk && useSL && slAdjusted > 0.0)
      volume = CalcLotSizeByRisk(symbol, price, slAdjusted, riskPercent, true);

   MqlTradeRequest request;
   MqlTradeResult  result;

   ZeroMemory(request);
   ZeroMemory(result);

   request.action       = TRADE_ACTION_DEAL;
   request.symbol       = symbol;
   request.volume       = volume;
   request.type         = type;
   request.price        = NormalizePrice(symbol, price);
   request.deviation    = 50;
   request.magic        = MagicNumber;
   request.comment      = "TV|" + tf;
   request.type_filling = GetFillingMode(symbol);

   if(useSL && slAdjusted > 0) request.sl = slAdjusted;
   if(useTP && tp3 > 0)        request.tp = NormalizePrice(symbol, tp3);

   bool sent = OrderSend(request, result);

   Print("========== TRADE ATTEMPT ==========");
   Print("Sent: ",    sent);
   Print("Symbol: ",  symbol);
   Print("Type: ",    type == ORDER_TYPE_BUY ? "BUY" : "SELL");
   Print("Lot: ",     volume);
   if(MaxLotSize > 0.0)
      Print("Max Lot Cap: ", MaxLotSize);
   Print("Entry: ",   price);
   Print("Use SL: ",  useSL);
   Print("Use TP: ",  useTP);
   Print("Use Risk: ", useRisk);
   Print("Risk %: ",  riskPercent);
   Print("SL: ",      useSL ? DoubleToString(slAdjusted, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) : "OFF");
   Print("TP3: ",     useTP ? DoubleToString(tp3, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) : "OFF");
   Print("Retcode: ", result.retcode);
   Print("Comment: ", result.comment);
   Print("===================================");

   if(result.retcode == TRADE_RETCODE_DONE)
      Print("Trade opened successfully.");
   else
      Print("Trade failed: ", result.comment);
}

//+------------------------------------------------------------------+
void MoveSLToBreakeven(string symbol, string tf)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);

      if(!PositionSelectByTicket(ticket))                   continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)      continue;

      string comment = PositionGetString(POSITION_COMMENT);
      if(StringFind(comment, "TV|" + tf) < 0) continue;

      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double tp    = PositionGetDouble(POSITION_TP);

      MqlTradeRequest request;
      MqlTradeResult  result;

      ZeroMemory(request);
      ZeroMemory(result);

      request.action   = TRADE_ACTION_SLTP;
      request.position = ticket;
      request.symbol   = symbol;
      request.sl       = NormalizePrice(symbol, entry);
      request.tp       = tp;
      request.magic    = MagicNumber;

      bool sent = OrderSend(request, result);

      Print("========== BREAKEVEN ATTEMPT ==========");
      Print("Sent: ",    sent);
      Print("Ticket: ",  ticket);
      Print("Symbol: ",  symbol);
      Print("New SL: ",  entry);
      Print("Retcode: ", result.retcode);
      Print("Comment: ", result.comment);
      Print("=======================================");

      if(result.retcode == TRADE_RETCODE_DONE)
         Print("SL moved to breakeven.");
      else
         Print("Breakeven failed: ", result.comment);
   }
}

//+------------------------------------------------------------------+
//  SYMBOL AUTO-DETECTION
//  1. Normalise the incoming ticker to a clean base keyword
//  2. Try exact match first
//  3. Scan ALL broker symbols for the best partial match
//  4. Log what was found so you can see it in Experts tab
//+------------------------------------------------------------------+
// Helper: scan all broker symbols for any keyword in a list
// Returns shortest matching symbol name, or "" if nothing found
string ScanBrokerSymbols(string &keywords[], int count)
{
   string found  = "";
   int    bestLen = 9999;
   int    total  = SymbolsTotal(false);

   for(int i = 0; i < total; i++)
   {
      string sym      = SymbolName(i, false);
      string symUpper = sym;
      StringToUpper(symUpper);
      int symLen = StringLen(sym);

      for(int k = 0; k < count; k++)
      {
         int kwLen = StringLen(keywords[k]);
         if(kwLen < 2) continue; // skip single-char keywords
         if(symLen >= kwLen && StringFind(symUpper, keywords[k]) >= 0)
         {
            if(found == "" || symLen < bestLen)
            {
               found   = sym;
               bestLen = symLen;
            }
            break; // found a match for this symbol, move to next
         }
      }
   }
   return found;
}

string ConvertSymbol(string s)
{
   StringToUpper(s);

   // Strip exchange prefix e.g. "PEPPERSTONE:NAS100" ? "NAS100"
   int colonPos = StringFind(s, ":");
   if(colonPos >= 0) s = StringSubstr(s, colonPos + 1);

   // --- Build keyword list for this asset ---
   // Each asset gets ALL the names brokers might use
   string keywords[];
   int    kwCount = 0;

   if(s == "UJ" || StringFind(s, "USDJPY") >= 0)
   {
      ArrayResize(keywords, 1); keywords[0]="USDJPY"; kwCount=1;
   }
   else if(s == "GJ" || StringFind(s, "GBPJPY") >= 0)
   {
      ArrayResize(keywords, 1); keywords[0]="GBPJPY"; kwCount=1;
   }
   else if(s == "EJ" || StringFind(s, "EURJPY") >= 0)
   {
      ArrayResize(keywords, 1); keywords[0]="EURJPY"; kwCount=1;
   }
   else if(StringFind(s, "EURUSD") >= 0)
   {
      ArrayResize(keywords, 1); keywords[0]="EURUSD"; kwCount=1;
   }
   else if(StringFind(s, "GBPUSD") >= 0)
   {
      ArrayResize(keywords, 1); keywords[0]="GBPUSD"; kwCount=1;
   }
   else if(StringFind(s, "GOLD") >= 0 || StringFind(s, "XAUUSD") >= 0)
   {
      ArrayResize(keywords, 3);
      keywords[0]="XAUUSD"; keywords[1]="GOLD"; keywords[2]="XAU";
      kwCount=3;
   }
   else if(StringFind(s, "SILVER") >= 0 || StringFind(s, "XAGUSD") >= 0)
   {
      ArrayResize(keywords, 3);
      keywords[0]="XAGUSD"; keywords[1]="SILVER"; keywords[2]="XAG";
      kwCount=3;
   }
   else if(StringFind(s, "BITCOIN") >= 0 || StringFind(s, "BTCUSD") >= 0 || StringFind(s, "BTC") >= 0)
   {
      // Brokers use: BTCUSD, BITCOIN, BTC, XBTUSD, BTCUSDT
      ArrayResize(keywords, 5);
      keywords[0]="BTCUSD"; keywords[1]="BITCOIN"; keywords[2]="BTC";
      keywords[3]="XBTUSD"; keywords[4]="BTCUSDT";
      kwCount=5;
   }
   else if(StringFind(s, "ETHUSD") >= 0 || StringFind(s, "ETH") >= 0)
   {
      ArrayResize(keywords, 3);
      keywords[0]="ETHUSD"; keywords[1]="ETHEREUM"; keywords[2]="ETH";
      kwCount=3;
   }
   else if(StringFind(s, "NAS100") >= 0 || StringFind(s, "US100") >= 0 ||
           StringFind(s, "NASDAQ") >= 0 || StringFind(s, "NDX")   >= 0)
   {
      // Brokers use: NAS100, US100, USTEC, NDX, NQ100, NASDAQ
      ArrayResize(keywords, 6);
      keywords[0]="NAS100"; keywords[1]="US100";  keywords[2]="USTEC";
      keywords[3]="NDX";    keywords[4]="NQ100";  keywords[5]="NASDAQ";
      kwCount=6;
   }
   else if(StringFind(s, "US30") >= 0 || StringFind(s, "DOW") >= 0 || StringFind(s, "DJI") >= 0)
   {
      ArrayResize(keywords, 4);
      keywords[0]="US30"; keywords[1]="DOW"; keywords[2]="DJI"; keywords[3]="DJ30";
      kwCount=4;
   }
   else if(StringFind(s, "SP500") >= 0 || StringFind(s, "SPX") >= 0 || StringFind(s, "US500") >= 0)
   {
      ArrayResize(keywords, 4);
      keywords[0]="US500"; keywords[1]="SP500"; keywords[2]="SPX"; keywords[3]="S&P";
      kwCount=4;
   }
   else if(StringFind(s, "USOIL") >= 0 || StringFind(s, "WTI") >= 0 || StringFind(s, "OIL") >= 0)
   {
      ArrayResize(keywords, 4);
      keywords[0]="USOIL"; keywords[1]="WTI"; keywords[2]="OIL"; keywords[3]="CRUDE";
      kwCount=4;
   }
   else
   {
      // Unknown symbol — just search using whatever came in
      ArrayResize(keywords, 1); keywords[0]=s; kwCount=1;
   }

   // --- Step 1: try exact match on all keywords first ---
   for(int k = 0; k < kwCount; k++)
   {
      if(SymbolSelect(keywords[k], true) && SymbolInfoDouble(keywords[k], SYMBOL_BID) > 0)
      {
         Print("Symbol resolved (exact): ", s, " ? ", keywords[k]);
         return keywords[k];
      }
   }

   // --- Step 2: scan all broker symbols using keyword list ---
   string found = ScanBrokerSymbols(keywords, kwCount);

   if(found != "")
   {
      SymbolSelect(found, true);
      Print("Symbol resolved (auto-scan): ", s, " ? ", found);
      return found;
   }

   Print("Symbol NOT found on broker: ", s, " — check Market Watch manually.");
   return s;
}

//+------------------------------------------------------------------+
string JsonString(string json, string key, int fromPos)
{
   string pattern = "\"" + key + "\"";
   int keyPos = StringFind(json, pattern, fromPos);
   if(keyPos < 0) return "";

   int colon = StringFind(json, ":", keyPos);
   int start = StringFind(json, "\"", colon) + 1;
   int end   = StringFind(json, "\"", start);

   if(start < 1 || end < 0) return "";
   return StringSubstr(json, start, end - start);
}

//+------------------------------------------------------------------+
double JsonNumber(string json, string key, int fromPos)
{
   string pattern = "\"" + key + "\"";
   int keyPos = StringFind(json, pattern, fromPos);
   if(keyPos < 0) return 0;

   int colon = StringFind(json, ":", keyPos);
   int start = colon + 1;

   while(start < StringLen(json) && StringGetCharacter(json, start) == ' ')
      start++;

   int end = start;
   while(end < StringLen(json))
   {
      ushort c = StringGetCharacter(json, end);
      if((c >= '0' && c <= '9') || c == '.' || c == '-') end++;
      else break;
   }

   return StringToDouble(StringSubstr(json, start, end - start));
}

//+------------------------------------------------------------------+
bool IsProcessed(string key)
{
   for(int i = 0; i < processedCount; i++)
      if(processedSignals[i] == key) return true;
   return false;
}

//+------------------------------------------------------------------+
void MarkProcessed(string key)
{
   if(processedCount >= 1000)
   {
      for(int i = 0; i < 1000; i++) processedSignals[i] = "";
      processedCount = 0;
   }
   processedSignals[processedCount] = key;
   processedCount++;
}

//+------------------------------------------------------------------+
double NormalizeVolume(string symbol, double volume)
{
   double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

   if(volume < minLot) volume = minLot;
   if(volume > maxLot) volume = maxLot;

   volume = MathFloor(volume / lotStep) * lotStep;
   return NormalizeDouble(volume, 2);
}

//+------------------------------------------------------------------+
double NormalizePrice(string symbol, double price)
{
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   return NormalizeDouble(price, digits);
}

//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode(string symbol)
{
   int filling = (int)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK) return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
double ApplySLBuffer(string symbol, ENUM_ORDER_TYPE type, double sl)
{
   if(sl <= 0) return 0;

   double point  = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double buffer = SL_Buffer_Points * point;

   if(type == ORDER_TYPE_BUY)  sl -= buffer;
   if(type == ORDER_TYPE_SELL) sl += buffer;

   return NormalizePrice(symbol, sl);
}


// ==========================================================
// ================= LIFECYCLE UPGRADE ======================
// ==========================================================

// ADD BELOW processedSignals[]
string trackedTradeIDs[];
ulong trackedTickets[];
int trackedCount = 0;

// ==========================================================
// TRACK TRADE ID
// ==========================================================

void TrackTrade(string tradeID, ulong ticket)
{
   if(tradeID == "") return;

   trackedTradeIDs[trackedCount] = tradeID;
   trackedTickets[trackedCount] = ticket;

   trackedCount++;
}

// ==========================================================
// FIND TICKET
// ==========================================================

ulong FindTrackedTicket(string tradeID)
{
   for(int i = 0; i < trackedCount; i++)
   {
      if(trackedTradeIDs[i] == tradeID)
         return trackedTickets[i];
   }

   return 0;
}

// ==========================================================
// NOTES
// ==========================================================
//
// 1. Add inside OnInit():
//
// ArrayResize(trackedTradeIDs, 1000);
// ArrayResize(trackedTickets, 1000);
//
// 2. Inside ParseSignals():
//
// string tradeID = JsonString(json, "trade_id", actionKey);
//
// 3. Replace ExecuteTrade calls:
//
// ExecuteTrade(symbol, ORDER_TYPE_BUY, sl, tp3, tf, tradeID);
//
// ExecuteTrade(symbol, ORDER_TYPE_SELL, sl, tp3, tf, tradeID);
//
// 4. Replace ExecuteTrade header:
//
// void ExecuteTrade(
//    string symbol,
//    ENUM_ORDER_TYPE type,
//    double sl,
//    double tp3,
//    string tf,
//    string tradeID
// )
//
// 5. After successful trade open:
//
// TrackTrade(tradeID, result.order);
//
// 6. TP/SL lifecycle events:
//
// tp1_hit
// tp2_hit
// tp3_hit
// sl_hit
//
// now supported for future lifecycle management.
