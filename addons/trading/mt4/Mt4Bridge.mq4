//+------------------------------------------------------------------+
//| Mt4Bridge.mq4 - streams prices from MetaTrader 4 to the agent     |
//| and carries its order commands back. Attach one copy per chart. |
//|                                                                  |
//| Setup (once, in the terminal):                                   |
//|   Tools > Options > Expert Advisors > Allow WebRequest for       |
//|   listed URL, and add http://127.0.0.1 (python main.py trading, port 80). |
//|                                                                  |
//| AllowTrading = false makes the EA stream data only and refuse    |
//| every order. Set it to false before attaching to a real account. |
//+------------------------------------------------------------------+
#property copyright "agent"
#property version   "1.00"
#property strict

input string BridgeUrl     = "http://127.0.0.1";  // agent endpoint (must be whitelisted)
input string BridgeSecret  = "";           // empty = read it from MQL4/Files/mt4_bridge_secret.txt
input bool   AllowTrading  = true;        // true = execute order commands from the agent
input int    TickThrottleMs = 200;         // at most one tick message per symbol this often
input int    PositionsEverySec = 1;        // how often open positions and account are pushed
input int    CommandsEveryMs   = 250;      // how often the EA asks for new commands
input string SymbolsCsv    = "";          // extra symbols to stream, comma separated (chart symbol is always included)
input int    MaxSlippage   = 20;          // points
input int    HISTORY_BARS  = 5000;        // closed M5 bars sent at start, per symbol (for backtests)

datetime g_lastBarTime[];
datetime g_lastM1Time[];
bool     g_historySent[];       // per symbol: its M5 history has reached the agent
double   g_lastBid[];          // last quote sent per symbol, to skip unchanged ticks
double   g_lastAsk[];
string   g_symbols[];
ulong    g_lastTickMs = 0;
datetime g_lastPositions = 0;
ulong    g_lastCommands  = 0;
datetime g_lastHistoryTry = 0;
datetime g_lastErrorLog = 0;
datetime g_lastDeals = 0;
string   g_eaId = "";
string   g_secret = "";         // from the input, or from the local secret file

//+------------------------------------------------------------------+
int OnInit()
{
   g_secret = LoadSecret();
   g_eaId = IntegerToString(AccountNumber()) + "-" + Symbol();
   BuildSymbolList();
   ArrayResize(g_lastBarTime, ArraySize(g_symbols));
   ArrayInitialize(g_lastBarTime, 0);
   ArrayResize(g_lastM1Time, ArraySize(g_symbols));
   ArrayInitialize(g_lastM1Time, 0);
   ArrayResize(g_historySent, ArraySize(g_symbols));
   ArrayInitialize(g_historySent, false);
   ArrayResize(g_lastBid, ArraySize(g_symbols));
   ArrayResize(g_lastAsk, ArraySize(g_symbols));
   ArrayInitialize(g_lastBid, 0.0);
   ArrayInitialize(g_lastAsk, 0.0);
   PushHistory();
   EventSetMillisecondTimer(50);
   Print("Mt4Bridge started for ", ArraySize(g_symbols), " symbol(s). Trading allowed: ", AllowTrading);
   return(INIT_SUCCEEDED);
}

