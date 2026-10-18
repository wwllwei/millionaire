import pandas as pd
import yfinance as yf

def get_history(ticker: str, period: str = "2y") -> pd.DataFrame:
    ticker = ticker.strip().upper()
    if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
        raise ValueError("Invalid ticker format.")
    df = yf.download(ticker, period=period, interval="1d", auto_adjust=True,
                     progress=False, threads=False)
    if df is None or df.empty:
        raise ValueError(f"No price history found for {ticker}.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    cols = ["Open", "High", "Low", "Close", "Volume"]
    missing = set(cols) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    df = df[cols].rename(columns={"Open":"open","High":"high","Low":"low",
        "Close":"close","Volume":"volume"})
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.sort_index().dropna(subset=["open","high","low","close"])
    if len(df) < 30:
        raise ValueError("Insufficient price history.")
    return df
