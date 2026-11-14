
from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import TYPE_CHECKING

import pandas as pd

from ..config import CollectorConfig, DEFAULT_SYMBOLS
from .get_us_tickers import USER_AGENT as SEC_USER_AGENT

if TYPE_CHECKING:
    from ..duckdb_parquet import DuckDBParquetStorage


class SymbolUniverseService:
    """Maintains a weekly refreshed US common-stock and ETF universe."""

    SEC_TICKERS_URL = (
        "https://www.sec.gov/files/company_tickers_exchange.json"
    )
    NASDAQ_TRADED_URL = (
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
    )
    OTHER_LISTED_URL = (
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
    )

    REFRESH_INTERVAL = timedelta(days=7)

    EXCHANGE_NAMES = {
        "Q": "Nasdaq",
        "N": "NYSE",
        "A": "NYSE American",
        "P": "NYSE Arca",
        "Z": "Cboe BZX",
        "V": "IEX",
    }

    EXCLUDED_SECURITY_TYPES = re.compile(
        r"\b("
        r"preferred|"
        r"warrant|warrants|"
        r"right|rights|"
        r"unit|units|"
        r"subscription receipt|subscription receipts|"
        r"contingent value right|contingent value rights|"
        r"debenture|debentures|"
        r"bond|bonds|"
        r"note|notes|"
        r"exchange traded note|exchange-traded note|"
        r"depositary share|depositary shares|"
        r"when issued|when-issued|"
        r"closed.end fund"
        r")\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        config: CollectorConfig,
        storage: DuckDBParquetStorage,
    ):
        self.config = config
        self.storage = storage
        self.csv_path = config.symbols_csv_path

    # -----------------------------------------------------------------
    # Download methods
    # -----------------------------------------------------------------

    @staticmethod
    def _download_text(url: str, timeout: int = 30) -> str:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": SEC_USER_AGENT},
        )

        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8-sig")

    @classmethod
    def _download_json(cls, url: str, timeout: int = 30) -> dict:
        return json.loads(cls._download_text(url, timeout))

    # -----------------------------------------------------------------
    # Symbol helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _normalise_symbol(value) -> str:
        if value is None:
            return ""

        symbol = str(value).strip().upper()

        if symbol in {"", "NAN", "NONE"}:
            return ""

        return symbol

    @classmethod
    def _symbol_keys(cls, symbol: str) -> list[str]:
        """Generate equivalent keys for share-class symbol matching."""

        symbol = cls._normalise_symbol(symbol)

        if not symbol:
            return []

        keys = [symbol]

        for alternate in (
            symbol.replace(".", "-"),
            symbol.replace("-", "."),
        ):
            if alternate not in keys:
                keys.append(alternate)

        return keys

    @classmethod
    def _is_common_stock_or_etf(
        cls,
        security_name: str,
        is_etf: bool,
    ) -> bool:
        """Keep ETFs and securities not clearly identified as other types."""

        if is_etf:
            return True

        if not security_name:
            return False

        return cls.EXCLUDED_SECURITY_TYPES.search(
            str(security_name)
        ) is None

    # -----------------------------------------------------------------
    # Nasdaq Trader parsing
    # -----------------------------------------------------------------

    @staticmethod
    def _read_pipe_file(content: str) -> pd.DataFrame:
        lines = [
            line
            for line in content.splitlines()
            if line.strip()
            and not line.startswith("File Creation Time")
        ]

        if not lines:
            raise ValueError("Nasdaq Trader directory is empty")

        df = pd.read_csv(
            StringIO("\n".join(lines)),
            sep="|",
            dtype=str,
        ).fillna("")

        return df

    @classmethod
    def _parse_nasdaq_directories(
        cls,
        nasdaq_text: str,
        other_text: str,
    ) -> dict[str, dict]:
        """Create a lookup of listing metadata by ticker symbol."""

        directories = [
            (
                cls._read_pipe_file(other_text),
                "ACT Symbol",
                "Security Name",
                "Exchange",
            ),
            (
                cls._read_pipe_file(nasdaq_text),
                "Symbol",
                "Security Name",
                "Listing Exchange",
            ),
        ]

        lookup = {}

        for df, symbol_col, name_col, exchange_col in directories:
            required = {symbol_col, name_col, exchange_col}

            if not required.issubset(df.columns):
                raise ValueError(
                    f"Nasdaq directory missing columns: "
                    f"{required - set(df.columns)}"
                )

            for _, row in df.iterrows():
                symbol = cls._normalise_symbol(row[symbol_col])

                if not symbol:
                    continue

                if str(row.get("Test Issue", "")).strip().upper() == "Y":
                    continue

                if (
                    "Nasdaq Traded" in df.columns
                    and str(row["Nasdaq Traded"]).strip().upper() != "Y"
                ):
                    continue

                exchange_code = str(
                    row[exchange_col]
                ).strip().upper()

                metadata = {
                    "symbol": symbol,
                    "security_name": str(row[name_col]).strip(),
                    "listing_exchange": cls.EXCHANGE_NAMES.get(
                        exchange_code,
                        exchange_code,
                    ),
                    "is_etf": (
                        str(row.get("ETF", "")).strip().upper() == "Y"
                    ),
                }

                for key in cls._symbol_keys(symbol):
                    lookup.setdefault(key, metadata)

        if not lookup:
            raise ValueError(
                "No valid listings found in Nasdaq Trader directories"
            )

        return lookup

    # -----------------------------------------------------------------
    # SEC universe construction
    # -----------------------------------------------------------------

    @classmethod
    def parse_sec_universe(
        cls,
        sec_data: dict,
        nasdaq_text: str,
        other_text: str,
    ) -> pd.DataFrame:
        """
        Build the US common-stock and ETF universe.

        SEC provides ticker and company identity.
        Nasdaq Trader provides listing and security-type metadata.
        """

        if not isinstance(sec_data, dict):
            raise ValueError("Invalid SEC ticker response")

        fields = sec_data.get("fields", [])
        rows = sec_data.get("data", [])

        if not fields or not rows:
            raise ValueError("SEC ticker response contains no data")

        required_fields = {"ticker", "name", "exchange"}

        if not required_fields.issubset(set(fields)):
            raise ValueError(
                f"SEC response missing fields: "
                f"{required_fields - set(fields)}"
            )

        sec_df = pd.DataFrame(rows, columns=fields)

        metadata_lookup = cls._parse_nasdaq_directories(
            nasdaq_text,
            other_text,
        )

        records = []

        for _, row in sec_df.iterrows():
            symbol = cls._normalise_symbol(row.get("ticker"))
            company_name = str(row.get("name", "")).strip()

            if not symbol or not company_name:
                continue

            metadata = None

            for key in cls._symbol_keys(symbol):
                metadata = metadata_lookup.get(key)
                if metadata:
                    break

            # Require a matching Nasdaq Trader listing.
            if metadata is None:
                continue

            is_etf = metadata["is_etf"]
            listing_name = metadata["security_name"]

            if not cls._is_common_stock_or_etf(
                listing_name,
                is_etf,
            ):
                continue

            records.append({
                "symbol": symbol,
                "security_name": company_name,
                "listing_exchange": metadata["listing_exchange"],
                "security_type": (
                    "ETF" if is_etf else "COMMON_STOCK"
                ),
                "is_etf": is_etf,
                "source": "SEC + Nasdaq Trader",
            })

        if not records:
            raise ValueError(
                "No common stocks or ETFs matched between SEC "
                "and Nasdaq Trader directories"
            )

        df = pd.DataFrame(records)

        return (
            df.drop_duplicates(subset=["symbol"], keep="first")
            .sort_values("symbol")
            .reset_index(drop=True)
        )

    # -----------------------------------------------------------------
    # Refresh and storage
    # -----------------------------------------------------------------

    def refresh_due(self) -> bool:
        last_refresh = self.storage.get_universe_last_refresh()

        if last_refresh is None:
            return True

        if last_refresh.tzinfo is None:
            last_refresh = last_refresh.replace(tzinfo=timezone.utc)

        return (
            datetime.now(timezone.utc) - last_refresh
            >= self.REFRESH_INTERVAL
        )

    def refresh(self, force: bool = False) -> bool:
        if not force and not self.refresh_due():
            logging.info(
                "Ticker universe is current; next refresh is due after 7 days"
            )
            return False

        logging.info(
            "Refreshing US ticker universe from SEC and Nasdaq Trader"
        )

        try:
            sec_data = self._download_json(self.SEC_TICKERS_URL)
            nasdaq_text = self._download_text(self.NASDAQ_TRADED_URL)
            other_text = self._download_text(self.OTHER_LISTED_URL)

            df = self.parse_sec_universe(
                sec_data,
                nasdaq_text,
                other_text,
            )

            df["refreshed_at"] = datetime.now(timezone.utc)

            self.storage.replace_symbol_universe(df)
            self._write_csv_backup(df)

            logging.info(
                "Ticker universe refreshed: %s symbols (%s ETFs)",
                len(df),
                int(df["is_etf"].sum()),
            )

            return True

        except Exception as exc:
            logging.warning(
                "Online ticker-universe refresh failed: %s",
                exc,
            )

            existing = self.storage.get_symbols()

            if existing:
                logging.warning(
                    "Using %s symbols from existing DuckDB universe",
                    len(existing),
                )
                return False

            if self._load_csv_backup():
                logging.warning(
                    "Loaded ticker universe from CSV backup: %s symbols",
                    len(self.storage.get_symbols()),
                )
                return False

            logging.warning(
                "No saved ticker universe available; "
                "collector can still use DEFAULT_SYMBOLS"
            )

            return False

    # -----------------------------------------------------------------
    # CSV backup
    # -----------------------------------------------------------------

    def _write_csv_backup(self, df: pd.DataFrame) -> None:
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        columns = [
            "symbol",
            "security_name",
            "listing_exchange",
            "security_type",
            "is_etf",
            "source",
            "refreshed_at",
        ]

        temp_path = self.csv_path.with_suffix(
            self.csv_path.suffix + ".tmp"
        )

        df[columns].to_csv(
            temp_path,
            index=False,
            encoding="utf-8",
        )

        temp_path.replace(self.csv_path)

    def _load_csv_backup(self) -> bool:
        if not self.csv_path.exists():
            return False

        try:
            df = pd.read_csv(
                self.csv_path,
                dtype={"symbol": str},
            )

            required = {
                "symbol",
                "security_name",
                "listing_exchange",
                "security_type",
                "is_etf",
            }

            if not required.issubset(df.columns) or df.empty:
                return False

            if "source" not in df.columns:
                df["source"] = "CSV backup"

            if "refreshed_at" not in df.columns:
                df["refreshed_at"] = datetime.fromtimestamp(
                    self.csv_path.stat().st_mtime,
                    timezone.utc,
                )

            df["symbol"] = df["symbol"].map(self._normalise_symbol)

            df["is_etf"] = (
                df["is_etf"]
                .astype(str)
                .str.lower()
                .isin(["true", "1", "y", "yes"])
            )

            df["refreshed_at"] = pd.to_datetime(
                df["refreshed_at"],
                utc=True,
                errors="coerce",
            )

            if df["refreshed_at"].isna().all():
                df["refreshed_at"] = datetime.fromtimestamp(
                    self.csv_path.stat().st_mtime,
                    timezone.utc,
                )

            self.storage.replace_symbol_universe(df)

            return True

        except Exception as exc:
            logging.warning(
                "Could not load ticker-universe CSV backup %s: %s",
                self.csv_path,
                exc,
            )
            return False

    def get_symbols(self) -> list[str]:
        symbols = self.storage.get_symbols()

        if symbols:
            return symbols

        if self._load_csv_backup():
            symbols = self.storage.get_symbols()

            if symbols:
                return symbols

        logging.warning(
            "No saved ticker universe found; falling back to DEFAULT_SYMBOLS"
        )

        return list(DEFAULT_SYMBOLS)


