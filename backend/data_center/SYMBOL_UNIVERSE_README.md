# US ticker universe integration

This is an additive change to the existing `data_center` package. It does not delete, migrate, or rewrite `bars_1m` or `download_log` data. It adds a `symbol_universe` table to the existing DuckDB file and a CSV backup at `data/symbols.csv`.

## Commands

Run commands from the directory that contains the `data_center` package:

```powershell
# Refresh ticker metadata only; does not connect to IBKR
python -m data_center.main --universe-only

# Existing safe default: use DEFAULT_SYMBOLS (currently configured in config.py)
python -m data_center.main --daily

# Explicit symbols always override universe selection
python -m data_center.main --symbols SPY,QQQ,AAPL --daily

# Collect the complete saved common-stock + ETF universe for the recent 7 days
python -m data_center.main --universe --daily

# Backfill two years for the full universe (potentially a very large, long-running job)
python -m data_center.main --universe

# Force a metadata refresh, then collect explicit symbols
python -m data_center.main --refresh-universe --symbols META,TSLA --daily
```

The universe refresh is checked on each run and downloaded at most once per seven days unless `--refresh-universe` or `--universe-only` is specified. The downloader reads Nasdaq Trader's `nasdaqtraded.txt` and `otherlisted.txt` directories, filters out test issues and non-common-stock instruments, and uses the directory's ETF flag to identify ETFs.

If the download fails, the service keeps an existing DuckDB universe. If the table is empty, it tries `data/symbols.csv`; if no saved universe is available, collection can still proceed with `DEFAULT_SYMBOLS` unless `--symbols` was explicitly provided. A malformed/empty download is not allowed to replace a valid saved universe.

The universe contains directory-listed candidates, not a guarantee that IBKR can qualify every ticker or provide historical bars for it. IBKR contract qualification and collection failures remain recorded by the existing collector. TWS/IB Gateway reconnection behavior is unchanged: the collector stops on disconnect and must be rerun after manual reconnection.
