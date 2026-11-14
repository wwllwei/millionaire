from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

if TYPE_CHECKING:
    from ..duckdb_parquet import DuckDBParquetStorage


class CompanyFundamentalsService:
    """Fetch and persist the latest company reference and valuation fields."""

    PROVIDER = "Yahoo Finance (yfinance)"
    REFRESH_INTERVAL = timedelta(days=7)

    def __init__(self, storage: DuckDBParquetStorage):
        self.storage = storage

    @staticmethod
    def _number(value, *, integer: bool = False):
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(result):
            return None
        return int(result) if integer else result

    def refresh(self, symbols: list[str]) -> int:
        """Refresh stale symbols and stop when Yahoo signals rate limiting."""
        cutoff = datetime.now(timezone.utc) - self.REFRESH_INTERVAL
        recently_updated = self.storage.get_recent_fundamentals_symbols(cutoff)
        requested = list(dict.fromkeys(
            str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()
        ))
        pending = [
            symbol for symbol in requested if symbol not in recently_updated
        ]
        updated = 0
        logging.info(
            "Refreshing fundamentals for %s symbols; skipping %s updated in the last %s days",
            len(pending),
            len(requested) - len(pending),
            self.REFRESH_INTERVAL.days,
        )
        for index, symbol in enumerate(pending, start=1):
            try:
                info = yf.Ticker(symbol).info or {}
                record = {
                    "symbol": symbol,
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "market_cap": self._number(info.get("marketCap"), integer=True),
                    "pe_ratio": self._number(info.get("trailingPE")),
                    "forward_pe": self._number(info.get("forwardPE")),
                    "shares_outstanding": self._number(
                        info.get("sharesOutstanding"), integer=True
                    ),
                    "currency": info.get("currency"),
                    "provider": self.PROVIDER,
                    "updated_at": datetime.now(timezone.utc),
                }
                self.storage.upsert_company_fundamentals(pd.DataFrame([record]))
                updated += 1
                if index % 100 == 0:
                    logging.info(
                        "Fundamentals progress: %s/%s pending symbols processed",
                        index,
                        len(pending),
                    )
            except YFRateLimitError as exc:
                logging.warning(
                    "Yahoo rate-limited fundamentals at %s after %s updates; "
                    "stopping this run. Successful records are saved; retry later. (%s)",
                    symbol,
                    updated,
                    exc,
                )
                break
            except Exception as exc:
                logging.warning("Fundamentals refresh failed for %s: %s", symbol, exc)
        logging.info("Fundamentals refresh saved %s records", updated)
        return updated
