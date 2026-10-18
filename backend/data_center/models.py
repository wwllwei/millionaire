from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

BAR_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "wap", "bar_count", "source",
]

@dataclass(frozen=True)
class DownloadResult:
    symbol: str
    session_date: date
    row_count: int
    attempts: int
    status: str
    message: str | None = None

@dataclass(frozen=True)
class HistoricalBars:
    bars: list[Any]
    attempts: int
