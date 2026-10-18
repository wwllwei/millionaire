from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PARQUET_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "stock_research.duckdb"
LOG_DIR = BASE_DIR / "logs"

DEFAULT_SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT"]
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

@dataclass(frozen=True)
class CollectorConfig:
    ib_host: str = "127.0.0.1"
    ib_port: int = 7497
    ib_client_id: int = 17
    bar_size: str = "1 min"
    what_to_show: str = "TRADES"
    use_rth: bool = True
    request_delay_seconds: float = 1.0
    max_retries: int = 3
    request_timeout_seconds: int = 90
    connect_timeout_seconds: int = 30
    data_dir: Path = DATA_DIR
    parquet_dir: Path = PARQUET_DIR
    db_path: Path = DB_PATH
    log_dir: Path = LOG_DIR

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.parquet_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
