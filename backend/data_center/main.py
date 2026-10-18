from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from .config import CollectorConfig, DEFAULT_SYMBOLS
from .services.ingestion import IngestionService
from .sources.ibkr_source import IBKRSource
from .storage.duckdb_parquet import DuckDBParquetStorage

def setup_logging(config: CollectorConfig) -> None:
    config.prepare_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(config.log_dir / "collector.log", encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR one-minute historical data collector")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols, e.g. SPY,QQQ,AAPL,MSFT")
    parser.add_argument("--years", type=int, default=2, help="Calendar years to backfill (default: 2)")
    parser.add_argument("--daily", action="store_true", help="Refresh the recent 7 calendar days only")
    parser.add_argument("--port", type=int, default=7497, help="TWS/Gateway API port")
    parser.add_argument("--client-id", type=int, default=17, help="Unique API client ID")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    if args.years < 1:
        raise ValueError("--years must be at least 1")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise ValueError("At least one symbol must be provided")

    config = CollectorConfig(ib_port=args.port, ib_client_id=args.client_id)
    setup_logging(config)
    today = date.today()
    end_day = today - timedelta(days=1)
    if args.daily:
        start_day = today - timedelta(days=7)
        refresh_days = 7
    else:
        start_day = today - timedelta(days=365 * args.years)
        refresh_days = 0

    logging.info("Mode=%s Symbols=%s Range=%s to %s", "daily" if args.daily else "backfill", symbols, start_day, end_day)
    source = IBKRSource(config)
    storage = DuckDBParquetStorage(config)
    try:
        # Deliberately no automatic reconnect loop: if TWS disconnects, stop.
        # Reconnect TWS manually and rerun; completed dates resume from download_log.
        source.connect()
        service = IngestionService(source, storage, config)
        service.run(symbols, start_day, end_day, refresh_recent_days=refresh_days)
        logging.info("All requested symbols completed successfully.")
    finally:
        source.disconnect()
        storage.close()
        logging.info("Collector stopped")

if __name__ == "__main__":
    main()
