//+------------------------------------------------------------------+
//| Mt4Bridge.mq4 - streams prices from MetaTrader 4 to the agent     |
//| and carries its order commands back. Attach one copy per chart. |
//|                                                                  |
//| Setup (once, in the terminal):                                   |
//|   Tools > Options > Expert Advisors > Allow WebRequest for       |
//|   listed URL, and add the agent URL below (e.g. http://127.0.0.1:8000). |
//|                                                                  |
//| Trading is OFF by default (AllowTrading = false). The EA then    |
//| only streams data and never sends an order.                      |
//+------------------------------------------------------------------+
#property copyright "agent"
#property version   "1.00"
#property strict

input string BridgeUrl     = "http://127.0.0.1:8000/api/mt4";  // agent endpoint (must be whitelisted)
input string BridgeSecret  = "";           // must equal MT4_BRIDGE_SECRET in the agent's .env
input bool   AllowTrading  = false;        // true = execute order commands from the agent
input int    TickThrottleMs = 200;         // at most one tick message per symbol this often
input int    PositionsEverySec = 2;        // how often open positions and account are pushed
input int    CommandsEverySec  = 1;        // how often the EA asks for new commands
input string SymbolsCsv    = "";          // extra symbols to stream, comma separated (chart symbol is always included)
input int    MaxSlippage   = 20;          // points
input int    HISTORY_BARS  = 5000;        // closed M5 bars sent at start, per symbol (for backtests)

datetime g_lastBarTime[];
string   g_symbols[];
ulong    g_lastTickMs = 0;
datetime g_lastPositions = 0;
datetime g_lastCommands  = 0;
string   g_eaId = "";

