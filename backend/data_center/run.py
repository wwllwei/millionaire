"""
Created on Oct 19, 2026

@author: wwllw
"""

import sys
from pathlib import Path

# Add project root (C:\stock\millionaire) so "backend" is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from backend.data_center.services.symbol_universe import (
    _download_json,
    _download_text,
    parse_sec_universe,
)

sec = _download_json(
    "https://www.sec.gov/files/company_tickers_exchange.json"
)

nasdaq = _download_text(
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
)

other = _download_text(
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
)

df = parse_sec_universe(sec, nasdaq, other)

print(f"Total symbols: {len(df)}")
print(f"ETFs: {df['is_etf'].sum()}")
print(df.head(20))
print(df["listing_exchange"].value_counts())
print(df["security_type"].value_counts())