// The secret stays out of the source and out of the chart settings when it lives in this file.
string LoadSecret()
{
   if (BridgeSecret != "") return BridgeSecret;
   int h = FileOpen("mt4_bridge_secret.txt", FILE_READ | FILE_TXT | FILE_ANSI);
   if (h == INVALID_HANDLE)
   {
      Print("Mt4Bridge: no BridgeSecret input and no MQL4/Files/mt4_bridge_secret.txt");
      return "";
   }
   string text = FileReadString(h);
   FileClose(h);
   return StringTrimLeft(StringTrimRight(text));
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

//+------------------------------------------------------------------+
void OnTick()
{
   PushQuotes();
}

// OnTick fires on the chart symbol only, so the timer calls this too. Every symbol in the list is
// read here, and all of their quotes go out in ONE request per throttle window: WebRequest blocks
// the EA until the server answers, so one request per symbol would slow order handling.
void PushQuotes()
{
   ulong nowMs = GetTickCount();
   if (nowMs - g_lastTickMs < (ulong)TickThrottleMs) return;
   g_lastTickMs = nowMs;
   string batch = "";
   for (int i = 0; i < ArraySize(g_symbols); i++)
   {
      double bid = MarketInfo(g_symbols[i], MODE_BID);
      double ask = MarketInfo(g_symbols[i], MODE_ASK);
      if (bid <= 0 || ask <= 0) continue;
      // A new closed bar goes out even when the quote has not moved.
      PushNewBar(i);
      PushNewM1(i);
      // Unchanged quotes are not sent again: most ticks on a quiet pair repeat the last price.
      if (bid == g_lastBid[i] && ask == g_lastAsk[i]) continue;
      g_lastBid[i] = bid;
      g_lastAsk[i] = ask;
      int digits = (int)MarketInfo(g_symbols[i], MODE_DIGITS);
      string item = StringFormat("{\"type\":\"tick\",\"ea\":\"%s\",\"symbol\":\"%s\",\"bid\":%s,\"ask\":%s,\"time\":%d,"
                                 "\"digits\":%d,\"point\":%s,\"volume_min\":%s,\"volume_max\":%s,\"volume_step\":%s,\"contract_size\":%s,\"tick_value\":%s,\"tick_size\":%s}",
                                 g_eaId, g_symbols[i], DoubleToString(bid, digits), DoubleToString(ask, digits), (int)TimeCurrent(),
                                 digits, DoubleToString(MarketInfo(g_symbols[i], MODE_POINT), digits + 1),
                                 DoubleToString(MarketInfo(g_symbols[i], MODE_MINLOT), 2), DoubleToString(MarketInfo(g_symbols[i], MODE_MAXLOT), 2),
                                 DoubleToString(MarketInfo(g_symbols[i], MODE_LOTSTEP), 2), DoubleToString(MarketInfo(g_symbols[i], MODE_LOTSIZE), 2),
                                 DoubleToString(MarketInfo(g_symbols[i], MODE_TICKVALUE), 8), DoubleToString(MarketInfo(g_symbols[i], MODE_TICKSIZE), 8));
      batch += (batch == "" ? "" : ",") + item;
   }
   if (batch != "") Post("/ingest", "[" + batch + "]");
}

//+------------------------------------------------------------------+
void OnTimer()
{
   // TimeLocal, not TimeCurrent: server time stops without ticks, and the heartbeat and the
   // command poll must keep going when the market is closed or the terminal is reconnecting.
   datetime now = TimeLocal();
   ulong nowMs = GetTickCount();
   if (nowMs - g_lastCommands >= (ulong)CommandsEveryMs)
   {
      g_lastCommands = nowMs;
      PollCommands();
   }
   PushQuotes();
   if (now - g_lastHistoryTry >= 30)
   {
      g_lastHistoryTry = now;
      PushHistory();     // a symbol with no history at start (terminal still connecting) is retried here
   }
   if (now - g_lastPositions >= PositionsEverySec)
   {
      g_lastPositions = now;
      PushAccountAndPositions();
   }
   if (now - g_lastDeals >= 30)
   {
      g_lastDeals = now;
      PushDeals();
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
      if (g_historySent[i]) continue;
      string sym = g_symbols[i];
      int digits = (int)MarketInfo(sym, MODE_DIGITS);
      // iBars is 0 until the symbol's history has loaded. Skip it rather than send zero prices.
      int bars = MathMin(HISTORY_BARS, iBars(sym, PERIOD_M5) - 1);
      if (bars <= 0) { Print("Mt4Bridge: no M5 history yet for ", sym, "; it will stream from new bars"); continue; }
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
      g_historySent[i] = true;
      g_lastBarTime[i] = iTime(sym, PERIOD_M5, 1);
      PushM1History(i, digits);
   }
   if (batch != "") Post("/ingest", "[" + batch + "]");
}

//+------------------------------------------------------------------+
string M1Json(string sym, int shift, int digits)
{
   return StringFormat("{\"type\":\"bar\",\"ea\":\"%s\",\"symbol\":\"%s\",\"tf\":\"M1\",\"time\":%d,\"o\":%s,\"h\":%s,\"l\":%s,\"c\":%s,\"v\":%d}",
                       g_eaId, sym, (int)iTime(sym, PERIOD_M1, shift),
                       DoubleToString(iOpen(sym, PERIOD_M1, shift), digits), DoubleToString(iHigh(sym, PERIOD_M1, shift), digits),
                       DoubleToString(iLow(sym, PERIOD_M1, shift), digits), DoubleToString(iClose(sym, PERIOD_M1, shift), digits),
                       (int)iVolume(sym, PERIOD_M1, shift));
}

// Closed M1 bars for the short-horizon backtest, in chunks of 500 like the M5 history.
void PushM1History(int idx, int digits)
{
   string sym = g_symbols[idx];
   string batch = "";
   int inBatch = 0;
   int bars = MathMin(HISTORY_BARS, iBars(sym, PERIOD_M1) - 1);
   for (int s = bars; s >= 1; s--)
   {
      batch += (batch == "" ? "" : ",") + M1Json(sym, s, digits);
      inBatch++;
      if (inBatch >= 500) { Post("/ingest", "[" + batch + "]"); batch = ""; inBatch = 0; }
   }
   if (batch != "") Post("/ingest", "[" + batch + "]");
   if (bars > 0) g_lastM1Time[idx] = iTime(sym, PERIOD_M1, 1);
}

void PushNewM1(int idx)
{
   string sym = g_symbols[idx];
   datetime t = iTime(sym, PERIOD_M1, 1);
   if (t == 0 || t == g_lastM1Time[idx]) return;
   g_lastM1Time[idx] = t;
   Post("/ingest", M1Json(sym, 1, (int)MarketInfo(sym, MODE_DIGITS)));
}

//+------------------------------------------------------------------+
// The last CLOSED M5 bar of a symbol, sent once when it closes.
void PushNewBar(int idx)
{
   string sym = g_symbols[idx];
   datetime t = iTime(sym, PERIOD_M5, 1);
   if (t == 0 || t == g_lastBarTime[idx]) return;
   g_lastBarTime[idx] = t;
   // Digits is the CHART symbol's precision. Each symbol needs its own, or prices of other symbols get rounded wrong.
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   string json = StringFormat("{\"type\":\"bar\",\"ea\":\"%s\",\"symbol\":\"%s\",\"tf\":\"M5\",\"time\":%d,\"o\":%s,\"h\":%s,\"l\":%s,\"c\":%s,\"v\":%d}",
                              g_eaId, sym, (int)t,
                              DoubleToString(iOpen(sym, PERIOD_M5, 1), digits),
                              DoubleToString(iHigh(sym, PERIOD_M5, 1), digits),
                              DoubleToString(iLow(sym, PERIOD_M5, 1), digits),
                              DoubleToString(iClose(sym, PERIOD_M5, 1), digits),
                              (int)iVolume(sym, PERIOD_M5, 1));
   Post("/ingest", json);
}

//+------------------------------------------------------------------+
void PushAccountAndPositions()
{
   string pos = "";
   string pending = "";
   for (int i = 0; i < OrdersTotal(); i++)
   {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      int pd = (int)MarketInfo(OrderSymbol(), MODE_DIGITS);   // this position's symbol, not the chart's
      if (OrderType() > OP_SELL)
      {
         bool isBuy = (OrderType() == OP_BUYLIMIT || OrderType() == OP_BUYSTOP);
         string po = StringFormat("{\"ticket\":%d,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%s,\"price\":%s,\"sl\":%s,\"tp\":%s,\"time\":%d,\"comment\":\"%s\"}",
                                  OrderTicket(), OrderSymbol(), (isBuy ? "buy" : "sell"), DoubleToString(OrderLots(), 2),
                                  DoubleToString(OrderOpenPrice(), pd), DoubleToString(OrderStopLoss(), pd),
                                  DoubleToString(OrderTakeProfit(), pd), (int)OrderOpenTime(), JsonEscape(OrderComment()));
         pending += (pending == "" ? "" : ",") + po;
         continue;
      }
      string item = StringFormat("{\"ticket\":%d,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%s,\"open_price\":%s,\"price\":%s,\"sl\":%s,\"tp\":%s,\"profit\":%s,\"swap\":%s,\"time\":%d,\"comment\":\"%s\"}",
                                 OrderTicket(), OrderSymbol(), (OrderType() == OP_BUY ? "buy" : "sell"),
                                 DoubleToString(OrderLots(), 2), DoubleToString(OrderOpenPrice(), pd),
                                 DoubleToString((OrderType() == OP_BUY ? MarketInfo(OrderSymbol(), MODE_BID) : MarketInfo(OrderSymbol(), MODE_ASK)), pd),
                                 DoubleToString(OrderStopLoss(), pd), DoubleToString(OrderTakeProfit(), pd),
                                 DoubleToString(OrderProfit() + OrderCommission(), 2), DoubleToString(OrderSwap(), 2),
                                 (int)OrderOpenTime(), JsonEscape(OrderComment()));
      pos += (pos == "" ? "" : ",") + item;
   }
   string acct = StringFormat("{\"type\":\"account\",\"ea\":\"%s\",\"login\":%d,\"server\":\"%s\",\"currency\":\"%s\",\"balance\":%s,\"equity\":%s,\"margin\":%s,\"free_margin\":%s,\"profit\":%s,\"leverage\":%d,\"ver\":2,\"live\":%s,\"trade_allowed\":%s,\"positions\":[%s],\"orders\":[%s]}",
                              g_eaId, AccountNumber(), JsonEscape(AccountServer()), AccountCurrency(),
                              DoubleToString(AccountBalance(), 2), DoubleToString(AccountEquity(), 2),
                              DoubleToString(AccountMargin(), 2), DoubleToString(AccountFreeMargin(), 2),
                              DoubleToString(AccountProfit(), 2), AccountLeverage(),
                              (IsDemo() ? "false" : "true"),
                              (AllowTrading ? "true" : "false"), pos, pending);
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
      if (line == "resync")      // the server lost its bars, as after a restart
      {
         ArrayInitialize(g_historySent, false);
         g_lastHistoryTry = 0;
         continue;
      }
      string f[];
      if (StringSplit(line, '|', f) < 9) continue;
      string outcome = Execute(f[0], f[1], f[2], f[3], StrToDouble(f[4]), StrToDouble(f[5]), StrToDouble(f[6]), f[7], (int)StringToInteger(f[8]));
      body += f[0] + "|" + outcome + "\n";
   }
   if (body != "") Post("/result", body);
}

