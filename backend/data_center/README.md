# Stock Research Lab — data_center

The source, ingestion workflow, and storage are separated so another provider (for example yfinance) can later implement `MarketDataSource` and write into the same storage layer.

## Structure

```text
data_center/
  config.py                 Paths and collector settings
  models.py                 Shared bar columns and result models
  main.py                   CLI and application lifecycle
  sources/
    base.py                 Market-data provider interface
    ibkr_source.py          IBKR connection, requests, timestamp normalization
  storage_base.py           Storage interface
  duckdb_parquet.py         DuckDB schema, progress log, deduplication, Parquet export
  services/
    ingestion.py            NYSE calendar, backfill/daily ingestion workflow
    index_constituents.py   Current S&P 500, DJIA, and Nasdaq-100 membership
  data/                     Created at runtime: DuckDB and Parquet files
  logs/                     Created at runtime
```

## Install dependencies

Use the same Python environment as the existing collector:

```bash
pip install -r data_center/requirements.txt
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

Refresh the supported index constituent lists without connecting to IBKR:

```bash
python -m data_center.main --indexes-only
```

The supported codes are `SP500`, `DJIA`, `NASDAQ100`, `RUSSELL1000`,
`RUSSELL2000`, and `RUSSELL3000`. In Python, query the saved members with
`IndexConstituentService(storage).get_companies_by_index("R2000")`. Russell
1000 membership is sourced from a current Wikipedia component list. Russell
2000 and 3000 lists use IWM and IWV ETF holdings as public proxies, not official
FTSE Russell constituent files. These are current lists, not historical
membership data; the provider is recorded per row.

Collect one-minute bars for index members that are listed on NYSE or Nasdaq:

```bash
python -m data_center.main --indexes SP500,NASDAQ100 --daily
```

Use `--indexes SP500` to start with one index. Index-based selection joins the
saved index constituents to `symbol_universe`, then filters by `listing_exchange`.
If either list is empty, refresh both datasets first with `--universe-only` and
`--indexes-only`. Omitting `--daily` runs the normal two-year backfill, which
can take a long time for a large index selection.

Every market-data collection saves its symbol list and requested date range in
DuckDB. If the process is interrupted or errors, resume the latest unfinished
collection with:

```bash
python -m data_center.main --resume
```

The resume command uses the saved job snapshot and skips sessions already
marked `complete` in `download_log`. It retries failed or unfinished sessions.
For an original `--daily` run, it skips completed sessions during resume instead
of deliberately downloading the same recent week again.

For older runs that predate job snapshots, repeat the original symbol selection
and explicit date range. For example, the interrupted S&P 500 run in the log can
be continued with:

```bash
python -m data_center.main --indexes SP500 --start-date 2024-11-14 --end-date 2026-11-13
```

The `download_log` skips its already completed symbol/session pairs.

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
