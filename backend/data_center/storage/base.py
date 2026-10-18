from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
import pandas as pd

class BarStorage(ABC):
    @abstractmethod
    def get_completed_dates(self, symbol: str) -> set[date]: ...

    @abstractmethod
    def save_bars(self, df: pd.DataFrame) -> int: ...

    @abstractmethod
    def log_request(self, symbol: str, session_date: date, status: str, row_count: int = 0, attempts: int = 0, message: str | None = None) -> None: ...

    @abstractmethod
    def export_parquet(self, symbol: str) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