//+------------------------------------------------------------------+
// Worth another try: the price moved or the terminal was busy, and nothing was filled.
// A timeout (128) is left out on purpose: that order may have gone through.
bool Retryable(int err)
{
   return err == 4 || err == 129 || err == 135 || err == 136 || err == 137 || err == 138 || err == 146;
}

string Execute(string id, string action, string sym, string side, double volume, double sl, double tp, string comment, int ticket)
{
   if (!AllowTrading) return "refused: AllowTrading is off in the EA settings";
   int err = 0;
   if (action == "order")
   {
      int cmd = (side == "buy") ? OP_BUY : OP_SELL;
      for (int i = 0; i < 3; i++)
      {
         RefreshRates();
         double price = (cmd == OP_BUY) ? MarketInfo(sym, MODE_ASK) : MarketInfo(sym, MODE_BID);
         int t = OrderSend(sym, cmd, volume, NormalizeDouble(price, (int)MarketInfo(sym, MODE_DIGITS)), MaxSlippage,
                           sl, tp, StringSubstr(comment, 0, 31), 20261104, 0, clrNONE);
         if (t >= 0) return "ok:" + IntegerToString(t);
         err = GetLastError();
         Print("Mt4Bridge order failed: ", err, " (try ", i + 1, " of 3)");
         if (!Retryable(err)) break;
         Sleep(300);
      }
      return "error:" + IntegerToString(err);
   }
   if (action == "close")
   {
      if (!OrderSelect(ticket, SELECT_BY_TICKET)) return "error:not found";
      string osym = OrderSymbol();
      int otype = OrderType();
      for (int k = 0; k < 3; k++)
      {
         RefreshRates();
         double closePrice = (otype == OP_BUY) ? MarketInfo(osym, MODE_BID) : MarketInfo(osym, MODE_ASK);
         if (OrderClose(ticket, volume, NormalizeDouble(closePrice, (int)MarketInfo(osym, MODE_DIGITS)), MaxSlippage, clrNONE))
            return "ok:" + IntegerToString(ticket);
         err = GetLastError();
         Print("Mt4Bridge close failed: ", err, " (try ", k + 1, " of 3)");
         if (!Retryable(err)) break;
         Sleep(300);
      }
      return "error:" + IntegerToString(err);
   }
   if (action == "modify")
   {
      if (!OrderSelect(ticket, SELECT_BY_TICKET)) return "error:not found";
      if (OrderModify(ticket, OrderOpenPrice(), sl, tp, 0, clrNONE)) return "ok:" + IntegerToString(ticket);
      err = GetLastError();
      return (err == 1) ? "ok:" + IntegerToString(ticket) : "error:" + IntegerToString(err);   // 1 = nothing to change
   }
   if (action == "cancel")
   {
      if (OrderDelete(ticket, clrNONE)) return "ok:" + IntegerToString(ticket);
      return "error:" + IntegerToString(GetLastError());
   }
   return "error:unknown action";
}

