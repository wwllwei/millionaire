# Stock Research Lab — data_center

Modular version of `data_collector_updated.py`. The source, ingestion workflow, and storage are separated so another provider (for example yfinance) can later implement `MarketDataSource` and write into the same storage layer.

## Structure

```text
data_center/
  config.py                 Paths and collector settings
  models.py                 Shared bar columns and result models
  main.py                   CLI and application lifecycle
  sources/
    base.py                 Market-data provider interface
    ibkr_source.py          IBKR connection, requests, timestamp normalization
  storage/
    base.py                 Storage interface
    duckdb_parquet.py       DuckDB schema, progress log, deduplication, Parquet export
  services/
    ingestion.py            NYSE calendar, backfill/daily ingestion workflow
  data/                     Created at runtime: DuckDB and Parquet files
  logs/                     Created at runtime
```

## Install dependencies

Use the same Python environment as the existing collector:

```bash
pip install ib_async duckdb pandas pandas-market-calendars pyarrow
```

## Run from the directory containing `data_center/`

Run a two-year backfill:

```bash
python -m data_center.main
```

Run the recent seven-calendar-day refresh:

```bash
python -m data_center.main --daily
```

Choose symbols, TWS port, or client ID:

```bash
python -m data_center.main --symbols SPY,QQQ,AAPL --port 7497 --client-id 17
```

## Behavior preserved

- One-minute TRADES bars during regular trading hours.
- NYSE session calendar; weekends and exchange holidays are skipped.
- UTC-normalized timestamps, with session-date filtering in America/New_York.
- Bounded retries for request failures, empty session responses, and pacing errors.
- `bars_1m` primary key `(symbol, timestamp)` and `INSERT OR REPLACE` deduplication.
- `download_log` progress tracking; only `complete` dates are skipped during backfill. `needs_review` and `failed` dates are retried on a later run.
- Monthly Parquet exports at `data/parquet/symbol=.../year=.../month=.../bars.parquet`.
- **No automatic reconnect:** if TWS disconnects, collection stops. Reconnect TWS manually and rerun; previously completed dates are skipped.

## Data paths

The default database is `data_center/data/stock_research.duckdb`, and logs are written to `data_center/logs/collector.log`. If you need to keep using an existing database/Parquet directory, copy the existing files into these locations or change the paths in `config.py` before running.
