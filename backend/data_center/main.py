from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from .config import CollectorConfig, DEFAULT_SYMBOLS
from .services.ingestion import IngestionService
from .services.company_fundamentals import CompanyFundamentalsService
from .services.index_constituents import IndexConstituentService
from .services.symbol_universe import SymbolUniverseService
from .sources.ibkr_source import IBKRSource
from .duckdb_parquet import DuckDBParquetStorage


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
        "--indexes",
        default=None,
        help="Collect members of comma-separated saved indexes, e.g. SP500,NASDAQ100 (NYSE and Nasdaq listings by default)",
    )
    parser.add_argument(
        "--exchanges",
        default="NYSE,Nasdaq",
        help="Listing exchanges to include with --indexes, e.g. NYSE,Nasdaq",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the latest unfinished IBKR collection using its saved symbols and date range",
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=None,
        help="Explicit first calendar date, YYYY-MM-DD (use with --end-date)",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="Explicit last calendar date, YYYY-MM-DD (use with --start-date)",
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
        "--refresh-fundamentals",
        action="store_true",
        help="Refresh company fundamentals for the selected symbols",
    )
    parser.add_argument(
        "--fundamentals-only",
        action="store_true",
        help="Refresh fundamentals for the universe (or --symbols selection) and exit",
    )
    parser.add_argument(
        "--refresh-indexes",
        action="store_true",
        help="Refresh supported index constituent lists",
    )
    parser.add_argument(
        "--indexes-only",
        action="store_true",
        help="Refresh index constituent lists and exit",
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

        --indexes: Collect current members of one or more saved indexes.
                   Defaults to listings marked NYSE or Nasdaq; --exchanges can
                   override that filter. --symbols takes precedence.

        --refresh-universe: Force refresh the ticker list from Nasdaq Trader.
                            The universe is also refreshed automatically when 7 days old.

        --universe-only: Refresh ticker metadata and CSV backup, then exit.
                         It does not connect to IBKR or download market bars.

        --years: Number of calendar years to backfill.
                 Must be at least 1. Defaults to 2.
                 Ignored when --daily is specified.

        --daily: Refresh data for the recent seven calendar days
                 instead of performing a historical backfill.

        --resume: Resume the latest unfinished collection. The saved symbol
                  list and date range are reused; completed symbol/date sessions
                  are skipped.

        --start-date / --end-date: Set an exact date range for a collection.
                  Useful to replay a run created before job snapshots existed;
                  completed sessions in download_log will be skipped.

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
        - Job snapshots and download_log allow collections to resume with --resume.
        - The collection end date is yesterday.
    """

    args = parse_args()
    has_start_date = args.start_date is not None
    has_end_date = args.end_date is not None
    if has_start_date != has_end_date:
        raise ValueError("--start-date and --end-date must be supplied together")
    if has_start_date and args.start_date > args.end_date:
        raise ValueError("--start-date must be on or before --end-date")
    if args.daily and has_start_date:
        raise ValueError("Use either --daily or an explicit --start-date/--end-date range")
    if args.years < 1 and not args.resume and not has_start_date:
        raise ValueError("--years must be at least 1")
    if args.resume and any((
        args.symbols is not None,
        args.indexes is not None,
        args.universe,
        args.daily,
        args.refresh_fundamentals,
        args.fundamentals_only,
        args.refresh_indexes,
        args.indexes_only,
        args.universe_only,
        args.refresh_universe,
        has_start_date,
    )):
        raise ValueError("--resume uses the saved collection plan; remove other selection/refresh flags")
    if args.symbols is not None and (args.universe or args.indexes is not None):
        logging.warning("--symbols was also supplied; it takes precedence over --universe/--indexes")
    elif args.indexes is not None and args.universe:
        logging.warning("Both --indexes and --universe were supplied; --indexes takes precedence")

    config = CollectorConfig(ib_port=args.port, ib_client_id=args.client_id)
    setup_logging(config)
    storage = DuckDBParquetStorage(config)
    universe = SymbolUniverseService(config, storage)
    try:
        if args.resume:
            job = storage.get_latest_incomplete_collection_job()
            if job is None:
                raise ValueError(
                    "No unfinished collection snapshot is available. For a run "
                    "started before snapshots were added, repeat its original "
                    "selection and date range; completed sessions are skipped "
                    "from download_log."
                )
            job_id = job["job_id"]
            symbols = job["symbols"]
            start_day = job["start_day"]
            end_day = job["end_day"]
            # On resume, successful dates from an original --daily run are skipped.
            refresh_days = 0
            storage.set_collection_job_status(job_id, "running")
            logging.info(
                "Resuming collection %s: %s symbols, %s to %s",
                job_id,
                len(symbols),
                start_day,
                end_day,
            )
        else:
            if not args.indexes_only:
                universe.refresh(force=args.refresh_universe or args.universe_only)
            if args.universe_only:
                logging.info("Ticker-universe refresh finished; no IBKR connection was made")
                return

            if args.refresh_indexes or args.indexes_only:
                refreshed = IndexConstituentService(storage).refresh_all()
                logging.info("Index constituent records refreshed: %s", refreshed)
            if args.indexes_only:
                return

            if args.symbols is not None:
                symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
                if not symbols:
                    raise ValueError("--symbols was provided but no valid symbols were found")
            elif args.indexes is not None:
                index_codes = [value.strip() for value in args.indexes.split(",") if value.strip()]
                exchanges = [value.strip() for value in args.exchanges.split(",") if value.strip()]
                if not index_codes:
                    raise ValueError("--indexes was provided but no index names were found")
                if not exchanges:
                    raise ValueError("--exchanges was provided but no exchanges were found")
                symbols = IndexConstituentService(storage).get_symbols_by_indexes(
                    index_codes,
                    exchanges,
                )
                if not symbols:
                    raise ValueError(
                        "No index members matched the saved symbol universe for the requested "
                        "exchanges. Run --indexes-only to refresh index lists and "
                        "--universe-only to refresh listing metadata."
                    )
                logging.info(
                    "Selected %s symbols from indexes %s on exchanges %s",
                    len(symbols),
                    ",".join(index_codes),
                    ",".join(exchanges),
                )
            elif args.universe or args.fundamentals_only:
                symbols = universe.get_symbols()
            else:
                # Safety default: do not accidentally start a multi-thousand-symbol backfill.
                # Use --universe explicitly to collect the full saved ticker universe.
                symbols = list(DEFAULT_SYMBOLS)
            if not symbols:
                raise ValueError("No symbols selected for collection")

            if args.refresh_fundamentals or args.fundamentals_only:
                count = CompanyFundamentalsService(storage).refresh(symbols)
                logging.info(
                    "Company fundamentals refreshed: %s of %s symbols",
                    count,
                    len(symbols),
                )
            if args.fundamentals_only:
                return

            if has_start_date:
                start_day = args.start_date
                end_day = args.end_date
                refresh_days = 0
            elif args.daily:
                today = date.today()
                end_day = today - timedelta(days=1)
                start_day = today - timedelta(days=7)
                refresh_days = 7
            else:
                today = date.today()
                end_day = today - timedelta(days=1)
                start_day = today - timedelta(days=365 * args.years)
                refresh_days = 0
            job_id = storage.create_collection_job(
                symbols,
                start_day,
                end_day,
                refresh_recent_days=refresh_days,
            )

        if args.resume:
            mode_label = "resume"
            selection_label = "saved collection snapshot"
        else:
            mode_label = "daily" if args.daily else "backfill"
            selection_label = (
                "--symbols override" if args.symbols is not None else (
                    "index members" if args.indexes is not None else
                    ("saved universe" if args.universe else "DEFAULT_SYMBOLS")
                )
            )
        logging.info(
            "Mode=%s Symbols=%s Count=%s Range=%s to %s",
            mode_label,
            selection_label,
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
            storage.set_collection_job_status(job_id, "completed")
            logging.info("All requested symbols completed successfully.")
        except KeyboardInterrupt:
            storage.set_collection_job_status(job_id, "interrupted")
            logging.warning("Collection interrupted; resume it with --resume")
            raise
        except Exception:
            storage.set_collection_job_status(job_id, "failed")
            logging.exception("Collection stopped before completion; resume it with --resume")
            raise
        finally:
            source.disconnect()
    finally:
        storage.close()
        logging.info("Collector stopped")


if __name__ == "__main__":
    main()