//+------------------------------------------------------------------+
// The most recent closed trades, for the panel's history tab.
void PushDeals()
{
   string deals = "";
   int sent = 0;
   for (int i = OrdersHistoryTotal() - 1; i >= 0 && sent < 100; i--)
   {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
      if (OrderType() > OP_SELL) continue;   // balance lines and deleted pending orders
      string item = StringFormat("{\"ticket\":%d,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%s,\"price\":%s,\"profit\":%s,\"time\":%d,\"comment\":\"%s\"}",
                                 OrderTicket(), OrderSymbol(), (OrderType() == OP_BUY ? "buy" : "sell"),
                                 DoubleToString(OrderLots(), 2),
                                 DoubleToString(OrderClosePrice(), (int)MarketInfo(OrderSymbol(), MODE_DIGITS)),
                                 DoubleToString(OrderProfit() + OrderCommission() + OrderSwap(), 2),
                                 (int)OrderCloseTime(), JsonEscape(OrderComment()));
      deals += (deals == "" ? "" : ",") + item;
      sent++;
   }
   Post("/ingest", StringFormat("{\"type\":\"history\",\"ea\":\"%s\",\"deals\":[%s]}", g_eaId, deals));
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
// What went wrong, at most once every 30 seconds so a dead server does not flood the log.
bool ErrorLogDue()
{
   if (TimeLocal() - g_lastErrorLog < 30) return false;
   g_lastErrorLog = TimeLocal();
   return true;
}

void LogWebError(string method)
{
   int err = GetLastError();
   if (!ErrorLogDue()) return;
   string why = "see the MQL4 error list";
   if (err == 4060) why = "URL not allowed: add http://127.0.0.1 in Tools > Options > Expert Advisors";
   if (err == 5200) why = "invalid URL: MT4 refuses a port here, use http://127.0.0.1";
   if (err == 5201 || err == 5203) why = "no answer from the server: run python main.py trading";
   if (err == 5202) why = "the server timed out";
   Print("Mt4Bridge ", method, " failed: ", err, " (", why, ")");
}

void LogHttpStatus(int code)
{
   if (!ErrorLogDue()) return;
   Print("Mt4Bridge server said ", code, code == 401 ? " (BridgeSecret is wrong or empty)" : "");
}

// HTTP helpers. WebRequest needs the URL whitelisted in the terminal options.
void Post(string path, string body)
{
   char data[], result[];
   string headers = "Content-Type: application/json\r\nX-Bridge-Secret: " + g_secret + "\r\n";
   string resultHeaders;
   StringToCharArray(body, data, 0, StringLen(body));
   int code = WebRequest("POST", BridgeUrl + path, headers, 1000, data, result, resultHeaders);
   if (code == -1) LogWebError("POST");
   else if (code >= 400) LogHttpStatus(code);
}

string Get(string pathAndQuery)
{
   char data[], result[];
   string headers = "X-Bridge-Secret: " + g_secret + "\r\n";
   string resultHeaders;
   int code = WebRequest("GET", BridgeUrl + pathAndQuery, headers, 1000, data, result, resultHeaders);
   if (code == -1) { LogWebError("GET"); return ""; }
   if (code >= 400) { LogHttpStatus(code); return ""; }
   return CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
}
//+------------------------------------------------------------------+
