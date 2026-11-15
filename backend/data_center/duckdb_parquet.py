from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from .config import CollectorConfig
from .models import BAR_COLUMNS
from .storage_base import BarStorage

class DuckDBParquetStorage(BarStorage):
    def __init__(self, config: CollectorConfig):
        self.config = config
        config.prepare_directories()
        self.con = duckdb.connect(str(config.db_path))
        self.con.execute("SET TimeZone = 'UTC'")
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS bars_1m (
                symbol VARCHAR NOT NULL, timestamp TIMESTAMPTZ NOT NULL,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume DOUBLE, wap DOUBLE, bar_count INTEGER,
                source VARCHAR DEFAULT 'IBKR', PRIMARY KEY (symbol, timestamp)
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS download_log (
                symbol VARCHAR NOT NULL, request_date DATE NOT NULL,
                status VARCHAR NOT NULL, row_count INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0, message VARCHAR,
                updated_at TIMESTAMPTZ DEFAULT current_timestamp,
                PRIMARY KEY (symbol, request_date)
            )
        """)
        # Additive tables only: existing bar data and download progress are preserved.
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS symbol_universe (
                symbol VARCHAR PRIMARY KEY,
                security_name VARCHAR,
                listing_exchange VARCHAR,
                security_type VARCHAR,
                is_etf BOOLEAN,
                source VARCHAR,
                refreshed_at TIMESTAMPTZ
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS company_fundamentals (
                symbol VARCHAR PRIMARY KEY,
                sector VARCHAR,
                industry VARCHAR,
                market_cap BIGINT,
                pe_ratio DOUBLE,
                forward_pe DOUBLE,
                shares_outstanding BIGINT,
                currency VARCHAR,
                provider VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS index_constituents (
                index_code VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                company_name VARCHAR,
                provider VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (index_code, symbol)
            )
        """)


    def get_universe_last_refresh(self):
        row = self.con.execute("SELECT MAX(refreshed_at) FROM symbol_universe").fetchone()
        return row[0] if row else None

    def get_symbols(self) -> list[str]:
        rows = self.con.execute("SELECT symbol FROM symbol_universe ORDER BY symbol").fetchall()
        return [row[0] for row in rows]

    def replace_symbol_universe(self, df: pd.DataFrame) -> None:
        """Atomically replace only the ticker metadata table, never market bars."""
        required = ["symbol", "security_name", "listing_exchange", "security_type", "is_etf", "source", "refreshed_at"]
        clean = df.copy()
        for column in required:
            if column not in clean.columns:
                clean[column] = None
        clean = clean[required].dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="first")
        if clean.empty:
            raise ValueError("Refusing to replace ticker universe with an empty dataset")
        self.con.register("incoming_symbol_universe", clean)
        try:
            self.con.execute("BEGIN TRANSACTION")
            self.con.execute("DELETE FROM symbol_universe")
            self.con.execute("INSERT INTO symbol_universe SELECT * FROM incoming_symbol_universe")
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        finally:
            self.con.unregister("incoming_symbol_universe")

    def upsert_company_fundamentals(self, df: pd.DataFrame) -> None:
        """Replace the current fundamentals snapshot for each supplied symbol."""
        columns = [
            "symbol", "sector", "industry", "market_cap", "pe_ratio",
            "forward_pe", "shares_outstanding", "currency", "provider",
            "updated_at",
        ]
        clean = df[columns].dropna(subset=["symbol"]).drop_duplicates(
            subset=["symbol"], keep="last"
        )
        if clean.empty:
            return
        self.con.register("incoming_company_fundamentals", clean)
        try:
            self.con.execute("""
                INSERT OR REPLACE INTO company_fundamentals
                SELECT * FROM incoming_company_fundamentals
            """)
        finally:
            self.con.unregister("incoming_company_fundamentals")

    def get_recent_fundamentals_symbols(self, since) -> set[str]:
        rows = self.con.execute(
            "SELECT symbol FROM company_fundamentals WHERE updated_at >= ?",
            [since],
        ).fetchall()
        return {row[0] for row in rows}

    def replace_index_constituents(self, index_code: str, df: pd.DataFrame) -> None:
        columns = ["index_code", "symbol", "company_name", "provider", "updated_at"]
        clean = df[columns].dropna(subset=["index_code", "symbol"]).drop_duplicates(
            subset=["index_code", "symbol"], keep="last"
        )
        if clean.empty:
            raise ValueError(f"Refusing to replace {index_code} with an empty index list")
        self.con.register("incoming_index_constituents", clean)
        try:
            self.con.execute("BEGIN TRANSACTION")
            self.con.execute("DELETE FROM index_constituents WHERE index_code = ?", [index_code])
            self.con.execute("""
                INSERT INTO index_constituents
                SELECT * FROM incoming_index_constituents
            """)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        finally:
            self.con.unregister("incoming_index_constituents")

    def get_companies_by_index(self, index_code: str) -> list[dict[str, str | None]]:
        rows = self.con.execute("""
            SELECT symbol, company_name
            FROM index_constituents
            WHERE index_code = ?
            ORDER BY symbol
        """, [index_code]).fetchall()
        return [{"symbol": row[0], "company_name": row[1]} for row in rows]

    def get_symbols_for_indexes(
        self,
        index_codes: list[str],
        listing_exchanges: list[str],
    ) -> list[str]:
        """Return index members matched to the saved exchange-aware universe."""
        if not index_codes or not listing_exchanges:
            return []
        index_placeholders = ", ".join("?" for _ in index_codes)
        exchange_placeholders = ", ".join("?" for _ in listing_exchanges)
        rows = self.con.execute(f"""
            SELECT DISTINCT universe.symbol
            FROM symbol_universe AS universe
            JOIN index_constituents AS constituent
              ON REPLACE(UPPER(universe.symbol), '.', '-') =
                 REPLACE(UPPER(constituent.symbol), '.', '-')
            WHERE constituent.index_code IN ({index_placeholders})
              AND UPPER(universe.listing_exchange) IN ({exchange_placeholders})
            ORDER BY universe.symbol
        """, [*index_codes, *(exchange.upper() for exchange in listing_exchanges)]).fetchall()
        return [row[0] for row in rows]

    def get_completed_dates(self, symbol: str) -> set[date]:
        rows = self.con.execute("SELECT request_date FROM download_log WHERE symbol = ? AND status = 'complete'", [symbol]).fetchall()
        return {row[0] for row in rows}

    def log_request(self, symbol: str, session_date: date, status: str, row_count: int = 0, attempts: int = 0, message: str | None = None) -> None:
        self.con.execute("""
            INSERT OR REPLACE INTO download_log
            (symbol, request_date, status, row_count, attempts, message, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, current_timestamp)
        """, [symbol, session_date, status, row_count, attempts, message])

    def save_bars(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        df = df[BAR_COLUMNS]
        self.con.register("incoming_bars", df)
        try:
            self.con.execute("INSERT OR REPLACE INTO bars_1m SELECT * FROM incoming_bars")
        finally:
            self.con.unregister("incoming_bars")
        return len(df)

    def export_parquet(self, symbol: str) -> None:
        months = self.con.execute("""
            SELECT DISTINCT EXTRACT(year FROM timestamp)::INTEGER AS yr,
                   EXTRACT(month FROM timestamp)::INTEGER AS mo
            FROM bars_1m WHERE symbol = ? ORDER BY yr, mo
        """, [symbol]).fetchall()
        for year, month in months:
            start = pd.Timestamp(year=int(year), month=int(month), day=1, tz="UTC")
            end = pd.Timestamp(year=int(year) + (int(month) == 12), month=(int(month) % 12) + 1, day=1, tz="UTC")
            df = self.con.execute("""
                SELECT * FROM bars_1m WHERE symbol = ? AND timestamp >= ? AND timestamp < ? ORDER BY timestamp
            """, [symbol, start.to_pydatetime(), end.to_pydatetime()]).df()
            if df.empty:
                continue
            folder = self.config.parquet_dir / f"symbol={symbol}" / f"year={year}" / f"month={month:02d}"
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / "bars.parquet"
            temp = folder / "bars.tmp.parquet"
            df.to_parquet(temp, index=False, engine="pyarrow")
            temp.replace(output)
            logging.info("Exported %s rows to %s", len(df), output)

    def close(self) -> None:
        self.con.close()
