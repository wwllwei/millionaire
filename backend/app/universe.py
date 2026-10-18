"""US-listed stock/ETF universe from Nasdaq Trader's public symbol directory."""
from __future__ import annotations
import io
import time
import urllib.request
from typing import Any
import pandas as pd

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
_CACHE: dict[str, Any] = {"loaded_at": 0.0, "rows": []}
_CACHE_TTL_SECONDS = 24 * 60 * 60

# Securities that generally aren't suitable for a long-only stock swing scanner.
_NON_COMMON_MARKERS = (
    "PREFERRED", "PREFERENCE", "WARRANT", "RIGHTS", "RIGHT ", "UNIT ",
    "DEBENTURE", "NOTE ", "NOTES ", "BOND", "DEPOSITARY SHARE",
    "WHEN ISSUED", "SUBSCRIPTION RECEIPT", "PURCHASE CONTRACT",
)

def _download_table(url: str) -> pd.DataFrame:
    request = urllib.request.Request(url, headers={"User-Agent": "StockResearchLab/0.1 contact=local-user"})
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read().decode("utf-8", errors="replace")
    frame = pd.read_csv(io.StringIO(content), sep="|", dtype=str, on_bad_lines="skip").fillna("")
    # Nasdaq appends a footer row with the file-generation timestamp.
    first_col = frame.columns[0]
    frame = frame[~frame[first_col].astype(str).str.startswith("File Creation Time")]
    return frame

def _is_valid_symbol(symbol: str) -> bool:
    if not symbol or len(symbol) > 14:
        return False
    # Keep ordinary tickers and class-share tickers, but exclude test/temporary issues.
    return all(ch.isalnum() or ch in ".-$^" for ch in symbol)

def _is_common_stock(name: str) -> bool:
    upper = f" {name.upper()} "
    return "COMMON STOCK" in upper or "ORDINARY SHARES" in upper or "ORDINARY SHARE" in upper

def _row(symbol: str, name: str, exchange: str, is_etf: bool, source: str) -> dict[str, Any] | None:
    symbol, name = symbol.strip(), " ".join(name.split()).strip()
    if not _is_valid_symbol(symbol) or not name:
        return None
    upper = name.upper()
    if any(marker in upper for marker in _NON_COMMON_MARKERS):
        return None
    asset_type = "ETF" if is_etf else ("stock" if _is_common_stock(name) else "other")
    if asset_type == "other":
        return None
    # Yahoo Finance uses hyphens for many class-share symbols (e.g. BRK.B -> BRK-B).
    yahoo_ticker = symbol.replace(".", "-")
    return {"symbol": symbol, "yahoo_ticker": yahoo_ticker, "name": name,
            "exchange": exchange, "asset_type": asset_type, "source": source}

def get_universe(force_refresh: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not force_refresh and _CACHE["rows"] and now - _CACHE["loaded_at"] < _CACHE_TTL_SECONDS:
        return list(_CACHE["rows"])

    rows: list[dict[str, Any]] = []
    nasdaq = _download_table(NASDAQ_LISTED_URL)
    for _, r in nasdaq.iterrows():
        if str(r.get("Test Issue", "N")).upper() == "Y":
            continue
        symbol = str(r.get("Symbol", "")).strip()
        name = str(r.get("Security Name", "")).strip()
        is_etf = str(r.get("ETF", "N")).upper() == "Y"
        item = _row(symbol, name, "NASDAQ", is_etf, "Nasdaq Trader")
        if item:
            rows.append(item)

    other = _download_table(OTHER_LISTED_URL)
    for _, r in other.iterrows():
        if str(r.get("Test Issue", "N")).upper() == "Y":
            continue
        symbol = str(r.get("NASDAQ Symbol", "")).strip() or str(r.get("ACT Symbol", "")).strip()
        name = str(r.get("Security Name", "")).strip()
        exchange_code = str(r.get("Exchange", "")).strip().upper()
        exchange = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}.get(exchange_code, "Other US exchange")
        is_etf = str(r.get("ETF", "N")).upper() == "Y"
        item = _row(symbol, name, exchange, is_etf, "Nasdaq Trader")
        if item:
            rows.append(item)

    # De-duplicate by provider ticker; prefer an ETF-labelled record if duplicates exist.
    unique: dict[str, dict[str, Any]] = {}
    for item in rows:
        key = item["yahoo_ticker"].upper()
        if key not in unique or item["asset_type"] == "ETF":
            unique[key] = item
    result = sorted(unique.values(), key=lambda x: (x["symbol"].upper(), x["name"].upper()))
    _CACHE.update({"loaded_at": now, "rows": result})
    return list(result)
