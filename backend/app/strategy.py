import math
import numpy as np
import pandas as pd

def indicators(df):
    x = df.copy()
    c = x.close
    x["sma20"] = c.rolling(20).mean()
    x["sma50"] = c.rolling(50).mean()
    x["sma200"] = c.rolling(200).mean()
    d = c.diff()
    gain, loss = d.clip(lower=0).rolling(14).mean(), (-d.clip(upper=0)).rolling(14).mean()
    x["rsi14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    prev = c.shift()
    x["atr14"] = pd.concat([x.high-x.low,(x.high-prev).abs(),(x.low-prev).abs()],axis=1).max(axis=1).rolling(14).mean()
    return x

def safe(v, digits=3):
    return None if pd.isna(v) or not np.isfinite(v) else round(float(v), digits)

def get_signal(r):
    keys = ["sma20","sma50","sma200","rsi14","atr14"]
    if any(pd.isna(r[k]) for k in keys):
        return "INSUFFICIENT_DATA", ["More historical data is required."]
    trend = r.close > r.sma50 > r.sma200
    pullback = abs(r.close-r.sma20)/r.sma20 <= .025
    rsi_ok = 40 <= r.rsi14 <= 65
    reasons = [
        "Close > SMA50 > SMA200: " + ("yes" if trend else "no"),
        "Within 2.5% of SMA20: " + ("yes" if pullback else "no"),
        "RSI between 40 and 65: " + ("yes" if rsi_ok else "no")]
    return ("WATCH" if trend and pullback and rsi_ok else "NO_SETUP"), reasons

def analyze(ticker, history):
    x = indicators(history)
    r = x.iloc[-1]
    signal, reasons = get_signal(r)
    recent = x.tail(260).reset_index()
    bars = []
    for _, q in recent.iterrows():
        bars.append({"date":pd.Timestamp(q.iloc[0]).date().isoformat(),
            **{k:safe(q[k]) for k in ["open","high","low","close","sma20","sma50","sma200","rsi14","atr14"]},
            "volume":int(q.volume) if pd.notna(q.volume) else None})
    return {"ticker":ticker,"as_of":x.index[-1].date().isoformat(),"signal":signal,
        "signal_reasons":reasons,
        "latest":{k:safe(r[k]) for k in ["close","sma20","sma50","sma200","rsi14","atr14"]},
        "bars":bars,"data_source":"yfinance prototype; daily adjusted OHLC"}

def backtest(ticker, history, initial_cash=20000.0, risk_fraction=.005, fee_bps=2, slippage_bps=5):
    x = indicators(history).dropna(subset=["sma20","sma50","sma200","rsi14","atr14"])
    if len(x) < 30: raise ValueError("Insufficient history after indicators.")
    cash, shares, entry, stop, entry_date = float(initial_cash), 0, None, None, None
    cost = (fee_bps + slippage_bps) / 10000
    trades, curve = [], []
    for i in range(len(x)-1):
        r, nxt = x.iloc[i], x.iloc[i+1]
        date = x.index[i+1].date().isoformat()
        if shares:
            px = reason = None
            if nxt.open <= stop: px, reason = float(nxt.open), "stop_gap"
            elif nxt.low <= stop: px, reason = stop, "stop"
            elif r.close < r.sma50: px, reason = float(nxt.open), "trend_exit"
            if px is not None:
                proceeds = shares * px * (1-cost)
                pnl = proceeds - shares*entry
                cash += proceeds
                trades.append({"entry_date":entry_date,"exit_date":date,"shares":shares,
                    "entry_price":round(entry,3),"exit_price":round(px*(1-cost),3),
                    "pnl":round(pnl,2),"reason":reason})
                shares, entry, stop, entry_date = 0, None, None, None
        if not shares:
            sig, _ = get_signal(r)
            if sig == "WATCH":
                raw = float(nxt.open)
                risk = 2*float(r.atr14)
                qty = min(math.floor(cash*risk_fraction/risk) if risk>0 else 0,
                          math.floor(cash/(raw*(1+cost))))
                fill = raw*(1+cost)
                if qty>0 and qty*fill<=cash:
                    cash -= qty*fill
                    shares, entry, stop, entry_date = qty, fill, raw-risk, date
        curve.append({"date":x.index[i].date().isoformat(),"equity":round(cash+shares*float(r.close),2)})
    final = cash + shares*float(x.iloc[-1].close)
    if shares:
        trades.append({"entry_date":entry_date,"exit_date":None,"shares":shares,
          "entry_price":round(entry,3),"exit_price":None,"pnl":None,"reason":"open_position_at_end"})
    closed = [t for t in trades if t["pnl"] is not None]
    wins = [t for t in closed if t["pnl"]>0]
    losses = abs(sum(t["pnl"] for t in closed if t["pnl"]<0))
    peak, dd = initial_cash, 0
    for p in curve:
        peak=max(peak,p["equity"])
        if peak: dd=min(dd,(p["equity"]-peak)/peak)
    years=max((x.index[-1]-x.index[0]).days/365.25,.01)
    return {"ticker":ticker,"period_start":x.index[0].date().isoformat(),"period_end":x.index[-1].date().isoformat(),
      "initial_cash":round(initial_cash,2),"final_equity":round(final,2),
      "total_return_pct":round((final/initial_cash-1)*100,2),
      "cagr_pct":round(((final/initial_cash)**(1/years)-1)*100,2) if final>0 else -100,
      "max_drawdown_pct":round(dd*100,2),"closed_trades":len(closed),
      "win_rate_pct":round(len(wins)/len(closed)*100,2) if closed else None,
      "profit_factor":round(sum(t["pnl"] for t in wins)/losses,2) if losses else None,
      "open_position":bool(shares),"trades":trades[-100:],"equity_curve":curve[-500:],
      "assumptions":["Long-only, one ticker, no leverage.","No dividends, taxes, or delisted-stock bias.",
      "Simplified fills and costs; results do not predict future performance."]}