//+------------------------------------------------------------------+
int OnInit()
{
   g_eaId = IntegerToString(AccountNumber()) + "-" + Symbol();
   BuildSymbolList();
   ArrayResize(g_lastBarTime, ArraySize(g_symbols));
   ArrayInitialize(g_lastBarTime, 0);
   PushHistory();
   EventSetTimer(1);
   Print("Mt4Bridge started for ", ArraySize(g_symbols), " symbol(s). Trading allowed: ", AllowTrading);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

//+------------------------------------------------------------------+
void OnTick()
{
   // OnTick fires on the chart symbol only. Every symbol in the list is read here, and all
   // of their quotes go out in ONE request per throttle window: WebRequest blocks the EA
   // until the server answers, so one request per symbol would slow order handling.
   ulong nowMs = GetTickCount();
   if (nowMs - g_lastTickMs < (ulong)TickThrottleMs) return;
   g_lastTickMs = nowMs;
   string batch = "";
   for (int i = 0; i < ArraySize(g_symbols); i++)
   {
      double bid = MarketInfo(g_symbols[i], MODE_BID);
      double ask = MarketInfo(g_symbols[i], MODE_ASK);
      if (bid <= 0 || ask <= 0) continue;
      int digits = (int)MarketInfo(g_symbols[i], MODE_DIGITS);
      string item = StringFormat("{\"type\":\"tick\",\"ea\":\"%s\",\"symbol\":\"%s\",\"bid\":%s,\"ask\":%s,\"time\":%d,"
                                 "\"digits\":%d,\"point\":%s,\"volume_min\":%s,\"volume_max\":%s,\"volume_step\":%s,\"contract_size\":%s}",
                                 g_eaId, g_symbols[i], DoubleToString(bid, digits), DoubleToString(ask, digits), (int)TimeCurrent(),
                                 digits, DoubleToString(MarketInfo(g_symbols[i], MODE_POINT), digits + 1),
                                 DoubleToString(MarketInfo(g_symbols[i], MODE_MINLOT), 2), DoubleToString(MarketInfo(g_symbols[i], MODE_MAXLOT), 2),
                                 DoubleToString(MarketInfo(g_symbols[i], MODE_LOTSTEP), 2), DoubleToString(MarketInfo(g_symbols[i], MODE_LOTSIZE), 2));
      batch += (batch == "" ? "" : ",") + item;
      PushNewBar(i);
   }
   if (batch != "") Post("/ingest", "[" + batch + "]");
}

//+------------------------------------------------------------------+
void OnTimer()
{
   datetime now = TimeCurrent();
   if (now - g_lastPositions >= PositionsEverySec)
   {
      g_lastPositions = now;
      PushAccountAndPositions();
   }
   if (now - g_lastCommands >= CommandsEverySec)
   {
      g_lastCommands = now;
      PollCommands();
   }
}

//+------------------------------------------------------------------+
// On start: the last 300 closed M5 bars of every symbol, so the chart is not empty.
void PushHistory()
{
   // Up to HISTORY_BARS closed M5 bars per symbol, sent in chunks of 500 so one request stays small.
   string batch = "";
   int inBatch = 0;
   for (int i = 0; i < ArraySize(g_symbols); i++)
   {
      string sym = g_symbols[i];
      int digits = (int)MarketInfo(sym, MODE_DIGITS);
      int bars = MathMin(HISTORY_BARS, iBars(sym, PERIOD_M5) - 1);
      for (int s = bars; s >= 1; s--)
      {
         string item = StringFormat("{\"type\":\"bar\",\"ea\":\"%s\",\"symbol\":\"%s\",\"tf\":\"M5\",\"time\":%d,\"o\":%s,\"h\":%s,\"l\":%s,\"c\":%s,\"v\":%d}",
                                    g_eaId, sym, (int)iTime(sym, PERIOD_M5, s),
                                    DoubleToString(iOpen(sym, PERIOD_M5, s), digits), DoubleToString(iHigh(sym, PERIOD_M5, s), digits),
                                    DoubleToString(iLow(sym, PERIOD_M5, s), digits), DoubleToString(iClose(sym, PERIOD_M5, s), digits),
                                    (int)iVolume(sym, PERIOD_M5, s));
         batch += (batch == "" ? "" : ",") + item;
         inBatch++;
         if (inBatch >= 500) { Post("/ingest", "[" + batch + "]"); batch = ""; inBatch = 0; }
      }
      if (bars > 0) g_lastBarTime[i] = iTime(sym, PERIOD_M5, 1);
   }
   if (batch != "") Post("/ingest", "[" + batch + "]");
}

//+------------------------------------------------------------------+
// The last CLOSED M5 bar of a symbol, sent once when it closes.
void PushNewBar(int idx)
{
   string sym = g_symbols[idx];
   datetime t = iTime(sym, PERIOD_M5, 1);
   if (t == 0 || t == g_lastBarTime[idx]) return;
   g_lastBarTime[idx] = t;
   string json = StringFormat("{\"type\":\"bar\",\"ea\":\"%s\",\"symbol\":\"%s\",\"tf\":\"M5\",\"time\":%d,\"o\":%s,\"h\":%s,\"l\":%s,\"c\":%s,\"v\":%d}",
                              g_eaId, sym, (int)t,
                              DoubleToString(iOpen(sym, PERIOD_M5, 1), Digits),
                              DoubleToString(iHigh(sym, PERIOD_M5, 1), Digits),
                              DoubleToString(iLow(sym, PERIOD_M5, 1), Digits),
                              DoubleToString(iClose(sym, PERIOD_M5, 1), Digits),
                              (int)iVolume(sym, PERIOD_M5, 1));
   Post("/ingest", json);
}

//+------------------------------------------------------------------+
void PushAccountAndPositions()
{
   string pos = "";
   for (int i = 0; i < OrdersTotal(); i++)
   {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if (OrderType() > OP_SELL) continue;   // only market positions
      string item = StringFormat("{\"ticket\":%d,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%s,\"open_price\":%s,\"price\":%s,\"sl\":%s,\"tp\":%s,\"profit\":%s,\"swap\":%s,\"time\":%d,\"comment\":\"%s\"}",
                                 OrderTicket(), OrderSymbol(), (OrderType() == OP_BUY ? "buy" : "sell"),
                                 DoubleToString(OrderLots(), 2), DoubleToString(OrderOpenPrice(), Digits),
                                 DoubleToString((OrderType() == OP_BUY ? MarketInfo(OrderSymbol(), MODE_BID) : MarketInfo(OrderSymbol(), MODE_ASK)), Digits),
                                 DoubleToString(OrderStopLoss(), Digits), DoubleToString(OrderTakeProfit(), Digits),
                                 DoubleToString(OrderProfit() + OrderCommission(), 2), DoubleToString(OrderSwap(), 2),
                                 (int)OrderOpenTime(), JsonEscape(OrderComment()));
      pos += (pos == "" ? "" : ",") + item;
   }
   string acct = StringFormat("{\"type\":\"account\",\"ea\":\"%s\",\"login\":%d,\"server\":\"%s\",\"currency\":\"%s\",\"balance\":%s,\"equity\":%s,\"margin\":%s,\"free_margin\":%s,\"profit\":%s,\"leverage\":%d,\"live\":%s,\"trade_allowed\":%s,\"positions\":[%s]}",
                              g_eaId, AccountNumber(), JsonEscape(AccountServer()), AccountCurrency(),
                              DoubleToString(AccountBalance(), 2), DoubleToString(AccountEquity(), 2),
                              DoubleToString(AccountMargin(), 2), DoubleToString(AccountFreeMargin(), 2),
                              DoubleToString(AccountProfit(), 2), AccountLeverage(),
                              (IsDemo() ? "false" : "true"),
                              (AllowTrading ? "true" : "false"), pos);
   Post("/ingest", acct);
}

//+------------------------------------------------------------------+
// Asks the agent for pending commands. Each line: id|action|symbol|side|volume|sl|tp|comment|ticket
void PollCommands()
{
   string body = "";
   string resp = Get("/commands?ea=" + g_eaId);
   if (resp == "") return;
   string lines[];
   int n = StringSplit(resp, '\n', lines);
   for (int i = 0; i < n; i++)
   {
      string line = StringTrimLeft(StringTrimRight(lines[i]));
      if (line == "") continue;
      string f[];
      if (StringSplit(line, '|', f) < 9) continue;
      string outcome = Execute(f[0], f[1], f[2], f[3], StrToDouble(f[4]), StrToDouble(f[5]), StrToDouble(f[6]), f[7], (int)StringToInteger(f[8]));
      body += f[0] + "|" + outcome + "\n";
   }
   if (body != "") Post("/result", body);
}

//+------------------------------------------------------------------+
string Execute(string id, string action, string sym, string side, double volume, double sl, double tp, string comment, int ticket)
{
   if (!AllowTrading) return "refused: AllowTrading is off in the EA settings";
   if (action == "order")
   {
      int cmd = (side == "buy") ? OP_BUY : OP_SELL;
      double price = (cmd == OP_BUY) ? MarketInfo(sym, MODE_ASK) : MarketInfo(sym, MODE_BID);
      int t = OrderSend(sym, cmd, volume, NormalizeDouble(price, (int)MarketInfo(sym, MODE_DIGITS)), MaxSlippage,
                        sl, tp, StringSubstr(comment, 0, 31), 20261104, 0, clrNONE);
      if (t < 0) return "error:" + IntegerToString(GetLastError());
      return "ok:" + IntegerToString(t);
   }
   if (action == "close")
   {
      if (!OrderSelect(ticket, SELECT_BY_TICKET)) return "error:not found";
      double price = (OrderType() == OP_BUY) ? MarketInfo(OrderSymbol(), MODE_BID) : MarketInfo(OrderSymbol(), MODE_ASK);
      bool ok = OrderClose(ticket, volume, NormalizeDouble(price, (int)MarketInfo(OrderSymbol(), MODE_DIGITS)), MaxSlippage, clrNONE);
      return ok ? "ok:" + IntegerToString(ticket) : "error:" + IntegerToString(GetLastError());
   }
   return "error:unknown action";
}

//+------------------------------------------------------------------+
void BuildSymbolList()
{
   ArrayResize(g_symbols, 0);
   AddSymbol(Symbol());
   string parts[];
   int n = StringSplit(SymbolsCsv, ',', parts);
   for (int i = 0; i < n; i++)
   {
      string s = StringTrimLeft(StringTrimRight(parts[i]));
      if (s != "") AddSymbol(s);
   }
}

void AddSymbol(string s)
{
   for (int i = 0; i < ArraySize(g_symbols); i++) if (g_symbols[i] == s) return;
   int k = ArraySize(g_symbols);
   ArrayResize(g_symbols, k + 1);
   g_symbols[k] = s;
}

string JsonEscape(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   return s;
}

//+------------------------------------------------------------------+
// HTTP helpers. WebRequest needs the URL whitelisted in the terminal options.
void Post(string path, string body)
{
   char data[], result[];
   string headers = "Content-Type: application/json\r\nX-Bridge-Secret: " + BridgeSecret + "\r\n";
   string resultHeaders;
   StringToCharArray(body, data, 0, StringLen(body));
   int code = WebRequest("POST", BridgeUrl + path, headers, 1000, data, result, resultHeaders);
   if (code == -1) Print("Mt4Bridge POST failed: ", GetLastError(), " (is the URL whitelisted?)");
}

string Get(string pathAndQuery)
{
   char data[], result[];
   string headers = "X-Bridge-Secret: " + BridgeSecret + "\r\n";
   string resultHeaders;
   int code = WebRequest("GET", BridgeUrl + pathAndQuery, headers, 1000, data, result, resultHeaders);
   if (code == -1) { Print("Mt4Bridge GET failed: ", GetLastError()); return ""; }
   return CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
}
//+------------------------------------------------------------------+
