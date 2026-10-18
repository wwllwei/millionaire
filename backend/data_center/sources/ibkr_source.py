from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from ib_async import IB, Stock

from ..config import CollectorConfig, NY
from ..models import BAR_COLUMNS
from .base import MarketDataSource

class IBKRSource(MarketDataSource):
    def __init__(self, config: CollectorConfig):
        self.config = config
        self.ib = IB()

    def connect(self) -> None:
        logging.info("Connecting to IBKR at %s:%s, client ID %s", self.config.ib_host, self.config.ib_port, self.config.ib_client_id)
        self.ib.connect(self.config.ib_host, self.config.ib_port, clientId=self.config.ib_client_id, timeout=self.config.connect_timeout_seconds)
        if not self.ib.isConnected():
            raise ConnectionError("Could not connect to IBKR")
        logging.info("Connected to IBKR")

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def _get_contract(self, symbol: str):
        qualified = self.ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
        if not qualified:
            raise ValueError(f"IBKR could not qualify contract for {symbol}")
        return qualified[0]

    @staticmethod
    def _is_pacing_error(error_code: Any, error_message: Any) -> bool:
        message = str(error_message).lower()
        phrases = ("pacing violation", "pacing limit", "too many requests", "rate limit", "request rate exceeded")
        return any(phrase in message for phrase in phrases)

    def fetch_session_bars(self, symbol: str, session_date: date) -> tuple[list[Any], int]:
        contract = self._get_contract(symbol)
        end_dt = (datetime.combine(session_date + timedelta(days=1), datetime.min.time()).strftime("%Y%m%d %H:%M:%S") + " US/Eastern")
        last_error: Exception | None = None

        for attempt in range(1, self.config.max_retries + 1):
            request_errors: list[tuple[Any, str]] = []

            def on_error(req_id, error_code, error_string, *args):
                if self._is_pacing_error(error_code, error_string):
                    request_errors.append((error_code, str(error_string)))

            self.ib.errorEvent += on_error
            try:
                bars = self.ib.reqHistoricalData(
                    contract, endDateTime=end_dt, durationStr="1 D",
                    barSizeSetting=self.config.bar_size, whatToShow=self.config.what_to_show,
                    useRTH=self.config.use_rth, formatDate=2, keepUpToDate=False,
                    timeout=self.config.request_timeout_seconds,
                )
                if request_errors:
                    details = "; ".join(f"code {code}: {message}" for code, message in request_errors)
                    raise RuntimeError(f"IBKR pacing error: {details}")
                if bars:
                    return list(bars), attempt
                last_error = RuntimeError(f"IBKR returned 0 bars for NYSE session {session_date}")
                if attempt < self.config.max_retries:
                    delay = 3 * attempt
                    logging.warning("%s %s returned 0 bars (attempt %s/%s); retrying in %s seconds", symbol, session_date, attempt, self.config.max_retries, delay)
                    time.sleep(delay)
                    continue
            except Exception as exc:
                last_error = exc
                if not self.is_connected():
                    raise ConnectionError("IBKR disconnected during historical request") from exc
                pacing = "pacing" in str(exc).lower() or self._is_pacing_error(None, str(exc))
                if attempt >= self.config.max_retries:
                    break
                delay = 30 * (2 ** (attempt - 1)) if pacing else attempt * 3
                logging.warning("%s %s attempt %s/%s failed: %s. Retrying in %s seconds", symbol, session_date, attempt, self.config.max_retries, exc, delay)
                time.sleep(delay)
            finally:
                self.ib.errorEvent -= on_error

        raise RuntimeError(f"Request failed for {symbol} on {session_date}: {last_error}") from last_error

    @staticmethod
    def _parse_timestamp(value):
        if isinstance(value, (int, float)):
            return pd.to_datetime(value, unit="s", utc=True)
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize(NY)
        return ts.tz_convert("UTC")

    def bars_to_dataframe(self, symbol: str, bars: list[Any]) -> pd.DataFrame:
        records = [{
            "symbol": symbol,
            "timestamp": self._parse_timestamp(bar.date),
            "open": float(bar.open), "high": float(bar.high),
            "low": float(bar.low), "close": float(bar.close),
            "volume": float(bar.volume), "wap": float(bar.average),
            "bar_count": int(bar.barCount), "source": "IBKR",
        } for bar in bars]
        df = pd.DataFrame(records, columns=BAR_COLUMNS)
        if not df.empty:
            df = df.drop_duplicates(subset=["symbol", "timestamp"], keep="last").sort_values("timestamp")
        return df
