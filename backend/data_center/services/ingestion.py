from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas_market_calendars as mcal

from ..config import CollectorConfig, NY
from ..models import DownloadResult
from ..sources.base import MarketDataSource
from ..storage.base import BarStorage

NYSE_CALENDAR = mcal.get_calendar("NYSE")

def calendar_dates(start_day: date, end_day: date):
    """Yield NYSE sessions only, excluding weekends and exchange holidays."""
    if start_day > end_day:
        return
    schedule = NYSE_CALENDAR.schedule(start_date=start_day.isoformat(), end_date=end_day.isoformat())
    for session in schedule.index:
        yield session.date()

class IngestionService:
    def __init__(self, source: MarketDataSource, storage: BarStorage, config: CollectorConfig):
        self.source = source
        self.storage = storage
        self.config = config

    def collect_symbol(self, symbol: str, start_day: date, end_day: date, refresh_recent_days: int = 0) -> None:
        logging.info("Starting %s from %s through %s", symbol, start_day, end_day)
        completed = self.storage.get_completed_dates(symbol)
        refresh_from = date.today() - timedelta(days=refresh_recent_days) if refresh_recent_days else None

        for day in calendar_dates(start_day, end_day):
            force_refresh = refresh_from is not None and day >= refresh_from
            if day in completed and not force_refresh:
                continue
            attempts = 0
            try:
                bars, attempts = self.source.fetch_session_bars(symbol, day)
                df = self.source.bars_to_dataframe(symbol, bars)
                if not df.empty:
                    local_dates = df["timestamp"].dt.tz_convert(NY).dt.date
                    df = df.loc[local_dates == day].copy()
                count = self.storage.save_bars(df)
                if count == 0:
                    message = f"NYSE session returned {len(bars)} raw bars, but no bars matched the requested New York date after filtering"
                    self.storage.log_request(symbol, day, "needs_review", attempts=attempts, message=message)
                    logging.error("%s %s: 0 usable bars (raw=%s); marked needs_review", symbol, day, len(bars))
                else:
                    self.storage.log_request(symbol, day, "complete", row_count=count, attempts=attempts)
                    logging.info("%s %s: %s bars (raw=%s)", symbol, day, count, len(bars))
            except Exception as exc:
                self.storage.log_request(symbol, day, "failed", attempts=attempts, message=str(exc)[:1000])
                logging.exception("Failed %s on %s", symbol, day)
                # Stop immediately if TWS disconnects. Reconnect manually and rerun;
                # completed dates are skipped based on download_log.
                if not self.source.is_connected():
                    raise ConnectionError("IBKR disconnected; stopping collector. Reconnect TWS and rerun.") from exc
            time.sleep(self.config.request_delay_seconds)

        self.storage.export_parquet(symbol)
        logging.info("Finished %s", symbol)

    def run(self, symbols: list[str], start_day: date, end_day: date, refresh_recent_days: int = 0) -> None:
        for symbol in symbols:
            self.collect_symbol(symbol, start_day, end_day, refresh_recent_days)
