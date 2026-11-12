from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from .config import CollectorConfig, DEFAULT_SYMBOLS
from .services.ingestion import IngestionService
from .services.symbol_universe import SymbolUniverseService
from .sources.ibkr_source import IBKRSource
from .storage.duckdb_parquet import DuckDBParquetStorage


def setup_logging(config: CollectorConfig) -> None:
    config.prepare_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(config.log_dir / "collector.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IBKR one-minute historical data collector"
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Override the symbol selection with a comma-separated list, e.g. SPY,QQQ,AAPL,MSFT",
    )
    parser.add_argument(
        "--universe",
        action="store_true",
        help="Collect every symbol in the saved US common-stock/ETF universe (large, long-running job)",
    )
    parser.add_argument(
        "--refresh-universe",
        action="store_true",
        help="Force a refresh of the ticker universe before collection",
    )
    parser.add_argument(
        "--universe-only",
        action="store_true",
        help="Refresh the ticker universe and exit without downloading market bars",
    )
    parser.add_argument(
        "--years", type=int, default=2, help="Calendar years to backfill (default: 2)"
    )
    parser.add_argument(
        "--daily", action="store_true", help="Refresh the recent 7 calendar days only"
    )
    # parser.add_argument("--port", type=int, default=7496, help="TWS/Gateway API port")
    parser.add_argument("--port", type=int, default=4002, help="TWS/Gateway API port")
    parser.add_argument(
        "--client-id", type=int, default=17, help="Unique API client ID"
    )
    return parser.parse_args()


def main() -> None:
    """
    Run the IBKR one-minute historical stock data collector.

    Downloads 1-minute historical stock market data from IBKR/TWS and
    stores the data in DuckDB and Parquet files. Supports historical
    backfill and daily refresh of recent data.

    Command-line arguments:
        --symbols: Explicit comma-separated symbols; overrides --universe.
                   Example: --symbols SPY,QQQ,AAPL,MSFT

        --universe: Collect every saved US-listed common stock and ETF.
                    This can involve thousands of symbols and a very large backfill.

        --refresh-universe: Force refresh the ticker list from Nasdaq Trader.
                            The universe is also refreshed automatically when 7 days old.

        --universe-only: Refresh ticker metadata and CSV backup, then exit.
                         It does not connect to IBKR or download market bars.

        --years: Number of calendar years to backfill.
                 Must be at least 1. Defaults to 2.
                 Ignored when --daily is specified.

        --daily: Refresh data for the recent seven calendar days
                 instead of performing a historical backfill.

        --port: TWS or IB Gateway API port. Defaults to the configured 4002.

        --client-id: Unique IBKR API client ID. Defaults to 17.

    Examples:
        python -m data_center.main
        python -m data_center.main --symbols SPY,QQQ,AAPL
        python -m data_center.main --symbols SPY --years 3
        python -m data_center.main --daily
        python -m data_center.main --daily --symbols SPY,QQQ
        python -m data_center.main --universe-only
        python -m data_center.main --universe --daily
        python -m data_center.main --port 7497 --client-id 18

    Notes:
        - TWS or IB Gateway must be running and connected.
        - The collector does not automatically reconnect if the
          connection is lost. Reconnect manually and rerun the collector.
        - Previously completed dates can be resumed using download_log.
        - The collection end date is yesterday.
    """

    args = parse_args()
    if args.years < 1:
        raise ValueError("--years must be at least 1")
    if args.symbols is not None and args.universe:
        logging.warning("Both --symbols and --universe were supplied; --symbols takes precedence")

    config = CollectorConfig(ib_port=args.port, ib_client_id=args.client_id)
    setup_logging(config)
    storage = DuckDBParquetStorage(config)
    universe = SymbolUniverseService(config, storage)
    try:
        universe.refresh(force=args.refresh_universe or args.universe_only)
        if args.universe_only:
            logging.info("Ticker-universe refresh finished; no IBKR connection was made")
            return

        if args.symbols is not None:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if not symbols:
                raise ValueError("--symbols was provided but no valid symbols were found")
        elif args.universe:
            symbols = universe.get_symbols()
        else:
            # Safety default: do not accidentally start a multi-thousand-symbol backfill.
            # Use --universe explicitly to collect the full saved ticker universe.
            symbols = list(DEFAULT_SYMBOLS)
        if not symbols:
            raise ValueError("No symbols selected for collection")

        today = date.today()
        end_day = today - timedelta(days=1)
        if args.daily:
            start_day = today - timedelta(days=7)
            refresh_days = 7
        else:
            start_day = today - timedelta(days=365 * args.years)
            refresh_days = 0

        logging.info(
            "Mode=%s Symbols=%s Count=%s Range=%s to %s",
            "daily" if args.daily else "backfill",
            "--symbols override" if args.symbols is not None else ("saved universe" if args.universe else "DEFAULT_SYMBOLS"),
            len(symbols),
            start_day,
            end_day,
        )
        source = IBKRSource(config)
        try:
            # Deliberately no automatic reconnect loop: if TWS disconnects, stop.
            # Reconnect TWS manually and rerun; completed dates resume from download_log.
            source.connect()
            service = IngestionService(source, storage, config)
            service.run(symbols, start_day, end_day, refresh_recent_days=refresh_days)
            logging.info("All requested symbols completed successfully.")
        finally:
            source.disconnect()
    finally:
        storage.close()
        logging.info("Collector stopped")


if __name__ == "__main__":
    main()