# ---------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print("Downloading SEC ticker data...")
    sec_data = SymbolUniverseService._download_json(
        SymbolUniverseService.SEC_TICKERS_URL
    )

    print("Downloading Nasdaq Trader directories...")
    nasdaq_text = SymbolUniverseService._download_text(
        SymbolUniverseService.NASDAQ_TRADED_URL
    )
    other_text = SymbolUniverseService._download_text(
        SymbolUniverseService.OTHER_LISTED_URL
    )

    print("Building ticker universe...")
    df = SymbolUniverseService.parse_sec_universe(
        sec_data,
        nasdaq_text,
        other_text,
    )

    print("\nUniverse summary")
    print("----------------")
    print(f"Total symbols: {len(df):,}")
    print(f"ETFs: {int(df['is_etf'].sum()):,}")
    print(f"Common stocks: {int((~df['is_etf']).sum()):,}")

    print("\nBy exchange")
    print("-----------")
    print(df["listing_exchange"].value_counts().to_string())

    print("\nBy security type")
    print("----------------")
    print(df["security_type"].value_counts().to_string())

    print("\nSample records")
    print("--------------")
    print(df.head(20).to_string(index=False))

    # Save a test CSV in the current working directory.
    output_path = "symbol_universe_test.csv"
    df.to_csv(output_path, index=False, encoding="utf-8")

    print(f"\nTest CSV saved to: {output_path}")


if __name__ == "__main__":
    main()
