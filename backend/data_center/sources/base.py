from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import pandas as pd

class MarketDataSource(ABC):
    """Interface implemented by market-data providers (IBKR, yfinance, etc.)."""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def fetch_session_bars(self, symbol: str, session_date: date) -> tuple[list[Any], int]: ...

    @abstractmethod
    def bars_to_dataframe(self, symbol: str, bars: list[Any]) -> pd.DataFrame: ...
