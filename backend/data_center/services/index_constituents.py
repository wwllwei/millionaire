from __future__ import annotations

import logging
import re
import urllib.request
from datetime import datetime, timezone
from io import StringIO
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from ..duckdb_parquet import DuckDBParquetStorage


class IndexConstituentService:
    """Refresh and query current constituents for supported US stock indexes."""

    PROVIDER = "Wikipedia constituent lists"
    USER_AGENT = "StockResearchLab/1.0 (index-constituent-refresh)"
    INDEXES = {
        "SP500": {
            "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            "ticker_columns": ("symbol", "ticker", "ticker symbol"),
            "company_columns": ("security", "company", "company name"),
        },
        "DJIA": {
            "url": "https://en.wikipedia.org/wiki/List_of_Dow_Jones_Industrial_Average_companies",
            "ticker_columns": ("symbol", "ticker", "ticker symbol"),
            "company_columns": ("company", "security", "company name"),
        },
        "NASDAQ100": {
            "url": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
            "ticker_columns": ("ticker", "symbol", "ticker symbol"),
            "company_columns": ("company", "security", "company name"),
        },
        "RUSSELL1000": {
            "url": "https://en.wikipedia.org/wiki/List_of_Russell_1000_companies",
            "ticker_columns": ("symbol", "ticker", "ticker symbol"),
            "company_columns": ("company", "security", "company name"),
            "provider": "Wikipedia (Russell 1000 components)",
        },
        "RUSSELL2000": {
            "url": "https://stockanalysis.com/list/russell-2000-stocks/",
            "ticker_columns": ("symbol", "ticker", "ticker symbol"),
            "company_columns": ("company name", "company", "security"),
            "provider": "StockAnalysis (IWM ETF holdings proxy; not official index data)",
        },
        "RUSSELL3000": {
            "url": "https://stockanalysis.com/etf/iwv/holdings/",
            "ticker_columns": ("symbol", "ticker", "ticker symbol"),
            "company_columns": ("company name", "company", "security"),
            "provider": "StockAnalysis (IWV ETF holdings proxy; not official index data)",
        },
    }
    ALIASES = {
        "SP500": {"SP500", "SP 500", "S&P500", "S&P 500", "GSPC"},
        "DJIA": {"DJIA", "DOW", "DOW JONES", "DOW JONES INDUSTRIAL AVERAGE", "DJI"},
        "NASDAQ100": {"NASDAQ100", "NASDAQ 100", "NDX"},
        "RUSSELL1000": {"RUSSELL1000", "RUSSELL 1000", "R1000", "RUI"},
        "RUSSELL2000": {"RUSSELL2000", "RUSSELL 2000", "R2000", "RUT", "RTY"},
        "RUSSELL3000": {"RUSSELL3000", "RUSSELL 3000", "R3000", "RUA"},
    }

    def __init__(self, storage: DuckDBParquetStorage):
        self.storage = storage

    @classmethod
    def canonical_index_code(cls, value: str) -> str:
        normalized = re.sub(r"[^A-Z0-9]", "", str(value).upper())
        for index_code, aliases in cls.ALIASES.items():
            if normalized in {
                re.sub(r"[^A-Z0-9]", "", alias.upper()) for alias in aliases
            }:
                return index_code
        raise ValueError(
            f"Unsupported index {value!r}. Supported indexes: "
            f"{', '.join(cls.INDEXES)}"
        )

    @staticmethod
    def _download_html(url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": IndexConstituentService.USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")

    @staticmethod
    def _find_column(columns, candidates: tuple[str, ...]):
        by_name = {str(column).strip().casefold(): column for column in columns}
        for name in candidates:
            if name in by_name:
                return by_name[name]
        return None

    @classmethod
    def _parse_constituents(cls, html: str, spec: dict) -> pd.DataFrame:
        tables = pd.read_html(StringIO(html))
        for table in tables:
            ticker_col = cls._find_column(table.columns, spec["ticker_columns"])
            company_col = cls._find_column(table.columns, spec["company_columns"])
            if ticker_col is None or company_col is None:
                continue

            result = pd.DataFrame({
                "symbol": table[ticker_col].astype(str).str.strip().str.upper(),
                "company_name": table[company_col].astype(str).str.strip(),
            })
            result = result[
                result["symbol"].ne("")
                & result["symbol"].ne("NAN")
                & result["company_name"].ne("")
                & result["company_name"].ne("NAN")
            ]
            result = result.drop_duplicates(subset=["symbol"], keep="first")
            if not result.empty:
                return result.reset_index(drop=True)
        raise ValueError("Could not find a ticker/company table in the index page")

    def refresh_index(self, index: str) -> int:
        index_code = self.canonical_index_code(index)
        spec = self.INDEXES[index_code]
        html = self._download_html(spec["url"])
        constituents = self._parse_constituents(html, spec)
        now = datetime.now(timezone.utc)
        constituents.insert(0, "index_code", index_code)
        constituents["provider"] = spec.get("provider", self.PROVIDER)
        constituents["updated_at"] = now
        self.storage.replace_index_constituents(index_code, constituents)
        return len(constituents)

    def refresh_all(self) -> dict[str, int]:
        refreshed = {}
        for index_code in self.INDEXES:
            try:
                count = self.refresh_index(index_code)
                refreshed[index_code] = count
            except Exception as exc:
                logging.warning("Could not refresh %s constituents: %s", index_code, exc)
        return refreshed

    def get_companies_by_index(self, index: str) -> list[dict[str, str | None]]:
        return self.storage.get_companies_by_index(
            self.canonical_index_code(index)
        )

    def get_symbols_by_indexes(
        self,
        indexes: list[str],
        exchanges: list[str] | None = None,
    ) -> list[str]:
        """Resolve index constituents to saved listings on selected exchanges."""
        codes = list(dict.fromkeys(self.canonical_index_code(index) for index in indexes))
        allowed_exchanges = exchanges or ["NYSE", "Nasdaq"]
        return self.storage.get_symbols_for_indexes(codes, allowed_exchanges)
