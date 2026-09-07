"""High-level market-analysis workflow."""

from __future__ import annotations

import pandas as pd

from market_signal_lab.indicators import add_indicators
from market_signal_lab.signals import add_signal_scores


def analyze_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Calculate indicators and explainable signals for normalized OHLCV data."""
    return add_signal_scores(add_indicators(prices))
